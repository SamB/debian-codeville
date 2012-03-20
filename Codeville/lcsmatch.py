#!/usr/bin/env python

# Written by Bram Cohen and Uoti Urpala

from bisect import bisect

def unique_lcs(a, b):
    # set index[line in a] = position of line in a unless
    # unless a is a duplicate, in which case it's set to None
    index = {}
    for i in xrange(len(a)):
        line = a[i]
        if line in index:
            index[line] = None
        else:
            index[line]= i
    # make btoa[i] = position of line i in a, unless
    # that line doesn't occur exactly once in both, 
    # in which case it's set to None
    btoa = [None] * len(b)
    index2 = {}
    for pos, line in enumerate(b):
        next = index.get(line)
        if next is not None:
            if line in index2:
                # unset the previous mapping, which we now know to
                # be invalid because the line isn't unique
                btoa[index2[line]] = None
                del index[line]
            else:
                index2[line] = pos
                btoa[pos] = next
    # this is the Patience sorting algorithm
    # see http://en.wikipedia.org/wiki/Patience_sorting
    backpointers = [None] * len(b)
    stacks = []
    lasts = []
    k = 0
    for bpos, apos in enumerate(btoa):
        if apos is None:
            continue
        # skip over solitary matches with no surrounding matching context
        if (bpos == 0 or apos == 0 or a[apos-1] != b[bpos-1]) and \
                (bpos == len(b)-1 or apos == len(a)-1 or a[apos+1] != b[bpos+1]):
            continue
        # as an optimization, check if the next line comes right after
        # the previous line, because usually it does
        if stacks and stacks[k] < apos and (k == len(stacks) - 1 or stacks[k+1] > apos):
            k += 1
        else:
            k = bisect(stacks, apos)
        if k > 0:
            backpointers[bpos] = lasts[k-1]
        if k < len(stacks):
            stacks[k] = apos
            lasts[k] = bpos
        else:
            stacks.append(apos)
            lasts.append(bpos)
    if len(lasts) == 0:
        return []
    result = []
    k = lasts[-1]
    while k is not None:
        result.append((btoa[k], k))
        k = backpointers[k]
    result.reverse()
    return result

def test_lcs():
    assert unique_lcs('', '') == []
    assert unique_lcs('a', 'a') == []
    assert unique_lcs('a', 'b') == []
    assert unique_lcs('ab', 'ab') == [(0, 0), (1, 1)]
    assert unique_lcs('abcde', 'cdeab') == [(2, 0), (3, 1), (4, 2)]
    assert unique_lcs('cdeab', 'abcde') == [(0, 2), (1, 3), (2, 4)]
    assert unique_lcs('abXde', 'abYde') == [(0, 0), (1, 1), (3, 3), (4, 4)]
    return

def recurse_matches(a, b, ahi, bhi, answer, maxrecursion, is_junk):
    # this will never happen normally, this check is to prevent DOS attacks
    if maxrecursion < 0:
        return
    oldlength = len(answer)
    # check for new matches after the last confirmed match ends
    if oldlength == 0:
        alo = 0
        blo = 0
    else:
        lasta, lastb, lastlen = answer[-1]
        alo = lasta + lastlen
        blo = lastb + lastlen
    if alo == ahi or blo == bhi:
        return
    last_enda = alo
    last_endb = blo
    last_checkb = blo
    # extend individual line matches into match sections
    for apos, bpos in unique_lcs(a[alo:ahi], b[blo:bhi]):
        apos += alo
        bpos += blo
        # don't overlap with an existing match or check something which
        # already got thrown out as junk
        if bpos < last_checkb or apos < last_enda:
            continue
        # extend line match as far in either direction as possible
        enda = apos + 1
        endb = bpos + 1
        while enda < ahi and endb < bhi and a[enda] == b[endb]:
            enda += 1
            endb += 1
        while apos > last_enda and bpos > last_endb and a[apos-1] == b[bpos-1]:
            apos -= 1
            bpos -= 1
        # mark what's been checked now, so even if it's junked it doesn't
        # have to be checked again
        last_checkb = endb
        # if this section is junk, skip it
        numreal = 0
        for k in xrange(apos, enda):
            if not is_junk(a[k]):
                numreal += 1
                if numreal >= 2:
                    break
        else:
            # Niklaus Wirth can bite me
            continue
        last_enda = enda
        last_endb = endb
        # find matches which come before the new match section
        # this can happen because there may be lines which weren't unique
        # in the whole file but are unique in the subsection
        recurse_matches(a, b, apos, bpos, answer, maxrecursion - 1, is_junk)
        answer.append((apos, bpos, enda - apos))
    if len(answer) > oldlength:
        # find matches between the last match and the end
        recurse_matches(a, b, ahi, bhi, answer, maxrecursion - 1, is_junk)
    # else: fall back to difflib (possibly a good idea, possibly not)

def default_is_junk(x):
    return len(x.strip()) <= 2

def find_matches(a, b, is_junk = default_is_junk):
    # single-line identical files match, despite being too short for
    # a real match if they were part of a larger file
    if a == b:
        return [(0, 0, len(a))]
    answer = []
    recurse_matches(a, b, len(a), len(b), answer, 10, is_junk)
    return answer

try:
    import psyco
    psyco.bind(unique_lcs, 0)
except ImportError:
    pass


# Stuff below here is for testing

def x(a, b):
    t1 = time.time()
    r = unique_lcs(a, b)
    t2 = time.time()
    #print r
    for i, j in r:
        assert a[i] == b[j]
        #print a[i]

    print
    print 'time:', t2-t1

def return_false(x):
    return False

def x2(a, b, is_junk = return_false):
    t1 = time.time()
    r = find_matches(a, b, is_junk)
    t2 = time.time()
    #print r
    for i, j, l in r:
        assert a[i:i+l] == b[j:j+l]
        #print ''.join(a[i:i+l]),

    print
    print 'time:', t2-t1


def completely_random_test(x):
    a = [str(i) for i in range(100000)]
    b = list(a)
    random.shuffle(b)
    #print ' '.join(b)
    #print
    x(a, b)

def random_with_ranges_test(x):
    d = [[str(i), str(i+1)] for i in xrange(0, 100000, 2)]
    c = list(d)
    random.shuffle(c)
    a = []
    for i in d:
        a.extend(i)
    b = []
    for i in c:
        b.extend(i)
    #print ' '.join(b)
    #print
    x(a, b)

def is_A_test(s):
    return s == 'A'

def test_lcsmatch():
    global random, time
    import random
    import time

    cur_time = time.time()
    random.seed(cur_time)

    print 'Seeded tests with %s' % (cur_time,)

    completely_random_test(x)
    random_with_ranges_test(x)
    x2('0123456789abc', '    01 89a 34 67')
    x2('AB 1 CD 1 AB P XY Q AB 1 CD 1 AB', 'AB 2 CD 2 AB Q XY P AB 2 CD 2 AB')
    x2('AjBjC', 'AjC')
    x2('AjC', 'AjBjC')
    x2('AjBjC', 'AjC', is_A_test)
    x2('AjC', 'AjBjC', is_A_test)
    x2('x', 'x')
    x2('01 2', '01')
    x2('ABPXYQAB', 'ABQXYPAB')
    return
