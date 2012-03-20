#!/usr/bin/python

# Written by Uoti Urpala


from array import array


def unique_lcs(a, b, alo, ahi, blo, bhi):
    index = {}
    for i in xrange(blo, bhi):
        line = b[i]
        if line in index:
            index[line] = None
        else:
            index[line] = i
    s = {}
    get = index.get
    for i in xrange(alo, ahi):
        line = a[i]
        if get(line) is not None:
            if line in s:
                del s[line]
                del index[line]
            else:
                s[line] = index[line] + 1
    back = {}
    values = {}
    values[0] = 0
    matches = {}
    sz = bhi - blo + 1
    while True:
        sz2 = sz
        sz |= sz >> 1
        if sz == sz2:
            break
    sz += 1
    tree = array('b', [0] * (2 * sz))
    sz2 = sz
    while sz2 > 0:
        tree[sz2] = 1
        sz2 >>= 1
    get = s.get
    i = alo
    while i < ahi:
        line = a[i]
        i += 1
        j = get(line)
        if j is None:
            continue
        l = 1
        j2 = i
        while i < ahi and j < bhi and a[i] == b[j]:
            if a[i] in s:
                l += 1
            i += 1
            j += 1
        j -= blo
        matches[j] = j2 - 1
        left = j + sz
        while True:
            tree[left] = 1
            j2 = left >> 1
            if tree[j2]:
                break
            left = j2
        j2 = left
        while not left & 1 or not tree[left - 1]:
            left >>= 1
        left -= 1
        while not left & sz:
            left <<= 1
            if tree[left + 1]:
                left += 1
        left -= sz
        back[j] = left
        l += values[left]
        values[j] = l
        while True:
            while j2 & 1 or not tree[j2 + 1]:
                j2 >>= 1
            if j2 == 0:
                break
            j2 += 1
            while not j2 & sz:
                j2 <<= 1
                if not tree[j2]:
                    j2 += 1
            j = values[j2-sz]
            if j > l:
                break
            while True:
                tree[j2] = 0
                if tree[j2 ^ 1]:
                    break
                j2 >>= 1
            if j == l:
                break
    j = 1
    while not j & sz:
        j <<= 1
        if tree[j + 1]:
            j += 1
    r = []
    j -= sz
    while j:
        i = matches[j]
        j2 = s[a[i]] - 1
        r.append((i, j2, j + blo - j2))
        j = back[j]
    r.reverse()
    return r

def recurse(a, b, alo, ahi, blo, bhi, answer, maxrecursion=20):
    if maxrecursion < 0:
        return
    m = unique_lcs(a, b, alo, ahi, blo, bhi)
    i = alo
    j = blo
    for i2, j2, l in m:
        while i2 > i and j2 > j and a[i2-1] == b[j2-1]:
            i2 -= 1
            j2 -= 1
            l += 1
        if l < 2:
            continue
        if i2 > alo and j2 > blo:
            recurse(a, b, i, i2, j, j2, answer, maxrecursion - 1)
        answer.append((i2, j2, l))
        i = i2 + l
        j = j2 + l
    if i > alo and i < ahi and j < bhi:
        recurse(a, b, i, ahi, j, bhi, answer, maxrecursion - 1)









def x(a, b):
    import time
    t1 = time.time()
    r = unique_lcs(a, b, 0, len(a), 0, len(b))
    t2 = time.time()
    print r
    for i, j, l in r:
        assert a[i:i+l] == b[j:j+l]
        print ''.join(a[i:i+l]),

    print
    print 'time:', t2-t1

def x2(a, b):
    import time
    t1 = time.time()
    r = []
    recurse(a, b, 0, len(a), 0, len(b), r)
    t2 = time.time()
    print r
    for i, j, l in r:
        assert a[i:i+l] == b[j:j+l]
        print ''.join(a[i:i+l]),

    print
    print 'time:', t2-t1


def test(x):
    a = [str(i) for i in range(100000)]
    import random
    b = list(a)
    random.shuffle(b)
    print ' '.join(b)
    print
    x(a, b)

#test(x)
#x2('0123456789abc', '    01 89a 34 67')
#x2('AB 1 CD 1 AB P XY Q AB 1 CD 1 AB', 'AB 2 CD 2 AB Q XY P AB 2 CD 2 AB')
#x2('AjBjC', 'AjC')
#x2('01 2', '01')

try:
    import psyco
    psyco.bind(recurse)
except ImportError:
    pass

def find_matches(a, b):
    r = []
    recurse(a, b, 0, len(a), 0, len(b), r)
    return r

if __name__ == '__main__':
    import sys
    a = file(sys.argv[1]).readlines()
    b = file(sys.argv[2]).readlines()
    r = []
    recurse(a, b, 0, len(a), 0, len(b), r)
    i = 0
    j = 0
    for i2, j2, l in r:
        for i in range(i, i2):
            print '-' + a[i],
        for j in range(j, j2):
            print '+' + b[j],
        if l <= 6:
            for i in range(i2, i2 + l):
                print ' ' + a[i],
        else:
            if i2 > 0 or j2 > 0:
                for i in range(i2, i2 + 3):
                    print ' ' + a[i],
            print '@@ -'+str(i2+l-2)+' +'+str(j2+l-2)+' @@'
            for i in range(i2 + l - 3, i2 + l):
                print ' ' + a[i],
        i = i2 + l
        j = j2 + l
    for i in range(i, len(a)):
        print '-'  + a[i],
    for j in range(j, len(b)):
        print '+' + b[j],
