# Written by Bram Cohen
# see LICENSE.txt for license information

from lcsmatch import find_matches
from sets import Set

class SelectLinePoint:
    def __init__(self):
        self.lp = Set()
        self.rp = Set()
        return

    def push(self, points):
        self.lp.union_update(self.rp)
        self.rp = Set(points)
        return

    # this behaves like the general merge algorithm on a point by point basis
    def select(self, p1, p2):
        assert p2 is not None
        if p1 is None:
            return p2
        elif p1 == p2:
            return p1
        elif p2 in self.lp and p1 not in self.rp:
            return p1
        elif p2 not in self.lp and p1 in self.rp:
            return p2
        # it's a coincidental match, canonically choose the larger
        return max(p1, p2)

    def dump(self):
        return [p for p in self.lp | self.rp]


def find_conflict(local, local_line_points, local_points, remote, remote_line_points, remote_points):
    return _find_conflict(local, local_line_points, local_points, remote, remote_line_points, remote_points)[0]

def find_conflict_safe(local, local_line_points, local_points, remote, remote_line_points, remote_points):
    return find_conflict_multiple_safe([(local, local_line_points, local_points), (remote, remote_line_points, remote_points)])[:2]

def find_conflict_multiple_safe(precursors):
    olds = precursors[:]
    lines, line_points, points = olds.pop(0)
    while olds:
        nlines, nlinepoints, npoints = olds.pop(0)
        lines, line_points, points = _find_conflict(lines, line_points, points, nlines, nlinepoints, npoints)
        if None in line_points:
            return (None, None, None)
    return lines, line_points, points

def _is_applied(ll, pdict):
    for i in ll:
        if i not in pdict:
            return 0
    return 1

def _find_conflict(local, local_line_points, local_points, remote, remote_line_points, remote_points):
    assert len(local_line_points) == len(local) + 1
    assert len(remote_line_points) == len(remote) + 1

    lpdict = Set(local_points)
    for point in local_line_points:
        assert point in lpdict

    rpdict = Set(remote_points)
    for point in remote_line_points:
        assert point in rpdict

    result = []
    result_line_points = []
    lastl, lastr = 0, 0

    selector = SelectLinePoint()
    selector.push(local_points)
    selector.push(remote_points)

    for (lpos, rpos, matchlen) in find_matches(local, remote) + [(len(local), len(remote), 0)]:
        rapplied = _is_applied(remote_line_points[lastr:rpos + 1], lpdict)
        lapplied = _is_applied(local_line_points[lastl:lpos + 1], rpdict)
        if rapplied and not lapplied:
            result.extend(local[lastl:lpos + matchlen])
            result_line_points.extend(local_line_points[lastl:lpos + 1])
        elif lapplied and not rapplied:
            result.extend(remote[lastr:rpos + matchlen])
            result_line_points.extend(remote_line_points[lastr:rpos + 1])
        elif (lastl, lastr) == (len(local), len(remote)):
            result_line_points.append(selector.select(local_line_points[-1], remote_line_points[-1]))
        elif (lpos, rpos) == (0, 0):
            result.extend(local[:matchlen])
            result_line_points.append(selector.select(local_line_points[0], remote_line_points[0]))
        else:
            result.append((local[lastl:lpos], remote[lastr:rpos]))
            result.extend(local[lpos:lpos + matchlen])
            result_line_points.append((local_line_points[lastl:lpos + 1], remote_line_points[lastr:rpos + 1]))
            result_line_points.append(None)
        for i in xrange(1, matchlen):
            result_line_points.append(selector.select(local_line_points[lpos + i], remote_line_points[rpos + i]))
        lastl, lastr = lpos + matchlen, rpos + matchlen

    result_points = selector.dump()
    return (result, result_line_points, result_points)

