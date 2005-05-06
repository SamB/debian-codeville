# Written by Bram Cohen and Ross Cohen
# see LICENSE.txt for license information

from Codeville.bencode import bdecode, bencode
import binascii
from Codeville.db import db
from merge import find_conflict, find_conflict_multiple_safe, MergeError, replay
from os import path
from sets import Set
from sha import sha
import struct
from time import localtime, strftime
from xml.sax import saxutils
import zlib

roothandle = '\xdfp\xce\xbaNt5\xf9\xa1\xabd\xf2\xc4\x87\xc5\xd1\x0bI\x8d\xb4'
rootnode = '\xb3L\xac\x1f\x98B\x15\\\x8c\t0&\xd7m\xecK\xdd\n\x81\xc4'

class HistoryError(Exception):
    pass

def dmerge(da, db):
    dc = {}.fromkeys(da)
    dc.update({}.fromkeys(db))
    return dc.keys()

def rename_conflict_check(linfo, rinfo):
    if linfo['name'] == rinfo['name']:
        try:
            if linfo['parent'] == rinfo['parent']:
                return ('local', dmerge(linfo['rename point'], rinfo['rename point']))
        except KeyError:
            assert linfo['rename point'] == rinfo['rename point'] == [rootnode]
            return ('local', linfo['rename point'])
    for i in rinfo['rename point']:
        if i in linfo['points']:
            return ('local', linfo['rename point'])
    for i in linfo['rename point']:
        if i in rinfo['points']:
            return ('remote', rinfo['rename point'])
    return ('conflict', None)

def _name_use_count(co, state, point, func, txn):
    cursor = co.allnamesdb.cursor(txn=txn)
    lookup = state['parent'] + state['name']
    try:
        key, value = cursor.set(lookup)
    except (db.DBNotFoundError, TypeError):
        return []
    named = []
    while key == lookup:
        vinfo = func(co, value, point, txn)
        if vinfo is not None:
            if not vinfo.has_key('delete') and vinfo['parent'] == state['parent'] and vinfo['name'] == state['name']:
                named.append(value)
        # next_dup() is broken
        foo = cursor.next()
        if foo is None:
            break
        key, value = foo
    cursor.close()
    return named

def name_use_count(co, state, point, txn):
    return _name_use_count(co, state, point, __handle_name_at_point, txn)

def _children_count(co, handle, point, func, txn):
    cursor = co.allnamesdb.cursor(txn=txn)
    try:
        foo = cursor.set_range(handle)
    except (db.DBNotFoundError, TypeError):
        foo = None
    children = {}
    while foo is not None:
        key, value = foo
        parent = key[:20]
        if parent != handle:
            break
        vinfo = func(co, value, point, txn)
        if vinfo is not None:
            if not vinfo.has_key('delete') and vinfo['parent'] == handle:
                children[value] = None
        foo = cursor.next()
    cursor.close()
    return children.keys()

def children_count(co, handle, point, txn):
    return _children_count(co, handle, point, __handle_name_at_point, txn)

def parent_loop_check(co, handle, point, txn):
    hseen = {}
    while handle != roothandle:
        if hseen.has_key(handle):
            return handle
        hseen[handle] = 1
        change = handle_last_modified(co, co.names, handle, point, txn)
        if change is None:
            return handle
        handle = _handle_name_at_point(co, handle, change, txn)['parent']
    return None

def is_ancestor(co, ancestor, point, txn):
    if point is None:
        return 0
    return _is_ancestor(co, ancestor, [point], txn)

def _is_ancestor(co, ancestor, points, txn):
    #ainfo = bdecode(co.branchmapdb.get(ancestor, txn=txn))
    ainfo = db_get(co, co.branchmapdb, ancestor, txn)
    points = points[:]
    state = {}
    while len(points):
        pnext = points.pop()
        if pnext == ancestor:
            return 1
        if state.has_key(pnext):
            continue
        state[pnext] = 1
        #pinfo = bdecode(co.branchmapdb.get(pnext, txn=txn))
        pinfo = db_get(co, co.branchmapdb, pnext, txn)
        if pinfo['generation'] <= ainfo['generation']:
            continue
        points.extend(pinfo['precursors'])
    return 0

def read_diff(co, handle, point, txn):
    hinfo = bdecode(co.contents.dagdb.get(handle + point, txn=txn))
    hfile = open(path.join(co.cpath, binascii.hexlify(handle)), 'rb')
    diff = _read_diff(hinfo, hfile)
    hfile.close()
    return diff

def _read_diff(index, hfile):
    if not index.has_key('handle'):
        return None
    try:
        hfile.seek(index['handle']['offset'])
    except IOError:
        print index
        raise
    return hfile.read(index['handle']['length'])

