#!/usr/bin/env python3
"""Rejected solution: the obvious one.

Parses coordinates with float() and compares.  This is what most people write
first, and it is wrong for exactly the reason the problem exists: a double
carries about 15-17 significant decimal digits, so a coordinate one unit in
the last place outside the box rounds to the same double as the boundary
itself and is reported as inside.

Included in submissions/wrong_answer/ so that the test data is required to
catch it.  If this ever gets Accepted, the test data has stopped doing its
job.
"""
import sys


def main():
    data = sys.stdin.buffer.read().split()
    n = int(data[0])
    lat_lo, lat_hi, lon_lo, lon_hi = (float(t) for t in data[2:6])
    out = []
    p = 6
    for _ in range(n):
        name = data[p]
        lat = float(data[p + 1])
        lon = float(data[p + 2])
        p += 3
        if lat_lo <= lat <= lat_hi and lon_lo <= lon <= lon_hi:
            out.append(name.decode())
    sys.stdout.write(f"{len(out)}\n")
    sys.stdout.write("".join(s + "\n" for s in out))


if __name__ == "__main__":
    main()