def test_conflict():
    # conflict at begin
    ## local zero
    ### local wins
    assert _find_conflict(['aaa', 'bbb'], ['b', 'a', 'a'], ['a', 'b', 'c'], ['x', 'aaa', 'bbb'], ['a', 'c', 'a', 'a'], ['a', 'c'])[:2] == (['aaa', 'bbb'], ['b', 'a', 'a'])
    ### remote wins
    #### remote violate at begin
    assert _find_conflict(['aaa', 'bbb'], ['a', 'a', 'a'], ['a'], ['x', 'y', 'aaa', 'bbb'], ['b', 'a', 'a', 'a', 'a'], ['a', 'b'])[:2] == (['x', 'y', 'aaa', 'bbb'], ['b', 'a', 'a', 'a', 'a'])
    #### remote violate at end
    assert _find_conflict(['aaa', 'bbb'], ['a', 'a', 'a'], ['a'], ['x', 'y', 'aaa', 'bbb'], ['a', 'a', 'b', 'a', 'a'], ['a', 'b'])[:2] == (['x', 'y', 'aaa', 'bbb'], ['a', 'a', 'b', 'a', 'a'])
    ### neither win
    #### remote violate at begin
    assert _find_conflict(['aaa', 'bbb'], ['c', 'a', 'a'], ['a', 'c'], ['x', 'y', 'aaa', 'bbb'], ['b', 'a', 'a', 'a', 'a'], ['a', 'b'])[:2] == ([([], ['x', 'y']), 'aaa', 'bbb'], [(['c'], ['b', 'a', 'a']), None, 'a', 'a'])
    #### remote violate at end
    assert _find_conflict(['aaa', 'bbb'], ['c', 'a', 'a'], ['a', 'c'], ['x', 'y', 'aaa', 'bbb'], ['a', 'a', 'b', 'a', 'a'], ['a', 'b'])[:2] == ([([], ['x', 'y']), 'aaa', 'bbb'], [(['c'], ['a', 'a', 'b']), None, 'a', 'a'])

    ## remote zero
    ### local wins
    assert _find_conflict(['x', 'aaa', 'bbb'], ['a', 'b', 'a', 'a'], ['a', 'b'], ['aaa', 'bbb'], ['a', 'a', 'a'], ['a'])[:2] == (['x', 'aaa', 'bbb'], ['a', 'b', 'a', 'a'])
    ### remote wins
    assert _find_conflict(['x', 'aaa', 'bbb'], ['a', 'b', 'a', 'a'], ['a', 'b'], ['aaa', 'bbb'], ['c', 'a', 'a'], ['a', 'b', 'c'])[:2] == (['aaa', 'bbb'], ['c', 'a', 'a'])
    ### neither win
    #### local violate at begin
    assert _find_conflict(['x', 'y', 'aaa', 'bbb'], ['b', 'a', 'a', 'a', 'a'], ['a', 'b'], ['aaa', 'bbb'], ['c', 'a', 'a'], ['a', 'c'])[:2] == ([(['x', 'y'], []), 'aaa', 'bbb'], [(['b', 'a', 'a'], ['c']), None, 'a', 'a'])
    #### local violate at end
    assert _find_conflict(['x', 'y', 'aaa', 'bbb'], ['a', 'a', 'b', 'a', 'a'], ['a', 'b'], ['aaa', 'bbb'], ['c', 'a', 'a'], ['a', 'c'])[:2] == ([(['x', 'y'], []), 'aaa', 'bbb'], [(['a', 'a', 'b'], ['c']), None, 'a', 'a'])

    ## neither zero
    ### local wins
    assert _find_conflict(['x', 'y', 'aaa', 'bbb'], ['a', 'b', 'a', 'a', 'a'], ['a', 'b'], ['z', 'w', 'aaa', 'bbb'], ['a', 'a', 'a', 'a', 'a'], ['a'])[:2] == (['x', 'y', 'aaa', 'bbb'], ['a', 'b', 'a', 'a', 'a'])
    ### remote wins
    #### violates at beginning
    assert _find_conflict(['x', 'y', 'aaa', 'bbb'], ['a', 'a', 'a', 'a', 'a'], ['a'], ['z', 'w', 'aaa', 'bbb'], ['b', 'a', 'a', 'a', 'a'], ['a', 'b'])[:2] == (['z', 'w', 'aaa', 'bbb'], ['b', 'a', 'a', 'a', 'a'])
    #### violates at end
    assert _find_conflict(['x', 'y', 'aaa', 'bbb'], ['a', 'a', 'a', 'a', 'a'], ['a'], ['z', 'w', 'aaa', 'bbb'], ['a', 'a', 'b', 'a', 'a'], ['a', 'b'])[:2] == (['z', 'w', 'aaa', 'bbb'], ['a', 'a', 'b', 'a', 'a'])
    ### neither win
    #### violates at begin
    assert _find_conflict(['x', 'y', 'aaa', 'bbb'], ['c', 'a', 'a', 'a', 'a'], ['a', 'c'], ['z', 'w', 'aaa', 'bbb'], ['b', 'a', 'a', 'a', 'a'], ['a', 'b'])[:2] == ([(['x', 'y'], ['z', 'w']), 'aaa', 'bbb'], [(['c', 'a', 'a'], ['b', 'a', 'a']), None, 'a', 'a'])
    #### violates at end
    assert _find_conflict(['x', 'y', 'aaa', 'bbb'], ['a', 'a', 'c', 'a', 'a'], ['a', 'c'], ['z', 'w', 'aaa', 'bbb'], ['a', 'a', 'b', 'a', 'a'], ['a', 'b'])[:2] == ([(['x', 'y'], ['z', 'w']), 'aaa', 'bbb'], [(['a', 'a', 'c'], ['a', 'a', 'b']), None, 'a', 'a'])


    # conflict in middle
    ## local zero
    ### local wins
    assert _find_conflict(['aaa', 'bbb', 'ccc', 'ddd'], ['a', 'a', 'b', 'a', 'a'], ['a', 'b', 'c'], ['aaa', 'bbb', 'x', 'ccc', 'ddd'], ['a', 'a', 'c', 'c', 'a', 'a'], ['a', 'c'])[:2] == (['aaa', 'bbb', 'ccc', 'ddd'], ['a', 'a', 'b', 'a', 'a'])
    ### remote wins
    #### violate at begin
    assert _find_conflict(['aaa', 'bbb', 'ccc', 'ddd'], ['a', 'a', 'b', 'a', 'a'], ['a', 'b'], ['aaa', 'bbb', 'x', 'ccc', 'ddd'], ['a', 'a', 'c', 'a', 'a', 'a'], ['a', 'b', 'c'])[:2] == (['aaa', 'bbb', 'x', 'ccc', 'ddd'], ['a', 'a', 'c', 'a', 'a', 'a'])
    #### violate at end
    assert _find_conflict(['aaa', 'bbb', 'ccc', 'ddd'], ['a', 'a', 'b', 'a', 'a'], ['a', 'b'], ['aaa', 'bbb', 'x', 'ccc', 'ddd'], ['a', 'a', 'a', 'c', 'a', 'a'], ['a', 'b', 'c'])[:2] == (['aaa', 'bbb', 'x', 'ccc', 'ddd'], ['a', 'a', 'a', 'c', 'a', 'a'])
    ### neither win
    #### remote violate at begin
    assert _find_conflict(['aaa', 'bbb', 'ccc', 'ddd'], ['a', 'a', 'b', 'a', 'a'], ['a', 'b'], ['aaa', 'bbb', 'x', 'ccc', 'ddd'], ['a', 'a', 'c', 'a', 'a', 'a'], ['a', 'c'])[:2] == (['aaa', 'bbb', ([], ['x']), 'ccc', 'ddd'], ['a', 'a', (['b'], ['c', 'a']), None, 'a', 'a'])
    #### remote violate at end
    assert _find_conflict(['aaa', 'bbb', 'ccc', 'ddd'], ['a', 'a', 'b', 'a', 'a'], ['a', 'b'], ['aaa', 'bbb', 'x', 'ccc', 'ddd'], ['a', 'a', 'a', 'c', 'a', 'a'], ['a', 'c'])[:2] == (['aaa', 'bbb', ([], ['x']), 'ccc', 'ddd'], ['a', 'a', (['b'], ['a', 'c']), None, 'a', 'a'])

    ## remote zero
    ### local wins
    assert _find_conflict(['aaa', 'bbb', 'x', 'ccc', 'ddd'], ['a', 'a', 'x', 'x', 'a', 'a'], ['a', 'x', 'c'], ['aaa', 'bbb', 'ccc', 'ddd'], ['a', 'a', 'c', 'a', 'a'], ['a', 'c'])[:2] == (['aaa', 'bbb', 'x', 'ccc', 'ddd'], ['a', 'a', 'x', 'x', 'a', 'a'])
    ### remote wins
    assert _find_conflict(['aaa', 'bbb', 'x', 'ccc', 'ddd'], ['a', 'a', 'x', 'x', 'a', 'a'], ['a', 'x'], ['aaa', 'bbb', 'ccc', 'ddd'], ['a', 'a', 'c', 'a', 'a'], ['a', 'x', 'c'])[:2] == (['aaa', 'bbb', 'ccc', 'ddd'], ['a', 'a', 'c', 'a', 'a'])
    ### neither win
    #### local violate at begin
    assert _find_conflict(['aaa', 'bbb', 'x', 'ccc', 'ddd'], ['a', 'a', 'x', 'a', 'a', 'a'], ['a', 'x'], ['aaa', 'bbb', 'ccc', 'ddd'], ['a', 'a', 'c', 'a', 'a'], ['a', 'c'])[:2] == (['aaa', 'bbb', (['x'], []), 'ccc', 'ddd'], ['a', 'a', (['x', 'a'], ['c']), None, 'a', 'a'])
    #### local violate at end
    assert _find_conflict(['aaa', 'bbb', 'x', 'ccc', 'ddd'], ['a', 'a', 'a', 'x', 'a', 'a'], ['a', 'x'], ['aaa', 'bbb', 'ccc', 'ddd'], ['a', 'a', 'c', 'a', 'a'], ['a', 'c'])[:2] == (['aaa', 'bbb', (['x'], []), 'ccc', 'ddd'], ['a', 'a', (['a', 'x'], ['c']), None, 'a', 'a'])

    ## neither zero
    ### local wins
    assert _find_conflict(['aaa', 'bbb', 'x', 'ccc', 'ddd'], ['a', 'a', 'x', 'x', 'a', 'a'], ['a', 'x', 'y'], ['aaa', 'bbb', 'y', 'ccc', 'ddd'], ['a', 'a', 'y', 'y', 'a', 'a'], ['a', 'y'])[:2] == (['aaa', 'bbb', 'x', 'ccc', 'ddd'], ['a', 'a', 'x', 'x', 'a', 'a'])
    ### remote wins
    #### violate at begin
    assert _find_conflict(['aaa', 'bbb', 'x', 'ccc', 'ddd'], ['a', 'a', 'x', 'x', 'a', 'a'], ['a', 'x'], ['aaa', 'bbb', 'y', 'ccc', 'ddd'], ['a', 'a', 'y', 'a', 'a', 'a'], ['a', 'x', 'y'])[:2] == (['aaa', 'bbb', 'y', 'ccc', 'ddd'], ['a', 'a', 'y', 'a', 'a', 'a'])
    #### violate at end
    assert _find_conflict(['aaa', 'bbb', 'x', 'ccc', 'ddd'], ['a', 'a', 'x', 'x', 'a', 'a'], ['a', 'x'], ['aaa', 'bbb', 'y', 'ccc', 'ddd'], ['a', 'a', 'a', 'y', 'a', 'a'], ['a', 'x', 'y'])[:2] == (['aaa', 'bbb', 'y', 'ccc', 'ddd'], ['a', 'a', 'a', 'y', 'a', 'a'])
    ### neither win
    #### violates at begin
    assert _find_conflict(['aaa', 'bbb', 'x', 'ccc', 'ddd'], ['a', 'a', 'x', 'a', 'a', 'a'], ['a', 'x'], ['aaa', 'bbb', 'y', 'ccc', 'ddd'], ['a', 'a', 'y', 'a', 'a', 'a'], ['a', 'y'])[:2] == (['aaa', 'bbb', (['x'], ['y']), 'ccc', 'ddd'], ['a', 'a', (['x', 'a'], ['y', 'a']), None, 'a', 'a'])
    #### violates at end
    assert _find_conflict(['aaa', 'bbb', 'x', 'ccc', 'ddd'], ['a', 'a', 'a', 'x', 'a', 'a'], ['a', 'x'], ['aaa', 'bbb', 'y', 'ccc', 'ddd'], ['a', 'a', 'a', 'y', 'a', 'a'], ['a', 'y'])[:2] == (['aaa', 'bbb', (['x'], ['y']), 'ccc', 'ddd'], ['a', 'a', (['a', 'x'], ['a', 'y']), None, 'a', 'a'])



    # conflict at end
    ## local zero
    ### local wins
    assert _find_conflict(['aaa', 'bbb'], ['a', 'a', 'b'], ['a', 'b', 'x'], ['aaa', 'bbb', 'x'], ['a', 'a', 'x', 'x'], ['a', 'x'])[:2] == (['aaa', 'bbb'], ['a', 'a', 'b'])
    ### remote wins
    #### violate at begin
    assert _find_conflict(['aaa', 'bbb'], ['a', 'a', 'b'], ['a', 'b'], ['aaa', 'bbb', 'x'], ['a', 'a', 'x', 'a'], ['a', 'b', 'x'])[:2] == (['aaa', 'bbb', 'x'], ['a', 'a', 'x', 'a'])
    #### violate at end
    assert _find_conflict(['aaa', 'bbb'], ['a', 'a', 'b'], ['a', 'b'], ['aaa', 'bbb', 'x'], ['a', 'a', 'a', 'x'], ['a', 'b', 'x'])[:2] == (['aaa', 'bbb', 'x'], ['a', 'a', 'a', 'x'])
    ### neither win
    #### remote violate at begin
    assert _find_conflict(['aaa', 'bbb'], ['a', 'a', 'b'], ['a', 'b'], ['aaa', 'bbb', 'x'], ['a', 'a', 'x', 'a'], ['a', 'x'])[:2] == (['aaa', 'bbb', ([], ['x'])], ['a', 'a', (['b'], ['x', 'a']), None])
    #### remote violate at end
    assert _find_conflict(['aaa', 'bbb'], ['a', 'a', 'b'], ['a', 'b'], ['aaa', 'bbb', 'x'], ['a', 'a', 'a', 'x'], ['a', 'x'])[:2] == (['aaa', 'bbb', ([], ['x'])], ['a', 'a', (['b'], ['a', 'x']), None])

    ## remote zero
    ### local wins
    assert _find_conflict(['aaa', 'bbb', 'x'], ['a', 'a', 'x', 'x'], ['a', 'b', 'x'], ['aaa', 'bbb'], ['a', 'a', 'b'], ['a', 'b'])[:2] == (['aaa', 'bbb', 'x'], ['a', 'a', 'x', 'x'])
    ### remote wins
    assert _find_conflict(['aaa', 'bbb', 'x'], ['a', 'a', 'x', 'x'], ['a', 'x'], ['aaa', 'bbb'], ['a', 'a', 'b'], ['a', 'b', 'x'])[:2] == (['aaa', 'bbb'], ['a', 'a', 'b'])
    ### neither win
    #### local violate at begin
    assert _find_conflict(['aaa', 'bbb', 'x'], ['a', 'a', 'x', 'a'], ['a', 'x'], ['aaa', 'bbb'], ['a', 'a', 'b'], ['a', 'b'])[:2] == (['aaa', 'bbb', (['x'], [])], ['a', 'a', (['x', 'a'], ['b']), None])
    #### local violate at end
    assert _find_conflict(['aaa', 'bbb', 'x'], ['a', 'a', 'a', 'x'], ['a', 'x'], ['aaa', 'bbb'], ['a', 'a', 'b'], ['a', 'b'])[:2] == (['aaa', 'bbb', (['x'], [])], ['a', 'a', (['a', 'x'], ['b']), None])

    ## neither zero
    ### local wins
    assert _find_conflict(['aaa', 'bbb', 'x'], ['a', 'a', 'x', 'x'], ['a', 'x', 'y'], ['aaa', 'bbb', 'y'], ['a', 'a', 'y', 'y'], ['a', 'y'])[:2] == (['aaa', 'bbb', 'x'], ['a', 'a', 'x', 'x'])
    ### remote wins
    assert _find_conflict(['aaa', 'bbb', 'x'], ['a', 'a', 'x', 'x'], ['a', 'x'], ['aaa', 'bbb', 'y'], ['a', 'a', 'y', 'y'], ['a', 'x', 'y'])[:2] == (['aaa', 'bbb', 'y'], ['a', 'a', 'y', 'y'])
    ### neither win
    #### violates at begin
    assert _find_conflict(['aaa', 'bbb', 'x'], ['a', 'a', 'x', 'a'], ['a', 'x'], ['aaa', 'bbb', 'y'], ['a', 'a', 'y', 'a'], ['a', 'y'])[:2] == (['aaa', 'bbb', (['x'], ['y'])], ['a', 'a', (['x', 'a'], ['y', 'a']), None])
    #### violates at end
    assert _find_conflict(['aaa', 'bbb', 'x'], ['a', 'a', 'a', 'x'], ['a', 'x'], ['aaa', 'bbb', 'y'], ['a', 'a', 'a', 'y'], ['a', 'y'])[:2] == (['aaa', 'bbb', (['x'], ['y'])], ['a', 'a', (['a', 'x'], ['a', 'y']), None])



    # whole file conflict
    ## local zero
    ### local wins
    assert _find_conflict([], ['a'], ['a', 'b'], ['x'], ['b', 'b'], ['b'])[:2] == ([], ['a'])
    ### remote wins
    #### violate at begin
    assert _find_conflict([], ['a'], ['a'], ['x'], ['b', 'a'], ['a', 'b'])[:2] == (['x'], ['b', 'a'])
    #### violate at end
    assert _find_conflict([], ['a'], ['a'], ['x'], ['a', 'b'], ['a', 'b'])[:2] == (['x'], ['a', 'b'])
    ### neither win
    #### remote violate at begin
    assert _find_conflict([], ['a'], ['a', 'c'], ['x'], ['b', 'c'], ['b', 'c'])[:2] == ([([], ['x'])], [(['a'], ['b', 'c']), None])
    #### remote violate at end
    assert _find_conflict([], ['a'], ['a', 'c'], ['x'], ['c', 'b'], ['b', 'c'])[:2] == ([([], ['x'])], [(['a'], ['c', 'b']), None])

    ## remote zero
    ### local wins
    assert _find_conflict(['a'], ['a', 'a'], ['a', 'b'], [], ['b'], ['b'])[:2] == (['a'], ['a', 'a'])
    ### remote wins
    assert _find_conflict(['a'], ['a', 'a'], ['a'], [], ['b'], ['a', 'b'])[:2] == ([], ['b'])
    ### neither win
    #### local violate at begin
    assert _find_conflict(['a'], ['a', 'c'], ['a', 'c'], [], ['b'], ['b', 'c'])[:2] == ([(['a'], [])], [(['a', 'c'], ['b']), None])
    #### local violate at end
    assert _find_conflict(['a'], ['c', 'a'], ['a', 'c'], [], ['b'], ['b', 'c'])[:2] == ([(['a'], [])], [(['c', 'a'], ['b']), None])

    ## neither zero
    ### local wins
    assert _find_conflict(['a'], ['a', 'a'], ['a', 'b'], ['b'], ['b', 'b'], ['b'])[:2] == (['a'], ['a', 'a'])
    ### remote wins
    assert _find_conflict(['a'], ['a', 'a'], ['a'], ['b'], ['b', 'b'], ['a', 'b'])[:2] == (['b'], ['b', 'b'])
    ### neither win
    #### violate at begin
    assert _find_conflict(['a'], ['a', 'c'], ['a', 'c'], ['b'], ['b', 'c'], ['c', 'b'])[:2] == ([(['a'], ['b'])], [(['a', 'c'], ['b', 'c']), None])
    #### violate at end
    assert _find_conflict(['a'], ['c', 'a'], ['a', 'c'], ['b'], ['c', 'b'], ['c', 'b'])[:2] == ([(['a'], ['b'])], [(['c', 'a'], ['c', 'b']), None])
    
    # XXX: is this still relevant?
    # union counts for either
    #assert _find_conflict(['aaa', 'bbb'], [['a'], ['a', 'b'], ['a']], ['a', 'b'], ['ccc', 'ddd'], [['a'], ['c'], ['a']], ['a', 'c']) == (['ccc', 'ddd'], [['a'], ['c'], ['a']])


    # XXX: need more tests here
    # coincidental matches
    ## begin
    assert _find_conflict(['aaa', 'bbb'], ['b', 'a', 'a'], ['a', 'b'], ['aaa', 'bbb'], ['c', 'a', 'a'], ['a', 'c'])[:2] == (['aaa', 'bbb'], ['c', 'a', 'a'])
    ## middle
    assert _find_conflict(['aaa', 'bbb'], ['a', 'b', 'a'], ['a', 'b'], ['aaa', 'bbb'], ['a', 'c', 'a'], ['a', 'c'])[:2] == (['aaa', 'bbb'], ['a', 'c', 'a'])
    ## end
    assert _find_conflict(['aaa', 'bbb'], ['a', 'a', 'b'], ['a', 'b'], ['aaa', 'bbb'], ['a', 'a', 'c'], ['a', 'c'])[:2] == (['aaa', 'bbb'], ['a', 'a', 'c'])

    # multiple conflicts, different resolutions
    assert _find_conflict(['x', 'aaa', 'bbb', 'y', 'ccc', 'ddd', 'z'], ['x', 'x', 'a', 'y', 'y', 'a', 'z', 'z'], ['a', 'x', 'y', 'z', 'p'], ['p', 'aaa', 'bbb', 'q', 'ccc', 'ddd', 'r'], ['p', 'p', 'a', 'q', 'q', 'a', 'r', 'r'], ['a', 'p', 'q', 'r', 'z'])[:2] == (['x', 'aaa', 'bbb', (['y'], ['q']), 'ccc', 'ddd', 'r'], ['x', 'x', 'a', (['y', 'y'], ['q', 'q']), None, 'a', 'r', 'r'])


