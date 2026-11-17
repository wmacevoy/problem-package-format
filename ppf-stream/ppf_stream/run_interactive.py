#!/usr/bin/env python3
"""Local harness: run interactive test cases the way a judge system would.

    run_interactive.py <package> <submission> [case.in ...]

Wires the output validator and the submission together with two pipes, per
the specification's interactive invocation.  With no cases named, runs every
`.in` under the package's `data/`.

Not part of any package; this exists so setters can exercise a problem
without standing up a judge system.
"""

import pathlib
import subprocess
import sys
import tempfile
import time

VERDICT = {42: "AC", 43: "WA"}


def run(pkg, submission, case):
    validator = pkg / "output_validator" / "validate.py"
    ans = case.with_suffix(".ans")
    ans.touch(exist_ok=True)          # must exist; may be empty
    cmd = ([sys.executable, str(submission)] if submission.suffix == ".py"
           else [str(submission)])
    with tempfile.TemporaryDirectory() as fb:
        t0 = time.perf_counter()
        val = subprocess.Popen(
            [sys.executable, str(validator), str(case), str(ans), fb],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        sub = subprocess.Popen(cmd, stdin=val.stdout, stdout=val.stdin,
                               stderr=subprocess.DEVNULL)
        # The parent must drop its own handles or neither side ever sees EOF.
        val.stdout.close()
        val.stdin.close()
        sub.wait()
        rc = val.wait()
        why = val.stderr.read().decode().strip().splitlines()
        msg = pathlib.Path(fb) / "judgemessage.txt"
        detail = msg.read_text().strip() if msg.exists() else ""
    return rc, time.perf_counter() - t0, why[-1] if why else "", detail


def main():
    pkg = pathlib.Path(sys.argv[1]).resolve()
    submission = pathlib.Path(sys.argv[2]).resolve()
    cases = ([pathlib.Path(a) for a in sys.argv[3:]]
             or sorted((pkg / "data").rglob("*.in")))
    bad = 0
    for case in cases:
        rc, dt, why, detail = run(pkg, submission, case)
        print(f"  {case.name:<24} {VERDICT.get(rc, f'JUDGE ERROR({rc})'):<4} "
              f"{dt:6.2f}s   {why}")
        if detail:
            print(f"      {detail}")
        bad += rc not in VERDICT
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