def write_diff(co, handle, diff, txn):
    cdagdb = co.contents.dagdb
    try:
        fend = struct.unpack('<I', cdagdb.get(handle, txn=txn))[0]
        hfile = open(path.join(co.cpath, binascii.hexlify(handle)), 'r+b')
    except TypeError:
        fend = 0
        hfile = open(path.join(co.cpath, binascii.hexlify(handle)), 'wb')

    hfile.seek(fend)
    hfile.write(diff)
    hfile.close()
    index = {'offset': fend, 'length': len(diff)}
    fend += index['length']
    cdagdb.put(handle, struct.pack('<I', fend), txn=txn)
    return index

def _write_diff(co, hfile, handle, diff, txn):
    cdagdb = co.contents.dagdb
    try:
        fend = struct.unpack('<I', cdagdb.get(handle, txn=txn))[0]
    except (db.DBNotFoundError, TypeError):
        fend = 0
    hfile.seek(fend)
    hfile.write(diff)
    index = {'offset': fend, 'length': len(diff)}
    fend += index['length']
    cdagdb.put(handle, struct.pack('<I', fend), txn=txn)
    return index

def write_index(co, point, handle, index, txn):
    cdagdb = co.contents.dagdb
    try:
        old_index = bdecode(cdagdb.get(handle + point, txn=txn))
        old_index['handle'] = index
    except (db.DBNotFoundError, TypeError):
        old_index = {'handle': index}
    cdagdb.put(handle + point, bencode(old_index), txn=txn)

def write_changeset(co, point, cset, txn):
    co.lcrepo.put(point, cset, txn=txn)

def verify_history(co, point, named, modified, txn):
    for handle in named:
        handle_name_at_point(co, handle, point, txn, dochecks=1)

def sync_history(co, point, txn, cache=dict()):
    named, modified = [], []

    points = [point]
    while len(points):
        npoint = points[-1]

        if cache.has_key(npoint):
            pinfo = cache[npoint]
        else:
            pinfo = bdecode(co.lcrepo.get(npoint, txn=txn))
            cache[npoint] = pinfo

        pres = []
        for pre in pinfo['precursors']:
            if co.changesdb.has_key(binascii.hexlify(pre), txn):
                continue
            pres.append(pre)
        pres.reverse()

        points.extend(pres)
        if pres != []:
            continue

        npoint = points.pop()

        if co.changesdb.has_key(binascii.hexlify(npoint), txn):
            continue

        named, modified = _sync_history(co, npoint, txn, cache=cache)
        co.changesdb.put(binascii.hexlify(npoint), '', txn=txn)

    return named, modified

def _sync_history(co, point, txn, cache=dict()):
    pinfo = cache[point]

    # see if we can extend an existing branch
    pre, prebminfo = None, None
    generations, pre_important = [], []
    bminfo = {'precursors': pinfo['precursors']}
    for pre in bminfo['precursors']:
        prebminfo = db_get(co, co.branchmapdb, pre, txn)
        generations.append(prebminfo['generation'])

        if bminfo.has_key('branch'):
            continue

        binfo = db_get(co, co.branchdb, prebminfo['branch'], txn)
        if prebminfo['branchnum'] == binfo['last']:
            bminfo['branch'] = prebminfo['branch']
            bminfo['branchnum'] = prebminfo['branchnum'] + 1
            binfo['last'] += 1
            db_put(co, co.branchdb, bminfo['branch'], binfo, txn)

            pre_important.append(pre)

    # generation == 'distance to root node', the exception is for the root
    try:
        bminfo['generation'] = max(generations) + 1
    except ValueError:
        bminfo['generation'] = 0

    # if we couldn't extend a branch, start a new one
    if not bminfo.has_key('branch'):
        bminfo['branch'] = bdecode(co.linforepo.get('branchmax', txn=txn)) + 1
        co.linforepo.put('branchmax', bencode(bminfo['branch']), txn=txn)
        bminfo['branchnum'] = 0

        try:
            # using the last precursor for this did the best empirically,
            # beating out both first precursor and largest branch number.
            binfo = {'last': 0,
                     'parent': prebminfo['branch'],
                     'parentnum': prebminfo['branchnum']}
            pre_important.append(pre)

        except TypeError:
            # special stuff for the rootnode
            assert bminfo['branch'] == 1
            binfo = {'last': 0}

        db_put(co, co.branchdb, bminfo['branch'], binfo, txn)

    db_put(co, co.branchmapdb, point, bminfo, txn)

    # put new files into staticdb
    for (handle, value) in pinfo['handles'].items():
        if value.has_key('add'):
            validate_handle(handle, pinfo['precursors'], value)
            db_put(co, co.staticdb, handle, {'type': value['add']['type']}, txn)

    # figure out which files were modified here and hand off to helpers
    named, modified = handles_in_branch(co, pre_important, [point], txn, cache=cache)

    pinfo['point'] = point
    _update_mini_dag(co, co.names, _update_helper_name,
                     named, pinfo, txn)
    _update_mini_dag(co, co.contents, _update_helper_content,
                     modified, pinfo, txn)

    return (named, modified)

