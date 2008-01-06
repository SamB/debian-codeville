# Written by Bram Cohen and Ross Cohen
# see LICENSE.txt for license information

from bencode import bdecode, bencode
import binascii
from cdv_glob import Glob
from client_helpers import new_handle, create_handle, unique_name, _set_name
from client_helpers import filename_to_handle
from client_helpers import handle_to_filename, _handle_to_filename, handle_name
from client_helpers import set_edit, unset_edit, clean_merge_point, gen_changeset
from client_helpers import _add_file, conflicts_in_file, CommitError
from client_helpers import name_use_count, rename_race, children_count, parent_loop_check, _rename_safe_check
from client_helpers import mark_modified_files, find_update_files, find_commit_files
from client_net import ClientHandler, ClientError, ServerError
from client_net import network_prep, authenticate
from db import db, ChangeDBs, write_format_version, write_rebuild_version
from DFS import DFS
from diff import unified_diff
from getpass import getpass
from history import HistoryError, roothandle, rootnode
from history import dmerge, damerge, rename_conflict_check, db_get
from history import sync_history, is_ancestor, _is_ancestor
from history import handle_contents_at_point, handles_in_branch
from history import handle_name_at_point, fullpath_at_point
from history import handle_last_modified
from history import short_id, long_id, write_changeset, rebuild_from_points
from history import server_to_tuple, tuple_to_server, repo_head
from history import dump_changeinfo
from history import pretty_print_dag, pretty_print_big_dag
from history import simplify_precursors
import locale
from merge import find_conflict, find_conflict_multiple_safe, find_annotation
import merge
from network import NetworkError
import os
from os import path
from path import mdir, subpath, breakup, preserving_rename
from random import randrange
import re
from sets import Set
import sha
import shlex
import shutil
import stat
from sys import maxint, stdin, stdout, version_info, platform, stderr
import tempfile
from time import ctime, strftime, localtime

assert version_info >= (2,3), "Python 2.3 or higher is required"

term_encoding = stdin.encoding
text_encoding = locale.getpreferredencoding()

class CheckoutError(Exception):
    pass

class Checkout:
    def __init__(self, local, init=False, metadata_dir='.cdv', rw=True):
        self.local      = local
        self.conf_path  = path.join(local, metadata_dir)

        if init == True:
            try:
                os.mkdir(self.conf_path)
            except OSError, err:
                raise CheckoutError, 'Could not create metadata directory: %s' % (err[1],)

        self.dbenv = None
        self.txn   = None
        txn        = None
        if rw == True:
            flags = db.DB_CREATE|db.DB_INIT_MPOOL|db.DB_INIT_TXN|db.DB_PRIVATE
            flags |= db.DB_RECOVER

            self.dbenv = db.DBEnv()
            self.dbenv.set_cachesize(0, 4 * 1024 * 1024)
            self.dbenv.set_lg_bsize(1024 * 1024)
            self.dbenv.set_get_returns_none(2)
            self.dbenv.open(self.conf_path, flags)
            txn = self.txn_begin()

        self._openDBs(txn, init, rw)

        self.name_cache          = {}
        self.handle_name_cache   = {}
        self.db_cache            = {}

        self.temppath    = path.join(self.conf_path, 'temp')
        self.cpath       = path.join(self.conf_path, 'contents')
        mdir(self.temppath)
        mdir(self.cpath)

        self.nopass = 0
        self.user   = None

        if rw == True:
            if init:
                write_format_version(self.conf_path)
                write_rebuild_version(self.conf_path)
                populate_local_repos(self, txn)
            self.txn_commit(txn)

        return

    def _openDBs(self, txn, init, rw):
        flags = 0
        if not rw:
            flags |= db.DB_RDONLY

        if init:
            flags |= db.DB_CREATE

        self.lcrepo = db.DB(dbEnv=self.dbenv)
        self.lcrepo.open('changesets.db', dbtype=db.DB_BTREE, flags=flags, txn=txn)
        self.linforepo = db.DB(dbEnv=self.dbenv)
        self.linforepo.open('info.db', dbtype=db.DB_BTREE, flags=flags, txn=txn)
        self.changesdb = db.DB(dbEnv=self.dbenv)
        self.changesdb.open('changenums.db', dbtype=db.DB_BTREE, flags=flags, txn=txn)
        self.branchmapdb = db.DB(dbEnv=self.dbenv)
        self.branchmapdb.open('branchmap.db', dbtype=db.DB_BTREE, flags=flags, txn=txn)
        self.branchdb = db.DB(dbEnv=self.dbenv)
        self.branchdb.open('branch.db', dbtype=db.DB_RECNO, flags=flags, txn=txn)
        self.staticdb = db.DB(dbEnv=self.dbenv)
        self.staticdb.open('static.db', dbtype=db.DB_BTREE, flags=flags, txn=txn)

        # open the mini-dags and their indices
        self.contents = ChangeDBs(self.dbenv, 'content', flags, txn)
        self.names    = ChangeDBs(self.dbenv, 'name', flags, txn)

        self.allnamesdb = db.DB(dbEnv=self.dbenv)
        self.allnamesdb.set_flags(db.DB_DUPSORT)
        self.allnamesdb.open('allnames.db', dbtype=db.DB_BTREE, flags=flags, txn=txn)

        # checkout-specific dbs
        self.modtimesdb = db.DB(dbEnv=self.dbenv)
        self.modtimesdb.open('modtimes.db', dbtype=db.DB_BTREE, flags=flags, txn=txn)
        self.editsdb = db.DB(dbEnv=self.dbenv)
        self.editsdb.open('edits.db', dbtype=db.DB_BTREE, flags=flags, txn=txn)
        self.varsdb = db.DB(dbEnv=self.dbenv)
        self.varsdb.open('vars.db', dbtype=db.DB_BTREE, flags=flags, txn=txn)
        try:
            self.filenamesdb = db.DB(dbEnv=self.dbenv)
            self.filenamesdb.open('filenames.db', dbtype=db.DB_BTREE, flags=flags, txn=txn)
        except db.DBNoSuchFileError:
            self.filenamesdb = None

        return

    def close(self):
        if self.txn is not None:
            self.txn_abort(self.txn)

        self.lcrepo.close()
        self.linforepo.close()
        self.changesdb.close()
        self.branchmapdb.close()
        self.branchdb.close()
        self.staticdb.close()
        self.contents.close()
        self.names.close()
        self.allnamesdb.close()
        self.modtimesdb.close()
        self.editsdb.close()
        self.varsdb.close()
        if self.filenamesdb is not None:
            self.filenamesdb.close()

        if self.dbenv is not None:
            self.dbenv.txn_checkpoint()
            for lfile in self.dbenv.log_archive():
                os.remove(path.join(self.dbenv.db_home, lfile))
            self.dbenv.close()

        return

    def txn_begin(self):
        self.txn = self.dbenv.txn_begin()
        return self.txn

    def txn_abort(self, txn):
        assert self.txn == txn
        self.txn = None
        return txn.abort()

    def txn_commit(self, txn):
        assert self.txn == txn
        self.txn = None
        return txn.commit()

def cli_init(args):
    local = args[0]
    try:
        co = Checkout(local, init=True)
        co.close()
    except CheckoutError, msg:
        print 'error - %s' % (msg,)
        return 1
    return 0

def add(co, files):
    ltxn = co.txn_begin()

    cpats = ignore_patterns(co)

    glob = Glob(co, files)
    for file, expanded in glob.fs_walk():
        fpath = breakup(file)
        if '.cdv' in fpath:
            print 'warning - .cdv is a reserved name'
            continue
        # XXX: check for other frobridden names, i.e. '.' and '..'

        # ignore user specified patterns, but only for expansions
        if expanded:
            ignore = 0
            for cpat in cpats:
                if cpat.search(file) is not None:
                    ignore = 1
                    break
            if ignore:
                continue

        # add all the directories leading up to the file, then the file
        rep, parent = '', roothandle
        required = 0
        for d in fpath:
            rep = path.join(rep, d)
            if rep == file:
                required = not expanded
            parent = _add_file(co, rep, parent, required, ltxn)
            if not parent:
                co.txn_abort(ltxn)
                return 1

    co.txn_commit(ltxn)
    return 0

def delete(co, files):
    co.handle_name_cache = {}

    ltxn = co.txn_begin()

    fnames = []
    for handle, expanded in Glob(co, files).db_walk():
        file = handle_to_filename(co, handle)
        fnames.append((file, handle))

    # reverse the ordering so that dirs are deleted after their contents
    fnames.sort()
    fnames.reverse()

    # update the database
    for fname, handle in fnames:
        linfo = handle_name(co, handle, ltxn)
        if co.editsdb.has_key(handle, ltxn):
            co.editsdb.delete(handle, txn=ltxn)
        if linfo.has_key('add'):
            #unset_edit(co, handle, {}, ltxn)
            del co.handle_name_cache[handle]
        else:
            set_edit(co, handle, {'delete': 1}, ltxn)
            co.handle_name_cache[handle]['delete'] = 1

    # make sure the directories are empty
    for fname, handle in fnames:
        linfo = db_get(co, co.staticdb, handle, None)
        if linfo['type'] == 'dir':
            if len(children_count(co, handle, ltxn)):
                print 'error - %s is not empty' % (fname,)
                co.txn_abort(ltxn)
                return 1

    # finally, do the deleting
    for fname, handle in fnames:
        print 'deleting: ' + fname
        linfo = db_get(co, co.staticdb, handle, None)
        if linfo['type'] == 'dir':
            try:
                os.rmdir(path.join(co.local, fname))
            except OSError, err:
                print 'warning - %s: %s' % (err[1], fname)

        elif linfo['type'] == 'file':
            try:
                os.remove(path.join(co.local, fname))
            except OSError, err:
                print 'warning - %s: %s' % (err[1], fname)
            co.modtimesdb.delete(handle, txn=ltxn)
            co.filenamesdb.delete(fname, txn=ltxn)

    co.txn_commit(ltxn)
    return 0

def rename(co, oldname, newname):
    co.handle_name_cache = {}

    try:
        loldfile, foldname = subpath(co.local, oldname)
    except ValueError:
        print 'error - ' + oldname + ' is outside repository'
        return 1
    ohandle = filename_to_handle(co, foldname)
    if ohandle is None:
        print 'error - ' + foldname + ' is not in repository'
        return 1
    if not path.exists(oldname):
        print 'error - ' + oldname + ' does not exist'
        return 1
    try:
        lnewfile, fnewname = subpath(co.local, newname)
    except ValueError:
        print 'error - ' + newname + ' is outside repository'
        return 1

    dirmove, foo = 0, path.split(newname)[1]
    if foo == '' or foo == '..' or foo == '.':
        dirmove = 1
        npath, nname = fnewname, ''
    else:
        npath, nname = path.split(fnewname)

    nhandle = filename_to_handle(co, fnewname)
    if nhandle is not None:
        #if not dirmove or bdecode(co.staticdb.get(nhandle))['type'] != 'dir':
        if not dirmove or db_get(co, co.staticdb, nhandle, None)['type'] != 'dir':
            print 'error - ' + newname + ' already exists in repository'
            return 1
        phandle = nhandle
        nname = path.split(foldname)[1]
        fnewname = path.join(fnewname, nname)
        newname = path.join(newname, nname)
    else:
        phandle = filename_to_handle(co, npath)
        if phandle is None:
            print 'error - cannot rename into directory not in repository'
            return 1

    if nname == '.cdv':
        print 'error - .cdv is a reserved name'
        return 1
    if filename_to_handle(co, fnewname) is not None:
        print 'error - ' + newname + ' already exists in repository'
        return 1
    if path.exists(path.join(co.local, fnewname)):
        print 'error - ' + newname + ' already exists in filesystem'
        return 1

    print 'renaming: ' + foldname + ' -> ' + fnewname
    ltxn = co.txn_begin()
    _set_name(co, ohandle, phandle, nname, ltxn)
    os.rename(path.join(co.local, foldname), path.join(co.local, fnewname))
    _rebuild_fndb(co, ltxn)
    co.txn_commit(ltxn)
    return 0

