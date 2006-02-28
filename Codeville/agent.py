# Written by Ross Cohen
# see LICENSE.txt for license information

from bencode import bdecode, bencode
from crypt import crypt
from errno import EINTR
import os
try:
    import select
except ImportError:
    import selectpoll as select
import sha
import socket
import SRP
from cStringIO import StringIO
import struct

AUTH_UNUSED     = 0
AUTH_SOCK       = 1
AUTH_CONNECTION = 2

class Agent:
    def __init__(self):
        self.auth_path = None
        self.auth_file = None

        self.poll_obj = select.poll()

        self.hstatus = {}
        self.identities = {}

        self.allow_shutdown = 0
        return

    def listen_sock(self, auth_path, auth_file):
        if self.auth_file is not None:
            print 'Auth socket already set'
            return None

        self.auth_path = auth_path
        self.auth_file = auth_file

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(auth_file)
        sock.listen(8)

        self._new_socket(AUTH_SOCK, sock)
        return sock

    def listen(self):
        if self.auth_file is None:
            print 'No socket to listen on'
            return 1

        self.shutdown_flag = 0
        while not self.shutdown_flag:
            try:
                sock_list = self.poll_obj.poll()
            except select.error, reason:
                if reason[0] != EINTR:
                    raise
            else:
                self._after_poll(sock_list)

        self.cleanup()
        return 0

    def cleanup(self):
        for fd, status in self.hstatus.items():
            del self.hstatus[fd]
            self.poll_obj.unregister(fd)
            status['sock'].close()

        os.unlink(self.auth_file)
        if self.auth_path is not None:
            auth_path = self.auth_path
            self.auth_path = None
            os.rmdir(auth_path)
        return

    def _process_message(self, sock):
        status = self.hstatus[sock.fileno()]
        input = status['input']
        input.seek(status['in_offset'])
        data = input.read(4)
        if len(data) != 4:
            return

        msg_len, = struct.unpack('<i', data)
        if msg_len <= 0 or msg_len > 1024:
            self._write_error(sock, 'Bad message lenth')
            self._close_socket(sock)
            return
        data = input.read(msg_len)

        if len(data) < msg_len:
            return

        status['in_offset'] += 4 + msg_len

        try:
            msg = bdecode(data)
        except ValueError:
            self._write_error(sock, 'Bad message')
            self._close_socket(sock)
            return

        type = msg['type']

        if   type == 'CDV_AGENT_ADD_PASSWORD':
            id = sha.new('password' + msg['password']).digest()
            self.identities[id] = msg['password']
            self._write_answer(sock, bencode({'id': id}))

        elif type == 'CDV_AGENT_ADD_SECRET':
            id = sha.new('public hash check' + msg['secret']).digest()
            self.identities[id] = msg['secret']
            self._write_answer(sock, bencode({'id': id}))

        elif type == 'CDV_AGENT_ADD_ENCRYPTED_SECRET':
            if not self.identities.has_key(msg['id']):
                self._write_error(sock, 'No such identity')
                return
            secret = crypt(msg['secret'], self.identities[msg['id']])[0]

            id = sha.new('public hash check' + secret).digest()
            if id != msg['secret_id']:
                self._write_error(sock, 'id does not match')
                return

            self.identities[id] = secret
            self._write_answer(sock, bencode({}))

        elif type == 'CDV_AGENT_QUERY_IDENTITY':
            known = 0
            if self.identities.has_key(msg['id']):
                known = 1
            self._write_answer(sock, bencode({'known': known}))

        elif type == 'CDV_AGENT_SRP_PRIVATE_KEY':
            x = SRP.private_key(msg['user'], msg['s'], self.identities[msg['id']])
            self._write_answer(sock, bencode({'x': x}))

        elif type == 'CDV_AGENT_SESSION_KEY':
            if not self.identities.has_key(msg['id']):
                self._write_error(sock, 'No such identity')
                return

            if len(msg['salt1']) < 20 or len(msg['salt2']) < 20:
                self._write_error(sock, 'Bad salts')
                return

            if msg['salt1'] == msg['salt2']:
                self._write_error(sock, 'Bad salts')
                return

            base = 'session key' + self.identities[msg['id']] + \
                   msg['salt1'] + msg['salt2']
            key = sha.new(base).digest()
            answer = {'key': key}
            self._write_answer(sock, bencode(answer))

        elif type == 'CDV_AGENT_SHUTDOWN' and self.allow_shutdown:
            self.shutdown_flag = 1

        else:
            self._write_error(sock, 'Unknown command: ' + type)

        return

    def _after_poll(self, sock_list):
        for fd, event in sock_list:
            status = self.hstatus[fd]
            sock = status['sock']

            if status['type'] == AUTH_UNUSED:
                return

            elif status['type'] == AUTH_SOCK:
                tries = 3
                while tries:
                    try:
                        nsock = sock.accept()
                    except socket.error, reason:
                        if reason[0] != EINTR:
                            raise
                        tries -= 1
                    else:
                        break
                if tries == 0:
                    raise

                # XXX: python doesn't support SO_PEERCRED
                #sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED)

                self._new_socket(AUTH_CONNECTION, nsock[0])

            elif status['type'] == AUTH_CONNECTION:
                if event & select.POLLHUP:
                    self._close_socket(sock)
                    return

                if event & select.POLLIN:
                    data = sock.recv(1024)
                    if len(data) == 0:
                        self._close_socket(sock)
                        return

                    status['input'].seek(0, 2)
                    status['input'].write(data)
                    self._process_message(sock)

                if event & select.POLLOUT:
                    self._flush_socket(sock)

        return

    def _new_socket(self, type, sock):
        sock.setblocking(0)

        self.hstatus[sock.fileno()] = {'type':       type,
                                       'input':      StringIO(),
                                       'output':     StringIO(),
                                       'in_offset':  0,
                                       'out_offset': 0,
                                       'sock':       sock}

        flags = select.POLLIN
        if type != AUTH_SOCK:
            flags |= select.POLLHUP
        self.poll_obj.register(sock, flags)
        return

    def _close_socket(self, sock):
        fileno = sock.fileno()
        self.poll_obj.unregister(fileno)
        del self.hstatus[fileno]
        sock.close()
        return

    def _write_answer(self, sock, data):
        data = struct.pack('<i', len(data)) + data

        status = self.hstatus[sock.fileno()]
        status['output'].seek(0, 2)

        written = 0
        if status['out_offset'] == status['output'].tell():
            written = sock.send(data)

        if written != len(data):
            status['output'].write(data[written:])
            self.poll_obj.register(sock.fileno(),
                                   select.POLLIN|select.POLLOUT|select.POLLHUP)
        return

    def _write_error(self, sock, msg):
        self._write_answer(sock, bencode({'error': msg}))
        return

    def _flush_socket(self, sock):
        status = self.hstatus[sock.fileno()]
        status['output'].seek(status['out_offset'])

        data = status['output'].read(1024)
        while len(data):
            written = sock.send(data)
            status['out_offset'] += written
            if written < len(data):
                break

            data = status['output'].read(1024)

        if len(data) == 0:
            self.poll_obj.register(sock.fileno(), select.POLLIN|select.POLLHUP)

        return