def validate_handle(handle, precursors, hinfo):
    encinfo = bencode({'precursors': precursors, 'handle': hinfo})
    if sha(encinfo).digest() != handle:
        raise HistoryError, 'bad identifier'

def pretty_print_big_dag(co, heads):
    print 'digraph {'

    points = heads[:]
    cache = {}
    while len(points):
        point = points.pop()
        if cache.has_key(point):
            continue
        cache[point] = 1

        info = bdecode(co.lcrepo.get(point))
        sid = short_id(co, point)

        color = "orange"
        style = "dashed"
        if clean_merge_point(info) or point == rootnode:
            color = "lightblue"
            style = "solid"

        label = sid
        if point == rootnode:
            label = "root"

        print 'c_%s [label="%s",style=filled,color=%s]' % (sid, label, color)

        for pre in info['precursors']:
            print 'c_%s -> c_%s [style=%s]' % (short_id(co, pre), sid, style)
            style = "dashed"

        points.extend([point for point in info['precursors']])

    print '}'

def pretty_print_dag(co, handle, heads):
    print 'digraph {'

    points = []
    for point in heads:
        head = handle_last_modified(co, co.contents, handle, point, None)
        if head is not None:
            points.append(head)

    cdagdb = co.contents.dagdb
    cache = {}
    while len(points):
        point = points.pop()
        if cache.has_key(point):
            continue
        cache[point] = 1

        sid = short_id(co, point)
        print 'c_%s [label="%s"]' % (sid, sid)

        info = bdecode(cdagdb.get(handle + point))
        for pre, foo in info['precursors']:
            print 'c_%s -> c_%s' % (sid, short_id(co, pre))

        points.extend([point for point, index in info['precursors']])

    print '}'

def _update_mini_dag(co, changedbs, helper, handles, cset, txn):
    indexdb = changedbs.indexdb
    dagdb   = changedbs.dagdb

    pres = cset['precursors']
    point = cset['point']
    #bminfo = bdecode(co.branchmapdb.get(point, txn=txn))
    bminfo = db_get(co, co.branchmapdb, point, txn)
    bnum = struct.pack('>II', bminfo['branch'], bminfo['branchnum'])

    clean_merges = []
    for handle in handles:
        precursors = simplify_precursors(co, handle, changedbs, pres, txn)[0]

        mdinfo = {'handle': {}}
        if cset['handles'].has_key(handle):
            mdinfo['handle'] = helper(co, handle, point,
                                      cset['handles'][handle], txn)
        if mdinfo['handle'] == {}:
            del mdinfo['handle']
            if len(precursors) > 1:
                clean_merges.append(handle)
        mdinfo['precursors'] = precursors
        if precursors == []:
            assert cset['handles'][handle].has_key('add')

        dagdb.put(handle + point, bencode(mdinfo), txn=txn)
        indexdb.put(handle + bnum, point, txn=txn)

    if len(clean_merges) > 0:
        changedbs.mergedb.put(point, bencode(clean_merges), txn=txn)
    return

def simplify_precursors(co, handle, changedbs, pres, txn):
    # map big DAG precursors to little DAG
    dagdb = changedbs.dagdb
    precursors, indices = [], []
    for i in xrange(len(pres)):
        last = handle_last_modified(co, changedbs, handle, pres[i], txn)
        if last is None:
            continue

        # XXX: this is correct, but breaks old history
        if 0:
            pinfo = bdecode(dagdb.get(handle + last, txn=txn))
            if not pinfo.has_key('handle') and len(pinfo['precursors']) == 1:
                last = handle_last_modified(co, changedbs, handle,
                                            pinfo['precursors'][0][0], txn)
                if last is None:
                    continue

        precursors.append(last)
        indices.append(i)

    # second pass to eliminate precursors which are ancestors of others
    retval = []
    for i in xrange(len(precursors)):
        pre = precursors.pop(0)
        index = indices.pop(0)
        if _is_ancestor(co, pre, precursors, txn):
            continue
        precursors.append(pre)
        retval.append((pre, index))

    return retval, len(pres)

