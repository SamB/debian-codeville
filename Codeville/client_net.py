# Written by Ross Cohen
# see LICENSE.txt for license information

from auth import Auth, AuthError
from bencode import bdecode, bencode
import binascii
from client_helpers import handle_name, handle_to_filename, conflicts_in_file
from client_helpers import _handle_to_filename, set_edit
from crypt import crypt
from entropy import random_string, string_to_long, long_to_string
from history import read_diff, sync_history, is_ancestor
from history import WriteDiff, write_changeset
from history import dmerge, handles_in_branch, simplify_precursors
from history import fullpath_at_point, handle_contents_at_point
from history import tuple_to_server
import hmac
from merge import find_conflict_multiple_safe, find_conflict, find_resolution
from network import Request, Response, NetworkHandler, NetworkError
from os import path
from RawServer import RawServer
import sha
import SRP
from threading import Event
import zlib

UpdateInfo = 2
Queue = 3
Flushed = 4

class ClientError(Exception):
    pass

class ServerError(Exception):
    pass

class ClientHandler:
    def __init__(self, co):
        self.co = co
        self.socket = {}
        self.rs = RawServer(Event(), 100, 1000)
        self.nh = NetworkHandler(self)

    # Functions called from lower level code
    def message_came_in(self, s, data):
        try:
            msg = bdecode(data)
        except ValueError:
            self.close(s)
            raise NetworkError, 'garbage data'
        if msg.has_key('error'):
            raise ServerError, msg['error']
        socket = self.socket[s]
        srp = socket['srp']
        if socket['state'] == 1:
            K, m = self.auth.client_key(msg['s'], msg['B'], msg['u'],
                                        srp['keys'])
            socket['key'], socket['m_out'] = K, m
            self._send_msg(s, {'m': socket['m_out'].digest()})
            socket['state'] = 2
        elif socket['state'] == 2:
            socket['m_in'] = SRP.host_authenticator(socket['key'], srp['keys'][0], socket['m_out'].digest())
            if socket['m_in'].digest() != msg['auth']:
                raise ServerError, 'Bad host authentication'
                return
            self.nh.set_hmac(s, socket['m_in'], socket['m_out'])
            self.rs.doneflag.set()
        elif socket['state'] == 3:
            self.socket[s]['hash'] = msg['hash']
            self.rs.doneflag.set()
        elif socket['state'] == 4:
            self.close(s)
            secret = crypt(msg['secret'], socket['key'])[0]
            self.auth.save_secret(secret)
            self.rs.doneflag.set()
        elif socket['state'] == 5:
            self.close(s)
            self.rs.doneflag.set()
        elif socket['state'] == 6:
            if len(msg['salt']) < 20:
                self._send_error(s, None, 'Bad salt length')
                self.close(s)
                raise NetworkError, 'Bad salt from server'

            salt = random_string(20)

            key = self.auth.session_key(salt, msg['salt'])
            socket['m_in'] = hmac.new(key, '', sha)
            key = self.auth.session_key(msg['salt'], salt)
            socket['m_out'] = hmac.new(key, '', sha)

            self._send_msg(s, {'auth': socket['m_in'].digest(),
                               'salt': salt})
            socket['state'] = 7
        elif socket['state'] == 7:
            if msg['auth'] != socket['m_out'].digest():
                self._send_error(s, None, 'Bad auth')
                self.close(s)
                raise NetworkError, 'Bad server auth'
            self._req_mode(s, 1)
            self.nh.set_hmac(s, socket['m_in'], socket['m_out'])
            self.socket[s] = [{}, {}, {}, [], 1]
            self.rs.doneflag.set()
        else:
            self.close(s)

    def connection_flushed(self, s):
        queue = self.socket[s][Queue]
        socket = self.socket[s]
        socket[Flushed] = 1
        while len(queue) and socket[Flushed] == 1:
            mid, msg = queue.pop(0)
            diff = read_diff(self.co, msg['handle'], msg['changenum'], None)
            socket[Flushed] = self._send_response(s, mid, {'diff': diff})

    def connection_lost(self, s, msg):
        del self.socket[s]
        raise NetworkError, msg

    def request_came_in(self, s, mid, data):
        try:
            msg = bdecode(data)
        except ValueError:
            self.close(s)
            raise NetworkError, 'garbage request'
        self.request_handlers[msg['request']](self, s, mid, msg)

    def response_came_in(self, s, mid, data):
        try:
            msg = bdecode(data)
        except ValueError:
            self.close(s)
            raise NetworkError, 'bad data from server'

        rstate = self.socket[s][Request][mid]
        if msg.has_key('error'):
            # XXX: grody hack, diffs which don't belong in history
            if rstate['request'] == 'get diff':
                msg['diff'] = zlib.compress(bencode(''))
            else:
                raise ServerError, msg['error']
        if self.response_handlers[rstate['request']](self, s, mid, msg, rstate):
            del self.socket[s][Request][mid]

    # request handlers
    def _request_get_change(self, s, mid, msg):
        resp = {'changeset': self.co.lcrepo.get(msg['changenum'], txn=self.txn)}
        self._send_response(s, mid, resp)

    def _request_get_diff(self, s, mid, msg):
        if self.socket[s][Flushed] == 1:
            diff = read_diff(self.co, msg['handle'], msg['changenum'], self.txn)
            self.socket[s][Flushed] = self._send_response(s, mid, {'diff': diff})
        else:
            self.socket[s][Queue].append((mid, msg))

    request_handlers = {'get change':  _request_get_change,
                        'get diff':    _request_get_diff}

    # response handlers
    def _response_get_head(self, s, mid, msg, rstate):
        return self._merge_change(s, mid, msg['head'])

    def _merge_change(self, s, mid, head):
        lstate = self.socket[s][Request][mid]
        lstate['head'] = head

        self.socket[s][UpdateInfo]['head'] = head

        if self.co.lcrepo.has_key(head):
            named, modified, added, deleted = \
                   handles_in_branch(self.co, lstate['heads'], [head], None)
            self._update_checks(s, mid, named, modified)
            self._update_handle_list(s, lstate, named, modified, added, deleted)
            self._update_finish(s, lstate)
            self.socket[s][UpdateInfo]['head'] = head
            self.rs.doneflag.set()
            return 1

        rid = self._get_change(s, head)
        self.socket[s][Request][rid] = {'request': 'get change',
                                        'changenum': head,
                                        'ref': mid}
        lstate['requests'][head] = 1
        lstate['count'] = 1
        return 0

    def _response_get_change(self, s, mid, msg, rstate):
        lstate = self.socket[s][Request][rstate['ref']]
        if sha.new(msg['changeset']).digest() != rstate['changenum']:
            raise ServerError, 'bad changeset'

        # write it out, decode and eject from memory
        write_changeset(self.co, rstate['changenum'], msg['changeset'],
                        lstate['txn'])
        changeset = bdecode(msg['changeset'])
        lstate['changes'][rstate['changenum']] = changeset
        del msg['changeset']

        # get any precursors we don't have and haven't yet requested
        for change in changeset['precursors']:
            if self.co.lcrepo.has_key(change):
                continue
            if lstate['changes'].has_key(change):
                continue
            if lstate['requests'].has_key(change):
                continue
            rid = self._get_change(s, change)
            self.socket[s][Request][rid] = {'request': 'get change',
                                            'changenum': change,
                                            'ref': rstate['ref']}
            lstate['requests'][change] = 1
            lstate['count'] += 1

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

        # clean up state
        del lstate['requests'][rstate['changenum']]
        lstate['count'] -= 1
        if lstate['count'] == 0:
            sync_history(self.co, lstate['head'], lstate['txn'],
                                    cache=lstate['changes'])
            named, modified, added, deleted = \
                   handles_in_branch(self.co,
                                     lstate['heads'], [lstate['head']],
                                     lstate['txn'], cache=lstate['changes'])
            del lstate['changes']
            self._update_checks(s, rstate['ref'], named, modified)
            self._update_handle_list(s, lstate, named, modified, added, deleted)

            handle_list = lstate['handle list']
            # get all the related file diffs
            for i in xrange(len(handle_list)-1, -1, -1):
                handle = handle_list[i][1]
                if not diffs.has_key(handle):
                    continue
                changes = diffs[handle]

                requested = 0
                for change in changes.keys():
                    requested = 1
                    self._queue_diff(s, change, handle, rstate['ref'])
                lstate['count'] += requested
            self._get_diff(s, rstate['ref'])

        if lstate['count'] == 0:
            self._update_finish(s, lstate)
            del self.socket[s][Request][rstate['ref']]
            self.rs.doneflag.set()
        return 1

    def _response_get_diff(self, s, mid, msg, rstate):
        lstate = self.socket[s][Request][rstate['ref']]

        # send out the next one
        lstate['req-outstanding'] -= 1
        self._get_diff(s, rstate['ref'])

        diffs = lstate['diffs']
        diffs[rstate['handle']][rstate['change']] = msg['diff']
        lstate['counts'][rstate['handle']] -= 1
        if lstate['counts'][rstate['handle']] == 0:
            lstate['count'] -= 1

            # XXX: do better ordering
            WD = WriteDiff(self.co, rstate['handle'], lstate['txn'])
            for change, diff in diffs[rstate['handle']].items():
                WD.write(diff, change)
            WD.close()

            if lstate['modified'].has_key(rstate['handle']):
                updates(self.co, self.socket[s][UpdateInfo], lstate,
                        rstate['handle'])
            #del diffs[rstate['handle']]

        if lstate['count'] == 0:
            self._update_finish(s, lstate)
            self.socket[s][UpdateInfo]['head'] = lstate['head']
            del self.socket[s][Request][rstate['ref']]
            self.rs.doneflag.set()
        return 1

    def _response_return(self, s, mid, msg, rstate):
        self.rs.doneflag.set()
        return 1

    def _response_list_repositories(self, s, mid, msg, rstate):
        self.socket[s][UpdateInfo] = msg['list']
        self.rs.doneflag.set()
        return 1

    response_handlers = {'get head':            _response_get_head,
                         'get change':          _response_get_change,
                         'get diff':            _response_get_diff,
                         'commit':              _response_return,
                         'create repository':   _response_return,
                         'destroy repository':  _response_return,
                         'list repositories':   _response_list_repositories}

    # Internal helper functions
    def _update_checks(self, s, mid, named, modified):
        lstate = self.socket[s][Request][mid]
        check_modified(self.co, modified)
        lstate['modified'] = {}
        for handle in modified:
            lstate['modified'][handle] = 1
        self.socket[s][UpdateInfo]['modified'] = []
        self.socket[s][UpdateInfo]['modified conflicts'] = []

    def _update_handle_list(self, s, lstate, named, modified, added, deleted):
        uinfo = self.socket[s][UpdateInfo]
        func = lstate['update function']

        handles, nconflicts = func(self.co, uinfo,
                                   named, modified, added, deleted,
                                   lstate['txn'])

        handle_list = lstate['handle list']
        for handle, show in handles:
            letters = (' ', ' ')
            if uinfo['newfiles'].has_key(handle):
                letters = ('A', 'A')
            elif uinfo['deletes'].has_key(handle):
                letters = ('D', 'D')
            elif uinfo['names'].has_key(handle):
                letters = (' ', 'N')
            if nconflicts.has_key(handle):
                letters = (letters[0], 'C')
            handle_list.append((handle_to_filename(self.co, handle, lstate['txn']), handle, show, letters))
        handle_list.sort()
        handle_list.reverse()

        uinfo['name conflicts'] = nconflicts.keys()

    def _update_finish(self, s, lstate):
        updates(self.co, self.socket[s][UpdateInfo], lstate, None)

    def _req_mode(self, s, mode):
        self.nh.req_mode(s, mode)

    def _commit(self, s, repo, head):
        rid = self._send_request(s, {'request': 'commit',
                                     'repository': repo,
                                     'changenum': head})
        self.socket[s][Request][rid] = {'request': 'commit'}

    def _update(self, s, repo, func, txn):
        heads = bdecode(self.co.linforepo.get('heads'))
        lstate = {'request':           'get head',
                  'txn':               txn,
                  'changes':           {},
                  'requests':          {},
                  'diffs':             {},
                  'count':             0,
                  'counts':            {},
                  'handle list':       [],
                  'heads':             heads,
                  'reqq':              [],
                  'req-outstanding':   0,
                  'update function':   func}

        if repo.startswith('{') and repo.endswith('}'):
            head = binascii.unhexlify(repo[1:-1])
            rid = self.nh.next_id(s)
            self.socket[s][Request][rid] = lstate
            if self._merge_change(s, rid, head):
                del self.socket[s][Request][rid]
        else:
            rid = self._send_request(s, {'request': 'get head',
                                         'repository': repo})
            self.socket[s][Request][rid] = lstate

        return

    def _create_repo(self, s, repo):
        rid = self._send_request(s, {'request': 'create repository',
                                     'repository': repo})
        self.socket[s][Request][rid] = {'request': 'create repository',
                                        'repository': repo}

    def _remove_repo(self, s, repo):
        rid = self._send_request(s, {'request': 'destroy repository',
                                     'repository': repo})
        self.socket[s][Request][rid] = {'request': 'destroy repository',
                                        'repository': repo}

    def _list_repos(self, s):
        rid = self._send_request(s, {'request': 'list repositories'})
        self.socket[s][Request][rid] = {'request': 'list repositories'}

    def _get_change(self, s, change):
        req = {'request': 'get change', 'changenum': change}
        return self._send_request(s, req)

    def _queue_diff(self, s, change, handle, mid):
        rstate = self.socket[s][Request][mid]
        rstate['reqq'].append((change, handle))

    def _get_diff(self, s, mid):
        rstate = self.socket[s][Request][mid]
        while len(rstate['reqq']) and rstate['req-outstanding'] <= 40:
            change, handle = rstate['reqq'].pop(0)
            req = {'request': 'get diff', 'changenum': change,
                   'handle': handle}
            state = {'request': 'get diff', 'ref': mid, 'change': change,
                     'handle': handle}
            rid = self._send_request(s, req)
            self.socket[s][Request][rid] = state
            rstate['req-outstanding'] += 1

    def _send_msg(self, s, data):
        self.nh.send_msg(s, bencode(data))

    def _send_error(self, s, mid, msg):
        self._send_response(s, mid, {'error': msg})

    def _send_request(self, s, data):
        return self.nh.send_request(s, bencode(data))

    def _send_response(self, s, mid, data):
        return self.nh.send_response(s, mid, bencode(data))

    # Functions to be called from higher level code
    def start_connection(self, dns):
        s = self.nh.start_connection(dns)
        self.socket[s] = {'state': 0, 'srp': {}}
        return s

    def close(self, s):
        self.nh.close(s)
        del self.socket[s]

    def get_hash(self, s):
        self._send_msg(s, {'op': 'get hash', 'user': self.co.user})
        self.socket[s]['state'] = 3
        self.rs.listen_forever(self.nh)
        self.rs.doneflag.clear()
        secret_hash = self.socket[s]['hash']
        del self.socket[s]['hash']
        return secret_hash

    def srp_auth(self, s):
        socket = self.socket[s]
        assert socket['state'] == 0 or socket['state'] == 3
        srp = socket['srp'] = {}
        srp['keys'] = SRP.client_begin()
        self._send_msg(s, {'op': 'srp auth', 'user': self.co.user, 'A': srp['keys'][0]})
        socket['state'] = 1
        self.rs.listen_forever(self.nh)
        self.rs.doneflag.clear()

    def get_secret(self, s):
        self._send_msg(s, {'op': 'get secret'})
        self.socket[s]['state'] = 4
        self.rs.listen_forever(self.nh)
        self.rs.doneflag.clear()
        return

    def set_password(self, s, password):
        socket = self.socket[s]
        salt, v = SRP.new_passwd(self.co.user, password)
        cypherv = crypt(long_to_string(v), socket['key'])[0]
        self._send_msg(s, {'op': 'set password', 's': salt, 'v': cypherv})
        socket['state'] = 5
        self.rs.listen_forever(self.nh)
        self.rs.doneflag.clear()

    def secret_auth(self, s):
        socket = self.socket[s]
        self._send_msg(s, {'op': 'secret auth', 'user': self.co.user})
        socket['state'] = 6
        self.rs.listen_forever(self.nh)
        self.rs.doneflag.clear()

    def commit(self, s, repo, changenum, txn):
        self.txn = txn
        self._commit(s, repo, changenum)
        self.rs.listen_forever(self.nh)
        self.rs.doneflag.clear()

    def update(self, s, repo, func, txn):
        self._update(s, repo, func, txn)
        self.rs.listen_forever(self.nh)
        self.rs.doneflag.clear()
        updateinfo = self.socket[s][UpdateInfo]
        del self.socket[s][UpdateInfo]
        return updateinfo

    def create_repo(self, s, repo):
        self._create_repo(s, repo)
        self.rs.listen_forever(self.nh)
        self.rs.doneflag.clear()

    def remove_repo(self, s, repo):
        self._remove_repo(s, repo)
        self.rs.listen_forever(self.nh)
        self.rs.doneflag.clear()

    def list_repos(self, s):
        self._list_repos(s)
        self.rs.listen_forever(self.nh)
        self.rs.doneflag.clear()
        rlist = self.socket[s][UpdateInfo]
        del self.socket[s][UpdateInfo]
        return rlist

