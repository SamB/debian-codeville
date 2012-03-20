# Written by Bram Cohen
# see LICENSE.txt for license information

from sets import Set

def find_conflict(local, local_line_points, local_points, remote, remote_line_points, remote_points):
    return _find_conflict(local, local_line_points, local_points, remote, remote_line_points, remote_points)[0]

def find_conflict_safe(local, local_line_points, local_points, remote, remote_line_points, remote_points):
    return find_conflict_multiple_safe([(local, local_line_points, local_points), (remote, remote_line_points, remote_points)])[:2]

def find_conflict_multiple_safe(precursors):
    olds = precursors[:]
    lines, line_points, points = olds.pop(0)
    while olds:
        nlines, nlinepoints, npoints = olds.pop(0)
        lines, line_points = _find_conflict(lines, line_points, points, nlines, nlinepoints, npoints)
        points = _union(points, npoints)
        if None in line_points:
            return (None, None, None)
    return lines, line_points, points

def _is_applied(ll, pdict):
    for i in ll:
        for j in i:
            if j in pdict:
                break
        else:
            return 0
    return 1

def _find_conflict(local, local_line_points, local_points, remote, remote_line_points, remote_points):
    assert len(local_line_points) == len(local) + 1
    assert len(remote_line_points) == len(remote) + 1

    lpdict = Set(local_points)
    for i in local_line_points:
        for point in i:
            assert point in lpdict

    rpdict = Set(remote_points)
    for i in remote_line_points:
        for point in i:
            assert point in rpdict

    result = []
    result_line_points = []
    lastl, lastr = 0, 0

    for (lpos, rpos, matchlen) in _find_matches(local, remote) + [(len(local), len(remote), 0)]:
        rapplied = _is_applied(remote_line_points[lastr:rpos + 1], lpdict)
        lapplied = _is_applied(local_line_points[lastl:lpos + 1], rpdict)
        if rapplied and not lapplied:
            result.extend(local[lastl:lpos + matchlen])
            result_line_points.extend(local_line_points[lastl:lpos + 1])
        elif lapplied and not rapplied:
            result.extend(remote[lastr:rpos + matchlen])
            result_line_points.extend(remote_line_points[lastr:rpos + 1])
        elif (lastl, lastr) == (len(local), len(remote)):
            result_line_points.append(_union(local_line_points[-1], remote_line_points[-1]))
        elif (lpos, rpos) == (0, 0):
            result.extend(local[:matchlen])
            result_line_points.append(_union(local_line_points[0], remote_line_points[0]))
        else:
            result.append((local[lastl:lpos], remote[lastr:rpos]))
            result.extend(local[lpos:lpos + matchlen])
            result_line_points.append((local_line_points[lastl:lpos + 1], remote_line_points[lastr:rpos + 1]))
            result_line_points.append(None)
        for i in xrange(1, matchlen):
            result_line_points.append(_union(local_line_points[lpos + i], remote_line_points[rpos + i]))
        lastl, lastr = lpos + matchlen, rpos + matchlen
    return (result, result_line_points)

def _union(la, lb):
    lc = la[:]
    for i in lb:
        if i not in lc:
            lc.append(i)
    return lc

