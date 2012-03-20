# Written by Ross Cohen
# see LICENSE.txt for license information

from bencode import bdecode, bencode
from db import db
from history import roothandle, dmerge, rename_conflict_check
from history import handle_name_at_point, __handle_name_at_point
from history import _handle_name_at_point
from history import handle_contents_at_point
from history import handle_last_modified
from history import _name_use_count, _children_count
from history import write_diff, write_index, db_get, db_put, HistoryError
from history import clean_merge_point, simplify_precursors, repo_head
from history import _is_ancestor
from merge import find_resolution
import os
from os import path
from path import breakup
import sha
import stat
from time import time
import zlib

class CommitError(Exception):
    pass

def new_handle(co, txn):
    """Create a temporary handle for new files in the working copy"""
    num = bdecode(co.linforepo.get('lasthandle', txn=txn)) + 1
    handle = sha.new(str(num)).digest()
    co.linforepo.put('lasthandle', bencode(num), txn=txn)
    return handle

def create_handle(precursors, hinfo):
    """Create a permanent identifier for use in a changeset description"""
    binfo = bencode({'precursors': precursors, 'handle': hinfo})
    return sha.new(binfo).digest()

def handle_name(co, handle, txn):
    """Returns a dict specifying name, parent and other info"""
    if co.handle_name_cache.has_key(handle):
        return co.handle_name_cache[handle]

    # XXX: reading this repeatedly is excessive
    heads = bdecode(co.linforepo.get('heads', txn=txn))
    einfo = {}
    if co.editsdb.has_key(handle, txn):
        einfo = bdecode(co.editsdb.get(handle, txn=txn))
        if einfo.has_key('name'):
            einfo['points'] = ['1']
            for head in heads:
                pinfo = __handle_name_at_point(co, handle, head, txn)
                if pinfo is None:
                    continue
                einfo['points'] = dmerge(einfo['points'], pinfo['points'])
            einfo['rename point'] = ['1']
            co.handle_name_cache[handle] = einfo
            return einfo
    state = None
    for point in heads:
        pinfo = __handle_name_at_point(co, handle, point, txn)
        if pinfo is None:
            continue
        if pinfo.has_key('delete'):
            co.handle_name_cache[handle] = pinfo
            return pinfo
        if state is None:
            state = pinfo
            continue
        conflict, rename_points = rename_conflict_check(state, pinfo)
        if conflict == 'remote':
            state['name'] = pinfo['name']
            state['parent'] = pinfo['parent']
        state['rename point'] = rename_points
        state['points'] = dmerge(state['points'], pinfo['points'])
    if einfo.has_key('delete'):
        state['delete'] = einfo['delete']
    co.handle_name_cache[handle] = state
    return state

def _filename_to_handle(co, parent, name, txn, deletes=0):
    """Given a parent handle and a name, return all associated handles"""
    handles = []
    cursor = co.allnamesdb.cursor(txn=txn)
    lookup = parent + name
    try:
        key, value = cursor.set(lookup)
    except (db.DBNotFoundError, TypeError):
        key = None
    while key == lookup:
        hinfo = handle_name(co, value, txn)
        if hinfo is not None and hinfo['name'] == name and hinfo['parent'] == parent:
            if not hinfo.has_key('delete'):
                handles.append(value)

            elif deletes == 1 and co.editsdb.has_key(value):
                if bdecode(co.editsdb.get(value, txn=txn)).has_key('delete'):
                    handles.append(value)

        # next_dup() is broken
        foo = cursor.next()
        if foo is None:
            break
        key, value = foo

    cursor.close()
    return handles

def filename_to_handle(co, file, txn=None, deletes=0):
    """Given a full file path, return the associated handle"""
    if file == '':
        return roothandle
    lpath = breakup(file)
    handle = roothandle
    for name in lpath:
        handles = _filename_to_handle(co, handle, name, txn, deletes=deletes)
        if handles == []:
            return None
        handle = handles[0]
    return handle

