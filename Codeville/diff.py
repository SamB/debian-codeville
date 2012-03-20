#!/usr/bin/python

# Written by Ross Cohen
# see LICENSE.txt for license information

from lcsmatch import find_matches

def standard_diff(lines1, lines2):
    matches = find_matches(lines1, lines2)
    if len(matches) == 0 or matches[0][0] != 0 or matches[0][1] != 0:
        matches.insert(0, (0, 0, 0))
    if     matches[-1][0]+matches[-1][2] != len(lines1) or \
           matches[-1][1]+matches[-1][2] != len(lines2):
        matches.append((len(lines1), len(lines2), 0))

    text = []
    i = 0
    j = 0
    for i2, j2, l in matches:
        if i2 == i and j2 == j:
            i += l
            j += l
            continue
        if i2 == i:
            at = str(i)
        elif i2 == i + 1:
            at = str(i2)
        else:
            at = "%d,%d" % (i+1, i2)
        if j2 == j:
            bt = str(j)
        elif j2 == j + 1:
            bt = str(j2)
        else:
            bt = "%d,%d" % (j+1, j2)
        if i2 > i:
            if j2 > j:
                text.extend([at, 'c', bt, "\n"])
            else:
                text.extend([at, 'd', bt, "\n"])
        else:
            text.extend([at, 'a', bt, "\n"])
        for i in xrange(i, i2):
            text.extend(["< ", lines1[i], "\n"])
        if i2 > i and j2 > j:
            text.append("---\n")
        for j in xrange(j, j2):
            text.extend(["> ", lines2[j], "\n"])
        i = i2 + l
        j = j2 + l

    return ''.join(text)


def unified_diff(lines1, lines2):
    matches = find_matches(lines1, lines2)
    if     len(matches) == 0 or \
           matches[-1][0]+matches[-1][2] != len(lines1) or \
           matches[-1][1]+matches[-1][2] != len(lines2):
        matches.append((len(lines1), len(lines2), 0))

    text = []
    if matches[0][:2] == (0, 0):
        line = line2 = max(matches[0][2] - 3, 0)
        length = min(matches[0][2], 3)
        m = 1
    else:
        line, line2 = -1, 0
        length = 0
        m = 0
    while m < len(matches):
        # capture as much as possible under this header
        m_end = m
        while True:
            line_end, line2_end, length_end = matches[m_end]
            if length_end > 6 or m_end + 1 == len(matches):
                break
            m_end += 1
        length_end = min(length_end, 3)

        # print the diff header for all the hunks we can cover
        lfudge = max(0, line)
        header = "@@ -%d,%d +%d,%d @@\n" % \
                 (lfudge + 1,  line_end - lfudge + length_end,
                  line2 + 1, line2_end - line2 + length_end)
        text.append(header)

        # print out all the covered hunks with context
        for m in xrange(m, m_end+1):
            # fill out the previous context
            for line in xrange(line, line+length):
                text.append(' ' + lines1[line] + "\n")
            line2 += length - 1
            s1, s2, length = matches[m]
            # print the removed and added lines
            for line in xrange(line+1, s1):
                text.append('-' + lines1[line] + "\n")
            for line2 in xrange(line2+1, s2):
                text.append('+' + lines2[line2] + "\n")
            line  += 1
            line2 += 1

        len_full = length
        length = min(3, length)
        # print the trailing context
        for i in xrange(line, line + length):
            text.append(' ' + lines1[i] + "\n")

        # setup stuff for the next iteration
        line  = s1 + len_full - length
        line2 = s2 + len_full - length
        m += 1

    return ''.join(text)

if __name__ == '__main__':
    from sys import argv, exit
    from getopt import gnu_getopt, GetoptError

    try:
        optlist, args = gnu_getopt(argv, 'u')
    except GetoptError, msg:
        print "Unknown option \"%s\"" % (msg.args[0],)
        exit(1)

    if len(args) != 3:
        print "Usage: %s [-u] file1 file2" % (args[0],)
        exit(1)

    diff_func = standard_diff
    for opt, arg in optlist:
        if opt == '-u':
            diff_func = unified_diff

    h = open(args[1], 'rb')
    pre_lines = [line[:-1] for line in h.readlines()]
    h.close()

    h = open(args[2], 'rb')
    post_lines = [line[:-1] for line in h.readlines()]
    h.close()

    print diff_func(pre_lines, post_lines)
