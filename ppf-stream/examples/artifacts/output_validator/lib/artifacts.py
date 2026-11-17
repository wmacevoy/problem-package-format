"""Deterministic construction of an `artifacts` test case.

This module is the generator.  It lives inside `output_validator/` because in
this problem the output validator *is* the generator: the specification says
that for an interactive problem "the program will only receive the output
produced by the validator and will not have direct access to the input file",
so whatever the validator writes on standard output is the test case.

The `.in` file therefore holds parameters, not data.  Nothing here depends on
`random`, `numpy`, the wall clock, the locale, or floating point, so the same
parameters produce the same bytes on any machine.
"""

import hashlib
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from prng import Stream  # noqa: E402

# CPython 3.11 introduced a DEFAULT 4300-digit cap on int->str conversion.
# A 4000-digit coordinate is ~4003 digits; a product of two is ~8000 and
# raises ValueError.  Setting this explicitly is not optional.
sys.set_int_max_str_digits(0)

MAX_NAME_LEN = 100
WORDS_PATH = pathlib.Path(__file__).resolve().parent / "words.txt"

# Twelve percent of artifacts sit exactly on a box edge, twelve percent one
# unit in the last place inside it, twelve percent one unit outside.  Those
# three categories are what make the problem require exact arithmetic.
CATEGORIES = (("on", 12), ("just_in", 12), ("just_out", 12),
              ("inside", 24), ("anywhere", 40))
_CATS = [c for c, w in CATEGORIES for _ in range(w)]


def load_words():
    raw = WORDS_PATH.read_bytes()
    return sorted({w.decode("ascii") for w in raw.split(b"\n") if w})


def fmt(num, digits):
    sign = "-" if num < 0 else ""
    whole, frac = divmod(abs(num), 10 ** digits)
    if digits == 0:
        return f"{sign}{whole}"
    return f"{sign}{whole}.{str(frac).rjust(digits, '0')}"


def make_name(st, words):
    k = st.between(1, 4)
    parts = [st.choice(words) for _ in range(k)]
    while len(parts) > 1 and len("-".join(parts)) > MAX_NAME_LEN:
        parts.pop()
    return "-".join(parts)[:MAX_NAME_LEN]


def make_box(st, digits):
    scale = 10 ** digits
    lat_half = st.between(scale, 20 * scale)
    lon_half = st.between(scale, 40 * scale)
    lat_c = st.between(-90 * scale + lat_half, 90 * scale - lat_half)
    lon_c = st.between(-180 * scale + lon_half, 180 * scale - lon_half)
    return (lat_c - lat_half, lat_c + lat_half, lon_c - lon_half, lon_c + lon_half)


def place(st, box, digits):
    lat_lo, lat_hi, lon_lo, lon_hi = box
    scale = 10 ** digits
    cat = st.choice(_CATS)

    def near(lo, hi, d):
        return lo + d if st.below(2) else hi - d

    if cat == "on":
        return near(lat_lo, lat_hi, 0), st.between(lon_lo, lon_hi)
    if cat == "just_in":
        return near(lat_lo, lat_hi, 1), st.between(lon_lo, lon_hi)
    if cat == "just_out":
        return near(lat_lo, lat_hi, -1), st.between(lon_lo, lon_hi)
    if cat == "inside":
        return st.between(lat_lo, lat_hi), st.between(lon_lo, lon_hi)
    return st.between(-90 * scale, 90 * scale), st.between(-180 * scale, 180 * scale)


def build(case, salt, n, digits):
    """Return (test case bytes, expected answer bytes).

    The reference answer is computed here, while the data is being built, by
    exact integer comparison.  There is no separate accepted solution to run
    and no `.ans` file to store.
    """
    words = load_words()
    root = Stream(salt, "artifacts", case)
    box = make_box(root.sub("box"), digits)
    lat_lo, lat_hi, lon_lo, lon_hi = box

    chunks = [f"{n} {digits}\n".encode("ascii"),
              (" ".join(fmt(v, digits) for v in box) + "\n").encode("ascii")]
    inside = []
    for i in range(n):
        si = root.sub(i)
        name = make_name(si.sub("name"), words)
        lat, lon = place(si.sub("pos"), box, digits)
        chunks.append(f"{name} {fmt(lat, digits)} {fmt(lon, digits)}\n".encode("ascii"))
        if lat_lo <= lat <= lat_hi and lon_lo <= lon <= lon_hi:
            inside.append(name)

    answer = f"{len(inside)}\n".encode("ascii") + "".join(s + "\n" for s in inside).encode("ascii")
    return b"".join(chunks), answer


def digest(case, salt, n, digits):
    """Cache key: everything that determines the bytes."""
    h = hashlib.sha256()
    h.update(f"artifacts/1\x1f{salt}\x1f{case}\x1f{n}\x1f{digits}\x1f".encode())
    h.update(pathlib.Path(__file__).read_bytes())
    h.update((pathlib.Path(__file__).parent / "prng.py").read_bytes())
    h.update(WORDS_PATH.read_bytes())
    return h.hexdigest()