def test_conflict():
    # conflict at begin
    ## local zero
    ### local wins
    assert _find_conflict(['aaa', 'bbb'], [['b'], ['a'], ['a']], ['a', 'b', 'c'], ['x', 'aaa', 'bbb'], [['a'], ['c'], ['a'], ['a']], ['a', 'c']) == (['aaa', 'bbb'], [['b'], ['a'], ['a']])
    ### remote wins
    #### remote violate at begin
    assert _find_conflict(['aaa', 'bbb'], [['a'], ['a'], ['a']], ['a'], ['x', 'y', 'aaa', 'bbb'], [['b'], ['a'], ['a'], ['a'], ['a']], ['a', 'b']) == (['x', 'y', 'aaa', 'bbb'], [['b'], ['a'], ['a'], ['a'], ['a']])
    #### remote violate at end
    assert _find_conflict(['aaa', 'bbb'], [['a'], ['a'], ['a']], ['a'], ['x', 'y', 'aaa', 'bbb'], [['a'], ['a'], ['b'], ['a'], ['a']], ['a', 'b']) == (['x', 'y', 'aaa', 'bbb'], [['a'], ['a'], ['b'], ['a'], ['a']])
    ### neither win
    #### remote violate at begin
    assert _find_conflict(['aaa', 'bbb'], [['c'], ['a'], ['a']], ['a', 'c'], ['x', 'y', 'aaa', 'bbb'], [['b'], ['a'], ['a'], ['a'], ['a']], ['a', 'b']) == ([([], ['x', 'y']), 'aaa', 'bbb'], [([['c']], [['b'], ['a'], ['a']]), None, ['a'], ['a']])
    #### remote violate at end
    assert _find_conflict(['aaa', 'bbb'], [['c'], ['a'], ['a']], ['a', 'c'], ['x', 'y', 'aaa', 'bbb'], [['a'], ['a'], ['b'], ['a'], ['a']], ['a', 'b']) == ([([], ['x', 'y']), 'aaa', 'bbb'], [([['c']], [['a'], ['a'], ['b']]), None, ['a'], ['a']])

    ## remote zero
    ### local wins
    assert _find_conflict(['x', 'aaa', 'bbb'], [['a'], ['b'], ['a'], ['a']], ['a', 'b'], ['aaa', 'bbb'], [['a'], ['a'], ['a']], ['a']) == (['x', 'aaa', 'bbb'], [['a'], ['b'], ['a'], ['a']])
    ### remote wins
    assert _find_conflict(['x', 'aaa', 'bbb'], [['a'], ['b'], ['a'], ['a']], ['a', 'b'], ['aaa', 'bbb'], [['c'], ['a'], ['a']], ['a', 'b', 'c']) == (['aaa', 'bbb'], [['c'], ['a'], ['a']])
    ### neither win
    #### local violate at begin
    assert _find_conflict(['x', 'y', 'aaa', 'bbb'], [['b'], ['a'], ['a'], ['a'], ['a']], ['a', 'b'], ['aaa', 'bbb'], [['c'], ['a'], ['a']], ['a', 'c']) == ([(['x', 'y'], []), 'aaa', 'bbb'], [([['b'], ['a'], ['a']], [['c']]), None, ['a'], ['a']])
    #### local violate at end
    assert _find_conflict(['x', 'y', 'aaa', 'bbb'], [['a'], ['a'], ['b'], ['a'], ['a']], ['a', 'b'], ['aaa', 'bbb'], [['c'], ['a'], ['a']], ['a', 'c']) == ([(['x', 'y'], []), 'aaa', 'bbb'], [([['a'], ['a'], ['b']], [['c']]), None, ['a'], ['a']])

    ## neither zero
    ### local wins
    assert _find_conflict(['x', 'y', 'aaa', 'bbb'], [['a'], ['b'], ['a'], ['a'], ['a']], ['a', 'b'], ['z', 'w', 'aaa', 'bbb'], [['a'], ['a'], ['a'], ['a'], ['a']], ['a']) == (['x', 'y', 'aaa', 'bbb'], [['a'], ['b'], ['a'], ['a'], ['a']])
    ### remote wins
    #### violates at beginning
    assert _find_conflict(['x', 'y', 'aaa', 'bbb'], [['a'], ['a'], ['a'], ['a'], ['a']], ['a'], ['z', 'w', 'aaa', 'bbb'], [['b'], ['a'], ['a'], ['a'], ['a']], ['a', 'b']) == (['z', 'w', 'aaa', 'bbb'], [['b'], ['a'], ['a'], ['a'], ['a']])
    #### violates at end
    assert _find_conflict(['x', 'y', 'aaa', 'bbb'], [['a'], ['a'], ['a'], ['a'], ['a']], ['a'], ['z', 'w', 'aaa', 'bbb'], [['a'], ['a'], ['b'], ['a'], ['a']], ['a', 'b']) == (['z', 'w', 'aaa', 'bbb'], [['a'], ['a'], ['b'], ['a'], ['a']])
    ### neither win
    #### violates at begin
    assert _find_conflict(['x', 'y', 'aaa', 'bbb'], [['c'], ['a'], ['a'], ['a'], ['a']], ['a', 'c'], ['z', 'w', 'aaa', 'bbb'], [['b'], ['a'], ['a'], ['a'], ['a']], ['a', 'b']) == ([(['x', 'y'], ['z', 'w']), 'aaa', 'bbb'], [([['c'], ['a'], ['a']], [['b'], ['a'], ['a']]), None, ['a'], ['a']])
    #### violates at end
    assert _find_conflict(['x', 'y', 'aaa', 'bbb'], [['a'], ['a'], ['c'], ['a'], ['a']], ['a', 'c'], ['z', 'w', 'aaa', 'bbb'], [['a'], ['a'], ['b'], ['a'], ['a']], ['a', 'b']) == ([(['x', 'y'], ['z', 'w']), 'aaa', 'bbb'], [([['a'], ['a'], ['c']], [['a'], ['a'], ['b']]), None, ['a'], ['a']])


    # conflict in middle
    ## local zero
    ### local wins
    assert _find_conflict(['aaa', 'bbb', 'ccc', 'ddd'], [['a'], ['a'], ['b'], ['a'], ['a']], ['a', 'b', 'c'], ['aaa', 'bbb', 'x', 'ccc', 'ddd'], [['a'], ['a'], ['c'], ['c'], ['a'], ['a']], ['a', 'c']) == (['aaa', 'bbb', 'ccc', 'ddd'], [['a'], ['a'], ['b'], ['a'], ['a']])
    ### remote wins
    #### violate at begin
    assert _find_conflict(['aaa', 'bbb', 'ccc', 'ddd'], [['a'], ['a'], ['b'], ['a'], ['a']], ['a', 'b'], ['aaa', 'bbb', 'x', 'ccc', 'ddd'], [['a'], ['a'], ['c'], ['a'], ['a'], ['a']], ['a', 'b', 'c']) == (['aaa', 'bbb', 'x', 'ccc', 'ddd'], [['a'], ['a'], ['c'], ['a'], ['a'], ['a']])
    #### violate at end
    assert _find_conflict(['aaa', 'bbb', 'ccc', 'ddd'], [['a'], ['a'], ['b'], ['a'], ['a']], ['a', 'b'], ['aaa', 'bbb', 'x', 'ccc', 'ddd'], [['a'], ['a'], ['a'], ['c'], ['a'], ['a']], ['a', 'b', 'c']) == (['aaa', 'bbb', 'x', 'ccc', 'ddd'], [['a'], ['a'], ['a'], ['c'], ['a'], ['a']])
    ### neither win
    #### remote violate at begin
    assert _find_conflict(['aaa', 'bbb', 'ccc', 'ddd'], [['a'], ['a'], ['b'], ['a'], ['a']], ['a', 'b'], ['aaa', 'bbb', 'x', 'ccc', 'ddd'], [['a'], ['a'], ['c'], ['a'], ['a'], ['a']], ['a', 'c']) == (['aaa', 'bbb', ([], ['x']), 'ccc', 'ddd'], [['a'], ['a'], ([['b']], [['c'], ['a']]), None, ['a'], ['a']])
    #### remote violate at end
    assert _find_conflict(['aaa', 'bbb', 'ccc', 'ddd'], [['a'], ['a'], ['b'], ['a'], ['a']], ['a', 'b'], ['aaa', 'bbb', 'x', 'ccc', 'ddd'], [['a'], ['a'], ['a'], ['c'], ['a'], ['a']], ['a', 'c']) == (['aaa', 'bbb', ([], ['x']), 'ccc', 'ddd'], [['a'], ['a'], ([['b']], [['a'], ['c']]), None, ['a'], ['a']])

    ## remote zero
    ### local wins
    assert _find_conflict(['aaa', 'bbb', 'x', 'ccc', 'ddd'], [['a'], ['a'], ['x'], ['x'], ['a'], ['a']], ['a', 'x', 'c'], ['aaa', 'bbb', 'ccc', 'ddd'], [['a'], ['a'], ['c'], ['a'], ['a']], ['a', 'c']) == (['aaa', 'bbb', 'x', 'ccc', 'ddd'], [['a'], ['a'], ['x'], ['x'], ['a'], ['a']])
    ### remote wins
    assert _find_conflict(['aaa', 'bbb', 'x', 'ccc', 'ddd'], [['a'], ['a'], ['x'], ['x'], ['a'], ['a']], ['a', 'x'], ['aaa', 'bbb', 'ccc', 'ddd'], [['a'], ['a'], ['c'], ['a'], ['a']], ['a', 'x', 'c']) == (['aaa', 'bbb', 'ccc', 'ddd'], [['a'], ['a'], ['c'], ['a'], ['a']])
    ### neither win
    #### local violate at begin
    assert _find_conflict(['aaa', 'bbb', 'x', 'ccc', 'ddd'], [['a'], ['a'], ['x'], ['a'], ['a'], ['a']], ['a', 'x'], ['aaa', 'bbb', 'ccc', 'ddd'], [['a'], ['a'], ['c'], ['a'], ['a']], ['a', 'c']) == (['aaa', 'bbb', (['x'], []), 'ccc', 'ddd'], [['a'], ['a'], ([['x'], ['a']], [['c']]), None, ['a'], ['a']])
    #### local violate at end
    assert _find_conflict(['aaa', 'bbb', 'x', 'ccc', 'ddd'], [['a'], ['a'], ['a'], ['x'], ['a'], ['a']], ['a', 'x'], ['aaa', 'bbb', 'ccc', 'ddd'], [['a'], ['a'], ['c'], ['a'], ['a']], ['a', 'c']) == (['aaa', 'bbb', (['x'], []), 'ccc', 'ddd'], [['a'], ['a'], ([['a'], ['x']], [['c']]), None, ['a'], ['a']])

    ## neither zero
    ### local wins
    assert _find_conflict(['aaa', 'bbb', 'x', 'ccc', 'ddd'], [['a'], ['a'], ['x'], ['x'], ['a'], ['a']], ['a', 'x', 'y'], ['aaa', 'bbb', 'y', 'ccc', 'ddd'], [['a'], ['a'], ['y'], ['y'], ['a'], ['a']], ['a', 'y']) == (['aaa', 'bbb', 'x', 'ccc', 'ddd'], [['a'], ['a'], ['x'], ['x'], ['a'], ['a']])
    ### remote wins
    #### violate at begin
    assert _find_conflict(['aaa', 'bbb', 'x', 'ccc', 'ddd'], [['a'], ['a'], ['x'], ['x'], ['a'], ['a']], ['a', 'x'], ['aaa', 'bbb', 'y', 'ccc', 'ddd'], [['a'], ['a'], ['y'], ['a'], ['a'], ['a']], ['a', 'x', 'y']) == (['aaa', 'bbb', 'y', 'ccc', 'ddd'], [['a'], ['a'], ['y'], ['a'], ['a'], ['a']])
    #### violate at end
    assert _find_conflict(['aaa', 'bbb', 'x', 'ccc', 'ddd'], [['a'], ['a'], ['x'], ['x'], ['a'], ['a']], ['a', 'x'], ['aaa', 'bbb', 'y', 'ccc', 'ddd'], [['a'], ['a'], ['a'], ['y'], ['a'], ['a']], ['a', 'x', 'y']) == (['aaa', 'bbb', 'y', 'ccc', 'ddd'], [['a'], ['a'], ['a'], ['y'], ['a'], ['a']])
    ### neither win
    #### violates at begin
    assert _find_conflict(['aaa', 'bbb', 'x', 'ccc', 'ddd'], [['a'], ['a'], ['x'], ['a'], ['a'], ['a']], ['a', 'x'], ['aaa', 'bbb', 'y', 'ccc', 'ddd'], [['a'], ['a'], ['y'], ['a'], ['a'], ['a']], ['a', 'y']) == (['aaa', 'bbb', (['x'], ['y']), 'ccc', 'ddd'], [['a'], ['a'], ([['x'], ['a']], [['y'], ['a']]), None, ['a'], ['a']])
    #### violates at end
    assert _find_conflict(['aaa', 'bbb', 'x', 'ccc', 'ddd'], [['a'], ['a'], ['a'], ['x'], ['a'], ['a']], ['a', 'x'], ['aaa', 'bbb', 'y', 'ccc', 'ddd'], [['a'], ['a'], ['a'], ['y'], ['a'], ['a']], ['a', 'y']) == (['aaa', 'bbb', (['x'], ['y']), 'ccc', 'ddd'], [['a'], ['a'], ([['a'], ['x']], [['a'], ['y']]), None, ['a'], ['a']])



    # conflict at end
    ## local zero
    ### local wins
    assert _find_conflict(['aaa', 'bbb'], [['a'], ['a'], ['b']], ['a', 'b', 'x'], ['aaa', 'bbb', 'x'], [['a'], ['a'], ['x'], ['x']], ['a', 'x']) == (['aaa', 'bbb'], [['a'], ['a'], ['b']])
    ### remote wins
    #### violate at begin
    assert _find_conflict(['aaa', 'bbb'], [['a'], ['a'], ['b']], ['a', 'b'], ['aaa', 'bbb', 'x'], [['a'], ['a'], ['x'], ['a']], ['a', 'b', 'x']) == (['aaa', 'bbb', 'x'], [['a'], ['a'], ['x'], ['a']])
    #### violate at end
    assert _find_conflict(['aaa', 'bbb'], [['a'], ['a'], ['b']], ['a', 'b'], ['aaa', 'bbb', 'x'], [['a'], ['a'], ['a'], ['x']], ['a', 'b', 'x']) == (['aaa', 'bbb', 'x'], [['a'], ['a'], ['a'], ['x']])
    ### neither win
    #### remote violate at begin
    assert _find_conflict(['aaa', 'bbb'], [['a'], ['a'], ['b']], ['a', 'b'], ['aaa', 'bbb', 'x'], [['a'], ['a'], ['x'], ['a']], ['a', 'x']) == (['aaa', 'bbb', ([], ['x'])], [['a'], ['a'], ([['b']], [['x'], ['a']]), None])
    #### remote violate at end
    assert _find_conflict(['aaa', 'bbb'], [['a'], ['a'], ['b']], ['a', 'b'], ['aaa', 'bbb', 'x'], [['a'], ['a'], ['a'], ['x']], ['a', 'x']) == (['aaa', 'bbb', ([], ['x'])], [['a'], ['a'], ([['b']], [['a'], ['x']]), None])

    ## remote zero
    ### local wins
    assert _find_conflict(['aaa', 'bbb', 'x'], [['a'], ['a'], ['x'], ['x']], ['a', 'b', 'x'], ['aaa', 'bbb'], [['a'], ['a'], ['b']], ['a', 'b']) == (['aaa', 'bbb', 'x'], [['a'], ['a'], ['x'], ['x']])
    ### remote wins
    assert _find_conflict(['aaa', 'bbb', 'x'], [['a'], ['a'], ['x'], ['x']], ['a', 'x'], ['aaa', 'bbb'], [['a'], ['a'], ['b']], ['a', 'b', 'x']) == (['aaa', 'bbb'], [['a'], ['a'], ['b']])
    ### neither win
    #### local violate at begin
    assert _find_conflict(['aaa', 'bbb', 'x'], [['a'], ['a'], ['x'], ['a']], ['a', 'x'], ['aaa', 'bbb'], [['a'], ['a'], ['b']], ['a', 'b']) == (['aaa', 'bbb', (['x'], [])], [['a'], ['a'], ([['x'], ['a']], [['b']]), None])
    #### local violate at end
    assert _find_conflict(['aaa', 'bbb', 'x'], [['a'], ['a'], ['a'], ['x']], ['a', 'x'], ['aaa', 'bbb'], [['a'], ['a'], ['b']], ['a', 'b']) == (['aaa', 'bbb', (['x'], [])], [['a'], ['a'], ([['a'], ['x']], [['b']]), None])

    ## neither zero
    ### local wins
    assert _find_conflict(['aaa', 'bbb', 'x'], [['a'], ['a'], ['x'], ['x']], ['a', 'x', 'y'], ['aaa', 'bbb', 'y'], [['a'], ['a'], ['y'], ['y']], ['a', 'y']) == (['aaa', 'bbb', 'x'], [['a'], ['a'], ['x'], ['x']])
    ### remote wins
    assert _find_conflict(['aaa', 'bbb', 'x'], [['a'], ['a'], ['x'], ['x']], ['a', 'x'], ['aaa', 'bbb', 'y'], [['a'], ['a'], ['y'], ['y']], ['a', 'x', 'y']) == (['aaa', 'bbb', 'y'], [['a'], ['a'], ['y'], ['y']])
    ### neither win
    #### violates at begin
    assert _find_conflict(['aaa', 'bbb', 'x'], [['a'], ['a'], ['x'], ['a']], ['a', 'x'], ['aaa', 'bbb', 'y'], [['a'], ['a'], ['y'], ['a']], ['a', 'y']) == (['aaa', 'bbb', (['x'], ['y'])], [['a'], ['a'], ([['x'], ['a']], [['y'], ['a']]), None])
    #### violates at end
    assert _find_conflict(['aaa', 'bbb', 'x'], [['a'], ['a'], ['a'], ['x']], ['a', 'x'], ['aaa', 'bbb', 'y'], [['a'], ['a'], ['a'], ['y']], ['a', 'y']) == (['aaa', 'bbb', (['x'], ['y'])], [['a'], ['a'], ([['a'], ['x']], [['a'], ['y']]), None])



    # whole file conflict
    ## local zero
    ### local wins
    assert _find_conflict([], [['a']], ['a', 'b'], ['x'], [['b'], ['b']], ['b']) == ([], [['a']])
    ### remote wins
    #### violate at begin
    assert _find_conflict([], [['a']], ['a'], ['x'], [['b'], ['a']], ['a', 'b']) == (['x'], [['b'], ['a']])
    #### violate at end
    assert _find_conflict([], [['a']], ['a'], ['x'], [['a'], ['b']], ['a', 'b']) == (['x'], [['a'], ['b']])
    ### neither win
    #### remote violate at begin
    assert _find_conflict([], [['a']], ['a', 'c'], ['x'], [['b'], ['c']], ['b', 'c']) == ([([], ['x'])], [([['a']], [['b'], ['c']]), None])
    #### remote violate at end
    assert _find_conflict([], [['a']], ['a', 'c'], ['x'], [['c'], ['b']], ['b', 'c']) == ([([], ['x'])], [([['a']], [['c'], ['b']]), None])

    ## remote zero
    ### local wins
    assert _find_conflict(['a'], [['a'], ['a']], ['a', 'b'], [], [['b']], ['b']) == (['a'], [['a'], ['a']])
    ### remote wins
    assert _find_conflict(['a'], [['a'], ['a']], ['a'], [], [['b']], ['a', 'b']) == ([], [['b']])
    ### neither win
    #### local violate at begin
    assert _find_conflict(['a'], [['a'], ['c']], ['a', 'c'], [], [['b']], ['b', 'c']) == ([(['a'], [])], [([['a'], ['c']], [['b']]), None])
    #### local violate at end
    assert _find_conflict(['a'], [['c'], ['a']], ['a', 'c'], [], [['b']], ['b', 'c']) == ([(['a'], [])], [([['c'], ['a']], [['b']]), None])

    ## neither zero
    ### local wins
    assert _find_conflict(['a'], [['a'], ['a']], ['a', 'b'], ['b'], [['b'], ['b']], ['b']) == (['a'], [['a'], ['a']])
    ### remote wins
    assert _find_conflict(['a'], [['a'], ['a']], ['a'], ['b'], [['b'], ['b']], ['a', 'b']) == (['b'], [['b'], ['b']])
    ### neither win
    #### violate at begin
    assert _find_conflict(['a'], [['a'], ['c']], ['a', 'c'], ['b'], [['b'], ['c']], ['c', 'b']) == ([(['a'], ['b'])], [([['a'], ['c']], [['b'], ['c']]), None])
    #### violate at end
    assert _find_conflict(['a'], [['c'], ['a']], ['a', 'c'], ['b'], [['c'], ['b']], ['c', 'b']) == ([(['a'], ['b'])], [([['c'], ['a']], [['c'], ['b']]), None])
    
    # union counts for either
    assert _find_conflict(['aaa', 'bbb'], [['a'], ['a', 'b'], ['a']], ['a', 'b'], ['ccc', 'ddd'], [['a'], ['c'], ['a']], ['a', 'c']) == (['ccc', 'ddd'], [['a'], ['c'], ['a']])

    
    # coincidental matches
    ## begin
    assert _find_conflict(['aaa', 'bbb'], [['b'], ['a'], ['a']], ['a', 'b'], ['aaa', 'bbb'], [['c'], ['a'], ['a']], ['a', 'c']) == (['aaa', 'bbb'], [['b', 'c'], ['a'], ['a']])
    ## middle
    assert _find_conflict(['aaa', 'bbb'], [['a'], ['b'], ['a']], ['a', 'b'], ['aaa', 'bbb'], [['a'], ['c'], ['a']], ['a', 'c']) == (['aaa', 'bbb'], [['a'], ['b', 'c'], ['a']])
    ## end
    assert _find_conflict(['aaa', 'bbb'], [['a'], ['a'], ['b']], ['a', 'b'], ['aaa', 'bbb'], [['a'], ['a'], ['c']], ['a', 'c']) == (['aaa', 'bbb'], [['a'], ['a'], ['b', 'c']])

    # multiple conflicts, different resolutions
    assert _find_conflict(['x', 'aaa', 'bbb', 'y', 'ccc', 'ddd', 'z'], [['x'], ['x'], ['a'], ['y'], ['y'], ['a'], ['z'], ['z']], ['a', 'x', 'y', 'z', 'p'], ['p', 'aaa', 'bbb', 'q', 'ccc', 'ddd', 'r'], [['p'], ['p'], ['a'], ['q'], ['q'], ['a'], ['r'], ['r']], ['a', 'p', 'q', 'r', 'z']) == (['x', 'aaa', 'bbb', (['y'], ['q']), 'ccc', 'ddd', 'r'], [['x'], ['x'], ['a'], ([['y'], ['y']], [['q'], ['q']]), None, ['a'], ['r'], ['r']])