def _handle_to_filename(co, handle, names, txn):
    """Same as handle_to_filename, but takes an override mapping for handles"""
    lpath, hseen = [], {}
    while handle != roothandle:
        if names.has_key(handle):
            info = names[handle]
        else:
            info = handle_name(co, handle, txn)
            if info is None:
                lpath.insert(0, '???')
                break
        if hseen.has_key(handle):
            lpath.insert(0, info['name'] + '(loop)')
            break
        lpath.insert(0, info['name'])
        hseen[handle] = 1
        handle = info['parent']
    try:
        return path.join(*lpath)
    except TypeError:
        pass
    return ''

def handle_to_filename(co, handle, txn=None):
    """Convert the given handle to a full path name."""
    return _handle_to_filename(co, handle, {}, txn)

def set_edit(co, handle, info, txn):
    """Specify that a new attribute on a file is being edited."""
    if co.editsdb.has_key(handle):
        oinfo = bdecode(co.editsdb.get(handle, txn=txn))
        oinfo.update(info)
        info = oinfo
    co.editsdb.put(handle, bencode(info), txn=txn)

def unset_edit(co, handle, info, txn):
    """Remove an edit attribute on a file."""
    oinfo = bdecode(co.editsdb.get(handle, txn=txn))
    for attr in info:
        del oinfo[attr]
    if oinfo == {}:
        co.editsdb.delete(handle, txn=txn)
    else:
        co.editsdb.put(handle, bencode(oinfo), txn=txn)

def _add_file(co, rep, parent, required, ltxn):
    mode = os.lstat(path.join(co.local, rep)).st_mode
    handle = filename_to_handle(co, rep)
    if handle:
        #info = bdecode(co.staticdb.get(handle))
        info = db_get(co, co.staticdb, handle, None)
        if info['type'] == 'dir' and not stat.S_ISDIR(mode):
            print 'error - %s already added as a %s' % (rep, info['type'])
            return None
        if info['type'] == 'file' and not stat.S_ISREG(mode):
            print 'error - %s already added as a %s' % (rep, info['type'])
            return None
        if required:
            print 'warning - %s already added' % (rep,)
    else:
        print 'adding: ' + rep
        if stat.S_ISDIR(mode):
            type = 'dir'
        elif stat.S_ISREG(mode):
            type = 'file'
        else:
            print 'error - unrecognized file type for %s' % (rep,)
            return None
        if rep == '':
            handle = roothandle
        else:
            handle = new_handle(co, ltxn)
        #co.staticdb.put(handle, bencode({'type': type}), txn=ltxn)
        db_put(co, co.staticdb, handle, {'type': type}, ltxn)
        info = {'name': path.split(rep)[1], 'parent': parent, 'add': {}}
        set_edit(co, handle, info, ltxn)
        co.allnamesdb.put(parent + info['name'], handle, flags=db.DB_NODUPDATA, txn=ltxn)
        if type == 'file':
            co.modtimesdb.put(handle, bencode(0), txn=ltxn)
            co.filenamesdb.put(rep, handle, txn=ltxn)
    return handle

def _set_name(co, handle, parent, name, txn):
    linfo = {'parent': parent, 'name': name}
    co.handle_name_cache[handle] = linfo

    set_edit(co, handle, linfo, txn)
    try:
        co.allnamesdb.put(parent + name, handle, flags=db.DB_NODUPDATA, txn=txn)
    except db.DBKeyExistError:
        pass

def name_use_count(co, handle, txn):
    """Returns a list of handles with the same name as the given handle."""
    def _name_func(co, handle, foo, txn):
        return handle_name(co, handle, txn)
    return _name_use_count(co, handle_name(co, handle, txn), None, _name_func, txn)

def rename_race(co, handle, names, txn):
    #def _name_func(co, handle, names, txn):
    #    if names.has_key(handle):
    #        return names[handle]
    #    return None
    #named = _name_use_count(co, info, names, _name_func, txn)
    info = handle_name(co, handle, txn)
    cursor = co.allnamesdb.cursor(txn=txn)
    lookup = info['parent'] + info['name']
    try:
        key, value = cursor.set(lookup)
    except (db.DBNotFoundError, TypeError):
        return None
    while key == lookup:
        if names.has_key(value):
            vinfo = names[value]
            if vinfo['parent'] == info['parent'] and vinfo['name'] == info['name']:
                cursor.close()
                return value
        foo = cursor.next()
        if foo is None:
            break
        key, value = foo
    cursor.close()
    return None

