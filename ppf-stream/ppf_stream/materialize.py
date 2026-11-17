#!/usr/bin/env python3
"""Turn a streaming package into a conventional one.

    materialize.py <package> <outdir>

Runs each test case's `gen` and writes real `.in` and `.ans` files, so the
same sources can produce either form:

  - the streaming form, where `data/*.in` holds arguments and nothing is
    stored, for the repository; and
  - the conventional form, with every byte on disk, for uploading to a judge
    system, for archiving, and for `submission < big.in | diff big.ans -`
    while developing.

The point of keeping both reachable is that the setter's `judge` is an
ordinary output validator either way, so nothing about the problem changes
between the two.
"""

import pathlib
import shutil
import subprocess
import sys


def main():
    if len(sys.argv) != 3:
        print("usage: materialize.py <package> <outdir>", file=sys.stderr)
        return 1
    pkg, out = pathlib.Path(sys.argv[1]).resolve(), pathlib.Path(sys.argv[2]).resolve()
    ov = pkg / "output_validator"
    gen, sol = ov / "gen", ov / "solution"
    if not gen.exists():
        print(f"no {gen}", file=sys.stderr)
        return 1

    total = 0
    for src in sorted((pkg / "data").rglob("*.in")):
        rel = src.relative_to(pkg)
        dst = out / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        args = src.read_text().split()
        with open(dst, "wb") as fo:
            subprocess.run([str(gen), *args], stdout=fo, check=True)
        n = dst.stat().st_size
        total += n

        ans = dst.with_suffix(".ans")
        if sol.exists():
            with open(dst, "rb") as fi, open(ans, "wb") as fo:
                subprocess.run([str(sol)], stdin=fi, stdout=fo, check=True)
        else:
            ans.touch()
        print(f"  {rel}  {n:,} bytes")

    for extra in ("problem.yaml", "statement", "submissions", "output_validator"):
        s = pkg / extra
        if s.is_dir():
            shutil.copytree(s, out / extra, dirs_exist_ok=True)
        elif s.exists():
            shutil.copy2(s, out / extra)

    print(f"  total {total:,} bytes of test data")
    return 0


if __name__ == "__main__":
    sys.exit(main())