def _update_helper_content(co, handle, point, hinfo, txn):
    oinfo = {}
    if hinfo.has_key('hash'):
        if co.contents.dagdb.has_key(handle + point, txn):
            dinfo = bdecode(co.contents.dagdb.get(handle + point, txn=txn))
            oinfo['offset'] = dinfo['handle']['offset']
            oinfo['length'] = dinfo['handle']['length']
        else:
            # XXX: ugly in general
            oinfo['offset'] = -1
    if hinfo.has_key('add'):
        oinfo['add'] = 1
    elif hinfo.has_key('delete'):
        oinfo['delete'] = 1
    return oinfo
 
def _update_helper_name(co, handle, point, hinfo, txn):
    oinfo = {}
    if hinfo.has_key('name'):
        oinfo['name'] = hinfo['name']
        try:
            oinfo['parent'] = hinfo['parent']
            co.allnamesdb.put(hinfo['parent'] + hinfo['name'],
                              handle, flags=db.DB_NODUPDATA, txn=txn)
        except KeyError:
            assert handle == roothandle
            assert hinfo.has_key('add')
        except db.DBKeyExistError:
            pass

    if hinfo.has_key('add'):
        oinfo['add'] = 1
    elif hinfo.has_key('delete'):
        oinfo['delete'] = 1
    return oinfo

def changes_in_branch(co, lpoints, bpoints, txn, cache=None):
    points = bpoints[:]
    seen, changes = {}, []
    while len(points):
        pnext = points.pop()
        if seen.has_key(pnext):
            continue
        seen[pnext] = 1
        if _is_ancestor(co, pnext, lpoints, txn):
            continue
        if cache:
            if cache.has_key(pnext):
                pinfo = cache[pnext]
            else:
                pinfo = bdecode(co.lcrepo.get(pnext, txn=txn))
                cache[pnext] = pinfo
        else:
            pinfo = bdecode(co.lcrepo.get(pnext, txn=txn))

        points.extend(pinfo['precursors'])

        changes.append(pnext)

    return changes

def handles_in_branch(co, lpoints, bpoints, txn, cache=None, deleted_modified=False):
    points = bpoints[:]
    seen, named, modified, deleted = {}, {}, {}, {}
    while len(points):
        pnext = points.pop()
        if seen.has_key(pnext):
            continue
        seen[pnext] = 1
        if _is_ancestor(co, pnext, lpoints, txn):
            continue
        if cache:
            if cache.has_key(pnext):
                pinfo = cache[pnext]
            else:
                pinfo = bdecode(co.lcrepo.get(pnext, txn=txn))
                cache[pnext] = pinfo
        else:
            pinfo = bdecode(co.lcrepo.get(pnext, txn=txn))
        for handle, hinfo in pinfo['handles'].items():
            if hinfo.has_key('name'):
                named[handle] = 1
            if hinfo.has_key('hash'):
                modified[handle] = 1
            if hinfo.has_key('delete'):
                named[handle] = 1
                if not deleted_modified:
                    deleted[handle] = 1

        # XXX: afaik, this is only an issue for ambiguous clean merges, which
        # don't happen with name operations. requires more thought.
        if co.contents.mergedb.has_key(pnext, txn):
            clean_merges = bdecode(co.contents.mergedb.get(pnext, txn=txn))
            for handle in clean_merges:
                modified[handle] = 1
            # XXX: check for deletes?

        points.extend(pinfo['precursors'])
    for handle in deleted.keys():
        if modified.has_key(handle):
            del modified[handle]
    return (named.keys(), modified.keys())

def handle_last_modified(co, changedbs, handle, change, txn):
    indexdb = changedbs.indexdb
    dagdb   = changedbs.dagdb

    if dagdb.has_key(handle + change, txn):
        return change
    #hinfo = bdecode(co.branchmapdb.get(change, txn=txn))
    hinfo = db_get(co, co.branchmapdb, change, txn)
    bbranch, bnum = int(hinfo['branch']), hinfo['branchnum']
    retval = None
    cursor = indexdb.cursor(txn=txn)
    while 1:
        branchpoint = struct.pack('>20sII', handle, bbranch, bnum)
        try:
            foo = cursor.set_range(branchpoint)
        except db.DBNotFoundError:
            foo = None
        if foo is None:
            foo = cursor.last()
            if foo is None:
                break
            key, value = foo
        else:
            key, value = foo
            if key != branchpoint:
                try:
                    foo = cursor.prev_nodup()
                except db.DBNotFound:
                    foo = None
                if foo is None:
                    break
                key, value = foo
        rhandle, rbranch, rnum = struct.unpack('>20sII', key)
        if rhandle == handle and rbranch == bbranch:
            retval = value
            break
        #pinfo = bdecode(co.branchdb.get(bbranch, txn=txn))
        pinfo = db_get(co, co.branchdb, bbranch, txn)
        try:
            bbranch, bnum = int(pinfo['parent']), pinfo['parentnum']
        except KeyError:
            #_print_indexdb(co, indexdb, handle, txn)
            break
    cursor.close()

    return retval

