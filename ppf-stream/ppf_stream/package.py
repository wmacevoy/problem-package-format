#!/usr/bin/env python3
"""Build the package a chief judge uploads to the contest system.

    package.py PROBLEM --option 1|2 [-o OUT.zip] [--needs PATH ...]

  --option 1   expanded: every pin replaced by its checked data.  An ordinary
               problem; the contest system stores the full data.
  --option 2   generated on the judgehost: the problem becomes interactive in
               name only.  The output validator regenerates the data on first
               use, checks every pin, and caches it; the contest system stores
               pins and generators.  Works on an unchanged contest system, but
               a submission that reads its input to end of file hangs under
               problemtools' interactive harness, so accepted solutions that do
               are listed as a warning.

The repository is never modified: the package is built in a scratch copy and
written as OUT.zip (default: <problem>-option<N>.zip), files at the zip root.
Option 2 also warms this machine's cache (PPF_STREAM_CACHE) and checks every
pin before writing the zip.
"""

import argparse
import importlib.util
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

HERE = pathlib.Path(__file__).resolve().parent
EOF_READ = re.compile(r"sys\.stdin(\.buffer)?\.read\(\s*\)|sys\.stdin(\.buffer)?\.readlines\(|"
                      r"for\s+\w+\s+in\s+sys\.stdin\b|open\(\s*0\s*\)|fileinput\.input\(")


def load(name):
    spec = importlib.util.spec_from_file_location(f"ppf_{name}", HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def eof_readers(pkg):
    """Accepted Python solutions that look like they read stdin to end of file."""
    acc = pkg / "submissions" / "accepted"
    out = []
    for f in sorted(acc.rglob("*.py")) if acc.is_dir() else []:
        if EOF_READ.search(f.read_text(errors="replace")):
            out.append(f.relative_to(pkg).as_posix())
    return out


def default_needs(pkg):
    """Package paths the generator reads besides generators/ (a heuristic)."""
    text = "".join(f.read_text(errors="replace") for f in (pkg / "generators").rglob("*")
                   if f.is_file() and f.suffix not in (".xz", ".gz", ".zip"))
    return [p for p in ("submissions/accepted",) if (pkg / p).is_dir() and "accepted" in text]


def make_option2(pkg, needs):
    convert = load("convert")
    if (pkg / "output_validators").exists():
        raise SystemExit("package: option 2 needs the default output validator; "
                         "this problem has its own (use option 1)")
    if re.search(r"^validation:.*interactive", (pkg / "problem.yaml").read_text(), re.M):
        raise SystemExit("package: already interactive; option 2 does not apply (use option 1)")
    ov = pkg / "output_validators" / "ppf_stream"
    ov.mkdir(parents=True)
    for name in ("validate.py", "default_judge.py"):
        shutil.copy(HERE / name, ov / name)
    convert.executable(ov / "run", '#!/bin/sh\nexec python3 "$(dirname "$0")/validate.py" "$@"\n')
    convert.executable(ov / "build", convert.NOOP_BUILD)
    rg = ov / "regen"
    for p in ["generators", *needs]:
        src, dst = pkg / p, rg / p
        if src.is_dir():
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(src, dst)
    shutil.copy(pkg / "generators" / "regen.sh", rg / "regen.sh")
    convert.set_interactive(pkg)
    return convert.wrap_input_validators(pkg)


def write_zip(root, out):
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(root.rglob("*")):
            if f.is_dir() or "__pycache__" in f.parts:
                continue
            info = zipfile.ZipInfo(f.relative_to(root).as_posix(), date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = (0o755 if os.access(f, os.X_OK) else 0o644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, f.read_bytes())


def main():
    ap = argparse.ArgumentParser(description="Build an uploadable package (option 1 or 2).")
    ap.add_argument("problem", type=pathlib.Path)
    ap.add_argument("--option", choices=["1", "2"], required=True)
    ap.add_argument("-o", "--out", type=pathlib.Path)
    ap.add_argument("--needs", nargs="*", help="option 2: package paths the generator reads")
    a = ap.parse_args()
    src = a.problem.resolve()
    if not (src / "generators" / "regen.sh").is_file():
        raise SystemExit(f"package: {src} has no generators/regen.sh")
    out = (a.out or pathlib.Path(f"{src.name}-option{a.option}.zip")).resolve()

    with tempfile.TemporaryDirectory() as tmp:
        pkg = pathlib.Path(tmp) / src.name
        shutil.copytree(src, pkg, symlinks=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        if a.option == "1":
            r = subprocess.run([sys.executable, str(HERE / "expand.py"), str(pkg)])
            if r.returncode:
                return r.returncode
        else:
            needs = a.needs if a.needs is not None else default_needs(pkg)
            make_option2(pkg, needs)
            r = subprocess.run([sys.executable, str(pkg / "output_validators" / "ppf_stream" / "validate.py"),
                                "--warm", str(pkg / "data")])
            if r.returncode:
                print("package: a pin did not check; nothing written", file=sys.stderr)
                return r.returncode
        write_zip(pkg, out)

    print(f"package: wrote {out} ({out.stat().st_size:,} bytes), option {a.option}")
    if a.option == "2":
        readers = eof_readers(src)
        for f in readers:
            print(f"WARNING: {f} seems to read input to end of file; under option 2 it will "
                  f"time out on harnesses that keep input open (problemtools does)")
        if readers:
            print("WARNING: use option 1 for this problem, or confirm in a test run that "
                  "every accepted solution passes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