def _rebuild_fndb(co, txn):
    # XXX: crude, should do partial trees
    co.filenamesdb.truncate(txn=txn)
    for handle in co.modtimesdb.keys(txn):
        lfile = handle_to_filename(co, handle)
        co.filenamesdb.put(lfile, handle, txn=txn)
    return

def edit(co, files, by_id=False):
    txn = co.txn_begin()
    generator = None
    if by_id:
        generator = [(binascii.unhexlify(handle), 0) for handle in files]
    else:
        generator = Glob(co, files).db_walk()
    for handle, expanded in generator:
        file = handle_to_filename(co, handle)

        sinfo = db_get(co, co.staticdb, handle, None)
        if sinfo['type'] != 'file':
            print 'warning - %s is not a file' % (file,)
            continue

        print 'editting: %s' % (file,)
        set_edit(co, handle, {'hash': 1}, txn)
    co.txn_commit(txn)
    return 0

def set_password(co):
    network_prep(co)
    ch = ClientHandler(co)
    remote = server_to_tuple(co.repo)
    try:
        s = authenticate(co, ch, remote, None, srp=True)
        newpassword = getpass('New password: ')
        confpassword = getpass('Confirm new password: ')
        if newpassword != confpassword:
            print 'Confirmation failed. Password not changed.'
            return 1
        ch.set_password(s, newpassword)
    except NetworkError, msg:
        print 'password failed: ' + str(msg)
        return 1
    except ServerError, msg:
        print 'password failed: ' + str(msg)
        return 1
    print 'Password changed'
    return 0

def merge_analyze(co, heads, rhead, named, modified, deleted, txn):
    # clear out the merges which the new head resolved for us
    old_heads = heads[:]
    old_heads.remove(rhead)
    for handle, binfo in co.editsdb.items(txn):
        info = bdecode(binfo)

        if info.has_key('nmerge'):
            merge = False
            for head in old_heads:
                change = handle_last_modified(co, co.names, handle, head, txn)
                if change is not None and not is_ancestor(co, change, rhead, txn):
                    merge = True
            if not merge:
                unset_edit(co, handle, ['nmerge'], txn)

        if info.has_key('cmerge'):
            merge = False
            for head in old_heads:
                change = handle_last_modified(co, co.contents, handle, head, txn)
                if change is not None and not is_ancestor(co, change, rhead, txn):
                    merge = True
            if not merge:
                unset_edit(co, handle, ['cmerge'], txn)

    # keep track of what's been merged because we have to generate explicit
    # merge information for them later.
    lnamed, lmodified, ladded, ldeleted = \
            handles_in_branch(co, [rhead], heads, txn)

    sdeleted, sldeleted = Set(deleted), Set(ldeleted)

    sdmerges = sldeleted ^ sdeleted
    s_all_deleted = sldeleted | sdeleted

    lnamed = damerge(lnamed, ladded)
    lmodified = damerge(lmodified, ladded)

    slnamed, snamed = Set(lnamed), Set(named)
    for handle in (slnamed & snamed) - s_all_deleted:
        set_edit(co, handle, {'nmerge': 1}, txn)

    slmodified, smodified = Set(lmodified), Set(modified)
    for handle in (slmodified & smodified) - s_all_deleted:
        set_edit(co, handle, {'cmerge': 1}, txn)

    for handle in (snamed | smodified) & (sldeleted - sdeleted):
        set_edit(co, handle, {'delete': 1}, txn)

    for handle in (slnamed | slmodified) & (sdeleted - sldeleted):
        set_edit(co, handle, {'delete': 1}, txn)

    #for handle in named:
    #    for head in heads:
    #        change = handle_last_modified(co, co.names, handle, head, txn)
    #        if change is not None and not is_ancestor(co, change, rhead, txn):
    #            set_edit(co, handle, {'nmerge': 1}, txn)

    #for handle in modified:
    #    for head in heads:
    #        change = handle_last_modified(co, co.contents, handle, head, txn)
    #        if change is not None and not is_ancestor(co, change, rhead, txn):
    #            set_edit(co, handle, {'cmerge': 1}, txn)

    return

def _update_helper(co, uinfo, named, modified, added, deleted, txn):
    uinfo['newfiles'] = newfiles = {}
    uinfo['deletes'] = deletes = {}
    uinfo['names'] = names = {}
    uinfo['infofiles'] = infofiles = []

    if not co.merge:
        return ([(handle, 0) for handle in modified], {})

    local = co.local
    rhead = uinfo['head']
    heads = bdecode(co.linforepo.get('heads'))
    if co.branchmapdb.has_key(rhead) and _is_ancestor(co, rhead, heads, None):
        return ([], {})

    named = damerge(named, added, deleted)
    modified = damerge(modified, added, deleted)

    co.handle_name_cache = {}
    try:
        named_files = find_update_files(co, rhead, named, txn)
    except HistoryError, msg:
        raise ClientError, 'Got garbage from repository: ' + str(msg)

    handles, nconflicts = {}, {}
    for handle, linfo, rinfo in named_files:
        if linfo is None:
            if handle == roothandle:
                continue
            if not rinfo.has_key('delete'):
                newfiles[handle] = 1
                handles[handle] = 1
            else:
                handles[handle] = 0
            continue
        if rinfo.has_key('delete'):
            if not linfo.has_key('delete'):
                names[handle] = linfo
                deletes[handle] = 1
                handles[handle] = 1
            else:
                deletes[handle] = 0
                handles[handle] = 0
            continue
        elif linfo.has_key('delete'):
            handles[handle] = 0
            continue
        conflict, rename_points = rename_conflict_check(linfo, rinfo)
        if conflict == 'remote':
            names[handle] = linfo
            handles[handle] = 1
        elif conflict == 'conflict':
            #lfile = _handle_to_filename(co, handle, names, txn)
            #print 'file ' + lfile + ' was renamed both locally and remotely'
            names[handle] = linfo
            uname = unique_name(co, linfo['parent'], linfo['name'] + '.nameconflict', txn)
            _set_name(co, handle, linfo['parent'], uname, txn)
            infofiles.append((handle, rinfo['parent'], rinfo['name']))
            nconflicts[handle] = 1
            handles[handle] = 1

    # create list of all the handles to be updated, will not include ones
    # which don't exist and are in a deleted state after being pulled in
    for handle in modified:
        if handles.has_key(handle):
            continue

        linfo = handle_name(co, handle, txn)
        if linfo is None:
            rinfo = handle_name_at_point(co, handle, rhead, txn)
            if rinfo is None or rinfo.has_key('delete'):
                handles[handle] = 0
                continue
        elif linfo.has_key('delete'):
            handles[handle] = 0
            continue

        handles[handle] = 1

    orphans = []
    for handle in deletes.keys():
        for chandle in children_count(co, handle, txn):
            if deletes.has_key(chandle):
                continue

            if chandle in names:
                continue

            #file = _handle_to_filename(co, chandle, {}, txn)
            #print 'parent of ' + file + ' was deleted, orphaning'
            cinfo = handle_name(co, chandle, txn)
            names[chandle] = cinfo
            infofiles.append((chandle, cinfo['parent'], ''))
            nconflicts[chandle] = 1
            handles[chandle] = 1
            orphans.append((chandle, cinfo['name']))

    # generate the new list of heads, the new one might encompass zero, some
    # or all of our existing heads
    pre_heads, temp_heads = heads[:], []
    inserted_head = False
    for head in heads:
        if is_ancestor(co, head, rhead, txn):
            if not inserted_head:
                inserted_head = True
                temp_heads.append(rhead)
        else:
            temp_heads.append(head)
    if not inserted_head:
        temp_heads.append(rhead)
    heads = temp_heads
    co.linforepo.put('heads', bencode(heads), txn=txn)

    merge_analyze(co, heads, rhead, named, modified, deleted, txn)

    # clear the name cache
    co.handle_name_cache = {}

    for handle, name in orphans:
        uname = unique_name(co, roothandle, name + '.orphaned', txn)
        _set_name(co, handle, roothandle, uname, txn)

    for handle, linfo, rinfo in named_files:
        if deletes.has_key(handle):
            continue
        if not newfiles.has_key(handle) and linfo is None:
            continue
        linfo = handle_name(co, handle, txn)
        pinfo = handle_name(co, linfo['parent'], txn)
        if pinfo.has_key('delete'):
            #file = _handle_to_filename(co, handle, {}, txn)
            #print 'parent of ' + file + ' was deleted, orphaning'
            if not newfiles.has_key(handle):
                names[handle] = linfo
            uname = unique_name(co, roothandle, linfo['name'] + '.orphaned', txn)
            _set_name(co, handle, roothandle, uname, txn)
            infofiles.append((handle, linfo['parent'], ''))
            nconflicts[handle] = 1

    breaks = 1
    while breaks:
        breaks = 0
        for handle, info in names.items():
            if deletes.has_key(handle):
                continue
            #if bdecode(co.staticdb.get(handle, txn=txn))['type'] != 'dir':
            if db_get(co, co.staticdb, handle, txn)['type'] != 'dir':
                continue
            lhandle = parent_loop_check(co, handle, txn)
            if lhandle == handle:
                breaks += 1
                linfo = handle_name(co, handle, txn)
                #file = _handle_to_filename(co, handle, names, txn)
                #rfile = handle_to_filename(co, linfo['parent'], txn)
                #rfile = path.join(rfile, linfo['name'])
                #print 'parent loop for ' + file + ' -> ' + rfile
                uname = unique_name(co, linfo['parent'], linfo['name'] + '.parentloop', txn)
                _set_name(co, handle, info['parent'], uname, txn)
                infofiles.append((handle, info['parent'], ''))
                nconflicts[handle] = 1

    newnames = names.keys()
    newnames.extend(newfiles.keys())
    for handle in newnames:
        if deletes.has_key(handle):
            continue
        lhandles = name_use_count(co, handle, txn)
        if len(lhandles) == 1:
            continue
        lhandles.remove(handle)
        lhandle = lhandles[0]
        #print 'name conflict for ' + _handle_to_filename(co, handle, {}, txn)
        linfo = handle_name(co, lhandle, txn)
        names[lhandle] = linfo
        uname = unique_name(co, linfo['parent'], linfo['name'] + '.nameconflict.local', txn)
        _set_name(co, lhandle, linfo['parent'], uname, txn)
        nconflicts[lhandle] = 1
        handles[lhandle] = 1

        linfo = handle_name(co, handle, txn)
        uname = unique_name(co, linfo['parent'], linfo['name'] + '.nameconflict.remote', txn)
        _set_name(co, handle, linfo['parent'], uname, txn)
        nconflicts[handle] = 1

    for handle in newnames:
        if deletes.has_key(handle):
            continue
        linfo = handle_name(co, handle, txn)
        if newfiles.has_key(linfo['parent']):
            continue
        lfile = _handle_to_filename(co, linfo['parent'], names, txn)
        if not path.exists(path.join(local, lfile)):
            raise ClientError, 'directory ' + lfile + ' does not exist, you must revert it before updating'
        mode = os.lstat(path.join(local, lfile)).st_mode
        if not stat.S_ISDIR(mode):
            raise ClientError, lfile + ' is not a directory, you must revert it before updating'
        chandle = rename_race(co, handle, names, txn)
        lfile = path.join(lfile, linfo['name'])
        if not chandle and path.exists(path.join(local, lfile)):
            raise ClientError, 'file ' + lfile + ' was added or renamed remotely but already exists, you must move or delete'

    for handle in names.keys():
        lfile = _handle_to_filename(co, handle, names, txn)
        if not path.exists(path.join(local, lfile)):
            raise ClientError, 'file ' + lfile + ' does not exist, you must revert it before updating'
        #linfo = bdecode(co.staticdb.get(handle, txn=txn))
        linfo = db_get(co, co.staticdb, handle, txn)
        mode = os.lstat(path.join(local, lfile)).st_mode
        if linfo['type'] == 'file' and not stat.S_ISREG(mode):
            raise ClientError, 'file ' + lfile + ' is expected to be of type ' + linfo['type']
        if linfo['type'] == 'dir' and not stat.S_ISDIR(mode):
            raise ClientError, 'file ' + lfile + ' is expected to be of type ' + linfo['type']

    return handles.items(), nconflicts