def _find_matches(local, remote):
    if local == remote:
        return [(0, 0, len(local))]
    bdict = {}
    for i in xrange(0, len(remote)):
        bdict.setdefault(remote[i], []).append(i)
    return _fm(local, 0, len(local), remote, 0, len(remote), bdict)

def _fm(linesa, abegin, aend, linesb, bbegin, bend, bdict):
    bestlen = 0
    for i in xrange(abegin, aend):
        if i + bestlen >= aend:
            break
        for j in bdict.get(linesa[i], []):
            if j < bbegin:
                continue
            if j + bestlen >= bend:
                break
            if i > abegin and j > bbegin and linesa[i - 1] == linesb[j - 1]:
                continue
            if linesa[i + bestlen] != linesb[j + bestlen]:
                continue
            count = 0
            k = 0
            while i + k < aend and j + k < bend and linesa[i + k] == linesb[j + k]:
                if count < 2 and len(linesa[i + k].strip()) > 2:
                    count += 1
                k += 1
            if k > bestlen and count >= 2:
                besta = i
                bestb = j
                bestlen = k
                if i + bestlen >= aend:
                    break
    if bestlen == 0:
        return []
    return _fm(linesa, abegin, besta, linesb, bbegin, bestb, bdict) + [(besta, bestb, bestlen)] + _fm(linesa, besta + bestlen, aend, linesb, bestb + bestlen, bend, bdict)

