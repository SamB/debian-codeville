# Written by Ross Cohen
# See LICENSE.txt for license information.

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


class ChangeDBs:
    def __init__(self, dbenv, name, txn):
        self.indexdb = db.DB(dbEnv=dbenv)
        self.indexdb.open(name[0] + 'index.db', dbtype=db.DB_BTREE, flags=db.DB_CREATE, txn=txn)
        self.dagdb = db.DB(dbEnv=dbenv)
        self.dagdb.open(name + '-dag.db', dbtype=db.DB_BTREE, flags=db.DB_CREATE, txn=txn)
        self.mergedb = db.DB(dbEnv=dbenv)
        self.mergedb.open(name + '-merges.db', dbtype=db.DB_BTREE, flags=db.DB_CREATE, txn=txn)

        return

    def close(self):
        self.indexdb.close()
        self.dagdb.close()
        self.mergedb.close()
        return
