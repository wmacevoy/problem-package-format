#!/usr/bin/env python3
"""Input validator: <validator> [args] < inputfile, exit 42 if valid.

    --positive-lat   require every latitude to be strictly positive
"""
import re, sys
sys.set_int_max_str_digits(0)
VALID, INVALID = 42, 1
NAME = re.compile(rb"[a-z]+(-[a-z]+)*\Z")

def bad(msg):
    print(msg, file=sys.stderr)
    sys.exit(INVALID)

def dec(tok, digits, lo, hi, what):
    """Parse an exact decimal with exactly `digits` fractional places."""
    m = re.fullmatch(rb"(-?)(\d+)\.(\d+)" if digits else rb"(-?)(\d+)()", tok)
    if not m:
        bad(f"{what}: malformed decimal {tok[:40]!r}")
    if digits and len(m.group(3)) != digits:
        bad(f"{what}: {len(m.group(3))} fractional digits, expected {digits}")
    v = int(m.group(2) + m.group(3)) * (-1 if m.group(1) else 1)
    if not (lo <= v <= hi):
        bad(f"{what}: {tok[:24]!r} out of range")
    return v

def main():
    positive = "--positive-lat" in sys.argv[1:]
    tok = sys.stdin.buffer.read().split()
    if len(tok) < 6:
        bad("truncated input")
    n, d = int(tok[0]), int(tok[1])
    if not (1 <= n <= 1_000_000): bad(f"n={n} out of range")
    if not (0 <= d <= 4000):      bad(f"d={d} out of range")
    LAT, LON = 90 * 10**d, 180 * 10**d
    lat_lo = dec(tok[2], d, -LAT, LAT, "box lat_lo")
    lat_hi = dec(tok[3], d, -LAT, LAT, "box lat_hi")
    dec(tok[4], d, -LON, LON, "box lon_lo")
    dec(tok[5], d, -LON, LON, "box lon_hi")
    if lat_lo > lat_hi: bad("box latitude interval is empty")
    if positive and not (0 < lat_lo and lat_hi < 90 * 10**d):
        bad(f"box must lie inside (0,90), got lat_lo= {tok[2][:24]!r}")

    want = 6 + 3 * n
    if len(tok) != want:
        bad(f"header says n={n} so {want} tokens expected, found {len(tok)}")
    for i in range(n):
        name, la, lo = tok[6+3*i], tok[7+3*i], tok[8+3*i]
        if not NAME.match(name) or not (1 <= len(name) <= 100):
            bad(f"artifact {i}: bad name {name[:40]!r}")
        v = dec(la, d, -LAT, LAT, f"artifact {i} latitude")
        dec(lo, d, -LON, LON, f"artifact {i} longitude")
        if positive and not (0 < v < 90 * 10**d):
            bad(f"artifact {i}: latitude must be in (0,90), got {la[:24]!r}")
    sys.exit(VALID)

main()