def test_find_matches():
    assert _find_matches(['x', 'aaa', 'y'], ['z', 'aaa', 'y']) == []
    assert _find_matches(['a'], ['a']) == [(0, 0, 1)]
    assert _find_matches(['aaa', 'aaa', 'ppp'], ['aaa', 'aaa', 'qqq']) == [(0, 0, 2)]
    assert _find_matches(['ppp', 'aaa', 'aaa'], ['qqq', 'aaa', 'aaa']) == [(1, 1, 2)]
    assert _find_matches(['ppp', 'aaa', 'aaa', 'ppp'], ['qqq', 'aaa', 'aaa', 'qqq']) == [(1, 1, 2)]
    assert _find_matches(['aaa', 'aaa', 'bbb', 'ccc', 'aaa', 'aaa', 'aaa'], ['aaa', 'aaa', 'bbb', 'aaa', 'aaa', 'aaa', 'ccc']) == [(0, 0, 3), (4, 3, 3)]
    assert _find_matches(['aaa', 'aaa', 'bbb', 'aaa', 'aaa', 'aaa', 'ccc'], ['aaa', 'aaa', 'bbb', 'ccc', 'aaa', 'aaa', 'aaa']) == [(0, 0, 3), (3, 4, 3)]
    assert _find_matches(['aaa', 'aaa', 'aaa', 'bbb', 'ccc', 'aaa', 'aaa', 'aaa'], ['bbb', 'aaa', 'aaa', 'aaa', 'ccc', 'aaa', 'aaa', 'aaa']) == [(0, 1, 3), (4, 4, 4)]
    assert _find_matches(['bbb', 'aaa', 'aaa', 'aaa', 'ccc', 'aaa', 'aaa', 'aaa'], ['aaa', 'aaa', 'aaa', 'bbb', 'ccc', 'aaa', 'aaa', 'aaa']) == [(1, 0, 3), (4, 4, 4)]
    assert _find_matches(['aaa', 'aaa', 'bbb', 'bbb'], ['bbb', 'bbb', 'aaa', 'aaa']) == [(0, 2, 2)]
    assert _find_matches(['aaa', 'aaa', 'bbb', 'bbb', 'bbb'], ['bbb', 'bbb', 'bbb', 'aaa', 'aaa']) == [(2, 0, 3)]