def update(co, remote, merge=True):
    try:
        network_prep(co)
    except NetworkError, msg:
        print msg
        return 1

    editsdb, local = co.editsdb, co.local

    txn = co.txn_begin()
    mark_modified_files(co, txn)
    co.txn_commit(txn)

    co.merge = merge
    ch = ClientHandler(co)
    txn = co.txn_begin()
    try:
        s = authenticate(co, ch, remote, txn)
        co.txn_commit(txn)
    except NetworkError, msg:
        co.txn_abort(txn)
        print 'update failed: ' + str(msg)
        return 1
    except ServerError, msg:
        co.txn_abort(txn)
        print 'update failed: ' + str(msg)
        return 1

    txn = co.txn_begin()
    try:
        updateinfo = ch.update(s, remote[2], _update_helper, txn)
    except NetworkError, msg:
        print 'update failed: ' + str(msg)
        co.txn_abort(txn)
        return 1
    except (ClientError, ServerError), msg:
        print 'update failed: ' + str(msg)
        co.txn_abort(txn)
        ch.close(s)
        return 1
    ch.close(s)
    ch = None

    # record the repo head so we can do things relative to it later
    co.linforepo.put(tuple_to_server(remote), updateinfo['head'], txn=txn)

    if co.txn_commit(txn):
        print 'Updating local database failed, aborting...'
        return 1

    if not co.merge:
        print 'Repository head is ' + short_id(co, updateinfo['head'])
        print 'Update succeeded'
        return 0

    modified_files = updateinfo['modified']
    if not updateinfo.has_key('newfiles'):
        print 'Update succeeded'
        return 0
    newfiles = updateinfo['newfiles']
    deletes = updateinfo['deletes']
    names = updateinfo['names']
    infofiles = updateinfo['infofiles']

    # XXX: need to do something about making updates atomic
    txn = co.txn_begin()

    create_dirs = []
    for handle in newfiles.keys():
        lfile = _handle_to_filename(co, handle, names, None)

        #staticinfo = bdecode(co.staticdb.get(handle, txn=txn))
        staticinfo = db_get(co, co.staticdb, handle, txn)
        if staticinfo['type'] == 'dir':
            create_dirs.append(lfile)
        else:
            co.filenamesdb.put(lfile, handle, txn=txn)

    do_fndb_rebuild = False
    if names != {}:
        do_fndb_rebuild = True

    renames = names.keys()
    while renames:
        handle = renames.pop()
        if deletes.has_key(handle) or newfiles.has_key(handle):
            continue
        if not _rename_safe_check(co, handle, names, None):
            renames.insert(0, handle)
            continue
        spath = _handle_to_filename(co, handle, names, None)
        names[handle]['name']   = binascii.hexlify(handle)
        names[handle]['parent'] = roothandle
        dpath = _handle_to_filename(co, handle, names, None)
        os.rename(path.join(local, spath), path.join(local, dpath))

    delete_files, delete_dirs = [], []
    for handle, present in deletes.items():
        if not present:
            continue
        #info = bdecode(co.staticdb.get(handle))
        info = db_get(co, co.staticdb, handle, None)
        lfile = _handle_to_filename(co, handle, names, None)
        if info['type'] == 'dir':
            delete_dirs.append(lfile)
        elif info['type'] == 'file':
            delete_files.append((lfile, handle))
        if co.editsdb.has_key(handle, txn):
            co.editsdb.delete(handle, txn=txn)
        del names[handle]

    for lfile, handle in delete_files:
        os.remove(path.join(local, lfile))
        co.modtimesdb.delete(handle, txn=txn)
        co.filenamesdb.delete(lfile, txn=txn)

    if do_fndb_rebuild:
        _rebuild_fndb(co, txn)

    delete_dirs.sort()
    delete_dirs.reverse()
    for lfile in delete_dirs:
        try:
            os.rmdir(path.join(local, lfile))
        except OSError:
            print 'warning - %s could not be deleted because it is not empty' % \
                  (lfile,)

    create_dirs.sort()
    for lfile in create_dirs:
        mdir(path.join(local, lfile))

    for handle in names.keys():
        spath = _handle_to_filename(co, handle, names, None)
        del names[handle]
        dpath = _handle_to_filename(co, handle, names, None)
        os.rename(path.join(local, spath), path.join(local, dpath))

    for handle in modified_files:
        if deletes.has_key(handle):
            continue
        temppath = path.join(co.temppath, binascii.hexlify(handle))
        filename = path.join(co.local, _handle_to_filename(co, handle, names, txn))
        preserving_rename(temppath, filename)

    for handle, rparent, rname in infofiles:
        assert not deletes.has_key(handle)
        info = handle_name(co, handle, txn)
        file = unique_name(co, info['parent'], _handle_to_filename(co, handle, names, None) + '.info', None)
        rfile = path.join(handle_to_filename(co, rparent), rname)
        h = open(path.join(local, file), 'wb')
        h.write(rfile + '\n')
        h.close()

    if co.txn_commit(txn):
        print 'Updating local database failed, aborting...'
        return 1

    print 'Update succeeded'
    return 0

def cli_construct(co, spoint):
    point = long_id(co, spoint)

    # now create everything at the specified point
    adds, deletes = handles_in_branch(co, [rootnode], [point], None)[2:4]
    deletes_dict = {}.fromkeys(deletes)
    newfiles = []
    for handle in adds:
        if deletes_dict.has_key(handle):
            continue

        hfile = fullpath_at_point(co, handle, point, None)
        htype = bdecode(co.staticdb.get(handle))['type']
        newfiles.append((hfile, handle, htype))

    newfiles.sort()
    for hfile, handle, htype in newfiles:
        print 'preparing: %s' % (hfile,)
        if htype == 'file':
            cinfo = handle_contents_at_point(co, handle, point, None)
            temppath = path.join(co.temppath, binascii.hexlify(handle))
            fd = open(temppath, 'wb')
            fd.write('\n'.join(cinfo['lines']))
            fd.close()

    # put together a list of all the files we are managing
    handles = Glob(co, [path.join(co.local, '...')]).db_walk(deletes=0)
    sheep = []
    for handle, expanded in handles:
        hfile = handle_to_filename(co, handle, None)
        sheep.append((hfile, handle))

    # delete all of them
    sheep.sort()
    sheep.reverse()
    for hfile, handle in sheep:
        print 'removing: %s' % (hfile,)
        destpath = path.join(co.local, hfile)
        htype = bdecode(co.staticdb.get(handle))['type']
        try:
            if htype == 'dir':
                os.rmdir(destpath)
            else:
                os.unlink(destpath)
        except OSError, msg:
            print 'warning - %s' % (str(msg),)

    txn = co.txn_begin()

    # clear out whatever we were editing
    co.editsdb.truncate(txn)

    # rename everything to the right place
    co.modtimesdb.truncate(txn)
    co.filenamesdb.truncate(txn)
    for hfile, handle, htype in newfiles:
        print 'creating: %s' % (hfile,)
        destpath = path.join(co.local, hfile)
        if htype == 'dir':
            try:
                os.mkdir(destpath)
            except OSError:
                if not os.path.isdir(destpath):
                    raise
            continue

        elif htype == 'file':
            temppath = path.join(co.temppath, binascii.hexlify(handle))
            preserving_rename(temppath, destpath)
            mtime = int(path.getmtime(destpath))
            co.modtimesdb.put(handle, bencode(mtime), txn=txn)
            co.filenamesdb.put(hfile, handle, txn=txn)

    co.linforepo.put('heads', bencode([point]), txn=txn)
    co.txn_commit(txn)

    return 0

def rebuild(co, uheads):
    txn = co.txn_begin()

    if uheads == []:
        heads = bdecode(co.linforepo.get('heads'))
    else:
        heads = [long_id(co, head) for head in uheads]
    try:
        rebuild_from_points(co, heads, txn)
    except HistoryError, msg:
        print 'error - ' + str(msg)
        co.txn_abort(txn)
        return 1

    co.filenamesdb.truncate(txn)
    for handle in co.modtimesdb.keys():
        hinfo = handle_name(co, handle, None)
        if hinfo is None or hinfo.has_key('delete'):
            co.modtimesdb.delete(handle, txn)
            if co.editsdb.has_key(handle):
                co.editsdb.delete(handle, txn)

        lfile = handle_to_filename(co, handle, txn)
        co.filenamesdb.put(lfile, handle, txn=txn)

    for handle, value in co.editsdb.items(txn):
        if bdecode(value).has_key('delete'):
            co.editsdb.delete(handle, txn)
            set_edit(co, handle, {'delete': 1}, txn)

    for handle, value in co.editsdb.items(txn):
        linfo = bdecode(value)

        merges = []
        if linfo.has_key('nmerge'):
            merges.append('nmerge')
        if linfo.has_key('cmerge'):
            merges.append('cmerge')

        unset_edit(co, handle, merges, txn)

    for i in range(1, len(heads)):
        named, modified, added, deleted = \
               handles_in_branch(co, heads[:i], [heads[i]], txn)
        named    = damerge(named, added, deleted)
        modified = damerge(modified, added, deleted)

        merge_analyze(co, heads[:i+1], heads[i], named, modified, deleted, txn)

    print 'Rebuild done.'
    co.txn_commit(txn)
    write_rebuild_version(co.conf_path)
    return 0

def cli_is_ancestor(co, point1, point2):
    a, b = long_id(co, point1), long_id(co, point2)
    if is_ancestor(co, a, b, None):
        print point1 + ' is an ancestor of ' + point2
        return 0
    print point1 + ' is not an ancestor of ' + point2
    return 1

def cli_print_dag(co, uheads):
    if uheads == []:
        heads = bdecode(co.linforepo.get('heads'))
    else:
        heads = [long_id(co, head) for head in uheads]
    pretty_print_big_dag(co, heads)
    return 0

def cli_print_mini_dag(co, file, uheads, by_id):
    if by_id:
        handle = binascii.unhexlify(file)

    else:
        fname = subpath(co.local, file)[1]
        handle = filename_to_handle(co, fname)

    if uheads == []:
        heads = bdecode(co.linforepo.get('heads'))
    else:
        heads = [long_id(co, head) for head in uheads]
    pretty_print_dag(co, handle, heads)
    return 0

def cli_handle_to_filename(co, head, handles):
    handle = binascii.unhexlify(handles[0])
    if head == 'local':
        name = handle_to_filename(co, handle)
    else:
        point = long_id(co, head)
        name = fullpath_at_point(co, handle, point)
    print name
    return 0

def populate_local_repos(co, ltxn):
    if co.linforepo.has_key('heads', ltxn):
        return

    root = bencode({'precursors': [], 'handles': {roothandle: {'add': {'type': 'dir'}, 'name': ''}}})
    head = sha.new(root).digest()
    assert head == rootnode
    co.lcrepo.put(head, root, txn=ltxn)
    co.linforepo.put('heads', bencode([head]), txn=ltxn)
    co.linforepo.put('branchmax', bencode(0), txn=ltxn)
    co.linforepo.put('lasthandle', bencode(0), txn=ltxn)

    sync_history(co, head, ltxn)
    return

def _list_merge_files(co):
    if co.repo is None:
        return []

    repohead = repo_head(co, co.repo)
    if repohead == None:
        repohead = rootnode
    heads = bdecode(co.linforepo.get('heads'))

    named, modified, added, deleted = \
           handles_in_branch(co, [repohead], heads, None)
    handles = damerge(named, modified, added, deleted)

    named, modified, added, deleted = \
           Set(named), Set(modified), Set(added), Set(deleted)

    files = []
    for handle in handles:
        mletter, nletter = ' ', ' '

        if handle in added:
            if handle in deleted:
                continue
            mletter, nletter = 'A', 'A'

        elif handle in deleted:
            mletter, nletter = 'D', 'D'

        else:
            if handle in named:
                nletter = 'N'

            if handle in modified:
                mletter = 'M'

        assert not (mletter == ' ' and nletter == ' ')

        files.append((handle_to_filename(co, handle), mletter + nletter))

    files.sort()
    return files