def _print_indexdb(co, indexdb, handle, txn):
    print 'indexdb'
    cursor = indexdb.cursor(txn=txn)
    foo = cursor.set_range(handle)
    while foo != None:
        key, value = foo
        nhandle, branch, num = struct.unpack('<20sII', key)
        if handle != nhandle:
            break
        print "%d, %d" % (branch, num)
        foo = cursor.next()
    cursor.close()

def handle_name_at_point(co, handle, point, txn, dochecks=0):
    change = handle_last_modified(co, co.names, handle, point, txn)
    if change is None:
        return None
    co.name_cache = {}
    return _handle_name_at_point(co, handle, change, txn, dochecks=dochecks)

def __handle_name_at_point(co, handle, point, txn, dochecks=0):
    key = handle + point
    if not dochecks and co.name_cache.has_key(key):
        return co.name_cache[key]

    change = handle_last_modified(co, co.names, handle, point, txn)
    if change is None:
        return None
    return _handle_name_at_point(co, handle, change, txn, dochecks=dochecks)

def _handle_name_from_precursors(precursors, resolved):
    state = {}
    for pre in precursors:
        if state == {}:
            state = pre.copy()
            continue
        if pre.has_key('delete'):
            return pre
        outcome, rename_point = rename_conflict_check(state, pre)
        if not resolved and outcome == 'conflict':
            raise HistoryError, 'double name conflict'
        elif outcome == 'remote':
            state['name'] = pre['name']
            state['parent'] = pre['parent']
        state['rename point'] = rename_point
        state['points'] = dmerge(state['points'], pre['points'])
    return state

def _handle_name_at_point(co, handle, point, txn, dochecks=0):
    def walk_precursors(cset, dochecks):
        precursors, points = [], [point]
        for pre, index in cset['precursors']:
            foo = _handle_name_at_point(co, handle, pre, txn, dochecks=dochecks)
            if foo is None:
                continue
            points = dmerge(points, foo['points'])
            precursors.append(foo)
        return precursors, points

    cset = bdecode(co.names.dagdb.get(handle + point, txn=txn))
    if not cset.has_key('handle'):
        precursors, points = walk_precursors(cset, dochecks)
        state = _handle_name_from_precursors(precursors, 0)
    elif cset['handle'].has_key('delete'):
        precursors, points = walk_precursors(cset, dochecks)
        state = _handle_name_from_precursors(precursors, 1)
        state['delete'] = 1
    else:
        precursors, points = walk_precursors(cset, dochecks)
        state = {}
        state['name'] = cset['handle']['name']
        try:
            state['parent'] = cset['handle']['parent']
        except KeyError:
            assert handle == roothandle
            assert cset['handle'].has_key('add')
        state['rename point'] = [point]

    state['points'] = points

    if dochecks == 0:
        return state

    co.name_cache[handle + point] = state

    if state['name'] == '' and handle != roothandle:
        raise HistoryError, 'illegal name'
    if state['name'] == '.' or state['name'] == '..':
        raise HistoryError, 'illegal name'

    if state.has_key('delete'):
        if len(children_count(co, handle, point, txn)):
            raise HistoryError, 'non-empty directory can\'t be deleted'
        return state

    staticinfo = db_get(co, co.staticdb, handle, txn)
    if staticinfo['type'] == 'dir':
        try:
            if parent_loop_check(co, state['parent'], point, txn):
                raise HistoryError, 'parent loop'
        except KeyError:
            pass

    try:
        #parentinfo = bdecode(co.staticdb.get(state['parent'], txn=txn))
        parentinfo = db_get(co, co.staticdb, state['parent'], txn)
        if parentinfo['type'] != 'dir':
            raise HistoryError, 'parent not a directory'

        parentstate = __handle_name_at_point(co, state['parent'], point, txn)
        if parentstate is None:
            raise HistoryError, 'parent not in repository'
        if parentstate.has_key('delete'):
            raise HistoryError, 'file committed with deleted parent'
        if len(name_use_count(co, state, point, txn)) != 1:
            raise HistoryError, 'name already in use'
        if state['name'] == 'CVILLE':
            raise HistoryError, 'illegal name'
    except KeyError:
        assert handle == roothandle

    return state