def find_resolution(precursors, resolution):
    all = []
    for i in xrange(len(precursors)):
        for (matchold, matchnew, matchlen) in _find_matches(precursors[i][0], resolution):
            all.append((matchlen, i, matchold, matchnew))
    all.sort()
    ms = [[] for i in xrange(len(precursors))]
    result = [[] for i in xrange(len(resolution) + 1)]
    for (matchlen, i, matchold, matchnew) in all:
        pre = precursors[i][0]
        pre_line_points = precursors[i][1]
        (begina, beginb, mlen) = (matchold + 1, matchnew + 1, matchlen - 1)
        if (matchold, matchnew) == (0, 0):
            (begina, beginb, mlen) = (0, 0, mlen + 1)
        if (matchold + matchlen, matchnew + matchlen) == (len(pre), len(resolution)):
            mlen += 1
        for j in xrange(mlen):
            for v in pre_line_points[begina + j]:
                if v not in result[beginb + j]:
                    result[beginb + j].append(v)
        ms[i].append((matchold, matchnew, matchlen))
    #for x in result:
    #    x.sort()
    for x in ms:
        x.sort()
    hits = [False] * len(resolution)
    for i in xrange(len(ms)):
        for (matchold, matchnew, matchlen) in ms[i]:
            for j in xrange(matchlen):
                hits[matchnew + j] = True
    newlines = []
    pos = 0
    while pos < len(hits):
        if hits[pos]:
            pos += 1
        else:
            n = []
            j = pos
            while j < len(hits) and not hits[j]:
                n.append(resolution[j])
                j += 1
            newlines.append((pos, n))
            pos = j
    return (result, ms, newlines)