class GarbledCommitError(StandardError): pass

def _commit_helper(co, commit_files):
    output = []

    # check for a saved comment
    comment_file = path.join(co.conf_path, '.comment')
    if path.exists(comment_file):
        comment_fd = file(comment_file, 'r')
        comment = comment_fd.read()
        comment_fd.close()

        if comment[-1] == '\n':
            comment = comment[:-1]

        output.append(comment)

    else:
        output.append('')

    output.extend(['### Enter comment above', '### Files'])

    files, name_map = [], {}
    for handle, info in commit_files:
        name = handle_to_filename(co, handle)
        files.append((name, ''.join(_letters(info))))
        name_map[name] = (handle, info)

    files.sort()

    for name, letters in files:
        output.append("%s\t%s" % (letters, name))

    files = _list_merge_files(co)
    if len(files):
        output.append('### Merge files')
        for name, letters in files:
            output.append('%s\t%s' % (letters, name))

    output.append(os.linesep)

    fd, fname = tempfile.mkstemp()
    fhandle = os.fdopen(fd, 'w+')

    out_str = os.linesep.join(output)
    out_ustr = out_str.decode('utf8')
    out_str = out_ustr.encode(text_encoding)
    fhandle.write(out_str)

    fhandle.close()

    if platform == 'win32':
        spawn = os.spawnv
        editor = os.environ['WINDIR'] + '\\notepad.exe'
    else:
        spawn = os.spawnvp
        editor = 'vi'

    if os.environ.has_key('CDVEDITOR'):
        editor = os.environ['CDVEDITOR']
    elif os.environ.has_key('EDITOR'):
        editor = os.environ['EDITOR']

    args = editor.split() + [fname]
    errored = True
    while errored:
        errored = False
        if spawn(os.P_WAIT, args[0], args):
            raise CommitError, 'Could not run editor "%s"' % (editor,)

        fhandle = open(fname, 'rU')
        text = fhandle.read()
        fhandle.close()

        try:
            try:
                utext = text.decode(text_encoding)
            except UnicodeDecodeError:
                raise GarbledCommitError, \
                      "Invalid %s characters in comment" % (text_encoding,)

            lines = utext.encode('utf8').splitlines()

            cur_line = 0
            try:
                while lines[cur_line].startswith('### Error: '):
                    lines.pop(cur_line)
            except IndexError:
                pass

            while cur_line < len(lines):
                if lines[cur_line] == '### Enter comment above':
                    break
                cur_line += 1

            if cur_line == len(lines):
                raise GarbledCommitError, "Could not find end of comment"

            comment = '\n'.join(lines[:cur_line])

            cur_line += 1
            if lines[cur_line] != '### Files':
                raise GarbledCommitError, "Expected '### Files' line after end of comment"

            fcommit_files = []

            cur_line += 1
            while cur_line < len(lines):
                line = lines[cur_line]
                cur_line += 1
                if line.strip() == '':
                    continue
                if line.startswith('### '):
                    break
                try:
                    tab = line.index('\t')
                except ValueError:
                    raise GarbledCommitError, "Bad commit file line:\n%s" % \
                          (line[:].rstrip(),)
                name = line[tab+1:].rstrip()
                fcommit_files.append(name_map[name])

        except GarbledCommitError, msg:
            error = msg.args[0]
            errored = True

        if errored:
            answer = raw_input("Error: %s\nReturn to editor? [Y/n]: " % (error,))
            if answer == 'n':
                raise CommitError, msg

            output = ['### Error: %s' % (line,) for line in error.split('\n')]
            out_str = os.linesep.join(output)
            out_ustr = out_str.decode('utf8')
            out_str = out_ustr.encode(text_encoding)

            fhandle = open(fname, 'w')
            fhandle.write(out_str)
            fhandle.write(text)
            fhandle.close()

    os.remove(fname)

    return fcommit_files, comment

def commit(co, remote, comment, tstamp=None, backup=False, files=list()):
    try:
        if remote is None:
            if co.user is None:
                co.user = co.varsdb.get('user')
            if co.user is None:
                raise NetworkError, 'You must set the "user" variable'
            repohead = None
        else:
            network_prep(co)
            repohead = repo_head(co, co.repo)
            if repohead is None:
                repohead = rootnode
    except NetworkError, msg:
        print msg
        return 1

    co.handle_name_cache = {}

    ltxn = co.txn_begin()
    mark_modified_files(co, ltxn)
    co.txn_commit(ltxn)

    try:
        if files == []:
            handles = [(handle, 0) for handle in co.editsdb.keys()]
        else:
            handles = Glob(co, files).db_walk(deletes=1)

        commit_files = find_commit_files(co, handles)

        # get the comment from the user if it was supplied on the command line
        if not backup:
            if comment is None:
                commit_files, comment = _commit_helper(co, commit_files)

            # clean up the comment a bit
            comment = comment.rstrip()
            if comment == '' and not co.nopass:
                print 'No comment given, aborting.'
                return 1
            comment = comment + '\n'

    except CommitError, msg:
        print 'commit failed: ' + str(msg)
        return 1

    if comment is not None:
        # save comment in case commit fails
        tmp_fd, tmp_file = tempfile.mkstemp(dir=co.conf_path)
        tmp_fd = os.fdopen(tmp_fd, 'w')
        tmp_fd.write(comment)
        tmp_fd.close()

        comment_file = path.join(co.conf_path, '.comment')
        shutil.move(tmp_file, comment_file)

    # create and verify the changeset
    ltxn = co.txn_begin()

    point = None
    try:
        if not backup:
            point = gen_changeset(co, commit_files, comment, repohead, ltxn, tstamp=tstamp)
    except HistoryError, msg:
        co.txn_abort(ltxn)
        print 'error - ' + str(msg)
        return 1

    if point is not None:
        try:
            sync_history(co, point, ltxn)
        except HistoryError, msg:
            print 'commit failed: ' + str(msg)
            print 'This is likely the result of a partial commit with missing dependencies. Alternatively, it could be a bug in Codeville. If you believe this to be the case, please report this to the development list.'
            co.txn_abort(ltxn)
            return 1
    else:
        precursors = bdecode(co.linforepo.get('heads', txn=ltxn))
        point = precursors[0]

        if len(precursors) > 1 and backup is True:
            print 'error - cannot use backup flag when merging'
            co.txn_abort(ltxn)
            return 1

    if remote is not None:
        ch = ClientHandler(co)

        try:
            s = authenticate(co, ch, remote, ltxn)
        except NetworkError, msg:
            co.txn_abort(ltxn)
            print 'commit failed: ' + str(msg)
            return 1
        except ServerError, msg:
            co.txn_abort(ltxn)
            print 'commit failed: ' + str(msg)
            return 1

        try:
            ch.commit(s, remote[2], point, ltxn)
        except NetworkError, msg:
            co.txn_abort(ltxn)
            print 'commit failed: ' + str(msg)
            return 1
        except ServerError, msg:
            ch.close(s)
            co.txn_abort(ltxn)
            print 'commit failed: ' + str(msg)
            return 1

        ch.close(s)
        ch = None
        co.linforepo.put(tuple_to_server(remote), point, txn=ltxn)

    co.txn_commit(ltxn)

    # remove saved comment file
    os.remove(comment_file)

    print 'commit succeeded'
    return 0

def create_repo(co, remote):
    try:
        network_prep(co)
    except NetworkError, msg:
        print msg
        return 1

    ch = ClientHandler(co)

    txn = co.txn_begin()
    try:
        s = authenticate(co, ch, remote, txn)
        co.txn_commit(txn)
    except NetworkError, msg:
        co.txn_abort(txn)
        print 'creation failed: ' + str(msg)
        return 1
    except ServerError, msg:
        co.txn_abort(txn)
        print 'creation failed: ' + str(msg)
        return 1

    try:
        ch.create_repo(s, remote[2])
    except NetworkError, msg:
        print 'creation failed: ' + str(msg)
        return 1
    except ServerError, msg:
        print 'creation failed: ' + str(msg)
        retval = 1
    else:
        print 'creation succeeded'
        retval = 0

    ch.close(s)
    return retval

def remove_repo(co, remote):
    try:
        network_prep(co)
    except NetworkError, msg:
        print msg
        return 1

    ch = ClientHandler(co)

    txn = co.txn_begin()
    try:
        s = authenticate(co, ch, remote, txn)
        co.txn_commit(txn)
    except NetworkError, msg:
        co.txn_abort(txn)
        print 'destroy failed: ' + str(msg)
        return 1
    except ServerError, msg:
        co.txn_abort(txn)
        print 'destroy failed: ' + str(msg)
        return 1

    try:
        ch.remove_repo(s, remote[2])
    except NetworkError, msg:
        print 'destroy failed: ' + str(msg)
        return 1
    except ServerError, msg:
        print 'destroy failed: ' + str(msg)
        retval = 1
    else:
        print 'destroy succeeded'
        retval = 0

    ch.close(s)
    return retval

def list_repos(co):
    try:
        network_prep(co)
    except NetworkError, msg:
        print msg
        return 1

    ch = ClientHandler(co)

    txn = co.txn_begin()
    try:
        s = authenticate(co, ch, server_to_tuple(co.repo), txn)
        co.txn_commit(txn)
    except NetworkError, msg:
        co.txn_abort(txn)
        print 'list failed: ' + str(msg)
        return 1
    except ServerError, msg:
        co.txn_abort(txn)
        print 'list failed: ' + str(msg)
        return 1

    try:
        rlist = ch.list_repos(s)
    except NetworkError, msg:
        print 'list failed: ' + str(msg)
        return 1
    except ServerError, msg:
        print 'list failed: ' + str(msg)
        ch.close(s)
        return 1

    ch.close(s)
    print 'Server has the following repositories:\n\t',
    print '\n\t'.join(rlist)
    return 0

def _letters(value):
    if value.has_key('add'):
        return ['A', 'A']
    if value.has_key('delete'):
        return ['D', 'D']
    letters = [' ', ' ']
    if value.has_key('hash'):
        letters[0] = 'M'
    if value.has_key('name'):
        letters[1] = 'N'
    return letters

def describe(co, point, short, xml, dodiff, files):
    point = long_id(co, point)

    if xml:
        try:
            print dump_changeinfo(co, point)
        except ValueError:
            print 'error - XML can only be written for clean merges.'
            return 1
        return 0

    cset = bdecode(co.lcrepo.get(point))
    _print_change(co, point, cset, not short)

    if dodiff and point != rootnode:
        return diff(co, [short_id(co, cset['precursors'][0]), short_id(co, point)], files, True)

    return 0

def status(co, files, verbose):
    ltxn = co.txn_begin()
    mark_modified_files(co, ltxn)
    co.txn_commit(ltxn)

    # go do the stuff
    if verbose:
        try:
            plist = _status_verbose(co, files)
        except re.error, msg:
            print 'error - bad ignore list: %s' % (str(msg),)
            return 1

    else:
        plist = _status(co, files)

    # print the list of modified files
    if len(plist):
        print '### Files'
        plist.sort()
        olist = []
        for value in plist:
            olist.append(value[1][0] + value[1][1] + '\t' + value[0])
        print os.linesep.join(olist)

    # print the list of merged files
    plist = _list_merge_files(co)
    if len(plist):
        print '### Merge files'
        for name, letters in plist:
            print letters + '\t' + name

    return 0