class NoMoreMatchesException: pass

def find_resolution(precursors, resolution):
    result = [None] * (len(resolution) + 1)
    ms = [[] for i in xrange(len(precursors))]

    selector = SelectLinePoint()

    ms = []
    for i in xrange(len(precursors)):
        ms.append(find_matches(precursors[i][0], resolution))

        pre = precursors[i][0]
        pre_line_points = precursors[i][1]
        selector.push(precursors[i][2])

        for (matchold, matchnew, matchlen) in ms[i]:
            begina, beginb, mlen = matchold + 1, matchnew + 1, matchlen - 1
            if (matchold, matchnew) == (0, 0):
                (begina, beginb, mlen) = (0, 0, mlen + 1)
            if (matchold + matchlen, matchnew + matchlen) == (len(pre), len(resolution)):
                mlen += 1
            for j in xrange(mlen):
                result[beginb + j] = selector.select(result[beginb + j], pre_line_points[begina + j])

    # mark each line as to whether we need a newline for it
    hits = [False] * len(resolution)
    for i in xrange(len(ms)):
        for (matchold, matchnew, matchlen) in ms[i]:
            hits[matchnew:matchnew + matchlen] = [True] * matchlen

    # this may seem weird, but there's this really annoying ambiguous clean
    # merge case which sometimes turns up when the match algorithm finds an
    # "incorrect" match. the same conflicts which the person has just resolved
    # will happen again on merges through other repositories. this code
    # puts in new line points to prevent that from happening.
    if len(precursors) > 1:
        breaks = []
        for i in xrange(len(precursors)):
            l, lp, p = precursors[i]
            lastl, lastr = 0, 0
            ms[i].append((len(l), len(resolution), 0))
            for (lpos, rpos, mlen) in ms[i]:
                # if it doesn't declare itself the winner, it will cause
                # conflicts later
                rapplied = _is_applied(result[lastr:rpos + 1], p)
                if     (lpos, rpos) == (0, 0) or \
                       (lastl, lastr) == (len(l), len(resolution)):
                    pass
                elif rapplied:
                    breaks.append((lastr, rpos - lastr))

                    # fix hits so newlines are created
                    hits[lastr:rpos] = [False] * (rpos - lastr)

                lastl, lastr = lpos + mlen, rpos + mlen
        breaks.sort()

        # make the matches not overlap the breaks
        for i in xrange(len(ms)):
            newms = []
            cur_match = 0
            try:
                for (bpos, blen) in breaks:
                    lpos, rpos, mlen = ms[i][cur_match]

                    # no overlap, nothing to do
                    while rpos + mlen <= bpos:
                        newms.append(ms[i][cur_match])
                        cur_match += 1
                        if cur_match >= len(ms[i]):
                            raise NoMoreMatchesException
                        lpos, rpos, mlen = ms[i][cur_match]

                    # some overlap at the end of the match
                    if rpos < bpos:
                        newms.append((lpos, rpos, bpos - rpos))

                    # full overlap
                    while rpos + mlen <= bpos + blen:
                        cur_match += 1
                        if cur_match >= len(ms[i]):
                            raise NoMoreMatchesException
                        lpos, rpos, mlen = ms[i][cur_match]

                    # partial overlap, fix it up for the next iteration
                    if rpos < bpos + blen:
                        adj = bpos + blen - rpos
                        m = (lpos + adj, bpos + blen, mlen - adj)
                        ms[i][cur_match] = m
            except NoMoreMatchesException:
                pass

            # add remaining matches at the end, dropping the dummy
            newms.extend(ms[i][cur_match:-1])

            ms[i] = newms

        # reset results
        for (bpos, blen) in breaks:
            result[bpos:bpos + blen + 1] = [None] * (blen + 1)

    # figure out what's not covered by matches
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
    assert find_resolution([(['aaa', 'bbb', 'ccc'], ['x', 'y', 'z', 'w'], ['x', 'y', 'z', 'w']), (['aaa', 'bbb', 'ccc'], ['p', 'q', 'z', 'w'], ['p', 'q', 'z', 'w'])], ['aaa', 'bbb', 'ccc'])[0] == ['x', 'y', 'z', 'w']
    assert find_resolution([(['aaa', 'bbb', 'qqq'], ['a', 'b', 'c', 'd'], ['a', 'b', 'c', 'd']), (['qqq', 'bbb', 'ccc'], ['e', 'f', 'g', 'h'], ['e', 'f', 'g', 'h'])], ['aaa', 'bbb', 'ccc'])[0] == ['a', 'b', 'g', 'h']
    assert find_resolution([(['aaa'], ['a', 'a'], ['a'])], ['bbb'])[0] == [None, None]
    assert find_resolution([(['aaa'], ['x', 'x'], ['x']), (['aaa'], ['x', 'x'], ['x'])], ['aaa'])[1] == [[(0, 0, 1)], [(0, 0, 1)]]
    assert find_resolution([(['aaa'], ['x', 'x'], ['x']), (['aaa'], ['x', 'y'], ['x', 'y'])], ['aaa'])[1] == [[(0, 0, 1)], [(0, 0, 1)]]
    assert find_resolution([(['aaa', 'aaa'], ['x', 'x', 'x'], ['x'])], ['aaa', 'aaa', 'bbb', 'ccc'])[2] == [(2, ['bbb', 'ccc'])]
    assert find_resolution([(['aaa', 'aaa'], ['x', 'x', 'x'], ['x'])], ['bbb', 'ccc', 'aaa', 'aaa'])[2] == [(0, ['bbb', 'ccc'])]
    assert find_resolution([(['aaa', 'aaa'], ['x', 'x', 'x'], ['x'])], ['bbb', 'ccc'])[2] == [(0, ['bbb', 'ccc'])]
    assert find_resolution([(['aaa', 'aaa'], ['a', 'a', 'a'], ['a', 'b']), (['bbb', 'bbb'], ['b', 'b', 'b'], ['a', 'b'])], ['aaa', 'aaa']) == ([None, None, None], [[], []], [(0, ['aaa', 'aaa'])])
    assert find_resolution([(['aaa', 'bbb', 'bbb', 'bbb'], ['a', 'a', 'a', 'a', 'a'], ['a', 'b']), (['bbb', 'bbb', 'bbb', 'ccc'], ['b', 'b', 'b', 'b', 'b'], ['a', 'b'])], ['aaa', 'bbb', 'bbb', 'ccc']) == ([None, None, 'b', None, None], [[(1, 1, 2)], [(1, 1, 2)]], [(0, ['aaa']), (3, ['ccc'])])
    assert find_resolution([(['aaa', 'aaa', 'aaa'], ['a', 'c', 'a', 'c'], ['a', 'c']), (['aaa', 'aaa', 'aaa'], ['a', 'a', 'b', 'b'], ['a', 'b'])], ['aaa', 'aaa', 'aaa']) == (['a', 'c', 'b', 'c'], [[(0, 0, 3)], [(0, 0, 3)]], [])

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

    selector = SelectLinePoint()

    delpoints = [0]
    for i in xrange(len(matches)):
        selector.push(precursors[i][2])
        try:
            if matches[i][0][0] == 0 and matches[i][0][1] == 0:
                rpoints[0] = selector.select(rpoints[0], precursors[i][1][0])
        except IndexError:
            pass
        try:
            matchold, matchnew, matchlen = matches[i][-1]
            if matchnew + matchlen == resultlength and matchold + matchlen == len(precursors[i][0]):
                rpoints[resultlength] = selector.select(rpoints[resultlength], precursors[i][1][-1])
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
            pre_lp = precursors[i][1]
            for j in xrange(1, matchlen):
                rpoints[matchnew + j] = selector.select(rpoints[matchnew + j], pre_lp[matchold + j])

    points = selector.dump()

    # fill in line points around new lines
    for (begin, lines) in newlines:
        for i in xrange(len(lines)):
            if r[begin + i] is not None:
                raise MergeError, 'match covers newline'
            r[begin + i] = lines[i]
        for i in xrange(len(lines) + 1):
            assert rpoints[begin + i] is None
            rpoints[begin + i] = current_point

    # fill in line points between matches in case the hunk had no new lines
    for index in delpoints:
        if rpoints[index] is None:
            rpoints[index] = selector.select(rpoints[index], current_point)

    # sanity checks
    if None in r:
        raise MergeError, 'unfilled line'
    if None in rpoints:
        raise MergeError, 'unset line point'
    return (r, rpoints, points)

