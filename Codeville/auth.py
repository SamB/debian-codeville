# Written by Ross Cohen
# See LICENSE.txt for license information.

from bencode import bdecode, bencode
from db import db
from crypt import crypt
from errno import ECONNRESET
from getpass import getpass
from os import environ
import sha
import socket
import SRP
from StringIO import StringIO
import struct

class AuthError(Exception): pass

class Auth:
    def __init__(self, co, user, server, txn):
        self.co       = co
        self.user     = user
        self.server   = server
        self.txn      = txn

        self.secret_key    = "secret-%s-%s" % (user, server)
        self.hash_key      = 'hash-%s-%s'   % (user, server)
        self.pwid_key      = "pwid-%s-%s"   % (user, server)

        self.password = None
        self.secret   = None
        self.pwid     = co.linforepo.get(self.pwid_key, txn=txn)

        self.agent_sock   = None
        try:
            if environ.has_key('CDV_AUTH_SOCK'):
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.connect(environ['CDV_AUTH_SOCK'])
                self.agent_sock = sock
            elif co.nopass == 2:
                raise ValueError, 'No agent found'
        except socket.error:
            if co.nopass == 2:
                raise
        return

    def forget(self):
        if not self.co.nopass:
            self.password = None
            self.pwid = None
        self.secret = None
        return

    def check_hash(self, hash):
        self.hash = hash

        # if the agent can decrypt the session key, we're done
        if self._query_agent_for_id(hash):
            return 1

        secret_hash = self.co.linforepo.get(self.hash_key, txn=self.txn)
        esecret     = self.co.linforepo.get(self.secret_key, txn=self.txn)

        # check if the saved secret info is still valid
        if secret_hash != hash:
            return 0

        # if the agent knows the password then we can give it the encrypted
        # shared secret and we're done
        if self.pwid is not None and self._query_agent_for_id(self.pwid):
            msg = {'secret': esecret, 'secret_id': secret_hash,
                   'id': self.pwid}
            msg['type'] = 'CDV_AGENT_ADD_ENCRYPTED_SECRET'
            self._agent_make_request(msg)
            return 1

        # we have good saved secret info, but the agent is no help
        self._get_password()

        # double check the decrypted secret against the known hash
        # to make sure the user typed in the right password
        secret = crypt(esecret, self.password)[0]
        if sha.new('public hash check' + secret).digest() != secret_hash:
            raise AuthError, 'Bad password'

        self.secret = secret
        return 1

    def save_secret(self, secret):
        self.secret = secret

        # XXX: assert self.hash == hash
        #hash = sha.new('public hash check' + secret).digest()

        if self.agent_sock is not None:
            msg = {'id': self.hash, 'secret': secret}
            msg['type'] = 'CDV_AGENT_ADD_SECRET'
            ans = self._agent_make_request(msg)
            assert ans['id'] == self.hash

        else:
            assert self.co.nopass != 2

        if self.password is not None:
            crypted = crypt(secret, self.password)[0]
            self.co.linforepo.put(self.secret_key, crypted, txn=self.txn)
            self.co.linforepo.put(self.hash_key, self.hash, txn=self.txn)

        return

    def client_key(self, s, B, u, keys):
        use_agent = True
        if   self.pwid is None:
            use_agent = False
        elif not self._query_agent_for_id(self.pwid):
            use_agent = False

        if use_agent:
            func = self._private_key
            arg = None

        else:
            self._get_password()
            func = SRP.private_key
            arg = self.password

        K, m = SRP.client_key(self.user, arg, s, B, u, keys, func)
        return K, m

    def session_key(self, salt1, salt2):
        key = None

        if   self.secret is not None:
            base = 'session key' + self.secret + salt1 + salt2
            key = sha.new(base).digest()

        elif self.agent_sock is not None:
            msg = {'id': self.hash, 'salt1': salt1, 'salt2': salt2}
            msg['type'] = 'CDV_AGENT_SESSION_KEY'
            ans = self._agent_make_request(msg)
            key = ans['key']

        else:
            raise AuthError, 'Secret should have been set'

        if self.password is not None:
            self.co.linforepo.put(self.pwid_key, self.pwid, txn=self.txn)
        return key

    def _query_agent_for_id(self, id):
        if self.agent_sock is None:
            return 0
        msg = {'id': id}
        msg['type'] = 'CDV_AGENT_QUERY_IDENTITY'
        ans = self._agent_make_request(msg)
        return ans['known']

    def _get_password(self):
        if self.password is not None:
            return

        # hack for running tests
        if self.co.nopass:
            self.password = ''
        else:
            self.password = getpass()

        self.pwid = sha.new('password' + self.password).digest()

        if self.agent_sock is not None:
            msg = {'password': self.password}
            msg['type'] = 'CDV_AGENT_ADD_PASSWORD'
            ans = self._agent_make_request(msg)
            id = ans['id']
            if id != self.pwid:
                raise AuthError, 'cdv-agent misbehaving'

        return

    def _agent_make_request(self, msg):
        req = bencode(msg)
        try:
            self.agent_sock.sendall(struct.pack('<i', len(req)))
            self.agent_sock.sendall(req)
        except socket.error, reason:
            if reason == ECONNRESET:
                raise AuthError, 'Unexpected close from cdv-agent'
            raise

        input = StringIO()
        self._read_sock(input, 4)
        input.seek(0)

        togo, = struct.unpack('<i', input.read(4))
        if togo <= 0 or togo > 1024:
            raise AuthError, 'Bad message length from cdv-agent'
        self._read_sock(input, togo)

        input.seek(4)
        ans = bdecode(input.read())
        if ans.has_key('error'):
            raise AuthError, ans['error']
        return ans

    def _read_sock(self, input, togo):
        while togo > 0:
            try:
                data = self.agent_sock.recv(togo)
            except socket.error, reason:
                if reason[0] == ECONNRESET:
                    raise AuthError, 'Unexpected close from cdv-agent'
                raise

            input.write(data)
            togo -= len(data)
        return

    # for client_key to use
    def _private_key(self, user, s, foo):
        msg = {'user': user, 's': s, 'id': self.pwid}
        msg['type'] = 'CDV_AGENT_SRP_PRIVATE_KEY'
        ans = self._agent_make_request(msg)
        return ans['x']
