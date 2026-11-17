"""Vendored deterministic PRNG for problem test-data generation.

Design rules this module exists to enforce:

  * Counter-based, not sequential.  Every stream is a pure function of the
    strings used to derive it, so test cases are independent of one another
    and of generation order.  Inserting a new test case does not perturb any
    other test case.
  * No dependency that can drift.  Only hashlib (stdlib, SHA-256 fixed by
    FIPS 180-4) is used.  No `random`, no `numpy`, no C extension.
  * Every derived operation is specified here, not inherited from a library
    whose algorithm may change between versions.

Do not add a method to this module without writing down its algorithm.
"""

import hashlib

_SEP = b"\x1f"  # unit separator; cannot occur in the ASCII labels we use


def _key(parts):
    """Domain-separated key derivation.  Order and content of `parts` fully
    determine the stream; nothing else does."""
    blob = _SEP.join(str(p).encode("utf-8") for p in parts)
    return hashlib.sha256(blob).digest()


class Stream:
    """A counter-mode SHA-256 bit stream.

    Block i is SHA256(key || i as 8-byte big-endian).  Bits are consumed
    most-significant-first from the concatenation of blocks 0, 1, 2, ...
    """

    __slots__ = ("_k", "_buf", "_nbits", "_ctr")

    def __init__(self, *parts):
        self._k = _key(parts)
        self._buf = 0
        self._nbits = 0
        self._ctr = 0

    def _refill(self):
        blk = hashlib.sha256(self._k + self._ctr.to_bytes(8, "big")).digest()
        self._ctr += 1
        self._buf = (self._buf << 256) | int.from_bytes(blk, "big")
        self._nbits += 256

    def bits(self, k):
        """Return k uniform random bits as a non-negative int."""
        if k <= 0:
            return 0
        while self._nbits < k:
            self._refill()
        self._nbits -= k
        out = self._buf >> self._nbits
        self._buf &= (1 << self._nbits) - 1
        return out

    def below(self, n):
        """Uniform integer in [0, n).  Rejection sampling on the smallest
        covering power of two -- unbiased, and the algorithm is fixed here so
        it cannot change under us the way random.randrange can."""
        if n <= 0:
            raise ValueError("below(n) requires n >= 1")
        if n == 1:
            return 0
        k = (n - 1).bit_length()
        while True:
            v = self.bits(k)
            if v < n:
                return v

    def between(self, lo, hi):
        """Uniform integer in [lo, hi], inclusive on both ends."""
        return lo + self.below(hi - lo + 1)

    def choice(self, seq):
        return seq[self.below(len(seq))]

    def shuffle(self, lst):
        """Fisher-Yates, descending, swapping index i with below(i+1).
        Written out because random.shuffle carries no cross-version
        guarantee."""
        for i in range(len(lst) - 1, 0, -1):
            j = self.below(i + 1)
            lst[i], lst[j] = lst[j], lst[i]
        return lst

    def sub(self, *parts):
        """Derive an independent child stream.  Used to keep separate concerns
        (names vs coordinates) from perturbing each other."""
        return Stream(self._k.hex(), *parts)
