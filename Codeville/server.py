# Written by Ross Cohen
# see LICENSE.txt for license information

from bencode import bdecode, bencode
import binascii
from db import db, ChangeDBs, write_format_version, write_rebuild_version
from crypt import crypt
from entropy import random_string, string_to_long, long_to_string
from history import sync_history, is_ancestor
from history import roothandle, rootnode
from history import read_diff, WriteDiff, write_changeset
from history import handles_in_branch, handle_contents_at_point, handle_merge_check
from history import handle_name_at_point, handle_last_modified, HistoryError
from history import clean_merge_point, dump_changeinfo
import hmac
from network import Request, Response, NetworkHandler
import os
from os import makedirs, path
from passwd import Passwd
from random import randrange
from RawServer import RawServer
import re
import sha
import SRP
from sys import version_info
from threading import Event
from time import time, sleep
from traceback import print_exc, print_stack

assert version_info >= (2,2), "Python 2.2 or higher is required"

User = 2
Queue = 3
Flushed = 4

class ServerError(Exception): pass

class ServerRepository:
    def _db_init(self, local, metadata_dir='.cdv', rw=True, init=False):
        self.conf_path = path.join(local, metadata_dir)
        flags = 0
        if init:
            os.makedirs(self.conf_path)
            flags = db.DB_CREATE

        self.txns = {}

        cwd        = os.getcwd()
        self.dbenv = None
        ltxn       = None
        if rw == True:
            self.dbenv = db.DBEnv()
            self.dbenv.set_cachesize(0, 8 * 1024 * 1024)
            self.dbenv.set_lg_bsize(1024 * 1024)
            self.dbenv.set_get_returns_none(2)
            self.dbenv.open(self.conf_path, db.DB_CREATE|db.DB_INIT_MPOOL|db.DB_INIT_TXN|db.DB_PRIVATE|db.DB_RECOVER)

            ltxn = self.txn_begin()

        else:
            os.chdir(self.conf_path)
            flags = db.DB_RDONLY

        self.lcrepo = db.DB(dbEnv=self.dbenv)
        self.lcrepo.open('changesets.db', dbtype=db.DB_BTREE, flags=flags, txn=ltxn)
        self.changesdb = db.DB(dbEnv=self.dbenv)
        self.changesdb.open('changenums.db', dbtype=db.DB_BTREE, flags=flags, txn=ltxn)
        self.branchmapdb = db.DB(dbEnv=self.dbenv)
        self.branchmapdb.open('branchmap.db', dbtype=db.DB_BTREE, flags=flags, txn=ltxn)
        self.branchdb = db.DB(dbEnv=self.dbenv)
        self.branchdb.open('branch.db', dbtype=db.DB_RECNO, flags=flags, txn=ltxn)
        self.staticdb = db.DB(dbEnv=self.dbenv)
        self.staticdb.open('static.db', dbtype=db.DB_BTREE, flags=flags, txn=ltxn)
        self.linforepo = db.DB(dbEnv=self.dbenv)
        self.linforepo.open('info.db', dbtype=db.DB_BTREE, flags=flags, txn=ltxn)
        self.repolistdb = db.DB(dbEnv=self.dbenv)
        self.repolistdb.open('repolist.db', dbtype=db.DB_BTREE, flags=flags, txn=ltxn)

        # open the mini-dags and their indices
        self.contents = ChangeDBs(self.dbenv, 'content', flags, ltxn)
        self.names    = ChangeDBs(self.dbenv, 'name', flags, ltxn)

        self.allnamesdb = db.DB(dbEnv=self.dbenv)
        self.allnamesdb.set_flags(db.DB_DUPSORT)
        self.allnamesdb.open('allnames.db', dbtype=db.DB_BTREE, flags=flags, txn=ltxn)

        self.name_cache = {}
        self.db_cache = {}
        self.cpath = path.join(self.conf_path, 'contents')

        # populate the repository
        if init:
            root = bencode({'precursors': [], 'handles': {roothandle: {'add': {'type': 'dir'}, 'name': ''}}})
            self.lcrepo.put(rootnode, root, txn=ltxn)
            self.linforepo.put('branchmax', bencode(0), txn=ltxn)
            try:
                makedirs(self.cpath)
            except OSError:
                pass
            write_format_version(self.conf_path)
            write_rebuild_version(self.conf_path)

        if rw == True:
            self.txn_commit(ltxn)

        else:
            os.chdir(cwd)
        return

    def close(self):
        try:
            for txn in self.txns.keys():
                self.txn_abort(txn)
        except AttributeError:
            return

        self.lcrepo.close()
        self.changesdb.close()
        self.branchmapdb.close()
        self.branchdb.close()
        self.staticdb.close()
        self.linforepo.close()
        self.repolistdb.close()
        self.contents.close()
        self.names.close()
        self.allnamesdb.close()

        if self.dbenv is not None:
            self.dbenv.txn_checkpoint()
            for lfile in self.dbenv.log_archive():
                os.remove(path.join(self.dbenv.db_home, lfile))
            self.dbenv.close()

        return

    def txn_begin(self):
        txn = self.dbenv.txn_begin()
        self.txns[txn] = 1
        return txn

    def txn_commit(self, txn):
        txn.commit()
        del self.txns[txn]

    def txn_abort(self, txn):
        txn.abort()
        del self.txns[txn]