def test_resolve():
    assert find_resolution([(['aaa', 'bbb', 'ccc'], [['x'], ['y'], ['z'], ['w']]), (['aaa', 'bbb', 'ccc'], [['p'], ['q'], ['z'], ['w']])], ['aaa', 'bbb', 'ccc'])[0] == [['p', 'x'], ['q', 'y'], ['z'], ['w']]
    assert find_resolution([(['aaa', 'bbb', 'qqq'], [['a'], ['b'], ['c'], ['d']]), (['qqq', 'bbb', 'ccc'], [['e'], ['f'], ['g'], ['h']])], ['aaa', 'bbb', 'ccc'])[0] == [['a'], ['b'], ['g'], ['h']]
    assert find_resolution([(['aaa'], [['a'], ['a']])], ['bbb'])[0] == [[], []]
    assert find_resolution([(['aaa'], [['x'], ['x']]), (['aaa'], [['x'], ['x']])], ['aaa'])[1] == [[(0, 0, 1)], []]
    assert find_resolution([(['aaa'], [['x'], ['x']]), (['aaa'], [['x'], ['y']])], ['aaa'])[1] == [[(0, 0, 1)], [(0, 0, 1)]]
    assert find_resolution([(['aaa', 'aaa'], [['x'], ['x'], ['x']])], ['aaa', 'aaa', 'bbb', 'ccc'])[2] == [(2, ['bbb', 'ccc'])]
    assert find_resolution([(['aaa', 'aaa'], [['x'], ['x'], ['x']])], ['bbb', 'ccc', 'aaa', 'aaa'])[2] == [(0, ['bbb', 'ccc'])]
    assert find_resolution([(['aaa', 'aaa'], [['x'], ['x'], ['x']])], ['bbb', 'ccc'])[2] == [(0, ['bbb', 'ccc'])]