class MergeError(StandardError):
    pass

def test_replay():
    assert replay([(['aaa', 'bbb'], ['a', 'b', 'c'], ['a', 'b', 'c'])], [[(0, 0, 2)]], [(2, ['ccc', 'ddd'])], 'z')[:2] == (['aaa', 'bbb', 'ccc', 'ddd'], ['a', 'b', 'z', 'z', 'z'])
    assert replay([(['aaa', 'bbb'], ['a', 'b', 'c'], ['a', 'b', 'c'])], [[(0, 2, 2)]], [(0, ['ccc', 'ddd'])], 'z')[:2] == (['ccc', 'ddd', 'aaa', 'bbb'], ['z', 'z', 'z', 'b', 'c'])
    assert replay([(['aaa', 'bbb', 'ccc', 'ddd'], ['a', 'b', 'c', 'd', 'e'], ['a', 'b', 'c', 'd', 'e']), (['bbb', 'ccc', 'ddd', 'eee'], ['f', 'c', 'g', 'h', 'i'], ['f', 'c', 'g', 'h', 'i'])], [[(0, 0, 4)], [(0, 1, 4)]], [], 'z')[:2] == (['aaa', 'bbb', 'ccc', 'ddd', 'eee'], ['a', 'b', 'c', 'g', 'h', 'i'])
    assert replay([(['aaa', 'bbb', 'ccc', 'ddd', 'eee'], ['a', 'a', 'a', 'a', 'a', 'a'], ['a'])], [[(0, 0, 2), (3, 2, 2)]], [], 'z')[:2] == (['aaa', 'bbb', 'ddd', 'eee'], ['a', 'a', 'z', 'a', 'a'])
    assert replay([(['aaa', 'bbb', 'ccc', 'ddd'], ['a', 'a', 'a', 'a', 'a'], ['a'])], [[(2, 0, 2)]], [], 'z')[:2] == (['ccc', 'ddd'], ['z', 'a', 'a'])
    assert replay([(['aaa', 'bbb', 'ccc', 'ddd'], ['a', 'a', 'a', 'a', 'a'], ['a'])], [[(0, 0, 2)]], [], 'z')[:2] == (['aaa', 'bbb'], ['a', 'a', 'z'])