def children_count(co, handle, txn, deletes=0):
    """Returns a list of children of the specified handle."""
    def _name_func(co, handle, foo, txn):
        hinfo = handle_name(co, handle, txn)
        if hinfo is not None and hinfo.has_key('delete') and deletes == 1:
            # if the editsdb has it, then it was just deleted
            if co.editsdb.has_key(handle, txn):
                del hinfo['delete']
        return hinfo
    return _children_count(co, handle, None, _name_func, txn)

def _parent_loop_check(co, handle, names, txn):
    """Same as parent_loop_check, but takes a dict of handle mapping overrides."""
    hseen = {}
    while handle != roothandle:
        if hseen.has_key(handle):
            return handle
        hseen[handle] = 1
        if names.has_key(handle):
            pinfo = names[handle]
        else:
            pinfo = handle_name(co, handle, txn)
        if pinfo.has_key('delete'):
            return handle
        handle = pinfo['parent']
    return None

def parent_loop_check(co, handle, txn):
    """Check whether this handle exists under itself in the directory tree."""
    return _parent_loop_check(co, handle, {}, txn)

def _rename_safe_check(co, handle, names, txn):
    temp = names[handle]
    del names[handle]
    result = _parent_loop_check(co, handle, names, txn)
    names[handle] = temp
    if result is None:
        return 1
    return None

def unique_name(co, parent, file, txn):
    """Generate a name not already in use by the system."""
    if _filename_to_handle(co, parent, file, txn) == []:
        return file
    post = 2
    while _filename_to_handle(co, parent, file + str(post), txn) != []:
        post = post + 1
    return file + str(post)

def conflicts_in_file(co, file):
    h = open(path.join(co.local, file), 'rb')
    lines = h.read().split('\n')
    h.close()
    for l in lines:
        if     l == '<<<<<<< local' or \
               l == '=======' or \
               l == '>>>>>>> remote':
            return 1
    return 0

def find_update_files(co, rhead, named, txn):
    rnamed = []
    for handle in named:
        linfo = handle_name(co, handle, txn)
        rnamed.append((handle, linfo, __handle_name_at_point(co, handle, rhead, txn)))
    return rnamed

def mark_modified_files(co, txn):
    if co.varsdb.has_key('edit-mode'):
        if co.varsdb.get('edit-mode') == '1':
            return
    modtimesdb, editsdb, local = co.modtimesdb, co.editsdb, co.local
    for lfile, handle in co.filenamesdb.items(txn):
        if editsdb.has_key(handle, txn):
            info = bdecode(editsdb.get(handle, txn=txn))
            if info.has_key('hash') or info.has_key('add'):
                continue
        #info = bdecode(co.staticdb.get(handle, txn=txn))
        info = db_get(co, co.staticdb, handle, txn)
        if info['type'] != 'file':
            continue
        lfile = path.join(local, lfile)
        try:
            mtime = int(path.getmtime(lfile))
        except OSError:
            continue
        minfo = bdecode(modtimesdb.get(handle, txn=txn))
        if mtime != minfo:
            modtimesdb.put(handle, bencode(mtime), txn=txn)
            set_edit(co, handle, {'hash': 1}, txn)

def gen_diff(co, handle, precursors, lines, txn):
    pres, plen = simplify_precursors(co, handle, co.contents, precursors, txn)

    file_points = []
    for pre, index in pres:
        info = handle_contents_at_point(co, handle, pre, txn)
        file_points.append((info['lines'], info['line points'], info['points']))

    result, ms, newlines = find_resolution(file_points, lines)

    # explanation of conditions:
    # 1: check for a merge
    # 2: check if new lines were added by the user
    # 3: safety for the 4th condition
    # 4: check if the first match in the first (only) file covers everything
    if len(pres) > 1 or \
           len(newlines) != 0 or \
           not len(ms[0]) or \
           ms[0][0][2] != len(file_points[0][0]):

        # create a set of correct matches, minus ones which are optimized out
        matches = [[] for i in xrange(plen)]
        i = 0
        for pre, index in pres:
            matches[index] = ms[i]
            i += 1

        return {'matches': matches, 'newlines': newlines}

    return None