def network_prep(co):
    if co.user is None:
        co.user = co.varsdb.get('user')
    if co.user is None:
        raise NetworkError, 'You must set the "user" variable'
    if co.repo is None:
        raise NetworkError, 'No repository given'

def authenticate(co, ch, remote, txn, srp=False):
    for i in xrange(3):
        try:
            s = _authenticate(co, ch, remote, srp, txn)
        except (AuthError, ServerError), msg:
            print msg
        else:
            return s

    raise ServerError, 'Authentication failed'

def _authenticate(co, ch, remote, srp, txn):
    stuple = (remote[0], remote[1], '')
    server = tuple_to_server(stuple)

    auth = Auth(co, co.user, server, txn)
    ch.auth = auth

    s = ch.start_connection((remote[0], remote[1]))

    if srp:
        ch.srp_auth(s)
        return s

    hash = ch.get_hash(s)

    if not auth.check_hash(hash):
        # we don't know about the secret, fall back to SRP
        ch.srp_auth(s)
        ch.get_secret(s)

        s = ch.start_connection((remote[0], remote[1]))

    ch.secret_auth(s)
    auth.forget()

    return s

# XXX: functions below don't really belong here
def updates(co, uinfo, lstate, handle):
    handle_list = lstate['handle list']
    modified = uinfo['modified']
    mod_conflicts = uinfo['modified conflicts']

    keep_list, i = [], 0
    for i in xrange(len(handle_list)):
        lfile, lhandle, show, letters = handle_list.pop()
        mletter, nletter = letters

        if lhandle != handle and handle is not None:
            # it's possible that the server will send us stuff in a different
            # order than we asked for it.
            if lstate['modified'].has_key(lhandle):
                keep_list.append((lfile, lhandle, show, letters))
                continue
        elif lstate['modified'].has_key(lhandle):
            if show:
                try:
                    diff = lstate['diffs'][lhandle]
                    del lstate['diffs'][lhandle]
                except KeyError:
                    diff = {}
                l = update_file(co, lhandle, lstate['heads'], lstate['head'],
                                uinfo['names'], diff, lstate['txn'])
                if l:
                    mletter = 'C'
                    mod_conflicts.append(lhandle)
                elif mletter == ' ':
                    mletter = 'M'
                del lstate['modified'][lhandle]
                modified.append(lhandle)
            else:
                # XXX: perhaps run handle_contents_at_point as sanity check
                pass

        if show:
            print '%c%c\t%s' % (mletter, nletter, lfile)

        if lhandle == handle:
            break

    keep_list.reverse()
    handle_list.extend(keep_list)
    return