def _status(co, files):
    # collect info on editted files
    ed_set = Set(co.editsdb.keys())

    # no args means show all editted files
    if files == []:
        db_set = ed_set
    else:
        db_set = Set([handle for handle, expanded in Glob(co, files).db_walk(deletes=1)])

    # print the ones we care about
    plist = []
    for handle in (db_set & ed_set):
        file = handle_to_filename(co, handle)
        info = bdecode(co.editsdb.get(handle))
        plist.append((file, _letters(info)))

    return plist

def _status_verbose(co, files):
    # Read ignore patterns
    cpats = ignore_patterns(co)

    # no args means search the whole client
    if files == []:
        files.append(path.join(co.local, '...'))

    glob = Glob(co, files)

    # do the filename expansion
    fs_set = Set([file for file, expanded in glob.fs_walk()])

    # get the list of files we manage
    db_set = Set([handle_to_filename(co, handle) for handle, expanded in glob.db_walk(deletes=1)])

    # collect info on editted files
    ed_set, de_set = Set(), Set()
    for handle, value in co.editsdb.items():
        file = handle_to_filename(co, handle)
        if bdecode(value).has_key('delete'):
            de_set.add(file)
        ed_set.add(file)

    plist = []

    # record unmanaged files
    for file in (fs_set - db_set):
        ignore = 0
        for cpat in cpats:
            if cpat.search(file) is not None:
                ignore = 1
                break

        if ignore:
            continue

        plist.append((file, ['?', '?']))

    # record files inconsistent in the filesystem
    for file in (db_set - fs_set - de_set):
        plist.append((file, ['!', '!']))

    # record all the modified files
    for file in ((ed_set & db_set) - (db_set - fs_set - de_set)):
        handle = filename_to_handle(co, file, deletes=1)
        hinfo = bdecode(co.editsdb.get(handle))
        plist.append((file, _letters(hinfo)))

    return plist

def cli_heads(co):
    heads = bdecode(co.linforepo.get('heads'))
    pheads = [short_id(co, head) for head in heads]
    print ', '.join(pheads)
    return 0

def cli_last_modified(co, lname, uhead, by_id):
    if by_id:
        ohandle = binascii.unhexlify(lname)

    else:
        try:
            lfile, fname = subpath(co.local, lname)
        except ValueError:
            print 'error - ' + lname + ' is outside repository'
            return 1
        ohandle = filename_to_handle(co, fname)
        if ohandle is None:
            print 'error - ' + fname + ' is not in repository'
            return 1

    repohead = None
    if uhead is None:
        repohead = repo_head(co, co.repo)
        if repohead is None:
            heads = bdecode(co.linforepo.get('heads'))
            repohead = heads[0]
    else:
        repohead = long_id(co, uhead)
    point = handle_last_modified(co, co.contents, ohandle, repohead, None)
    print short_id(co, point)

    return 0

def ignore_patterns(co):
    patterns = []
    try:
        fd = open(path.join(co.conf_path, 'ignore'), 'rU')
        patterns = fd.readlines()
        fd.close()
    except IOError:
        pass

    # compile all the patterns and ensure they match full paths
    return [re.compile('^%s$' % pat.strip()) for pat in patterns]

def diff(co, revs, files, print_new):

    OK       = 0
    NEW_FILE = 1
    DELETED  = 2
    MISSING  = 3

    def file_lines(handle, rev, lfile):
        if rev == 'local':
            linfo = handle_name(co, handle, None)
            if linfo is None:
                return (NEW_FILE, [''])

            if linfo.has_key('delete'):
                return (DELETED, [''])

            try:
                h = open(path.join(co.local, lfile), 'rb')
                lines = h.read().split('\n')
                h.close()
            except IOError:
                return (MISSING, [''])

        else:
            linfo = handle_name_at_point(co, handle, rev, None)

            if linfo is None:
                return (NEW_FILE, [''])

            if linfo.has_key('delete'):
                return (DELETED, [''])

            pinfo = handle_contents_at_point(co, handle, rev, None)
            lines = pinfo['lines']

        return (OK, lines)

    def print_format(error, lfile):
        if   error == OK:
            return (1, lfile, None)
        elif error == NEW_FILE:
            return (0, '(new file)', 'File "%s" added.')
        elif error == DELETED:
            return (0, '(deleted)', 'File "%s" deleted.')
        elif error == MISSING:
            print 'WARNING - File not found: ' + lfile
            return (0, '(File not found!)', None)

        assert 0
        return

    co.handle_name_cache = {}
    ltxn = co.txn_begin()
    mark_modified_files(co, ltxn)
    co.txn_commit(ltxn)
    editsdb = co.editsdb

    pathfunc, patharg = [], []
    heads = bdecode(co.linforepo.get('heads'))
    for i in xrange(len(revs)):
        if revs[i] == 'repo':
            revs[i] = repo_head(co, co.repo)
            pathfunc.append(fullpath_at_point)
            patharg.append(revs[i])

        elif revs[i] == 'local':
            pathfunc.append(handle_to_filename)
            patharg.append(None)

        else:
            if revs[i] is None:
                revs[i] = heads[0]
            else:
                revs[i] = long_id(co, revs[i])

            pathfunc.append(fullpath_at_point)
            patharg.append(revs[i])

    branch = []
    for i in xrange(len(revs)):
        if revs[i] == 'local':
            branch.append(heads)
        else:
            branch.append([revs[i]])

    # assemble a list of all files with changes
    named, modified, added, deleted = \
           handles_in_branch(co, branch[0], branch[1], None)
    names2, modified2, added2, deleted2 = \
            handles_in_branch(co, branch[1], branch[0], None)
    all_handles = dmerge(modified, modified2)

    # include local edits if diffing against the local tree
    if revs[0] == 'local' or revs[1] == 'local':
        all_handles = dmerge(all_handles, editsdb.keys())

    handles = []
    if files == []:
        if print_new:
            handles = damerge(all_handles, added, deleted, added2, deleted2)

        else:
            handles = all_handles

    else:
        dall_handles = Set(all_handles)
        for handle, expanded in Glob(co, files).db_walk():
            if expanded and not handle in dall_handles:
                continue
            handles.append(handle)

    diffprog, diffpath = None, None
    if os.environ.has_key('CDVDIFF'):
        cdvdiff = shlex.split(os.environ['CDVDIFF'])
        if cdvdiff != []:
            diffargs = cdvdiff
            diffprog = diffargs[0]
            if platform == 'win32':
                # windows inteprets the argument, excitement abounds
                diffargs = ['"%s"' % (arg,) for arg in diffargs]
            diffpath = tempfile.mkdtemp('', 'cdv-')
            fd = open(path.join(diffpath, 'holder'), 'a')
            fd.close()

    hlist = []
    for handle in handles:
        hlist.append((pathfunc[0](co, handle, patharg[0]),
                      pathfunc[1](co, handle, patharg[1]),
                      handle))
    hlist.sort()

    if platform == 'win32':
        spawn = os.spawnv
    else:
        spawn = os.spawnvp

    retval = 0
    for pre_lfile, lfile, handle in hlist:
        #linfo = bdecode(co.staticdb.get(handle))
        linfo = db_get(co, co.staticdb, handle, None)
        if linfo['type'] != 'file':
            continue

        error, pre_lines = file_lines(handle, revs[0], pre_lfile)
        printable, pre_lfile, msg0 = print_format(error, pre_lfile)

        error, lines = file_lines(handle, revs[1], lfile)
        printable2, lfile, msg1 = print_format(error, lfile)

        printable += printable2
        if printable == 0:
            continue

        if printable == 1 and not print_new:
            if msg0 is not None:
                print msg0 % lfile
            if msg1 is not None:
                print msg1 % pre_lfile
            continue

        if diffprog:
            file1 = path.join(diffpath, 'old', pre_lfile)
            file2 = path.join(diffpath, 'new', lfile)

            os.makedirs(path.split(file1)[0])
            foo = open(file1, 'w+')
            foo.write('\n'.join(pre_lines))
            foo.close()

            os.makedirs(path.split(file2)[0])
            foo = open(file2, 'w+')
            foo.write('\n'.join(lines))
            foo.close()

            fileargs = [file1, file2]
            if platform == 'win32':
                fileargs = ['"%s"' % (arg) for arg in fileargs]
            args = diffargs + fileargs

            try:
                ret = spawn(os.P_WAIT, diffprog, args)
            except OSError:
                ret = 127

            os.remove(file1)
            os.removedirs(path.split(file1)[0])
            os.remove(file2)
            os.removedirs(path.split(file2)[0])
            if ret == 127:
                print "error - Could not run diff program specified by CDVDIFF"
                retval = 1
                break

        else:
            print '--- ' + pre_lfile
            print '+++ ' + lfile
            # the diff code assumes \n after each line, not between lines
            if pre_lines[-1] == '':
                pre_lines.pop()
            if lines[-1] == '':
                lines.pop()
            stdout.write(unified_diff(pre_lines, lines))

    if diffpath is not None:
        os.unlink(path.join(diffpath, 'holder'))
        os.rmdir(diffpath)
    return retval

def _comment_compress(comment):
    try:
        offset = comment.index('\n')
        comment = comment[:offset]
    except ValueError:
        pass

    if len(comment) > 76:
        comment = comment[:73] + '...'

    return comment

def _print_change(co, point, pinfo, v, owner=None, time=None):
    if not v:
        print 'Change %s' % (short_id(co, point),),
        if owner is not None:
            print '(%s)' % (owner,),
        print 'by ' + pinfo['user'],
        if time is not None:
            print 'on ' + ctime(time)
        elif pinfo.has_key('time'):
            print 'on ' + ctime(pinfo['time'])
        if pinfo.has_key('comment'):
            print '"' + _comment_compress(pinfo['comment']) + '"'

    else:
        print '### Change: ' + binascii.hexlify(point)
        print '### Short change: ' + short_id(co, point)
        if owner is not None:
            print '### Commit change: ' + owner

        if pinfo['precursors'] != []:
            print '### Precursors:',
            ps = []
            for p in pinfo['precursors']:
                ps.append(short_id(co, p))
            print ', '.join(ps)

        if pinfo.has_key('user'):
            print '### User: ' + pinfo['user']

        if time is not None:
            print '### Date: ' + ctime(time)
        elif pinfo.has_key('time'):
            print '### Date: ' + ctime(pinfo['time'])

        if pinfo.has_key('comment'):
            print '### Comment'
            print pinfo['comment'].rstrip()

        plist = []
        for handle, value in pinfo['handles'].items():
            if handle == roothandle:
                continue
            plist.append((fullpath_at_point(co, handle, point), _letters(value)))
        if len(plist):
            print '### Files'
            plist.sort()
            olist = []
            for value in plist:
                olist.append(value[1][0] + value[1][1] + '\t' + value[0])
            print os.linesep.join(olist)

    return

def _history_increment(co, handles, precursors, changes):
    for handle in handles:
        for pre in precursors:
            change = handle_last_modified(co, co.contents, handle, pre, None)
            if change is not None:
                changes.setdefault(change, {})[handle] = 1
    return

def _history_deps(node, args):
    co, cutoffs = args[0], args[1]
    if len(cutoffs) and _is_ancestor(co, node, cutoffs, None):
        return []
    cset = bdecode(co.lcrepo.get(node))
    return cset['precursors']

def _owner_deps(node, args):
    co, limit = args[0], args[1]

    # heuristic shortcut
    limit -= 1
    if limit == 0:
        return []
    args[1] = limit

    cset = bdecode(co.lcrepo.get(node))
    try:
        return [cset['precursors'][0]]
    except IndexError:
        pass
    return []