class ServerHandler(ServerRepository):
    def __init__(self, config):
        self.socket = {}
        self.rs = RawServer(Event(), 100, 1000)
        self.nh = NetworkHandler(self)
        self.shutdown = Event()
        self.config = config

        self.post_commit = []
        for pattern, action in self.config.items('post-commit'):
            try:
                self.post_commit.append((re.compile(pattern, re.I), action))
            except re.error, msg:
                raise ServerError, 'Bad post-commit pattern \"%s\": %s' % \
                      (pattern, msg)

        pw_file = path.join(config.get('control', 'datadir'), 'passwd')
        self.passwd = Passwd(pw_file)

    def db_init(self, init=False):
        local = self.config.get('control', 'datadir')
        self._db_init(local, init=init)

        self.file_locks = {}
        return

    #def close(self):
    #    # XXX: should send a shutdown response
    #    #for s in self.socket.keys():
    #    #    self._close(s)

    def external_connection_made(self, s):
        self.socket[s] = {'state': 0}

    def _srp_auth(self, s, msg):
        socket = self.socket[s]
        pw = socket['pw']
        srp = socket['srp'] = {}
        srp['B'], srp['u'], srp['K'], srp['m'] = SRP.host_begin(msg['user'], msg['A'], pw['s'], pw['v'])
        srp['A'] = msg['A']
        self._send_msg(s, {'s': pw['s'], 'B': srp['B'], 'u': srp['u']})
        socket['state'] = 1

    def _secret_auth(self, s):
        socket = self.socket[s]
        socket['salt'] = random_string(20)
        self._send_msg(s, {'salt': socket['salt']})
        socket['state'] = 4

    def message_came_in(self, s, data):
        socket = self.socket[s]
        try:
            msg = bdecode(data)
        except ValueError:
            self._send_error(s, None, 'garbage data')
            self._close(s)
            return
        if socket['state'] == 0:
            try:
                pw = socket['pw'] = self.passwd.get(msg['user'])
            except KeyError:
                self._send_error(s, None, 'Bad user')
                self._close(s)
                return
            socket['user'] = msg['user']
            if msg['op'] == 'get hash':
                self._send_msg(s, {'hash': sha.new('public hash check' + pw['secret']).digest()})
                socket['state'] = 3
            elif msg['op'] == 'secret auth':
                self._secret_auth(s)
            elif msg['op'] == 'srp auth':
                self._srp_auth(s, msg)
            else:
                self._close(s)
        elif socket['state'] == 1:
            srp = socket['srp']
            if srp['m'].digest() != msg['m']:
                self._send_error(s, None, 'Bad password')
                socket['state'] = 3
                return
            auth = SRP.host_authenticator(srp['K'], srp['A'], srp['m'].digest())
            self._send_msg(s, {'auth': auth.digest()})
            self.nh.set_hmac(s, srp['m'], auth)
            socket['state'] = 2
        elif socket['state'] == 2:
            srp = socket['srp']
            if msg['op'] == 'get secret':
                secret = socket['pw']['secret']
                esecret = crypt(secret, srp['K'])[0]
                self._send_msg(s, {'secret': esecret})
                socket['state'] = 3
            elif msg['op'] == 'set password':
                if socket['user'] == 'anonymous':
                    self._send_error(s, None, 'operation not permitted')
                    self._close(s)
                    return
                v = string_to_long(crypt(msg['v'], srp['K'])[0])
                self.passwd.define(socket['user'], v, msg['s'])
                self._send_msg(s, {'ok': 1})
                self._close(s)
        elif socket['state'] == 3:
            if msg['op'] == 'secret auth':
                self._secret_auth(s)
            elif msg['op'] == 'srp auth':
                self._srp_auth(s, msg)
            else:
                self._close(s)
        elif socket['state'] == 4:
            pw = socket['pw']

            if len(msg['salt']) < 20:
                self._send_error(s, None, 'Bad salt length')
                self._close(s)
                return

            if msg['salt'] + socket['salt'] == socket['salt'] + msg['salt']:
                self._send_error(s, None, 'Bad salt')
                self._close(s)
                return

            base = 'session key' + pw['secret'] + socket['salt'] + msg['salt']
            key = sha.new(base).digest()
            socket['m_in'] = hmac.new(key, '', sha)
            base = 'session key' + pw['secret'] + msg['salt'] + socket['salt']
            key = sha.new(base).digest()
            socket['m_out'] = hmac.new(key, '', sha)

            if msg['auth'] != socket['m_out'].digest():
                self._send_error(s, None, 'Bad password')
                socket['state'] = 3
                return

            self._send_msg(s, {'auth': socket['m_in'].digest()})
            self.nh.set_hmac(s, socket['m_in'], socket['m_out'])
            self._req_mode(s, 1)
            self.socket[s] = [{}, {}, socket['user'], [], 1]
        else:
            self._close(s)

    def connection_flushed(self, s):
        queue = self.socket[s][Queue]
        socket = self.socket[s]
        socket[Flushed] = 1
        while len(queue) and socket[Flushed] == 1:
            mid, msg = queue.pop(0)
            self._send_diff(s, mid, msg)

    def connection_lost(self, s, msg):
        if self.nh.get_req_mode(s):
            self._socket_cleanup(s)
        del self.socket[s]

    def bind(self, port):
        self.rs.bind(port, reuse=1)

    def listen_forever(self):
        self.rs.listen_forever(self.nh)
        self.shutdown.set()

    def request_came_in(self, s, mid, data):
        try:
            msg = bdecode(data)
        except ValueError:
            self._send_error(s, mid, 'garbage request')
            return
        try:
            self.request_handlers[msg['request']](self, s, mid, msg)
        except:
            print_exc()
            self._close(s)
        return

    def response_came_in(self, s, mid, data):
        try:
            msg = bdecode(data)
        except ValueError:
            self._close(s)
            return
        try:
            rstate = self.socket[s][Request][mid]
        except KeyError:
            self._close(s)
            return
        try:
            self.response_handlers[rstate['request']](self, s, mid, msg, rstate)
        except:
            print_exc()
            self._close(s)
        return

    # request handlers
    def _request_get_head(self, s, mid, msg):
        if not self.repolistdb.has_key(msg['repository']):
            self._send_error(s, mid, 'repository "' + msg['repository'] + '" does not exist')
            return
        resp = {'head': self.repolistdb.get(msg['repository'])}
        self._send_response(s, mid, resp)

    def _request_get_change(self, s, mid, msg):
        resp = {'changeset': self.lcrepo.get(msg['changenum'])}
        self._send_response(s, mid, resp)

    def _send_diff(self, s, mid, msg):
        diff = read_diff(self, msg['handle'], msg['changenum'], None)
        if diff is not None:
            self.socket[s][Flushed] = self._send_response(s, mid, {'diff': diff})
        else:
            self._send_error(s, mid, 'No such diff', close=False)
        return

    def _request_get_diff(self, s, mid, msg):
        if self.socket[s][Flushed] == 1:
            self._send_diff(s, mid, msg)
        else:
            self.socket[s][Queue].append((mid, msg))

    def _request_commit(self, s, mid, msg):
        socket = self.socket[s]
        if socket[User] == 'anonymous':
            self._send_error(s, mid, 'operation not permitted')
            return
        socket[Response][mid] = {'request': 'commit',
                                 'repository': msg['repository'],
                                 'head': msg['changenum'],
                                 'count': 1, 'counts': {},
                                 'changes': {}, 'requests': {}, 'diffs': {},
                                 'reqq': [], 'req-outstanding': 0}

        if not self.repolistdb.has_key(msg['repository']):
            self._send_error(s, mid, 'repository "' + msg['repository'] + '" does not exist')
            return

        lstate = socket[Response][mid]
        lstate['txn'] = self.txn_begin()
        lstate['cur head'] = self.repolistdb.get(msg['repository'])

        if self.lcrepo.has_key(msg['changenum']):
            try:
                self._commit_phase_1(s, mid)
                self._commit_phase_2(s, mid)
                return
            except HistoryError, msg:
                self._commit_fail(s, mid, str(msg))
                return

        rid = self._get_change(s, socket[Response][mid], msg['changenum'])
        socket[Request][rid] = {'request': 'get change',
                                'changenum': msg['changenum'],
                                'ref': mid}

    def _request_create_repository(self, s, mid, msg):
        if self.socket[s][User] == 'anonymous':
            self._send_error(s, mid, 'operation not permitted')
            return
        self._create_repo(s, mid, msg['repository'])

    def _request_destroy_repository(self, s, mid, msg):
        if self.socket[s][User] == 'anonymous':
            self._send_error(s, mid, 'operation not permitted')
            return
        self._remove_repo(s, mid, msg['repository'])

    def _request_list_repositories(self, s, mid, msg):
        self._send_response(s, mid, {'list': self.repolistdb.keys()})

    request_handlers = {'get head':           _request_get_head,
                        'get change':         _request_get_change,
                        'get diff':           _request_get_diff,
                        'commit':             _request_commit,
                        'create repository':  _request_create_repository,
                        'destroy repository': _request_destroy_repository,
                        'list repositories':  _request_list_repositories}

    # response handlers
    def _response_get_change(self, s, mid, msg, rstate):
        lstate = self.socket[s][Response][rstate['ref']]
        changeset = msg['changeset']
        del msg['changeset']
        if sha.new(changeset).digest() != rstate['changenum']:
            self._close(s)
            return
        write_changeset(self, rstate['changenum'], changeset, lstate['txn'])
        changeset = bdecode(changeset)
        lstate['changes'][rstate['changenum']] = changeset
        for change in changeset['precursors']:
            if self.lcrepo.has_key(change):
                continue
            if lstate['changes'].has_key(change):
                continue
            if lstate['requests'].has_key(change):
                continue
            rid = self._get_change(s, lstate, change)
            self.socket[s][Request][rid] = {'request': 'get change',
                                            'changenum': change,
                                            'ref': rstate['ref']}
            lstate['count'] += 1
        lstate['count'] -= 1
        del self.socket[s][Request][mid]

        # record all the diffs we'll need to request
        diffs = lstate['diffs']
        for handle, hinfo in changeset['handles'].items():
            if not hinfo.has_key('hash'):
                continue
            if not diffs.has_key(handle):
                diffs[handle] = {}
                lstate['counts'][handle] = 0
            diffs[handle][rstate['changenum']] = 1
            lstate['counts'][handle] += 1
        changeset = None

        try:
            if lstate['count'] == 0:
                self._commit_phase_1(s, rstate['ref'])

            if lstate['count'] == 0:
                self._commit_phase_2(s, rstate['ref'])
        except HistoryError, msg:
            self._commit_fail(s, rstate['ref'], str(msg))
            return

    def _response_get_diff(self, s, mid, msg, rstate):
        lstate = self.socket[s][Response][rstate['ref']]

        # send out the next one
        lstate['req-outstanding'] -= 1
        self._get_diff(s, rstate['ref'])

        handle = rstate['handle']

        diffs = lstate['diffs']
        diffs[handle][rstate['change']] = msg['diff']
        del self.socket[s][Request][mid]

        lstate['counts'][handle] -= 1
        if lstate['counts'][handle] == 0:
            lstate['count'] -= 1

            # write out the diffs
            WD = WriteDiff(self, handle, lstate['txn'])
            for change, diff in diffs[handle].items():
                WD.write(diff, change)
            WD.close()

            # XXX: suboptimal
            change = handle_last_modified(self, self.contents, handle, lstate['cur head'], lstate['txn'])
            if change is None or is_ancestor(self, change, lstate['head'], lstate['txn']):
                handle_contents_at_point(self, handle, lstate['head'], lstate['txn'], dcache=diffs[handle])
                assert lstate['modified'].has_key(handle)
                lstate['modified'][handle] = 1

            del diffs[handle]

        try:
            if lstate['count'] == 0:
                self._commit_phase_2(s, rstate['ref'])
        except HistoryError, msg:
            self._commit_fail(s, rstate['ref'], str(msg))
            return

    response_handlers = {'get change':   _response_get_change,
                         'get diff':     _response_get_diff}

    # helpers
    def _req_mode(self, s, mode):
        self.nh.req_mode(s, mode)

    def _create_repo(self, s, mid, repo):
        if self.repolistdb.has_key(repo):
            self._send_error(s, mid, 'repository "' + repo + '" already exists')
            return
        txn = self.txn_begin()
        self.repolistdb.put(repo, rootnode, txn=txn)
        self.txn_commit(txn)
        self._send_response(s, mid, {})

    def _remove_repo(self, s, mid, repo):
        if not self.repolistdb.has_key(repo):
            self._send_error(s, mid, 'repository "' + repo + '" does not exist')
            return
        txn = self.txn_begin()
        self.repolistdb.delete(repo, txn)
        self.txn_commit(txn)
        self._send_response(s, mid, {})

    def _commit_phase_1(self, s, mid):
        request = self.socket[s][Response][mid]
        txn = request['txn']
        head = request['cur head']

        # if this change is already committed then we have nothing to do
        if self.branchmapdb.has_key(request['head']) and is_ancestor(self, request['head'], head, None):
            request['no phase 2'] = 1
            return

        sync_history(self, request['head'], txn, cache=request['changes'])

        if self.config.getboolean('control', 'backup'):
            if not is_ancestor(self, request['cur head'], request['head'], txn):
                raise HistoryError, 'not an incremental backup'

            point = request['head']
            while point != rootnode:
                if point == request['cur head']:
                    break
                pinfo = bdecode(self.lcrepo.get(point, txn=txn))
                if not clean_merge_point(pinfo):
                    raise HistoryError, 'not an incremental backup'
                point = pinfo['precursors'][0]

        modified = handles_in_branch(self, [head], [request['head']], txn)[1]
        unlocked = self._lock_files(s, mid, modified)

        # bump the reference count by the locks we don't have
        request['count'] += len(unlocked)

        # mark all the diff requests which have to wait until we get the lock
        request['diff queue'] = {}
        for handle in unlocked:
            if request['diffs'].has_key(handle):
                request['diff queue'][handle] = 1

        # request all the related file diffs
        for handle, changes in request['diffs'].items():
            if request['diff queue'].has_key(handle):
                request['diff queue'][handle] = changes.keys()
                continue
            requested = 0
            for change in changes.keys():
                requested = 1
                self._queue_diff(s, change, handle, mid)
            request['count'] += requested
        self._get_diff(s, mid)

        # denote the merge checks we have to do later
        rmodified = request['modified'] = {}
        for handle in modified:
            rmodified[handle] = 0

    def _commit_phase_2(self, s, mid):
        request = self.socket[s][Response][mid]
        txn = request['txn']
        head = request['cur head']

        if request.has_key('no phase 2'):
            self.txn_commit(txn)
            del self.socket[s][Response][mid]
            self._send_response(s, mid, {})
            return

        # backup servers don't create clean merge heads
        req_head = request['head']
        if not self.config.getboolean('control', 'backup'):
            # create new clean merge head
            changeset = bencode({'precursors': [head, req_head],
                                 'user': self.socket[s][User],
                                 'time': int(time()),
                                 'handles': {}})
            new_head = request['new head'] = sha.new(changeset).digest()
            write_changeset(self, new_head, changeset, txn)
        else:
            new_head = request['head']

        self.repolistdb.put(request['repository'], new_head, txn=txn)
        sync_history(self, new_head, txn, cache=request['changes'])
        del request['changes']

        # validate all the files for which we already have the diffs
        locks = []
        for handle, checked in request['modified'].items():
            locks.append(handle)

            # did we already validate it?
            if checked:
                continue

            # if there are diffs then some other checkin verified them
            # we only need to make sure there aren't any implicit merges
            handle_merge_check(self, handle, new_head, txn)

        # complete everything and clean up
        self.txn_commit(txn)
        del self.socket[s][Response][mid]
        self._send_response(s, mid, {})

        for handle in locks:
            self._unlock_file(s, handle)

        for pattern, action in self.post_commit:
            if not pattern.search(request['repository']):
                continue

            try:
                afd = os.popen(action, 'w')
                afd.write(dump_changeinfo(self, new_head, repo=request['repository']))
                afd.close()
            except IOError, msg:
                print 'Command failed'
                print 'Command: ' + action
                print 'Error: ' + str(msg)

    def _commit_fail(self, s, mid, msg):
        lstate = self.socket[s][Response][mid]
        self.txn_abort(lstate['txn'])
        lstate['txn'] = None
        self._send_error(s, mid, msg)

    def _lock_files(self, s, mid, modified):
        retval = []
        lock = (s, mid)
        for handle in modified:
            self.file_locks.setdefault(handle, []).append(lock)
            if len(self.file_locks[handle]) > 1:
                retval.append(handle)

        return retval

    def _force_unlock_files(self, s, mid, handles):
        lock = (s, mid)
        for handle in handles:
            index = self.file_locks[handle].index(lock)
            self.file_locks[handle].pop(index)
            if index == 0:
                self._unlock_wakeup(handle)
        return

    def _unlock_file(self, s, handle):
        lock = self.file_locks[handle].pop(0)
        assert lock[0] == s

        self._unlock_wakeup(handle)
        return

    def _unlock_wakeup(self, handle):
        if len(self.file_locks[handle]) == 0:
            return
        # make the requests on behalf of the next connection
        lock = self.file_locks[handle][0]
        rstate = self.socket[lock[0]][Request][lock[1]]
        rstate['count'] -= 1
        if rstate['count'] == 0:
            # if it's a clean merge, go ahead and commit
            if rstate['diff queue'] == {}:
                try:
                    self._commit_phase_2(lock[0], lock[1])
                except HistoryError, msg:
                    self._commit_fail(lock[0], lock[1], str(msg))
                return

            # need the diffs, request them all
            for handle, changes in rstate['diff queue']:
                for change in changes:
                    self._queue_diff(lock[0], change, handle, lock[1])
            self._get_diff(lock[0], lock[1])
            del rstate['diff queue']
        return

    def _get_change(self, s, lstate, change):
        req = {'request': 'get change', 'changenum': change}
        lstate['requests'][change] = 1
        return self._send_request(s, req)

    def _queue_diff(self, s, change, handle, mid):
        rstate = self.socket[s][Response][mid]
        rstate['reqq'].append((change, handle))

    def _get_diff(self, s, mid):
        rstate = self.socket[s][Response][mid]
        while len(rstate['reqq']) and rstate['req-outstanding'] <= 20:
            change, handle = rstate['reqq'].pop(0)
            req = {'request': 'get diff', 'changenum': change,
                   'handle': handle}
            state = {'request': 'get diff', 'ref': mid, 'change': change,
                     'handle': handle}
            rid = self._send_request(s, req)
            self.socket[s][Request][rid] = state
            rstate['req-outstanding'] += 1

    def _send_msg(self, s, msg):
        return self.nh.send_msg(s, bencode(msg))

    def _send_error(self, s, mid, msg, close=True):
        retval = None
        if mid is None:
            retval = self._send_msg(s, {'error': msg})
        else:
            retval = self._send_response(s, mid, {'error': msg})
        if close:
            self._close(s)
        return retval

    def _send_request(self, s, data):
        return self.nh.send_request(s, bencode(data))

    def _send_response(self, s, mid, data):
        return self.nh.send_response(s, mid, bencode(data))

    def _socket_cleanup(self, s):
        for mid, response in self.socket[s][Response].items():
            if response['request'] != 'commit':
                continue
            if response.has_key('modified'):
                self._force_unlock_files(s, mid, response['modified'].keys())
            if response.has_key('txn') and response['txn'] is not None:
                self.txn_abort(response['txn'])

    def _close(self, s):
        if self.nh.get_req_mode(s):
            self._socket_cleanup(s)
        self.nh.close(s)
        del self.socket[s]
        #print 'closing socket:'
        #print_stack()
