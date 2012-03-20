# Written by Ross Cohen
# see LICENSE.txt for license information

from entropy import string_to_long, long_to_string
import sha

def crypt(text, key, counter=0L):
    keylen, length = len(key), len(text)
    pos, cyphertext = 0, []
    while pos < length:
        scounter = long_to_string(counter, keylen)
        hash = sha.new("ctr mode crypt" + key + scounter).digest()
        for i in xrange(min(length-pos, len(hash))):
            cyphertext.append(chr(ord(hash[i]) ^ ord(text[pos])))
            pos += 1
        counter += 1
    return (''.join(cyphertext), counter)