def annotate(precursors, matches, newlines, current_point):
    # figure out how long the result is going to be
    resultlength = 0
    for i in matches:
        if not len(i):
            continue
        matchold, matchnew, matchlen = i[-1]
        if matchnew + matchlen > resultlength:
            resultlength = matchnew + matchlen
    try:
        lastline = newlines[-1][0] + len(newlines[-1][1])
        if lastline > resultlength:
            resultlength = lastline
    except IndexError:
        pass

    selector = SelectLinePoint()

    r       = [None] * resultlength
    rpoints = [None] * resultlength

    for i in xrange(len(matches)):
        selector.push(precursors[i][2])
        for (matchold, matchnew, matchlen) in matches[i]:
            pre_lines = precursors[i][0]
            pre_lp = precursors[i][1]
            for j in xrange(matchlen):
                new_pos = matchnew + j
                old_pos = matchold + j

                # fill in lines from matching sections
                v = pre_lines[old_pos]
                if r[new_pos] is not None and r[new_pos] != v:
                    raise MergeError, 'conflicting values'
                r[new_pos] = v

                # fill in annotation
                rpoints[new_pos] = selector.select(rpoints[new_pos], pre_lp[old_pos])

    points = selector.dump()

    # fill in annotation for new lines
    for (begin, lines) in newlines:
        for i in xrange(len(lines)):
            # fill in result lines
            if r[begin + i] is not None:
                raise MergeError, 'match covers newline'
            r[begin + i] = lines[i]

            # then the current annotation
            assert rpoints[begin + i] is None
            rpoints[begin + i] = current_point

    # sanity checks
    if None in r:
        raise MergeError, 'unfilled line'
    if None in rpoints:
        raise MergeError, 'unset annotation'
    return (r, rpoints, points)

