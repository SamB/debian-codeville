import binascii
from Codeville.bencode import bdecode, bencode
from Codeville.client_helpers import create_handle, gen_diff
from Codeville.DFS import DFS
from Codeville.history import sync_history, write_changeset
from Codeville.history import roothandle, rootnode
from Codeville.history import read_diff, write_diff, write_index
from Codeville.history import handle_contents_at_point
from Codeville.history import handle_name_at_point
from Codeville.history import HistoryError
from Codeville.old.history import handle_contents_at_point as old_handle_contents_at_point
from Codeville.old.history import handle_name_at_point as old_handle_name_at_point
import copy
import sha
from sys import stdout
import zlib

class UpgradeRepository:
    def __init__(self, old_repo, new_repo, txn):
        self.point_map       = {}
        self.handle_map      = {}
        self.all_old_handles = {}

        self.old_repo = old_repo
        self.new_repo = new_repo
        self.txn      = txn
        return

    def sort_history(self, handle_list):
        history_dfs = DFS(self._history_deps, [self.old_repo])
        for point in handle_list:
            history_dfs.search(point)
        return history_dfs.result()

    def _history_deps(node, args):
        co = args[0]

        cset = bdecode(co.lcrepo.get(node))
        cset['precursors'].reverse()

        return cset['precursors']

    _history_deps = staticmethod(_history_deps)

    def sort_names(self, handles):
        name_dfs = DFS(self._name_deps, [handles])
        for old_handle in handles.keys():
            name_dfs.search(old_handle)
        return name_dfs.result()

    def _name_deps(node, args):
        handles = args[0]

        if handles.has_key(node) and handles[node].has_key('parent'):
            parent = handles[node]['parent']
            if handles.has_key(parent) and handles[parent].has_key('name'):
                return [parent]

        return []

    _name_deps = staticmethod(_name_deps)

    def clean_merges(self, UR, dagdb, point):
        clean_merges = {}
        handles      = []
        for handle in UR.all_old_handles.keys():
            if not dagdb.has_key(handle + point):
                continue

            hinfo = bdecode(dagdb.get(handle + point))
            if hinfo.has_key('handle'):
                continue

            if len(hinfo['precursors']) <= 1:
                continue

            clean_merges[handle] = 1
            handles.append(handle)

        return clean_merges, handles


def upgrade(old_repo, new_repo, changes, txn):
    UR = UpgradeRepository(old_repo, new_repo, txn)

    for old_handle in old_repo.staticdb.keys():
        hinfo = bdecode(old_repo.staticdb.get(old_handle))
        if hinfo['type'] == 'file':
            UR.all_old_handles[old_handle] = hinfo

    # sort the history
    ordering = UR.sort_history(changes)

    # sort again for better dag construction
    ordering.reverse()
    ordering = UR.sort_history(ordering)

    assert rootnode == ordering[0]

    print "%d changesets to convert" % (len(ordering), )

    for point in ordering:
        new_point = convert_cset(UR, point)

        stdout.write('.')
        stdout.flush()

    return UR

