#!/usr/bin/env python3
"""Convert a package whose large test data is in Git LFS to generated data.

    convert.py PACKAGE --regen "COMMAND" --mode build|interactive [--needs PATH ...]

Every LFS pointer under PACKAGE/data becomes a one-line directive,

    #ppf-stream regen secret/10_max.in sha256=<hex> size=<bytes>

pinned to the sha256 and size the pointer already records, so nothing is
downloaded.  COMMAND is the problem's own way to regenerate data/, run from the
package root; it is written to generators/regen.sh.  The LFS rules for those
files are removed from every .gitattributes between PACKAGE and the repository
root.  Then, by mode:

  build        (option 1)  nothing else.  `expand.py PACKAGE` restores the data
               at build time; judges and contestants see an ordinary problem.

  interactive  (option 2)  the problem becomes interactive in name only.
               output_validators/ppf_stream/ gets validate.py, default_judge.py,
               and regen/: a copy of generators/ and of each --needs path, so the
               validator can regenerate at judge time and cache the result.  Each
               input validator is wrapped by input_guard.py, which accepts a
               directive and runs the original on everything else.

Legacy packages only (no problem_format_version), using the default output
validator; a custom one is refused for now.  Check the result with
`expand.py PACKAGE --check` (build) or `validate.py --warm PACKAGE/data` (interactive).
"""

import argparse
import os
import pathlib
import re
import shutil
import stat
import sys

HERE = pathlib.Path(__file__).resolve().parent
POINTER = re.compile(rb"^version https://git-lfs.github.com/spec/v1\noid sha256:([0-9a-f]{64})\nsize (\d+)\n")
GUARD_NAME = "input_guard.py"
# Judges use a program directory's `run` only when it also has an executable
# `build`; without one they guess the language and pick a main file themselves.
NOOP_BUILD = "#!/bin/sh\n# Present so judges use ./run (nothing to compile).\nexit 0\n"
# Judges keep checktestdata in different places (problemtools bundles its own
# copy and does not put it on PATH), so a guarded .ctd validator looks for it.
CTD_FINDER = """#!/bin/sh
# Run checktestdata from $CHECKTESTDATA, PATH, or a bundled problemtools copy.
c="${CHECKTESTDATA:-$(command -v checktestdata 2>/dev/null)}"
if [ -z "$c" ]; then
  for f in /opt/venvs/*/lib/python3*/site-packages/problemtools/support/checktestdata \\
           /usr/lib/python3/dist-packages/problemtools/support/checktestdata \\
           /usr/local/lib/python3*/*-packages/problemtools/support/checktestdata; do
    [ -x "$f" ] && c=$f && break
  done
fi
[ -n "$c" ] || { echo "checktestdata not found: set CHECKTESTDATA" >&2; exit 1; }
# checktestdata exits 0 (valid) or 1 (invalid); input validators exit 42 or 43.
"$c" "$@"
case $? in 0) exit 42 ;; 1) exit 43 ;; *) exit 1 ;; esac
"""


def executable(path, text):
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def lfs_pointers(pkg):
    out = []
    for f in sorted((pkg / "data").rglob("*")):
        if f.is_file() and f.stat().st_size < 1024:
            m = POINTER.match(f.read_bytes())
            if m:
                out.append((f, m.group(1).decode(), int(m.group(2))))
    return out


def repo_root(pkg):
    for d in [pkg, *pkg.parents]:
        if (d / ".git").exists():
            return d
    return pkg


def drop_lfs_rules(pkg, files):
    """Remove the .gitattributes lines that name exactly these files."""
    changed = []
    root = repo_root(pkg)
    for d in [pkg, *pkg.parents]:
        ga = d / ".gitattributes"
        if ga.is_file():
            names = {str(f.relative_to(d)) for f in files}
            lines = ga.read_text().splitlines(keepends=True)
            keep = [l for l in lines if not (l.split() and l.split()[0] in names and "filter=lfs" in l)]
            if len(keep) != len(lines):
                ga.write_text("".join(keep))
                changed.append((ga, len(lines) - len(keep)))
        if d == root:
            break
    return changed