def fullpath_at_point(co, handle, point, txn=None):
    name = None
    while handle != roothandle:
        pinfo = handle_name_at_point(co, handle, point, txn)
        if pinfo is None:
            return None
        if name is None:
            name = pinfo['name']
        else:
            name = path.join(pinfo['name'], name)
        handle = pinfo['parent']
    return name

def _mini_dag_refcount(co, handle, point, txn, cache=None, info_cache=None):
    assert info_cache is not None
    if cache is None:
        cache = {}
    points = [point]
    while len(points):
        point = points.pop()
        if cache.has_key(point):
            cache[point]['refcount'] += 1
            continue
        cache[point] = {'refcount': 1}
        
        pinfo = bdecode(co.contents.dagdb.get(handle + point, txn=txn))
        info_cache[point] = pinfo

        for p, i in pinfo['precursors']:
            points.append(p)
    return cache

def handle_contents_at_point(co, handle, point, txn, dcache=None):
    if dcache is None:
        dcache = {}
    #staticinfo = bdecode(co.staticdb.get(handle, txn=txn))
    staticinfo = db_get(co, co.staticdb, handle, txn)
    if staticinfo['type'] != 'file':
        raise ValueError, 'no contents for non-file'

    change = handle_last_modified(co, co.contents, handle, point, txn)
    if change is None:
        return None

    hcache = {}
    cache = _mini_dag_refcount(co, handle, change, txn, info_cache=hcache)
    hfile = open(path.join(co.cpath, binascii.hexlify(handle)), 'rb')

    points = [change]
    while len(points):
        point = points[-1]

        # we may have already done this one
        if cache.has_key(point) and cache[point].has_key('info'):
            points.pop()
            continue

        # cache this, since we visit most nodes twice
        if hcache.has_key(point):
            hinfo = hcache[point]
        else:
            hinfo = bdecode(co.contents.dagdb.get(handle + point, txn=txn))
            hcache[point] = hinfo

        # check if we've got the precursors
        dirty = False
        for pre, foo in hinfo['precursors']:
            if not cache[pre].has_key('info'):
                dirty = True
                points.append(pre)
        if dirty:
            continue
        points.pop()

        # read the diff
        if dcache.has_key(point):
            diff = dcache[point]
        else:
            diff = _read_diff(hinfo, hfile)
        if diff is not None:
            diff = bdecode(zlib.decompress(diff))

        # put together the precursor list and decrement refcounts
        precursors = []
        for pre, foo in hinfo['precursors']:
            precursors.append(cache[pre]['info'])

            cache[pre]['refcount'] -= 1
            if cache[pre]['refcount'] == 0:
                del cache[pre]

        # finally, get the contents
        cache[point]['info'] = _handle_contents_at_point(point, hinfo,
                                                         precursors, diff)

    hfile.close()

    cache[change]['info']['type'] = staticinfo['type']
    return cache[change]['info']

def _handle_contents_at_point(point, hinfo, precursors, diff):
    state = {}
    points = []
    for pre in precursors:
        points = dmerge(points, pre['points'])
    state['points'] = points

    matches = []
    if diff is not None:
        for pre, index in hinfo['precursors']:
            matches.append(diff['matches'][index])

        if diff.has_key('add'):
            if precursors != []:
                raise HistoryError, 'handle already exists'
        elif precursors == []:
            raise HistoryError, 'cannot modify non-existent file'

        if diff.has_key('delete'):
            state['delete'] = diff['delete']

    if not state.has_key('delete'):
        fpre = []
        for pre in precursors:
            fpre.append((pre['lines'], pre['line points'], pre['points']))
        if diff is not None:
            try:
                lines, line_points = replay(fpre, matches,
                                            diff['newlines'], point)
            except MergeError, msg:
                raise HistoryError, 'merge error: ' + str(msg)
            except KeyError:
                raise HistoryError, 'malformed change'

            points.append(point)
        else:
            lines, line_points, points = find_conflict_multiple_safe(fpre)
            if lines is None:
                # XXX: this is a pretty gross hack
                if len(fpre) == 2:
                    s0 = Set(fpre[0][2])
                    s1 = Set(fpre[1][2])
                    if s0 == s1:
                        raise HistoryError, 'merge error'
                    elif s0.issubset(s1):
                        lines, line_points, points = fpre[1]
                    elif s0.issuperset(s1):
                        lines, line_points, points = fpre[0]
                    else:
                        raise HistoryError, 'merge error'
                else:
                    raise HistoryError, 'merge error'
        state['lines'] = lines
        state['line points'] = line_points

    return state

