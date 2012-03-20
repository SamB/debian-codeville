"""Secure Remote Passwords.  This is slightly different from the standard
implementation (with regard to the definition of 'u', the authentication
hash, and the fact that the database is a pickle).  Also the default random
number generator is not cryptographically strong.  It may be good enough to
password protect your MUD, if not your bank account.  Note that the passwd
database should not be world readable, or it will be vulnerable to a
dictionary attack (like the standard Unix password file).  See the SRP
distribution at http://srp.stanford.edu/srp/ for more information."""

from entropy import random_long, random_string, string_to_long, long_to_string
import hmac
import sha

# Some constants defining the sizes of various entities.

saltlen = 16    # bytes
tlen = 128      # bits
ablen = 128     # bits

# The prime field to work in, and the base to use.  Note that this must be
# common to both client and host. (Alternatively, the host can send these
# values to the client, who should then verify that they are safe.)
# The first number is a prime p of the form 2q + 1, where q is also prime.
# The second number is a generator in the field GF(p).

pf = (137656596376486790043182744734961384933899167257744121335064027192370741112305920493080254690601316526576747330553110881621319493425219214435734356437905637147670206858858966652975541347966997276817657605917471296442404150473520316654025988200256062845025470327802138620845134916799507318209468806715548156999L,
      8623462398472349872L)

# New exceptions we raise.

class ImproperKeyValue(Exception): pass

def hash(s):
    """Hash a value with some hashing algorithm."""
    if type(s) != type(''):
	s = long_to_string(s)

    return sha.new(s).digest()

def private_key(u, s, p):
    """Given the username, salt, and cleartext password, return the private
    key, which is the long integer form of the hashed arguments."""
    h = hash(s + hash(u + p))
    x = string_to_long(h)
    return x

def _create_new_verifier(u, p, pf):
    """Given a username, cleartext password, and a prime field, pick a
    random salt and calculate the verifier.  The salt, verifier tuple is
    returned."""
    s = random_string(saltlen)
    n, g = pf
    v = pow(g, private_key(u, s, p), n)
    return (s, v)

def new_passwd(user, password):
    salt, verifier = _create_new_verifier(user, password, pf)
    return (salt, verifier)

# This is the authentication protocol.  There are two parts, the client and
# the host.  These functions are called from the client side.

def client_begin():
    # Here we could optionally query the host for the pfid and salt, or
    # indeed the pf itself plus salt.  We'd have to verify that n and g
    # are valid in the latter case, and we need a local copy anyway in the
    # former.

    pfid = 0
    n, g = pf

    # Pick a random number and send it to the host, who responds with
    # the user's salt and more random numbers.  Note that in the standard
    # SRP implementation, u is derived from B.

    a = random_long(ablen)
    A = pow(g, a, n)

    return (A, a, g, n)

def client_key(user, passphrase, s, B, u, keys, key_func=private_key):
    A, a, g, n = keys

    # We don't trust the host.  Perhaps the host is being spoofed.

    if B <= 0 or n <= B:
	raise ImproperKeyValue

    # Calculate the shared, secret session key.

    x = key_func(user, s, passphrase)
    v = 3 * pow(g, x, n)
    t = B
    if t < v:
	t = t + n
    S = pow(t - v, a + u * x, n)
    K = hash(S)

    # Compute the authentication proof.
    # This verifies that we do indeed know the same session key,
    # implying that we knew the correct password (even though the host
    # doesn't know the password!)

    m = _client_authenticator(K, n, g, user, s, A, B, u)

    return (K, m)

# The next function is called from the host side.

def host_begin(user, A, s, v):
    """Look the user up in the passwd database, calculate our version of
    the session key, and return it along with a keyed hash of the values
    used in the calculation as proof.  The client must match this proof."""
    n, g = pf

    # We don't trust the client, who might be trying to send bogus data in
    # order to break the protocol.

    if A <= 0 or n <= A:
	raise ImproperKeyValue

    # Pick our random public keys.

    B = 0
    while B == 0:
	b = random_long(ablen)
	B = ((3*v) + pow(g, b, n)) % n
    u = pow(g, random_long(tlen), n)

    # Calculate the (private, shared secret) session key.

    t = (A * pow(v, u, n)) % n
    if t <= 1 or t + 1 == n:
	raise ImproperKeyValue  # WeakKeyValue -- could be our fault so retry
    S = pow(t, b, n)
    K = hash(S)

    # Create the proof using a keyed hash.

    m = _client_authenticator(K, n, g, user, s, A, B, u)

    return (B, u, K, m)

# These two functions calculate the "proofs": keyed hashes of values used
# in the computation of the key.

def _client_authenticator(K, n, g, user, s, A, B, u):
    A = long_to_string(A, 128)
    B = long_to_string(B, 128)
    u = long_to_string(u, 128)
    return hmac.new(K, hash(n) + hash(g) + hash(user) + s + A + B + u, sha)

def host_authenticator(K, A, m):
    A = long_to_string(A, 128)
    return hmac.new(K, A + m, sha)

def test_SRP():
    s, v = new_passwd('user', 'password')
    keys = client_begin()
    B, u, Khost, mhost = host_begin('user', keys[0], s, v)
    Kclient, mclient = client_key('user', 'password', s, B, u, keys)
    assert Khost == Kclient
    assert mhost.digest() == mclient.digest()
    Kclient, mclient = client_key('user', 'bad password', s, B, u, keys)
    assert Khost != Kclient
    assert mhost.digest() != mclient.digest()
    Kclient, mclient = client_key('user', 'password', '12345', B, u, keys)
    assert Khost != Kclient
    assert mhost.digest() != mclient.digest()