def wrap_input_validators(pkg):
    """input_validators/X -> input_validators/X/ with a guard in front of the original."""
    iv = pkg / "input_validators"
    if not iv.is_dir():
        return []
    wrapped = []
    for prog in sorted(iv.iterdir()):
        if prog.name.startswith("."):
            continue
        if prog.is_dir():
            if (prog / GUARD_NAME).exists():
                continue
            if (prog / "run").is_file():
                (prog / "run").rename(prog / "run.orig")
                cmd = 'sh "$d/run.orig"'      # judges run `run` with sh; it need not be executable
            else:
                pys = sorted(prog.glob("*.py"))
                if len(pys) != 1:
                    raise SystemExit(f"convert: cannot tell how to run {prog}")
                cmd = f'python3 "$d/{pys[0].name}"'
            target = prog
        else:
            target = iv / prog.stem
            if target.exists():
                target = iv / (prog.stem + "_guarded")
            target.mkdir()
            shutil.move(str(prog), target / prog.name)
            ext = prog.suffix
            if ext == ".py":
                cmd = f'python3 "$d/{prog.name}"'
            elif ext == ".ctd":
                executable(target / "ctd.sh", CTD_FINDER)
                cmd = f'sh "$d/ctd.sh" "$d/{prog.name}"'
            elif ext in (".cpp", ".cc"):
                executable(target / "build", f'#!/bin/sh\nset -e\nd="$(cd "$(dirname "$0")" && pwd)"\n'
                                             f'c++ -O2 -std=gnu++17 -o "$d/orig" "$d/{prog.name}"\n')
                cmd = '"$d/orig"'
            else:
                raise SystemExit(f"convert: cannot tell how to run input validator {prog}")
        shutil.copy(HERE / GUARD_NAME, target / GUARD_NAME)
        if not (target / "build").exists():
            executable(target / "build", NOOP_BUILD)
        executable(target / "run", '#!/bin/sh\nd="$(cd "$(dirname "$0")" && pwd)"\n'
                                   f'exec python3 "$d/{GUARD_NAME}" {cmd} -- "$@"\n')
        wrapped.append(target.relative_to(pkg))
    return wrapped


def set_interactive(pkg):
    y = pkg / "problem.yaml"
    text = y.read_text()
    if re.search(r"^problem_format_version:", text, re.M):
        raise SystemExit("convert: only legacy packages are supported so far")
    if re.search(r"^validation:", text, re.M):
        text = re.sub(r"^validation:.*$", "validation: custom interactive", text, count=1, flags=re.M)
    else:
        text = text.rstrip("\n") + "\nvalidation: custom interactive\n"
    y.write_text(text)


def main():
    ap = argparse.ArgumentParser(description="Convert LFS test data to pinned, generated data.")
    ap.add_argument("package", type=pathlib.Path)
    ap.add_argument("--regen", required=True, help="command, run from the package root, that writes data/")
    ap.add_argument("--mode", choices=["build", "interactive"], required=True)
    ap.add_argument("--needs", nargs="*", default=[], help="package paths the generator reads, besides generators/")
    a = ap.parse_args()
    pkg = a.package.resolve()

    ptrs = lfs_pointers(pkg)
    if not ptrs:
        raise SystemExit(f"convert: no LFS pointers under {pkg / 'data'}")
    if a.mode == "interactive" and (pkg / "output_validators").exists():
        raise SystemExit("convert: a custom output validator is not supported yet")

    regen = pkg / "generators" / "regen.sh"
    executable(regen, "#!/bin/sh\n# Regenerate data/ from the package root. Used by ppf-stream to restore\n"
                      "# the files pinned by '#ppf-stream regen' directives.\nset -e\n"
                      f"mkdir -p data/sample data/secret\n{a.regen}\n")

    for f, sha, size in ptrs:
        rel = f.relative_to(pkg / "data")
        f.write_text(f"#ppf-stream regen {rel} sha256={sha} size={size}\n")
    rules = drop_lfs_rules(pkg, [f for f, _, _ in ptrs])

    if a.mode == "interactive":
        ov = pkg / "output_validators" / "ppf_stream"
        ov.mkdir(parents=True)
        for name in ("validate.py", "default_judge.py"):
            shutil.copy(HERE / name, ov / name)
        executable(ov / "run", '#!/bin/sh\nexec python3 "$(dirname "$0")/validate.py" "$@"\n')
        executable(ov / "build", NOOP_BUILD)
        rg = ov / "regen"
        for p in ["generators", *a.needs]:
            src = pkg / p
            dst = rg / p
            if src.is_dir():
                shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(src, dst)
        shutil.copy(regen, rg / "regen.sh")
        set_interactive(pkg)
        wrapped = wrap_input_validators(pkg)
    else:
        wrapped = []

    print(f"converted {len(ptrs)} file(s) in {pkg.name} ({a.mode}): "
          f"{sum(s for _, _, s in ptrs):,} bytes now generated")
    for ga, n in rules:
        print(f"  {ga}: removed {n} LFS rule(s)")
    for w in wrapped:
        print(f"  guarded input validator {w}")


if __name__ == "__main__":
    main()
