# Written by Ross Cohen
# See LICENSE.txt for license information.

from os import path

try:
    from bsddb3 import db
    version_info = db.__version__.split('.')
    if version_info < [4,1]:
        raise ImportError
    if db.version() < (4,1):
        raise ImportError
except ImportError:
    from bsddb import db
    version_info = db.__version__.split('.')
    if version_info < [4,1]:
        raise ImportError, 'bsddb 4.1 or higher is required'
    if db.version() < (4,1):
        raise ImportError, 'berkeleydb 4.1 or higher is required'

history_format_version = 1
rebuild_format_version = 4

class VersionMismatchException(Exception): pass

def check_format_version(dir):
    try:
        fd = open(path.join(dir, 'format'), 'r')
        ver = int(fd.read())
        fd.close()
    except IOError:
        ver = 0
    if ver != history_format_version:
        raise VersionMismatchException, (history_format_version, ver)
    return

def check_rebuild_version(dir):
    try:
        fd = open(path.join(dir, 'rebuild_format'), 'r')
        ver = int(fd.read())
        fd.close()
    except IOError:
        ver = 0
    if ver != rebuild_format_version:
        raise VersionMismatchException, (rebuild_format_version, ver)
    return

def write_format_version(dir):
    fd = open(path.join(dir, 'format'), 'w')
    fd.write(str(history_format_version))
    fd.close()
    return

def write_rebuild_version(dir):
    fd = open(path.join(dir, 'rebuild_format'), 'w')
    fd.write(str(rebuild_format_version))
    fd.close()

    return

class ChangeDBs:
    def __init__(self, dbenv, name, flags, txn):
        self.indexdb = db.DB(dbEnv=dbenv)
        self.indexdb.open(name[0] + 'index.db', dbtype=db.DB_BTREE, flags=flags, txn=txn)
        self.dagdb = db.DB(dbEnv=dbenv)
        self.dagdb.open(name + '-dag.db', dbtype=db.DB_BTREE, flags=flags, txn=txn)
        self.mergedb = db.DB(dbEnv=dbenv)
        self.mergedb.open(name + '-merges.db', dbtype=db.DB_BTREE, flags=flags, txn=txn)

        return

    def close(self):
        self.indexdb.close()
        self.dagdb.close()
        self.mergedb.close()
        return