def update_file(co, handle, pre_heads, rhead, names, dcache, txn):
    def gen_file_points(prune):
        file_points, points = [], ['1']

        true_pre_heads = simplify_precursors(co, handle, co.contents,
                                             pre_heads, txn)[0]

        # don't use pre_heads which are ancestors of rhead
        for pre, index in true_pre_heads:
            if prune and is_ancestor(co, pre, rhead, txn):
                continue

            info = handle_contents_at_point(co, handle, pre, txn, dcache=dcache)
            if info is None:
                continue
            points = dmerge(points, info['points'])
            file_points.append((info['lines'], info['line points'], info['points']))
        return (file_points, points)

    if not co.merge:
        return 0

    rinfo = handle_contents_at_point(co, handle, rhead, txn, dcache=dcache)

    if rinfo.has_key('delete'):
        # File was deleted remotely, we're done
        # XXX: validate remote history
        return 0

    elif co.editsdb.has_key(handle) and bdecode(co.editsdb.get(handle)).has_key('hash'):
        lfile = _handle_to_filename(co, handle, names, txn)
        lfile = path.join(co.local, lfile)

        h = open(lfile, 'rb')
        lines = h.read().split('\n')
        h.close()
        file_points, points = gen_file_points(0)
        line_points = find_resolution(file_points, lines)[0]
        for i in xrange(len(line_points)):
            if line_points[i] is None:
                line_points[i] = '1'

        olines = find_conflict(lines, line_points, points, rinfo['lines'],
                               rinfo['line points'], rinfo['points'])

    else:
        file_points, points = gen_file_points(1)
        if file_points == []:
            # The remote copy is a superset of local changes
            olines = rinfo['lines']
        else:
            lines, line_points, points = find_conflict_multiple_safe(file_points)
            olines = find_conflict(lines, line_points, points, rinfo['lines'],
                                   rinfo['line points'], rinfo['points'])

    ls, conflict = [], 0
    for l in olines:
        if type(l) is str:
            ls.append(l)
        else:
            conflict = 1
            ls.append('<<<<<<< local')
            ls.extend(l[0])
            ls.append('=======')
            ls.extend(l[1])
            ls.append('>>>>>>> remote')
    lfile = path.join(co.temppath, binascii.hexlify(handle))
    h = open(lfile, 'wb')
    h.write('\n'.join(ls))
    h.close()
    if conflict:
        set_edit(co, handle, {'hash': 1}, txn)
    mtime = int(path.getmtime(lfile))
    co.modtimesdb.put(handle, bencode(mtime), txn=txn)

    return conflict

def check_modified(co, modified_files):
    for handle in modified_files:
        if handle_name(co, handle, None) is None:
            continue
        pinfo = co.editsdb.get(handle)
        if pinfo is None or not bdecode(pinfo).has_key('hash'):
            continue
        file = handle_to_filename(co, handle)
        if conflicts_in_file(co, file):
            msg = 'file ' + file + ' has conflicts, resolve before updating'
            raise ClientError, msg

try:
    import psyco
    psyco.bind(update_file, 0)
except ImportError:
    pass
