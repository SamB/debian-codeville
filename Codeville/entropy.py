# Written by Ross Cohen
# See LICENSE.txt for license information.

import sys

try:
    from os import urandom as random_string
except ImportError:
    if sys.platform == 'win32':
        from winrandom import winrandom as random_string

    else:
        dev = None

        def random_string(bytes):
            """Generate a random string with the given length."""
            global dev
            if dev is None:
                dev = open('/dev/urandom', 'r')
            return dev.read(bytes)

def random_long(bits):
    """Generate a random long integer with the given number of bits."""
    return string_to_long(random_string((bits+7)/8)) % (1L<<bits)

def string_to_long(s):
    """Convert a string of bytes into a long integer."""
    r = 0L
    for c in s:
	r = (r << 8) + ord(c)
    return r

def long_to_string(i, length=0):
    """Convert a long integer into a string of bytes."""
    s = ''
    while i > 0:
	s = chr(i & 255) + s
	i = i >> 8
    s = '\x00' * (length - len(s)) + s
    return s
