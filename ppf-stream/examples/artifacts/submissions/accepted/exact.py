#!/usr/bin/env python3
"""Accepted solution: exact decimal comparison.

Every coordinate in the input carries exactly d fractional digits, so the
decimal point is positional and can simply be removed.  What remains is an
integer numerator over 10**d, and box membership is an integer comparison.
No float is constructed, so the verdict cannot depend on the platform.
"""
import sys

sys.set_int_max_str_digits(0)


def num(tok):
    """Exact decimal token -> integer numerator over 10**d."""
    neg = tok.startswith("-")
    if neg or tok.startswith("+"):
        tok = tok[1:]
    tok = tok.replace(".", "", 1)
    v = int(tok)
    return -v if neg else v


def main():
    data = sys.stdin.buffer.read().split()
    n = int(data[0])
    lat_lo, lat_hi, lon_lo, lon_hi = (num(t.decode()) for t in data[2:6])
    out = []
    p = 6
    for _ in range(n):
        name = data[p]
        lat = num(data[p + 1].decode())
        lon = num(data[p + 2].decode())
        p += 3
        if lat_lo <= lat <= lat_hi and lon_lo <= lon <= lon_hi:
            out.append(name.decode())
    sys.stdout.write(f"{len(out)}\n")
    sys.stdout.write("".join(s + "\n" for s in out))


if __name__ == "__main__":
    main()