def gen_changeset(co, files, comment, repohead, txn, tstamp=None):
    def per_file_hash(co, handle, hinfo, precursors, lfile, txn):
        try:
            h = open(lfile, 'rb')
        except IOError:
            raise HistoryError, 'Could not open file ' + lfile
        lines = h.read().split('\n')
        h.close()

        dinfo = gen_diff(co, handle, precursors, lines, txn)
        if hinfo.has_key('add'):
            dinfo['add'] = 1
        if hinfo.has_key('delete'):
            dinfo['delete'] = 1
        try:
            diff = bencode(dinfo)
        except ValueError:
            return None
        mtime = int(path.getmtime(lfile))
        co.modtimesdb.put(handle, bencode(mtime), txn=txn)
        hinfo['hash'] = sha.new(diff).digest()
        return zlib.compress(diff, 6)

    precursors = bdecode(co.linforepo.get('heads'))

    # include the last known repository head in the list of changes. this is
    # extra useful info and also forces a merge change which a comment can
    # then be attached to.
    # XXX: can do the wrong thing in odd merge-and-not-update cases
    if repohead is not None and repohead not in precursors:
        while not _is_ancestor(co, repohead, precursors, txn):
            info = bdecode(co.lcrepo.get(repohead, txn=txn))
            try:
                repohead = info['precursors'][0]
            except IndexError:
                repohead = rootnode

        if repohead not in precursors:
            precursors.insert(0, repohead)

    changeset = {'precursors': precursors}

    changeset['handles'] = handles = {}
    adds, nedits, edits, types, names = {}, [], [], {}, {}
    for handle, linfo in files:
        if linfo.has_key('add'):
            adds[handle] = 1
        #types[handle] = bdecode(co.staticdb.get(handle))['type']
        types[handle] = db_get(co, co.staticdb, handle, None)['type']

        handles[handle] = cinfo = {}
        if linfo.has_key('delete'):
            assert not linfo.has_key('add')
            assert not linfo.has_key('hash')
            cinfo['delete'] = 1
        elif     linfo.has_key('name') or \
                 linfo.has_key('nmerge'):
            nedits.append((handle, linfo))

        if linfo.has_key('add'):
            assert not linfo.has_key('hash')
            cinfo['add'] = {'type': types[handle]}
        elif     linfo.has_key('hash') or \
                 linfo.has_key('cmerge'):
            assert types[handle] == 'file'
            edits.append(handle)
        co.editsdb.delete(handle, txn=txn)

    # generate the name diffs
    for handle, linfo in nedits:
        # check if this is really a merge or not
        # XXX: theoretically we can trust the 'nmerge' flag as set (and
        # cleared) by _update_helper()
        merge = False
        change = prev_change = None
        for head in precursors:
            change = handle_last_modified(co, co.names, handle, head, txn)
            if change is None:
                continue

            if prev_change is None:
                prev_change = change
                continue

            left_anc = _is_ancestor(co, prev_change, [change], txn)
            right_anc = _is_ancestor(co, change, [prev_change], txn)

            if left_anc:
                prev_change = change
            elif not right_anc:
                merge = True
                break

        # XXX: sanity check for now, we have to do most of the work anyway
        assert not (linfo.has_key('nmerge') ^ merge)

        # no merge, but maybe the user made an explicit change
        if not linfo.has_key('nmerge') and change is not None:
            old_info = handle_name_at_point(co, handle, change, txn, lookup=False)
            if old_info['name'] == linfo['name'] and \
               old_info['parent'] == linfo['parent']:
                continue

        # looks like we need to include an explicit name change
        cinfo = handles[handle]
        hinfo = handle_name(co, handle, txn)
        cinfo['parent'] = hinfo['parent']
        cinfo['name'] = hinfo['name']
        names[handle] = cinfo

    # generate the diffs
    indices = {}
    for handle in edits:
        lfile = path.join(co.local, _handle_to_filename(co, handle, names, txn))
        diff = per_file_hash(co, handle, handles[handle], precursors, lfile, txn)
        if diff is None:
            continue
        indices[handle] = write_diff(co, handle, diff, txn)

    # clear out things which didn't actually have changes
    for handle, linfo in files:
        if handles[handle] == {}:
            del handles[handle]

    # change all the temporary IDs to permanent, verifiable ones
    ladds, nmap = adds.keys(), {}
    while len(ladds):
        handle = ladds.pop()
        # check if this handle was already dealt with
        if not adds.has_key(handle):
            continue
        parent = handles[handle]['parent']
        # if the parent was also added, it needs to be renumbered first
        if adds.has_key(parent):
            ladds.extend((handle, parent))
            continue
        hinfo = handles[handle]
        # if the parent has been renumbered, pick up the change
        if nmap.has_key(parent):
            hinfo['parent'] = nmap[parent]
        # generate the permanent ID
        if types[handle] == 'file':
            # generate diffs
            fname = _handle_to_filename(co, handle, names, txn)
            lfile = path.join(co.local, fname)
            diff = per_file_hash(co, handle, handles[handle], [], lfile, txn)
            newhandle = create_handle(precursors, hinfo)
            indices[newhandle] = write_diff(co, newhandle, diff, txn)
            # update the db accordingly
            co.modtimesdb.delete(handle, txn=txn)
            mtime = int(path.getmtime(lfile))
            co.modtimesdb.put(newhandle, bencode(mtime), txn=txn)
            co.filenamesdb.put(fname, newhandle, txn=txn)
        else:
            newhandle = create_handle(precursors, hinfo)
        handles[newhandle] = handles[handle]
        names[newhandle] = names[handle]
        types[newhandle] = types[handle]
        del handles[handle]
        del adds[handle]

        # more db updating
        co.staticdb.delete(handle, txn=txn)
        #co.staticdb.put(newhandle, bencode({'type': types[handle]}), txn=txn)
        db_put(co, co.staticdb, newhandle, {'type': types[handle]}, txn)
        nmap[handle] = newhandle
        # XXX: clean up allnamesdb

    # do reparenting of all the non-added files
    for handle in names.keys():
        if nmap.has_key(handle):
            continue
        hinfo = handles[handle]
        if hinfo.has_key('delete'):
            continue
        if nmap.has_key(hinfo['parent']):
            hinfo['parent'] = nmap[hinfo['parent']]

    if changeset['handles'] == {} and len(changeset['precursors']) == 1:
        return None

    # fill in a few other pieces of information
    if comment is not None:
        changeset['comment'] = comment
    changeset['user'] = co.user
    if tstamp is None:
        tstamp = time()
    changeset['time'] = int(tstamp)

    # put together the changeset and calculate the point
    bchangeset = bencode(changeset)
    point = sha.new(bchangeset).digest()

    # write the file locations of the diffs to the db
    for handle, index in indices.items():
        write_index(co, point, handle, index, txn)

    # write the new change to the db and make it the new head
    co.lcrepo.put(point, bchangeset, txn=txn)
    co.linforepo.put('heads', bencode([point]), txn=txn)
    #co.editsdb.truncate(txn=txn)
    return point

def find_commit_files(co, handles):
    r = []
    for handle, expanded in handles:
        lfile = handle_to_filename(co, handle)
        if not co.editsdb.has_key(handle):
            if not expanded:
                print 'warning - %s is not opened for edit' % (lfile,)
            continue

        linfo = bdecode(co.editsdb.get(handle))
        if linfo.has_key('hash'):
            if conflicts_in_file(co, lfile):
                raise CommitError, 'conflicts in %s, must resolve first' % (lfile,)
        r.append((handle, linfo))
    return r
