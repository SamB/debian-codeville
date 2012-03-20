# Written by Bram Cohen
# see LICENSE.txt for license information

import os
from select import select, error
from time import sleep
from types import IntType
from bisect import bisect
POLLIN = 1
POLLOUT = 2
POLLERR = 8
POLLHUP = 16
POLLERR = 32

class poll:
    def __init__(self):
        self.rlist = []
        self.wlist = []
        self.elist = []
        
    def register(self, f, t):
        if type(f) != IntType:
            f = f.fileno()
        if (t & POLLIN) != 0:
            insert(self.rlist, f)
        else:
            remove(self.rlist, f)
        if (t & POLLOUT) != 0:
            insert(self.wlist, f)
        else:
            remove(self.wlist, f)
        if (t & POLLERR) != 0:
            insert(self.elist, f)
        else:
            remove(self.elist, f)
        
    def unregister(self, f):
        if type(f) != IntType:
            f = f.fileno()
        remove(self.rlist, f)
        remove(self.wlist, f)
        remove(self.elist, f)

    def poll(self, timeout = None):
        if self.rlist != [] or self.wlist != [] or self.elist != []:
            r, w, e = select(self.rlist, self.wlist, self.elist, timeout)
        else:
            sleep(timeout)
            return []
        result = {}
        for s in r:
            result[s] = POLLIN
        for s in w:
            res = result.get(s, 0)
            res |= POLLOUT
            result[s] = res
        for s in e:
            res = result.get(s, 0)
            res |= POLLERR
            result[s] = res
        return result.items()

def remove(list, item):
    i = bisect(list, item)
    if i > 0 and list[i-1] == item:
        del list[i-1]

def insert(list, item):
    i = bisect(list, item)
    if i == 0 or list[i-1] != item:
        list.insert(i, item)

def test_remove():
    x = [2, 4, 6]
    remove(x, 2)
    assert x == [4, 6]
    x = [2, 4, 6]
    remove(x, 4)
    assert x == [2, 6]
    x = [2, 4, 6]
    remove(x, 6)
    assert x == [2, 4]
    x = [2, 4, 6]
    remove(x, 5)
    assert x == [2, 4, 6]
    x = [2, 4, 6]
    remove(x, 1)
    assert x == [2, 4, 6]
    x = [2, 4, 6]
    remove(x, 7)
    assert x == [2, 4, 6]
    x = [2, 4, 6]
    remove(x, 5)
    assert x == [2, 4, 6]
    x = []
    remove(x, 3)
    assert x == []

def test_insert():
    x = [2, 4]
    insert(x, 1)
    assert x == [1, 2, 4]
    x = [2, 4]
    insert(x, 3)
    assert x == [2, 3, 4]
    x = [2, 4]
    insert(x, 5)
    assert x == [2, 4, 5]
    x = [2, 4]
    insert(x, 2)
    assert x == [2, 4]
    x = [2, 4]
    insert(x, 4)
    assert x == [2, 4]
    x = [2, 3, 4]
    insert(x, 3)
    assert x == [2, 3, 4]
    x = []
    insert(x, 3)
    assert x == [3]
