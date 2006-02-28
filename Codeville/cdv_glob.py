# Written by Ross Cohen
# see LICENSE.txt for license information

from client_helpers import children_count
from client_helpers import filename_to_handle
import fnmatch
import os, sys
from os import path
from path import subpath
import re

class Glob:
    def __init__(self, co, files):
        self.co    = co
        self.local = co.local

        if sys.platform == 'win32':
            self._glob_cache = {}
            unglobbedfiles = files
            files = []
            for file in unglobbedfiles:
                files += self._glob(file)
            del self._glob_cache

        # normalize the filenames
        self.files = []
        for file in files:
            try:
                self.files.append(subpath(self.local, file)[1])
            except ValueError:
                print 'warning - %s is not under the repository' % (file,)
        return

    def fs_walk(self, expand=1):
        for file in self.files:
            parent, name = path.split(file)
            if name == '...':
                if expand:
                    for lfile in self._fs_expand(parent):
                        yield (lfile, 1)
                continue

            if not path.exists(path.join(self.local, file)):
                print 'warning - %s does not exist' % (file,)
                continue

            yield (file, 0)

        return

    def db_walk(self, deletes=0, txn=None):
        for file in self.files:
            parent, name = path.split(file)
            if name == '...':
                for lhandle in self._db_expand(parent, deletes, txn):
                    yield (lhandle, 1)
                continue

            handle = filename_to_handle(self.co, file, txn, deletes=deletes)
            if handle is None:
                print 'warning - %s is not in the repository' % (file,)
                continue

            yield (handle, 0)

        return

    def _fs_expand(self, local):
        root_cmp = path.join(self.local, '.cdv')
        filter   = path.join(self.local, '.cdv', '')

        root_len   = len(self.local + path.sep)
        filter_len = len(filter)

        for root, dirs, files, in os.walk(path.join(self.local, local)):
            if root == root_cmp:
                dirs[:] = []
                continue

            if root[:filter_len] == filter:
                dirs[:] = []
                continue

            nroot = root[root_len:]
            if nroot != local:
                yield nroot

            for file in files:
                yield path.join(nroot, file)

        return

    def _db_expand(self, local, deletes, txn):
        handle = filename_to_handle(self.co, local, txn)
        dirs = [handle]
        while len(dirs):
            lhandle = dirs.pop()

            children = children_count(self.co, lhandle, txn, deletes=deletes)
            dirs.extend(children)

            for handle in children:
                yield handle

        return

    def _glob(self, pathname):
        if not self._has_magic(pathname):
            return [pathname]
        dirname, basename = os.path.split(pathname)
        if not dirname:
            return self._glob1(os.curdir, basename)
        elif self._has_magic(dirname):
            list = self._glob(dirname)
        else:
            list = [dirname]
        if not self._has_magic(basename):
            result = []
            for dirname in list:
                if basename or os.path.isdir(dirname):
                    name = os.path.join(dirname, basename)
                    if os.path.lexists(name):
                        result.append(name)
        else:
            result = []
            for dirname in list:
                sublist = self._glob1(dirname, basename)
                for name in sublist:
                    result.append(os.path.join(dirname, name))
        if len(result) == 0:
            result.append(pathname)
        return result

    def _glob1(self, dirname, pattern):
        if not dirname: dirname = os.curdir
        if dirname in self._glob_cache:
            names = self._glob_cache[dirname]
        else:
            try:
                names = os.listdir(dirname)
            except os.error:
                return []
            self._glob_cache[dirname] = names
        if pattern[0]!='.':
            names=filter(lambda x: x[0]!='.',names)
        return fnmatch.filter(names,pattern)


    _magic_check = re.compile('[*?[]')

    def _has_magic(self, s):
        return self._magic_check.search(s) is not None
