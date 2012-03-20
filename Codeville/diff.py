#!/usr/bin/python

# Written by Ross Cohen
# see LICENSE.txt for license information

from merge import _find_matches

def diff_file(lines1, lines2):
    # the rest of the code assumes \n between lines, we assume \n after each
    if lines1[-1] == '':
        lines1.pop()
    if lines2[-1] == '':
        lines2.pop()
    matches = _find_matches(lines1, lines2)
    if len(matches) == 0 or matches[0][0] != 0 or matches[0][1] != 0:
        matches.insert(0, (0, 0, 0))
    if matches[-1][0]+matches[-1][2] != len(lines1) or matches[-1][1]+matches[-1][2] != len(lines2):
        matches.append((len(lines1), len(lines2), 0))
    text, diff = '', ''
    i, hi = 0, 0
    hextent = extent = min(3, matches[0][2])
    while i < len(matches)-1:
        prev_start1, prev_start2, prev_length = matches[i]
        next_start1, next_start2, next_length = matches[i+1]
        point = prev_start1 + prev_length
        while extent:
            diff += ' ' + lines1[point-extent] + "\n"
            extent -= 1
        while point < next_start1:
            diff += '-' + lines1[point] + "\n"
            point += 1
        point = prev_start2 + prev_length
        while point < next_start2:
            diff += '+' + lines2[point] + "\n"
            point += 1
        extent2 = min(3, next_length)
        j = 0
        while j < extent2:
            diff += ' ' + lines2[next_start2+j] + "\n"
            j += 1
        if next_length > 6:
            text += _diff_header(matches, hi, i+1, hextent, extent2) + diff
            diff = ''
            hextent = extent = 3
            hi = i+1
        else:
            extent = next_length - min(3, next_length)
        i += 1
    if diff != '':
        text += _diff_header(matches, hi, i, hextent, extent2) + diff
    return text

def _diff_header(matches, i, endi, extent, extent2):
    pre_start = matches[i][0] + matches[i][2] - extent
    post_start = matches[i][1] + matches[i][2] - extent
    pre_length = matches[endi][0] - pre_start + extent2
    post_length = matches[endi][1] - post_start + extent2
    header = "@@ -" + str(pre_start + 1) + "," + str(pre_length) + " +"
    header += str(post_start  + 1) + "," + str(post_length) + " @@\n"
    return header