def replay(precursors, matches, newlines, current_point):
    resultlength = 0
    for i in matches:
        if not len(i):
            continue
        matchold, matchnew, matchlen = i[-1]
        if matchnew + matchlen > resultlength:
            resultlength = matchnew + matchlen
    try:
        foo = newlines[-1][0] + len(newlines[-1][1])
        if foo > resultlength:
            resultlength = foo
    except IndexError:
        pass
    r = [None] * resultlength
    rpoints = [None] * (resultlength + 1)

    def addrp(pos, vs, rpoints = rpoints):
        if rpoints[pos] is None:
            rpoints[pos] = vs[:]
            return
        rp = rpoints[pos]
        for v in vs:
            if v not in rp:
                rp.append(v)
        return

    delpoints = [0]
    for i in xrange(len(matches)):
        try:
            if matches[i][0][0] == 0 and matches[i][0][1] == 0:
                addrp(0, precursors[i][1][0])
        except IndexError:
            pass
        try:
            matchold, matchnew, matchlen = matches[i][-1]
            if matchnew + matchlen == resultlength and matchold + matchlen == len(precursors[i][0]):
                addrp(resultlength, precursors[i][1][-1])
        except IndexError:
            pass
        for (matchold, matchnew, matchlen) in matches[i]:
            foo = precursors[i][0]
            if matchold + matchlen > len(foo):
                raise MergeError, 'match too long'
            for j in xrange(matchlen):
                pos = matchnew + j
                v = foo[matchold + j]
                if r[pos] is not None and r[pos] != v:
                    raise MergeError, 'conflicting values'
                r[pos] = v
            delpoints.append(matchnew + matchlen)
            foo = precursors[i][1]
            for j in xrange(1, matchlen):
                addrp(matchnew + j, foo[matchold + j])
    foo = [current_point]
    for (begin, lines) in newlines:
        for i in xrange(len(lines)):
            if r[begin + i] is not None:
                raise MergeError, 'match covers newline'
            r[begin + i] = lines[i]
        for i in xrange(len(lines) + 1):
            addrp(begin + i, foo)
    for index in delpoints:
        if rpoints[index] is None:
            addrp(index, foo)
    if None in r:
        raise MergeError, 'unfilled line'
    if None in rpoints:
        raise MergeError, 'unset line point'
    return (r, rpoints)