def history(co, head, limit, skip, v, by_id, files):
    repohead = None
    heads = None
    if head is not None:
        heads = [head]
    else:
        heads = bdecode(co.linforepo.get('heads'))
        repohead = repo_head(co, co.repo)
        if repohead is None:
            if co.repo is not None:
                repohead = rootnode
                heads.insert(0, repohead)
        else:
            heads.insert(0, repohead)

    head = heads[0]

    # the repository head may have more than we have merged locally
    if repohead is not None:
        while not _is_ancestor(co, head, heads, None):
            head = bdecode(co.lcrepo.get(head))['precursors'][0]

    # make an initial list of points to print based on user-specified files
    owner_cutoff = limit + skip
    changes = None
    if files != []:
        changes = {}
        if by_id:
            handles = [binascii.unhexlify(handle) for handle in files]
        else:
            handles = [handle for handle, expanded in Glob(co, files).db_walk()]
        _history_increment(co, handles, heads, changes)
        # the heuristic breaks if we're not printing everything
        owner_cutoff = -1

    # get a list of the clean merge heads for this repository
    dfs = DFS(_owner_deps, [co, owner_cutoff + 1])
    dfs.search(head)
    owners = dfs.result()

    # sort all the history points in reverse print order
    cutoffs = []
    if owners[0] != rootnode:
        cutoffs.append(owners[0])
    dfs = DFS(_history_deps, [co, cutoffs])
    dfs.search(rootnode)
    for head in heads:
        dfs.search(head)
    ordering = dfs.result()
    ordering.reverse()
    # pop off the root node
    assert ordering[-1] == rootnode
    ordering.pop()

    owner = 'local'
    time = None
    for point in ordering:
        hinfo = bdecode(co.lcrepo.get(point))

        clean = False
        if clean_merge_point(hinfo):
            clean = True

        if point == owners[-1]:
            # committed change, but we don't know the server merge change
            owners.pop()
            time  = hinfo['time']
            owner = '----'

            if clean:
                # this is the server merge change
                owner = short_id(co, point)
                assert changes is None or not changes.has_key(point)
                continue

        # only display if it's not a clean merge and it was asked for
        if clean:
            continue

        if changes is not None and not changes.has_key(point):
            continue

        # figure out the next set of changes to print
        if changes is not None and changes.has_key(point):
            _history_increment(co, changes[point].keys(), hinfo['precursors'],
                               changes)
            del changes[point]

        if skip > 0:
            skip -= 1
            continue

        if limit == 0:
            break
        limit -=1

        _print_change(co, point, hinfo, v, owner=owner, time=time)
        if v:
            print '-' * 78,
        print

    return 0

def revert(co, files, unmod_flag):
    co.handle_name_cache = {}
    txn = co.txn_begin()
    mark_modified_files(co, txn)

    heads = bdecode(co.linforepo.get('heads'))
    editsdb = co.editsdb
    #modified, names, deletes, newhandles = [], {}, {}, {}
    modified = []
    for handle, expanded in Glob(co, files).db_walk(deletes=1):
        filename = handle_to_filename(co, handle, txn)
        filepath = path.join(co.local, filename)

        editted  = False
        exists   = path.exists(filepath)

        if editsdb.has_key(handle, txn):
            info = bdecode(editsdb.get(handle, txn=txn))
            if     info.has_key('add') or \
                   info.has_key('name') or \
                   info.has_key('delete'):
                print 'warning - cannot revert name operation on %s' % (filename,)

            # XXX: hack until reverts on name ops work
            if info.has_key('add'):
                exists = True

            elif info.has_key('hash'):
                editted = True

        elif exists:
            if not expanded:
                file = handle_to_filename(co, handle, txn)
                print 'warning - %s is not opened for edit' % (filename,)
            continue

        sinfo = bdecode(co.staticdb.get(handle, txn=txn))
        if sinfo['type'] == 'file' and (editted or not exists):
            file_points = []
            for point in heads:
                linfo = handle_contents_at_point(co, handle, point, None)
                if linfo is None:
                    continue
                file_points.append((linfo['lines'], linfo['line points'], linfo['points']))

            # XXX: hack until merge-through-conflict code is done
            if len(file_points) == 2:
                local = file_points[0]
                remote = file_points[1]
                lines = find_conflict(local[0],  local[1],  local[2],
                                      remote[0], remote[1], remote[2])

            else:
                lines = find_conflict_multiple_safe(file_points)[0]

            if lines is None:
                print 'error - cannot revert %s' % (filename,)
                co.txn_abort(txn)
                return 1
            #modified.append((handle, linfo))

            ls, conflict = [], 0
            for l in lines:
                if type(l) is str:
                    ls.append(l)
                else:
                    conflict = 1
                    ls.append('<<<<<<< local')
                    ls.extend(l[0])
                    ls.append('=======')
                    ls.extend(l[1])
                    ls.append('>>>>>>> remote')

            if unmod_flag:
                if not exists:
                    continue

                h = open(filepath, 'rb')
                contents = h.read()
                h.close()
                if '\n'.join(ls) == contents:
                    unset_edit(co, handle, ['hash'], txn)
                    mtime = int(path.getmtime(filepath))
                    co.modtimesdb.put(handle, bencode(mtime), txn=txn)
                    print 'reverting: %s' % (filename,)
                continue

            if not conflict and editted:
                unset_edit(co, handle, ['hash'], txn)
            hfile = path.join(co.temppath, binascii.hexlify(handle))
            h = open(hfile, 'wb')
            h.write('\n'.join(ls))
            h.close()
            modified.append((handle, filepath))
            print 'reverting: %s' % (filename,)

    # XXX: use update code

    for handle, filename in modified:
        temppath = path.join(co.temppath, binascii.hexlify(handle))
        destpath = path.join(co.local, filename)
        preserving_rename(temppath, destpath)
        mtime = int(path.getmtime(destpath))
        co.modtimesdb.put(handle, bencode(mtime), txn=txn)

    co.txn_commit(txn)
    print 'revert succeeded'
    return 0

def annotate(co, rev, files):
    """Print each line of files with information on the last modification"""
    if rev == 'repo':
        rev = repo_head(co, co.repo)
    elif rev != 'local':
        rev = long_id(co, rev)

    if len(files) == 0:
        files = ['...']

    if rev == 'local':
        precursors = bdecode(co.linforepo.get('heads'))
        
        repohead = repo_head(co, co.repo)
        if repohead is None:
            repohead = rootnode
            
        if repohead not in precursors:
            while not _is_ancestor(co, repohead, precursors, None):
                info = bdecode(co.lcrepo.get(repohead))
                try:
                    repohead = info['precursors'][0]
                except IndexError:
                    repohead = rootnode
                    
            if repohead not in precursors:
                precursors.insert(0, repohead)
    
    cache = {}
    for handle, expanded in Glob(co, files).db_walk(deletes=1):
        sinfo = bdecode(co.staticdb.get(handle))
        if sinfo['type'] == 'file':
            if rev == 'local':
                filename = handle_to_filename(co, handle)
                file_points = []
                pres = simplify_precursors(co, handle, co.contents, precursors, None)[0]
                for pre, index in pres:
                    info = handle_contents_at_point(co, handle, pre, None, replayfunc=merge.annotate)
                    file_points.append((info['lines'], info['line points'], info['points']))
                lfile = path.join(co.local, filename)
                try:
                    h = open(lfile, 'rb')
                except IOError:
                    print 'error - cannot open %s' % (filename,)
                    return 1
                lines = h.read().split('\n')
                h.close()
                
                lpoints = find_annotation(file_points, lines)
            else:
                filename = fullpath_at_point (co, handle, rev)
                cinfo = handle_contents_at_point(co, handle, rev, None, replayfunc=merge.annotate)
                if cinfo is None or cinfo.has_key('delete'):
                    if expanded:
                        continue
                    print 'error - cannot find %s' % (filename,)
                    return 1

                lines = cinfo['lines']
                lpoints = cinfo['line points']

            print >> stderr, 'Annotations for %s' % (filename,)
            print >> stderr, '***************'
            for i in xrange(len(lines)):
                point = lpoints[i]
                if point is None:
                    lrev = 'local'
                    user = '-----'
                    time = '-----'

                elif cache.has_key(point):
                    lrev, user, time = cache[point]

                else:
                    lrev = short_id(co, point)
                    pinfo = bdecode(co.lcrepo.get(point))
                    user = pinfo['user']
                    if pinfo.has_key('time'):
                        time = strftime('%Y-%m-%d', localtime(pinfo['time']))
                    else:
                        time = '-----'

                    cache[point] = (lrev, user, time)

                print '%s (%s %s):' % (lrev, user, time,), lines[i]

    return 0
    
class PathError(Exception):
    pass

def find_co(local, metadata_dir='.cdv'):
    if not path.exists(local):
        raise Exception, 'path ' + local + ' does not exist'
    while not path.exists(path.join(local, metadata_dir)):
        parent = path.split(local)[0]
        if local == parent:
            raise PathError, 'cannot find checkout, use "cdv init" to create one'
        local = parent
    return local

#try:
#    import psyco
#    psyco.bind(diff, 0)
#except ImportError:
#    pass

# Everything below here is for testing

def file_contents(file):
    h = open(file, 'rb')
    contents = h.read()
    h.close()
    return contents

def set_file_contents(file, contents):
    h = open(file, 'wb')
    h.write(contents)
    h.close()

def append_file_contents(file, contents):
    h = open(file, 'ab')
    h.write(contents)
    h.close()

def rm_rf(files):
    for local in files:
        mode = os.lstat(local).st_mode
        if stat.S_ISDIR(mode):
            for lamb in os.listdir(local):
                rm_rf([path.join(local, lamb)])
            os.rmdir(local)
        elif stat.S_ISREG(mode):
            os.unlink(local)

sh = None
sht = None
ag = None
agt = None

def init_test(local, remote, remote2):
    global sh, sht
    global ag, agt
    if path.exists(local):
        rm_rf([local])
    os.makedirs(path.join(local, 'repo'))
    set_file_contents(path.join(local, 'repo', 'codeville_repository'), '')

    from passwd import Passwd
    pw = Passwd(path.join(local, 'repo', 'passwd'), create=1)
    pw.add('unittest', '')
    pw.add('unittest2', '')

    from ConfigParser import ConfigParser
    sconfig = ConfigParser()
    sconfig.add_section('control')
    sconfig.set('control', 'backup', 'False')
    sconfig.set('control', 'datadir', path.join(local, 'repo'))
    sconfig.add_section('post-commit')

    sh = ServerHandler(sconfig)
    sh.bind(remote[1])
    sh.db_init(init=True)
    sht = Thread(target = sh.listen_forever, args = [])
    sht.start()

    from agent import Agent
    ag = Agent()
    auth_path = tempfile.mkdtemp('', 'cdv-')
    auth_file = path.join(auth_path, 'agent.test')
    ag.listen_sock(auth_path, auth_file)
    agt = Thread(target = ag.listen, args = [])
    agt.start()

    os.makedirs(path.join(local, 'co'))
    co = Checkout(path.join(local, 'co'), init=True)
    txn = co.txn_begin()
    co.varsdb.put('user', 'unittest', txn=txn)
    co.txn_commit(txn)

    os.makedirs(path.join(local, 'co2'))
    co2 = Checkout(path.join(local, 'co2'), init=True)
    txn = co2.txn_begin()
    co2.varsdb.put('user', 'unittest2', txn=txn)
    co2.txn_commit(txn)

    co.nopass = co2.nopass = 1
    co.repo = co2.repo = tuple_to_server(remote)
    create_repo(co, remote)
    create_repo(co, remote2)
    return co, co2

def shutdown_test(local, cos):
    if sh is not None:
        sh.rs.doneflag.set()
        sh.rs.start_connection(('localhost', 6602))
        sh.shutdown.wait()
        sh.close()

    from errno import ECONNRESET
    import socket
    if ag is not None:
        ag.shutdown_flag = 1
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(ag.auth_file)
        try:
            sock.recv(1)
        except socket.error, reason:
            assert reason[0] == ECONNRESET
        else:
            sock.close()

    for co in cos:
        co.close()

def reset_co(co):
    txn = co.txn_begin()
    co.linforepo.put('heads', bencode([rootnode]), txn=txn)
    co.editsdb.truncate(txn=txn)
    co.modtimesdb.truncate(txn=txn)
    co.filenamesdb.truncate(txn=txn)
    co.txn_commit(txn)
    co.handle_name_cache = {}
    for lamb in os.listdir(co.local):
        if lamb == '.cdv':
            continue
        rm_rf([path.join(co.local, lamb)])