def find_annotation(precursors, resolution):
    result = [None] * len(resolution)

    selector = SelectLinePoint()

    for i in xrange(len(precursors)):
        match = find_matches(precursors[i][0], resolution)

        pre = precursors[i][0]
        pre_line_points = precursors[i][1]
        selector.push(precursors[i][2])

        for (matchold, matchnew, matchlen) in match:
            for j in xrange(matchlen):
                result[matchnew + j] = selector.select(result[matchnew + j], pre_line_points[matchold + j])

    return result

def test_annotate():
    assert annotate([(['aaa', 'bbb'], ['a', 'b'], ['a', 'b'])], [[(0, 0, 2)]], [(2, ['ccc', 'ddd'])], 'z')[:2] == (['aaa', 'bbb', 'ccc', 'ddd'], ['a', 'b', 'z', 'z'])
    assert annotate([(['aaa', 'bbb'], ['a', 'b'], ['a', 'b'])], [[(0, 2, 2)]], [(0, ['ccc', 'ddd'])], 'z')[:2] == (['ccc', 'ddd', 'aaa', 'bbb'], ['z', 'z', 'a', 'b'])
    assert annotate([(['aaa', 'bbb', 'ccc', 'ddd'], ['a', 'b', 'c', 'd'], ['a', 'b', 'c', 'd']), (['bbb', 'ccc', 'ddd', 'eee'], ['e', 'c', 'f', 'g'], ['e', 'c', 'f', 'g'])], [[(0, 0, 4)], [(0, 1, 4)]], [], 'z')[:2] == (['aaa', 'bbb', 'ccc', 'ddd', 'eee'], ['a', 'e', 'c', 'f', 'g'])
    assert annotate([(['aaa', 'bbb', 'ccc', 'ddd', 'eee'], ['a', 'a', 'a', 'a', 'a'], ['a'])], [[(0, 0, 2), (3, 2, 2)]], [], 'z')[:2] == (['aaa', 'bbb', 'ddd', 'eee'], ['a', 'a', 'a', 'a'])
    assert annotate([(['aaa', 'bbb', 'ccc', 'ddd'], ['a', 'a', 'a', 'a'], ['a'])], [[(2, 0, 2)]], [], 'z')[:2] == (['ccc', 'ddd'], ['a', 'a'])
    assert annotate([(['aaa', 'bbb', 'ccc', 'ddd'], ['a', 'a', 'a', 'a'], ['a'])], [[(0, 0, 2)]], [], 'z')[:2] == (['aaa', 'bbb'], ['a', 'a'])

try:
    import psyco
    psyco.bind(replay)
except ImportError:
    pass
