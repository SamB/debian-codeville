# Written by Ross Cohen
# see LICENSE.txt for license information

import os

def preserving_rename(src, dest):
    # preserve whatever permissions the user set
    try:
        statinfo = os.stat(dest)
        os.chmod(src, statinfo.st_mode)
        # windows doesn't allow atomic renames to overwrite
        os.remove(dest)
    except OSError:
        # file may be missing from the filesystem
        pass
    os.rename(src, dest)
    return

def mdir(d):
    try:
        os.makedirs(d)
    except OSError:
        pass

def abspath(rel_path):
    "Because os.path.abspath on Windows drops '...'"
    parent_path, filename = os.path.split(rel_path)
    if filename == '...':
        return os.path.join(os.path.abspath(parent_path), filename)
    return os.path.abspath(rel_path)

def subpath(local, file):
    llocal = breakup(os.path.abspath(local))
    lfile = breakup(abspath(file))
    if lfile[:len(llocal)] != llocal:
        raise ValueError
    fname = ''
    for d in lfile[len(llocal):]:
        fname = os.path.join(fname, d)
    return (lfile, fname)

def breakup(s):
    def _breakup(s):
        if s == '':
            return []
        a, b = os.path.split(s)
        return _breakup(a) + [b]

    drive, s = os.path.splitdrive(s)
    bdrive, bsep = [], []
    if drive != '':
        bdrive = [drive]
        bsep   = [os.sep]
    if s.startswith(os.sep):
        bsep = [os.sep]
        s = s[len(os.sep):]
    return bdrive + bsep + _breakup(s)
