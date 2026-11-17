#!/usr/bin/env python3
"""The specification's default output validator, as a program.

    default_judge.py input answer feedback_dir [flags] < team_output

An interactive problem must name its own output validator, so a problem that
used the default one needs a copy of it once it streams its data.  This is that
copy: tokens compared one by one, whitespace runs equivalent, case-insensitive,
with the flags the specification defines:

    case_sensitive             compare tokens exactly, letter case included
    space_change_sensitive     whitespace must match too
    float_relative_tolerance e accept floats within relative error e
    float_absolute_tolerance e accept floats within absolute error e
    float_tolerance e          both of the above

Exit 42 accepted, 43 wrong answer; the first difference goes to judgemessage.txt.
"""

import math
import pathlib
import re
import sys

AC, WA, JUDGE_ERROR = 42, 43, 1


def parse_flags(argv):
    opts = {"case": False, "space": False, "rel": None, "abs": None}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "case_sensitive":
            opts["case"] = True
        elif a == "space_change_sensitive":
            opts["space"] = True
        elif a in ("float_relative_tolerance", "float_absolute_tolerance", "float_tolerance"):
            if i + 1 >= len(argv):
                raise ValueError(f"{a} needs a value")
            v = float(argv[i + 1])
            i += 1
            if a != "float_absolute_tolerance":
                opts["rel"] = v
            if a != "float_relative_tolerance":
                opts["abs"] = v
        else:
            raise ValueError(f"unknown flag {a!r}")
        i += 1
    return opts


def tokens(text, space):
    if space:
        return [t for t in re.split(r"(\s+)", text) if t != ""]
    return text.split()


def as_float(t):
    try:
        x = float(t)
    except ValueError:
        return None
    return x if math.isfinite(x) else None


def same(judge, team, o):
    if judge == team:
        return True
    if not o["case"] and judge.lower() == team.lower():
        return True
    if o["rel"] is None and o["abs"] is None:
        return False
    a, b = as_float(judge), as_float(team)
    if a is None or b is None:
        return False
    d = abs(a - b)
    return (o["abs"] is not None and d <= o["abs"]) or (o["rel"] is not None and d <= o["rel"] * abs(a))


def main():
    if len(sys.argv) < 4:
        print("usage: default_judge.py input answer feedback_dir [flags] < team_output", file=sys.stderr)
        return JUDGE_ERROR
    _, answer, feedback = sys.argv[1:4]
    try:
        o = parse_flags(sys.argv[4:])
        judge = pathlib.Path(answer).read_bytes().decode("latin-1")
    except (ValueError, OSError) as e:
        print(f"default_judge: {e}", file=sys.stderr)
        return JUDGE_ERROR
    team = sys.stdin.buffer.read().decode("latin-1")
    jt, tt = tokens(judge, o["space"]), tokens(team, o["space"])

    msg = None
    for i, (j, t) in enumerate(zip(jt, tt)):
        if not same(j, t, o):
            msg = f"token {i + 1}: expected {j[:60]!r}, got {t[:60]!r}"
            break
    if msg is None and len(jt) != len(tt):
        msg = (f"output ended after {len(tt)} tokens, expected {len(jt)}" if len(tt) < len(jt)
               else f"{len(tt) - len(jt)} tokens of trailing output")
    if msg is None:
        return AC
    try:
        (pathlib.Path(feedback) / "judgemessage.txt").write_text(msg + "\n")
    except OSError:
        pass
    return WA


if __name__ == "__main__":
    sys.exit(main())