def print_file_with_points(pre):
    def _print_points(line_points):
        ps = []
        for line_point in line_points:
            ps.append(binascii.hexlify(line_point))
        return ', '.join(ps)

    lines, line_points, points = pre
    out = [_print_points(line_points[0])]
    for i in xrange(len(lines)):
        out.append(lines[i])
        out.append(_print_points(line_points[i+1]))
    return '\n'.join(out)

from merge import _find_conflict

def print_conflict(co, fpre):
    p1, p2 = fpre[0], fpre[1]
    olines, oline_points = _find_conflict(fpre[0][0], fpre[0][1], fpre[0][2],
                                          fpre[1][0], fpre[1][1], fpre[1][2])

    ls = []
    offset = [0, 0]
    for i in xrange(len(olines)):
        l = olines[i]

        if type(l) is str:
            offset[0] += 1
            offset[1] += 1
            continue

        print '@@ -%d,%d +%d,%d @@' % (offset[0], len(l[0]), offset[0], len(l[1]))

        offset[0] += len(l[0])
        offset[1] += len(l[1])

        lp = oline_points[i]
        ls.append('<<<<<<< local')

        ps = ', '.join([short_id(co, p) for p in lp[0][0]])
        ls.append(ps)
        for j in xrange(len(l[0])):
            ls.append(l[0][j])
            ps = ', '.join([short_id(co, p) for p in lp[0][j+1]])
            ls.append(ps)

        ls.append('=======')

        ps = ', '.join([short_id(co, p) for p in lp[1][0]])
        ls.append(ps)
        for j in xrange(len(l[1])):
            ls.append(l[1][j])
            ps = ', '.join([short_id(co, p) for p in lp[1][j+1]])
            ls.append(ps)

        ls.append('>>>>>>> remote')

    return '\n'.join(ls)

def rebuild_from_points(co, points, txn):
    co.changesdb.truncate(txn)
    co.branchdb.truncate(txn)
    co.branchmapdb.truncate(txn)
    co.names.indexdb.truncate(txn)
    co.names.dagdb.truncate(txn)
    co.names.mergedb.truncate(txn)
    co.contents.indexdb.truncate(txn)
    co.contents.mergedb.truncate(txn)
    # we don't truncate the cdagdb because it contains the offsets and lengths
    # for the diffs in the files, which we can't recreate. the sync below will
    # read those parameters out and rewrite the cdagdb, anyway.
    co.linforepo.put('branchmax', bencode(0), txn=txn)

    cdagdb = co.contents.dagdb
    for key, value in cdagdb.items(txn):
        if len(key) != 40:
            continue
        if not bdecode(value).has_key('handle'):
            cdagdb.delete(key, txn=txn)

    for point in points:
        sync_history(co, point, txn)

def clean_merge_point(info):
    if info['handles'] != {}:
        return 0
    if len(info['precursors']) != 2:
        return 0
    if info.has_key('comment'):
        return 0
    return 1

def short_id(co, point):
    apoint = binascii.hexlify(point)
    length = 3
    key = apoint[:length]
    cursor = co.changesdb.cursor()
    while key.startswith(apoint[:length]) and length < len(apoint):
        length += 1
        try:
            key, value = cursor.set_range(apoint[:length])
        except (db.DBNotFoundError, TypeError):
            break
        if key != apoint:
            continue
        foo = cursor.next()
        if foo is None:
            break
        key, value = foo
    cursor.close()
    return apoint[:length]

class ChangeNotKnown(Exception): pass

class ChangeNotUnique(Exception): pass

class ChangeBadRepository(Exception): pass

def long_id(co, point):
    if point.startswith('cdv://'):
        key = repo_head(co, tuple_to_server(server_to_tuple(point)))
        if key is None:
            raise ChangeBadRepository, point
        return key

    cursor = co.changesdb.cursor()
    try:
        key, value = cursor.set_range(point)
    except (db.DBNotFoundError, TypeError):
        cursor.close()
        raise ChangeNotKnown, point
    if not key.startswith(point):
        cursor.close()
        raise ChangeNotKnown, point
    try:
        key2, value = cursor.next()
    except (db.DBNotFoundError, TypeError):
        cursor.close()
        return binascii.unhexlify(key)
    if key2.startswith(point):
        keys = [key]
        while key2.startswith(point):
            keys.append(key2)
            try:
                key2, value = cursor.next()
            except (db.DBNotFoundError, TypeError):
                break
        cursor.close()
        raise ChangeNotUnique, (point, keys)
    cursor.close()
    return binascii.unhexlify(key)