def convert_cset(UR, point):
    indices = {}

    old_cset = bdecode(UR.old_repo.lcrepo.get(point))

    new_cset = {}
    new_cset['precursors'] = [UR.point_map[pre] for pre in old_cset['precursors']]

    if old_cset.has_key('time'):
        new_cset['time'] = old_cset['time']

    if old_cset.has_key('user'):
        new_cset['user'] = old_cset['user']

    # some heuristics for comments and whether this was a server change
    clean_merge = True
    force_new_cset = False

    if old_cset.has_key('comment'):
        clean_merge = False
        new_cset['comment'] = old_cset['comment'].rstrip()
        if len(new_cset['comment']):
            new_cset['comment'] = new_cset['comment'] + '\n'

    elif point == rootnode:
        pass

    elif old_cset['handles'] != {} or len(old_cset['precursors']) != 2:
        clean_merge = False
        new_cset['comment'] = '--- comment inserted by cdvupgrade ---\n'

    # sort the handles
    handle_list = UR.sort_names(old_cset['handles'])

    # find implicit clean content merges
    clean_merges, hl = UR.clean_merges(UR, UR.old_repo.contents.dagdb, point)
    handle_list.extend(hl)

    # find implicit clean name merges
    clean_nmerges, hl = UR.clean_merges(UR, UR.old_repo.names.dagdb, point)
    handle_list.extend(hl)

    new_cset['handles'] = handles = {}
    for old_handle in handle_list:
        old_hinfo = None
        try:
            old_hinfo = old_cset['handles'][old_handle]
        except KeyError:
            old_hinfo = {}

        # not much has changed
        new_hinfo = copy.copy(old_hinfo)

        new_handle = None
        if UR.handle_map.has_key(old_handle):
            new_handle = UR.handle_map[old_handle]

        # make name changes explicit
        if clean_nmerges.has_key(old_handle):
            name = old_handle_name_at_point(UR.old_repo, old_handle, point, None)
            new_hinfo['parent'] = name['parent']
            new_hinfo['name'] = name['name']

        # fixup the parent pointers
        if old_hinfo.has_key('parent'):
            new_hinfo['parent'] = UR.handle_map[old_hinfo['parent']]

        if old_hinfo.has_key('hash') or clean_merges.has_key(old_handle):
            # figure out what the file is supposed to look like now
            lines = old_handle_contents_at_point(UR.old_repo, old_handle, point, None)['lines']

            # if the file is being added, there are no precursors
            precursors = []
            if new_handle is not None and not old_hinfo.has_key('add'):
                precursors = new_cset['precursors']

            # generate the diff against the new repo
            dinfo = gen_diff(UR.new_repo, new_handle, precursors, lines, UR.txn)
            if old_hinfo.has_key('add'):
                dinfo['add'] = 1
                assert dinfo['matches'] == []

            if dinfo is not None:
                diff = bencode(dinfo)
                new_hinfo['hash'] = sha.new(diff).digest()

                # if this used to be a clean merge, we have to replace it
                if not old_cset.has_key(old_handle) or not old_cset[old_handle].has_key('hash'):
                    force_new_cset = True

            elif new_hinfo.has_key('hash'):
                del new_hinfo['hash']

            # sanity check
            if new_handle is None:
                assert old_hinfo.has_key('add')
                assert old_hinfo['add']['type'] == 'file'

            # if the file is new, we have to create the handle before writing
            # the diff
            if old_hinfo.has_key('add'):
                nhandle = create_handle(new_cset['precursors'], new_hinfo)
                assert new_handle is None or new_handle == nhandle
                new_handle = nhandle
                UR.handle_map[old_handle] = new_handle

            # write out the new diff
            if new_hinfo.has_key('hash'):
                zdiff = zlib.compress(diff, 6)
                indices[new_handle] = write_diff(UR.new_repo, new_handle, zdiff, UR.txn)

        elif old_hinfo.has_key('add'):
            assert old_hinfo['add']['type'] == 'dir'

            nhandle = create_handle(new_cset['precursors'], new_hinfo)
            assert new_handle is None or new_handle == nhandle
            new_handle = nhandle
            UR.handle_map[old_handle] = new_handle

        if new_hinfo != {}:
            handles[new_handle] = new_hinfo

    # if it used to be a clean merge, preserve the line of clean merge heads
    index_point = None
    if clean_merge and force_new_cset:
        forced_cset = new_cset

        forced_cset['comment'] = '--- change created by cdvupgrade ---\n'

        bforced_cset = bencode(forced_cset)
        forced_point = sha.new(bforced_cset).digest()
        UR.new_repo.lcrepo.put(forced_point, bforced_cset, txn=UR.txn)

        index_point = forced_point

        new_cset = {'precursors': [forced_cset['precursors'][0], forced_point],
                    'user':       forced_cset['user'],
                    'time':       forced_cset['time'],
                    'handles':    {}}

    # calculate the new point name and write it out
    bnew_cset = bencode(new_cset)
    new_point = sha.new(bnew_cset).digest()
    UR.new_repo.lcrepo.put(new_point, bnew_cset, txn=UR.txn)

    UR.point_map[point] = new_point

    if index_point is None:
        index_point = new_point

    # now that we know the new point name, write out the indices
    for new_handle, index in indices.items():
        write_index(UR.new_repo, index_point, new_handle, index, UR.txn)

    # diff generation depends on history syncing
    named, modified = sync_history(UR.new_repo, new_point, UR.txn)

    for new_handle in modified:
        handle_contents_at_point(UR.new_repo, new_handle, new_point, UR.txn)

    return new_point
