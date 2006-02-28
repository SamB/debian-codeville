# Written by Ross Cohen
# see LICENSE.txt for license information

import binascii
from entropy import random_string
from os import chmod, fdopen, path, remove, rename, stat
import SRP
from sys import platform
from tempfile import mkstemp

if platform != 'win32':
    from os import lchown

class Passwd:
    def __init__(self, pw_file, create=False, uid=-1):
        self.users = {}
        self.pw_file = pw_file
        self.pw_dir = path.split(pw_file)[0]
        self.modtime = 0
        if create:
            h = open(self.pw_file, 'a')
            if uid != -1:
                lchown(self.pw_file, uid, -1)
            chmod(self.pw_file, 0600)
            h.close()
        self._read()

    def get(self, user):
        self._read()
        if not self.users.has_key(user):
            raise KeyError
        info = self.users[user]
        if not info.has_key('secret'):
            info['secret'] = random_string(20)
        return info

    def add(self, user, password):
        self._read()
        if self.users.has_key(user):
            raise ValueError, 'User exists'
        s, v = SRP.new_passwd(user, password)
        self.users[user] = {'s': s, 'v': v}
        self._dump()

    def set(self, user, password):
        self._read()
        if not self.users.has_key(user):
            raise ValueError, 'No such user'
        s, v = SRP.new_passwd(user, password)
        self.users[user] = {'s': s, 'v': v}
        self._dump()

    def define(self, user, v, s):
        self._read()
        if not self.users.has_key(user):
            raise ValueError, 'No such user'
        self.users[user] = {'s': s, 'v': v}
        self._dump()

    def delete(self, user):
        self._read()
        try:
            del self.users[user]
        except KeyError:
            raise ValueError, 'No such user'
        self._dump()

    def _read(self):
        h = open(self.pw_file, 'rU')

        modtime = path.getmtime(self.pw_file)
        if self.modtime == modtime:
            return
        self.modtime = modtime

        l, used = None, {}
        while l != '':
            l = h.readline()
            if l.strip() == '':
                continue

            try:
                user, salt, verifier, garbage = l.split(':', 3)
                salt = binascii.unhexlify(salt)
            except ValueError:
                print 'garbage in passwd file'
                raise ValueError

            if not self.users.has_key(user):
                self.users[user] = {'s': salt, 'v': long(verifier)}
            else:
                self.users[user]['s'] = salt
                self.users[user]['v'] = long(verifier)

            used[user] = 1

        h.close()

        # remove the deleted entries
        for user in self.users.keys():
            if not used.has_key(user):
                del self.users[user]

        self.modtime = path.getmtime(self.pw_file)

    def _dump(self):
        text = []
        for user, pw in self.users.items():
            text.append('%s:%s:%d:\n' % (user, binascii.hexlify(pw['s']), pw['v']))
        text.sort()

        fd, fname = mkstemp('', 'cdv', self.pw_dir)
        h = fdopen(fd, 'w')
        h.writelines(text)
        h.close()

        if platform != 'win32':
            statinfo = stat(self.pw_file)
            lchown(fname, statinfo.st_uid, statinfo.st_gid)
        chmod(fname, 0600)
        if platform == 'win32':
            remove(self.pw_file)
        rename(fname, self.pw_file)
        self.modtime = path.getmtime(self.pw_file)