def reset_test(co, co2, remote, remote2=None):
    remove_repo(co, remote)
    create_repo(co, remote)
    if remote2:
        remove_repo(co, remote2)
        create_repo(co, remote2)
    reset_co(co)
    reset_co(co2)

def test_client():
    global ServerHandler, Thread
    from server import ServerHandler
    from threading import Thread

    local = path.abspath('test')
    cop = path.join(local, 'co')
    co2p = path.join(local, 'co2')

    repo = server_to_tuple('cdv://localhost:6602/unittest')
    repo2 = server_to_tuple('cdv://localhost:6602/unittest2')

    co, co2 = init_test(local, repo, repo2)
    try:
        _test_client(co, cop, co2, co2p, repo, repo2)
    except (AssertionError, Exception):
        shutdown_test(local, [co, co2])
        raise

    shutdown_test(local, [co, co2])
    if path.exists(local):
        rm_rf([local])
    return

def _test_client(co, cop, co2, co2p, repo, repo2):
    print 'TESTING merge conflict'
    set_file_contents(path.join(cop, 'a'), "aaa\nbbb\nccc\nddd\neee\nfff\n")
    add(co, [path.join(cop, 'a')])
    assert commit(co, repo, '') == 0
    assert update(co2, repo) == 0
    set_file_contents(path.join(cop, 'a'), "aaa\nbbb\nccc\nfoo\nddd\neee\nfff\n")
    os.utime(path.join(cop, 'a'), (0, 0))
    assert commit(co, repo, '') == 0
    set_file_contents(path.join(co2p, 'a'), "aaa\nbbb\nccc\nbar\nddd\neee\nfff\n")
    os.utime(path.join(co2p, 'a'), (0, 0))
    assert commit(co2, repo, '') == 1
    assert update(co2, repo) == 0
    assert file_contents(path.join(co2p, 'a')) == "aaa\nbbb\nccc\n<<<<<<< local\nbar\n=======\nfoo\n>>>>>>> remote\nddd\neee\nfff\n"
    print 'TESTING add conflict'
    reset_test(co, co2, repo)
    set_file_contents(path.join(cop, 'a'), 'foo')
    add(co, [path.join(cop, 'a')])
    assert commit(co, repo, '') == 0
    set_file_contents(path.join(co2p, 'a'), 'bar')
    add(co2, [path.join(co2p, 'a')])
    assert commit(co2, repo, '') == 1
    assert update(co2, repo) == 0
    assert path.exists(path.join(co2p, 'a.nameconflict.local'))
    assert path.exists(path.join(co2p, 'a.nameconflict.remote'))

    # use the agent for the rest of the tests
    os.environ['CDV_AUTH_SOCK'] = ag.auth_file
    co.nopass = co2.nopass = 2

    print 'TESTING rename and add file of same name'
    reset_test(co, co2, repo)
    set_file_contents(path.join(cop, 'a'), '')
    add(co, [path.join(cop, 'a')])
    assert commit(co, repo, '') == 0
    assert update(co2, repo) == 0
    rename(co2, path.join(co2p, 'a'), path.join(co2p, 'b'))
    set_file_contents(path.join(co2p, 'a'), '')
    add(co2, [path.join(co2p, 'a')])
    assert commit(co2, repo, '') == 0
    assert update(co, repo) == 0
    assert path.exists(path.join(cop, 'a'))
    assert path.exists(path.join(cop, 'b'))
    print 'TESTING add file conflicting with remote rename'
    reset_test(co, co2, repo)
    set_file_contents(path.join(cop, 'a'), '')
    add(co, [path.join(cop, 'a')])
    assert commit(co, repo, '') == 0
    assert update(co2, repo) == 0
    rename(co, path.join(cop, 'a'), path.join(cop, 'b'))
    assert commit(co, repo, '') == 0
    set_file_contents(path.join(co2p, 'b'), '')
    add(co2, [path.join(co2p, 'b')])
    assert commit(co2, repo, '') == 1
    assert update(co2, repo) == 0
    assert path.exists(path.join(co2p, 'b.nameconflict.local'))
    assert path.exists(path.join(co2p, 'b.nameconflict.remote'))
    print 'TESTING add file conflicting with remote rename and merge conflict'
    reset_test(co, co2, repo)
    set_file_contents(path.join(cop, 'a'), "foo\n")
    add(co, [path.join(cop, 'a')])
    assert commit(co, repo, '') == 0
    assert update(co2, repo) == 0
    append_file_contents(path.join(cop, 'a'), "bar\n")
    edit(co, [path.join(cop, 'a')])
    rename(co, path.join(cop, 'a'), path.join(cop, 'b'))
    assert commit(co, repo, '') == 0
    set_file_contents(path.join(co2p, 'b'), '')
    add(co2, [path.join(co2p, 'b')])
    append_file_contents(path.join(co2p, 'a'), "baz\n")
    edit(co2, [path.join(co2p, 'a')])
    assert commit(co2, repo, '') == 1
    assert update(co2, repo) == 0
    assert path.exists(path.join(co2p, 'b.nameconflict.local'))
    assert path.exists(path.join(co2p, 'b.nameconflict.remote'))
    assert file_contents(path.join(co2p, 'b.nameconflict.remote')) == '<<<<<<< local\nfoo\nbaz\n\n=======\nfoo\nbar\n\n>>>>>>> remote'
    print 'TESTING conflicting local and remote rename'
    reset_test(co, co2, repo)
    set_file_contents(path.join(cop, 'a'), '')
    add(co, [path.join(cop, 'a')])
    assert commit(co, repo, '') == 0
    assert update(co2, repo) == 0
    rename(co, path.join(cop, 'a'), path.join(cop, 'b'))
    assert commit(co, repo, '') == 0
    rename(co2, path.join(co2p, 'a'), path.join(co2p, 'c'))
    assert commit(co2, repo, '') == 1
    assert update(co2, repo) == 0
    assert path.exists(path.join(co2p, 'c.nameconflict'))
    assert path.exists(path.join(co2p, 'c.nameconflict.info'))
    print 'TESTING multiple conflicting local and remote rename'
    reset_test(co, co2, repo)
    os.makedirs(path.join(cop, 'a'))
    set_file_contents(path.join(cop, 'a', 'x'), '')
    os.makedirs(path.join(cop, 'b'))
    set_file_contents(path.join(cop, 'b', 'y'), '')
    os.makedirs(path.join(cop, 'c'))
    set_file_contents(path.join(cop, 'c', 'z'), '')
    add(co, [path.join(cop, 'a', 'x'), path.join(cop, 'b', 'y'), path.join(cop, 'c', 'z')])
    assert commit(co, repo, '') == 0
    assert update(co2, repo) == 0
    rename(co, path.join(cop, 'b', 'y'), path.join(cop, 'a', 'y'))
    rename(co, path.join(cop, 'c', 'z'), path.join(cop, 'a', 'z'))
    assert commit(co, repo, '') == 0
    rename(co2, path.join(co2p, 'b', 'y'), path.join(co2p, 'b', 'x'))
    rename(co2, path.join(co2p, 'c', 'z'), path.join(co2p, 'c', 'x'))
    assert commit(co2, repo, '') == 1
    assert update(co2, repo) == 0
    assert path.exists(path.join(co2p, 'b', 'x.nameconflict'))
    assert path.exists(path.join(co2p, 'b', 'x.nameconflict.info'))
    assert path.exists(path.join(co2p, 'c', 'x.nameconflict'))
    assert path.exists(path.join(co2p, 'c', 'x.nameconflict.info'))
    print 'TESTING rename and back again'
    reset_test(co, co2, repo)
    set_file_contents(path.join(cop, 'a'), 'a')
    add(co, [path.join(cop, 'a')])
    assert commit(co, repo, '') == 0
    assert update(co2, repo) == 0
    rename(co, path.join(cop, 'a'), path.join(cop, 'b'))
    assert commit(co, repo, '') == 0
    rename(co, path.join(cop, 'b'), path.join(cop, 'a'))
    assert commit(co, repo, '') == 0
    assert update(co2, repo) == 0
    assert path.exists(path.join(co2p, 'a'))
    print 'TESTING rename swap'
    reset_test(co, co2, repo)
    set_file_contents(path.join(cop, 'a'), 'a')
    set_file_contents(path.join(cop, 'b'), 'b')
    add(co, [path.join(cop, 'a'), path.join(cop, 'b')])
    assert commit(co, repo, '') == 0
    assert update(co2, repo) == 0
    rename(co, path.join(cop, 'a'), path.join(cop, 'c'))
    rename(co, path.join(cop, 'b'), path.join(cop, 'a'))
    rename(co, path.join(cop, 'c'), path.join(cop, 'b'))
    assert commit(co, repo, '') == 0
    assert update(co2, repo) == 0
    assert file_contents(path.join(co2p, 'a')) == 'b'
    assert file_contents(path.join(co2p, 'b')) == 'a'
    print 'TESTING rename circular'
    reset_test(co, co2, repo)
    set_file_contents(path.join(cop, 'a'), 'a')
    set_file_contents(path.join(cop, 'b'), 'b')
    set_file_contents(path.join(cop, 'c'), 'c')
    add(co, [path.join(cop, 'a'), path.join(cop, 'b'), path.join(cop, 'c')])
    assert commit(co, repo, '') == 0
    assert update(co2, repo) == 0
    rename(co, path.join(cop, 'a'), path.join(cop, 'd'))
    rename(co, path.join(cop, 'b'), path.join(cop, 'a'))
    rename(co, path.join(cop, 'c'), path.join(cop, 'b'))
    rename(co, path.join(cop, 'd'), path.join(cop, 'c'))
    assert commit(co, repo, '') == 0
    assert update(co2, repo) == 0
    assert file_contents(path.join(co2p, 'a')) == 'b'
    assert file_contents(path.join(co2p, 'b')) == 'c'
    assert file_contents(path.join(co2p, 'c')) == 'a'
    print 'TESTING clean reparent with loops in intermediate rename stages'
    reset_test(co, co2, repo)
    os.makedirs(path.join(cop, 'a', 'b', 'c', 'd'))
    add(co, [path.join(cop, 'a', 'b', 'c', 'd')])
    assert commit(co, repo, '') == 0
    assert update(co2, repo) == 0
    rename(co, path.join(cop, 'a', 'b', 'c', 'd'), path.join(cop, 'd'))
    rename(co, path.join(cop, 'a', 'b', 'c'), path.join(cop, 'd', 'c'))
    rename(co, path.join(cop, 'a', 'b'), path.join(cop, 'd', 'c', 'b'))
    rename(co, path.join(cop, 'a'), path.join(cop, 'd', 'c', 'b', 'a'))
    assert commit(co, repo, '') == 0
    assert update(co2, repo) == 0
    assert path.exists(path.join(co2p, 'd', 'c', 'b', 'a'))
    print 'TESTING reparent twisted conflict'
    reset_test(co, co2, repo)
    os.makedirs(path.join(cop, 'a'))
    os.makedirs(path.join(cop, 'b'))
    add(co, [path.join(cop, 'a'), path.join(cop, 'b')])
    assert commit(co, repo, '') == 0
    assert update(co2, repo) == 0
    rename(co, path.join(cop, 'a'), path.join(cop, 'b', 'a'))
    assert commit(co, repo, '') == 0
    rename(co2, path.join(co2p, 'b'), path.join(co2p, 'a', 'b'))
    assert commit(co2, repo, '') == 1
    assert update(co2, repo) == 0
    assert path.exists(path.join(co2p, 'a.parentloop', 'b'))
    assert path.exists(path.join(co2p, 'a.parentloop.info'))
    print 'TESTING reparent twisted conflict'
    reset_test(co, co2, repo)
    os.makedirs(path.join(cop, 'c', 'a'))
    os.makedirs(path.join(cop, 'b'))
    add(co, [path.join(cop, 'c', 'a'), path.join(cop, 'b')])
    assert commit(co, repo, '') == 0
    assert update(co2, repo) == 0
    rename(co, path.join(cop, 'c', 'a'), path.join(cop, 'b', 'a'))
    rename(co, path.join(cop, 'c'), path.join(cop, 'b', 'a', 'c'))
    assert commit(co, repo, '') == 0
    rename(co2, path.join(co2p, 'b'), path.join(co2p, 'c', 'a', 'b'))
    assert commit(co2, repo, '') == 1
    assert update(co2, repo) == 0
    assert path.exists(path.join(co2p, 'c.parentloop', 'a.parentloop', 'b'))
    assert path.exists(path.join(co2p, 'c.parentloop.info'))
    assert path.exists(path.join(co2p, 'c.parentloop', 'a.parentloop.info'))
    print 'TESTING rename incidental'
    reset_test(co, co2, repo)
    os.makedirs(path.join(cop, 'a'))
    add(co, [path.join(cop, 'a')])
    assert commit(co, repo, '') == 0
    assert update(co2, repo) == 0
    rename(co, path.join(cop, 'a'), path.join(cop, 'b'))
    assert commit(co, repo, '') == 0
    rename(co2, path.join(co2p, 'a'), path.join(co2p, 'b'))
    assert commit(co2, repo, '') == 1
    assert update(co2, repo) == 0
    assert commit(co2, repo, '') == 0
    rename(co, path.join(cop, 'b'), path.join(cop, 'c'))
    assert commit(co, repo, '') == 0
    assert update(co2, repo) == 0
    assert path.exists(path.join(co2p, 'c'))
    print 'TESTING rename dependent'
    reset_test(co, co2, repo)
    os.makedirs(path.join(cop, 'a', 'a', 'a'))
    set_file_contents(path.join(cop, 'a', 'a', 'a', 'a'), '')
    add(co, [path.join(cop, 'a', 'a', 'a', 'a')])
    assert commit(co, repo, '') == 0
    assert update(co2, repo) == 0
    rename(co, path.join(cop, 'a', 'a', 'a', 'a'), path.join(cop, 'a', 'a', 'a', 'b'))
    rename(co, path.join(cop, 'a', 'a', 'a'), path.join(cop, 'a', 'a', 'b'))
    rename(co, path.join(cop, 'a', 'a'), path.join(cop, 'a', 'b'))
    rename(co, path.join(cop, 'a'), path.join(cop, 'b'))
    assert commit(co, repo, '') == 0
    assert update(co2, repo) == 0
    assert path.exists(path.join(co2p, 'b', 'b', 'b', 'b'))
    print 'TESTING update overrides coincidental name merge'
    reset_test(co, co2, repo)
    set_file_contents(path.join(cop, 'a'), '')
    add(co, [path.join(cop, 'a')])
    assert commit(co, repo, '') == 0
    assert update(co2, repo) == 0
    rename(co, path.join(cop, 'a'), path.join(cop, 'b'))
    assert commit(co, repo, '') == 0
    rename(co2, path.join(co2p, 'a'), path.join(co2p, 'b'))
    assert commit(co2, repo2, '') == 0
    assert update(co, repo2) == 0
    assert update(co2, repo) == 0
    assert commit(co2, repo, '') == 0
    assert update(co, repo) == 0
    rename(co, path.join(cop, 'b'), path.join(cop, 'c'))
    assert commit(co, repo, '') == 0
    assert update(co2, repo) == 0
    assert path.exists(path.join(co2p, 'c'))
    print 'TESTING delete orphan'
    reset_test(co, co2, repo)
    os.makedirs(path.join(cop, 'a'))
    add(co, [path.join(cop, 'a')])
    assert commit(co, repo, '') == 0
    assert update(co2, repo) == 0
    set_file_contents(path.join(cop, 'a', 'a'), '')
    add(co, [path.join(cop, 'a', 'a')])
    assert commit(co, repo, '') == 0
    delete(co2, [path.join(co2p, 'a')])
    assert commit(co2, repo, '') == 1
    assert update(co2, repo) == 0
    assert path.exists(path.join(co2p, 'a.orphaned'))
    assert path.exists(path.join(co2p, 'a.orphaned.info'))
    print 'TESTING remote orphan'
    reset_test(co, co2, repo)
    os.makedirs(path.join(cop, 'a'))
    add(co, [path.join(cop, 'a')])
    assert commit(co, repo, '') == 0
    assert update(co2, repo) == 0
    delete(co2, [path.join(co2p, 'a')])
    assert commit(co2, repo, '') == 0
    set_file_contents(path.join(cop, 'a', 'a'), '')
    add(co, [path.join(cop, 'a', 'a')])
    assert commit(co, repo, '') == 1
    assert update(co, repo) == 0
    assert path.exists(path.join(cop, 'a.orphaned'))
    assert path.exists(path.join(cop, 'a.orphaned.info'))
    print 'TESTING delete and reuse name'
    reset_test(co, co2, repo)
    os.makedirs(path.join(cop, 'a', 'a'))
    os.makedirs(path.join(cop, 'b'))
    add(co, [path.join(cop, 'a', 'a'), path.join(cop, 'b')])
    assert commit(co, repo, '') == 0
    assert update(co2, repo) == 0
    delete(co, [path.join(cop, 'b')])
    rename(co, path.join(cop, 'a'), path.join(cop, 'b'))
    assert commit(co, repo, '') == 0
    assert update(co2, repo) == 0
    assert path.exists(path.join(co2p, 'b', 'a'))
    assert not path.exists(path.join(co2p, 'a'))
    print 'TESTING delete dependent'
    reset_test(co, co2, repo)
    os.makedirs(path.join(cop, 'a', 'a', 'a', 'a'))
    add(co, [path.join(cop, 'a', 'a', 'a', 'a')])
    assert commit(co, repo, '') == 0
    assert update(co2, repo) == 0
    delete(co, [path.join(cop, 'a', 'a', 'a', 'a')])
    delete(co, [path.join(cop, 'a', 'a', 'a')])
    delete(co, [path.join(cop, 'a', 'a')])
    delete(co, [path.join(cop, 'a')])
    assert commit(co, repo, '') == 0
    assert update(co2, repo) == 0
    assert not path.exists(path.join(co2p, 'a'))
    print 'TESTING delete within parent loop'
    reset_test(co, co2, repo)
    os.makedirs(path.join(cop, 'a'))
    os.makedirs(path.join(cop, 'b'))
    add(co, [path.join(cop, 'a'), path.join(cop, 'b')])
    assert commit(co, repo, '') == 0
    assert update(co2, repo) == 0
    rename(co, path.join(cop, 'a'), path.join(cop, 'b', 'a'))
    assert commit(co, None, '') == 0
    assert update(co, repo) == 0
    delete(co, [path.join(cop, 'b', 'a')])
    assert commit(co, repo, '') == 0
    rename(co2, path.join(co2p, 'b'), path.join(co2p, 'a', 'b'))
    assert commit(co2, repo, '') == 1
    assert update(co2, repo) == 0
    assert path.exists(path.join(co2p, 'b.orphaned'))
    assert path.exists(path.join(co2p, 'b.orphaned.info'))
    print 'TESTING delete entire parent loop'
    reset_test(co, co2, repo)
    os.makedirs(path.join(cop, 'a'))
    os.makedirs(path.join(cop, 'b'))
    add(co, [path.join(cop, 'a'), path.join(cop, 'b')])
    assert commit(co, repo, '') == 0
    assert update(co2, repo) == 0
    rename(co, path.join(cop, 'a'), path.join(cop, 'b', 'a'))
    assert commit(co, None, '') == 0
    delete(co, [path.join(cop, 'b', 'a')])
    assert commit(co, repo, '') == 0
    rename(co2, path.join(co2p, 'b'), path.join(co2p, 'a', 'b'))
    assert commit(co2, None, '') == 0
    delete(co2, [path.join(co2p, 'a', 'b')])
    assert commit(co2, repo, '') == 0
    assert update(co, repo) == 0
    assert update(co2, repo) == 0
    assert not path.exists(path.join(cop, 'a'))
    assert not path.exists(path.join(cop, 'b'))
    assert not path.exists(path.join(co2p, 'a'))
    assert not path.exists(path.join(co2p, 'b'))
    print 'TESTING remote add and delete'
    reset_test(co, co2, repo)
    set_file_contents(path.join(cop, 'a'), '')
    add(co, [path.join(cop, 'a')])
    assert commit(co, repo, '') == 0
    delete(co, [path.join(cop, 'a')])
    assert commit(co, repo, '') == 0
    assert update(co2, repo) == 0
    assert not path.exists(path.join(co2p, 'a'))
    print 'TESTING unique name mangling'
    reset_test(co, co2, repo)
    set_file_contents(path.join(cop, 'a'), '')
    add(co, [path.join(cop, 'a')])
    assert commit(co, repo, '') == 0
    assert update(co2, repo) == 0
    rename(co, path.join(cop, 'a'), path.join(cop, 'a1'))
    assert commit(co, repo, '') == 0
    set_file_contents(path.join(co2p, 'a2.nameconflict'), '')
    add(co2, [path.join(co2p, 'a2.nameconflict')])
    rename(co2, path.join(co2p, 'a'), path.join(co2p, 'a2'))
    assert update(co2, repo) == 0
    assert path.exists(path.join(co2p, 'a2.nameconflict'))
    assert path.exists(path.join(co2p, 'a2.nameconflict2'))
    assert path.exists(path.join(co2p, 'a2.nameconflict2.info'))
    # XXX: had to relax this restriction for now, fix next history rewrite
    #print 'TESTING deleted file modified remotely'
    #reset_test(co, co2, repo)
    #set_file_contents(path.join(cop, 'a'), '')
    #add(co, [path.join(cop, 'a')])
    #assert commit(co, repo, '') == 0
    #assert update(co2, repo) == 0
    #delete(co, [path.join(cop, 'a')])
    #assert commit(co, repo, '') == 0
    #set_file_contents(path.join(co2p, 'a'), 'foo')
    #edit(co2, [path.join(co2p, 'a')])
    #assert commit(co2, repo, '') == 1
    #assert update(co2, repo) == 0
    #assert commit(co2, repo, '') == 0
    #assert update(co, repo) == 0
    #assert not path.exists(path.join(cop, 'a'))
    print 'TESTING independent local and remote deletes'
    set_file_contents(path.join(cop, 'a'), '')
    add(co, [path.join(cop, 'a')])
    assert commit(co, repo, '') == 0
    assert update(co2, repo) == 0
    delete(co, [path.join(cop, 'a')])
    assert commit(co, repo, '') == 0
    delete(co2, [path.join(co2p, 'a')])
    assert commit(co2, repo, '') == 0
    assert update(co2, repo) == 0
    assert not path.exists(path.join(cop, 'a'))
    assert not path.exists(path.join(co2p, 'a'))
    print 'TESTING local filesystem conflicts with repository'
    reset_test(co, co2, repo)
    os.makedirs(path.join(cop, 'a'))
    add(co, [path.join(cop, 'a')])
    assert commit(co, repo, '') == 0
    set_file_contents(path.join(co2p, 'a'), '')
    assert update(co2, repo) == 1
    assert path.isfile(path.join(co2p, 'a'))
    print 'TESTING non-existent parents needed for update'
    reset_test(co, co2, repo)
    os.makedirs(path.join(cop, 'a'))
    os.makedirs(path.join(cop, 'b'))
    add(co, [path.join(cop, 'a',), path.join(cop, 'b',)])
    assert commit(co, repo, '') == 0
    assert update(co2, repo) == 0
    rename(co, path.join(cop, 'a'), path.join(cop, 'b', 'a'))
    assert commit(co, repo, '') == 0
    os.rmdir(path.join(co2p, 'a'))
    assert update(co2, repo) == 1
    os.rmdir(path.join(co2p, 'b'))
    assert update(co2, repo) == 1
    set_file_contents(path.join(co2p, 'b'), '')
    assert update(co2, repo) == 1
    assert not path.exists(path.join(co2p, 'a'))
    assert path.isfile(path.join(co2p, 'b'))