class MergeError(StandardError):
    pass

def test_replay():
    assert replay([(['aaa', 'bbb'], [['a'], ['b'], ['c']])], [[(0, 0, 2)]], [(2, ['ccc', 'ddd'])], 'z') == (['aaa', 'bbb', 'ccc', 'ddd'], [['a'], ['b'], ['z'], ['z'], ['z']])
    assert replay([(['aaa', 'bbb'], [['a'], ['b'], ['c']])], [[(0, 2, 2)]], [(0, ['ccc', 'ddd'])], 'z') == (['ccc', 'ddd', 'aaa', 'bbb'], [['z'], ['z'], ['z'], ['b'], ['c']])
    assert replay([(['aaa', 'bbb', 'ccc', 'ddd'], [['a'], ['b'], ['c'], ['d'], ['e']]), (['bbb', 'ccc', 'ddd', 'eee'], [['f'], ['c'], ['g'], ['h'], ['i']])], [[(0, 0, 4)], [(0, 1, 4)]], [], 'z') == (['aaa', 'bbb', 'ccc', 'ddd', 'eee'], [['a'], ['b'], ['c'], ['d', 'g'], ['h'], ['i']])
    assert replay([(['aaa', 'bbb', 'ccc', 'ddd', 'eee'], [['a'], ['a'], ['a'], ['a'], ['a'], ['a']])], [[(0, 0, 2), (3, 2, 2)]], [], 'z') == (['aaa', 'bbb', 'ddd', 'eee'], [['a'], ['a'], ['z'], ['a'], ['a']])
    assert replay([(['aaa', 'bbb', 'ccc', 'ddd'], [['a'], ['a'], ['a'], ['a'], ['a']])], [[(2, 0, 2)]], [], 'z') == (['ccc', 'ddd'], [['z'], ['a'], ['a']])
    assert replay([(['aaa', 'bbb', 'ccc', 'ddd'], [['a'], ['a'], ['a'], ['a'], ['a']])], [[(0, 0, 2)]], [], 'z') == (['aaa', 'bbb'], [['a'], ['a'], ['z']])

try:
    import psyco
    psyco.bind(replay)
    psyco.bind(_find_matches)
except ImportError:
    pass