def repo_head(co, repo):
    if repo is None:
        return None
    repo = tuple_to_server(server_to_tuple(repo))
    return co.linforepo.get(repo)

def server_to_tuple(server_string):
    if server_string.startswith('cdv://'):
        server_string = server_string[6:]
    temp = server_string.split('/', 1)

    if len(temp) != 2 or temp[1] == '':
        repo = None
    else:
        repo = temp[1]
        if repo[-1] == '/':
            repo = repo[:-1]

    temp  = temp[0].split(':', 1)
    if len(temp) == 1:
        port = 6601
    else:
        try:
            port = int(temp[1])
        except ValueError:
            return None
    server_string = temp[0]
    return (temp[0], port, repo)

def tuple_to_server(tuple):
    return 'cdv://%s:%d/%s' % tuple

def print_change(co, change, changeset):
    escape = saxutils.escape

    output = []
    output.append("<change>")

    output.append("<changenumber>" + binascii.hexlify(change) + "</changenumber>")
    output.append("<short-changenumber>" + short_id(co, change) + "</short-changenumber>")
    output.append("<user>" + escape(changeset['user']) + "</user>")
    try:
        output.append("<comment>" + escape(changeset['comment']) + "</comment>")
    except KeyError:
        pass
    output.append("<date>" + str(changeset['time']) + "</date>")
    output.append("<pretty-date>" + strftime('%a %b %d %H:%M:%S %Y %Z', localtime(changeset['time'])) + "</pretty-date>")

    adds, deletes, renames, mods = {}, {}, {}, {}
    for handle, value in changeset['handles'].items():
        if value.has_key('add'):
            adds[handle] = value['add']
        else:
            if value.has_key('delete'):
                deletes[handle] = 1
            if value.has_key('name'):
                renames[handle] = 1
            if value.has_key('hash'):
                mods[handle] = 1

    output.append("<added-files>")
    for handle, info in adds.items():
        output.append("<file>")
        output.append("<name>" + escape(fullpath_at_point(co, handle, change)) + "</name>")
        output.append("<type>" + info['type'] + "</type>")
        output.append("</file>")
    output.append("</added-files>")

    output.append("<deleted-files>")
    for handle in deletes.keys():
        output.append("<file>")
        output.append("<name>" + escape(fullpath_at_point(co, handle, change)) + "</name>")
        output.append("</file>")
    output.append("</deleted-files>")

    output.append("<renamed-files>")
    for handle in renames.keys():
        output.append("<file>")
        output.append("<name>" + escape(fullpath_at_point(co, handle, change)) + "</name>")
        output.append("</file>")
    output.append("</renamed-files>")

    output.append("<modified-files>")
    for handle in mods.keys():
        output.append("<file>")
        output.append("<name>" + escape(fullpath_at_point(co, handle, change)) + "</name>")
        output.append("</file>")
    output.append("</modified-files>")

    output.append("</change>")
    return output

def dump_changeinfo(co, change, repo=None):
    output = []

    changeset = bdecode(co.lcrepo.get(change))
    if not clean_merge_point(changeset):
        raise ValueError

    output.append("<root>")
    if repo:
        output.append("<repository>" + saxutils.escape(repo) + "</repository>")
    output.append("<changenumber>" + binascii.hexlify(change) + "</changenumber>")
    output.append("<short-changenumber>" + short_id(co, change) + "</short-changenumber>")
    output.append("<committer>" + saxutils.escape(changeset['user']) + "</committer>")
    output.append("<date>" + str(changeset['time']) + "</date>")

    for change in changes_in_branch(co, [changeset['precursors'][0]],
                                    changeset['precursors'][1:], None):
        changeset = bdecode(co.lcrepo.get(change))
        if not clean_merge_point(changeset):
            output.extend(print_change(co, change, changeset))

    output.append("</root>\n")
    return '\n'.join(output)

def db_get(co, cdb, key, txn):
    try:
        cache = co.db_cache[db]
    except KeyError:
        cache = co.db_cache[db] = {}
    if cache.has_key(key):
        return cache[key]
    cache[key] = bdecode(cdb.get(key, txn=txn))
    #try:
    #    return cache[key]
    #except KeyError:
    #    cache[key] = bdecode(cdb.get(key, txn=txn))
    return cache[key]

def db_put(co, cdb, key, value, txn):
    cdb.put(key, bencode(value), txn=txn)
    try:
        cache = co.db_cache[db]
    except KeyError:
        cache = co.db_cache[db] = {}
    cache[key] = value

try:
    import psyco
    psyco.bind(_is_ancestor, 0)
except ImportError:
    pass
