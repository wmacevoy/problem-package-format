#!/usr/bin/env python3
"""Option 3: keep data no generator can make in generators/data.tar.xz.

    archive.py PACKAGE [CASE ...] [--replace]

A shim for a generator that is broken, drifted, or missing.  Each CASE (e.g.
secret/10_max.in; default: every data file of 2 MiB or more that holds data,
not a pin) is added to generators/data.tar.xz, and its data file becomes a
`#ppf-stream regen` pin like any generated case, so expand.py, package.py
(options 1 and 2), sync.py and patches all work unchanged.

generators/regen.sh unpacks the archive after the generator runs, so archived
cases override generated ones.  With no regen.sh, or with --replace (the
generator crashes), regen.sh only unpacks.  A case's patch is dropped: the
archive holds the edited bytes.  Archives are byte-for-byte reproducible.

To retire the shim when the generator is fixed: put the generator back in
regen.sh, delete the archive, and run `sync.py PACKAGE --repin`.
"""

import hashlib
import io
import lzma
import pathlib
import sys
import tarfile

LIMIT = 2 * 1024 * 1024
ARCHIVE = pathlib.Path("generators") / "data.tar.xz"
PATCHES = pathlib.Path("generators") / "patches"
MARK = "# ppf-stream option 3: unpack generators/data.tar.xz over data/"
UNPACK = f'''{MARK}
python3 - <<'PY'
import tarfile
with tarfile.open("generators/data.tar.xz") as t:
    for m in t.getmembers():
        assert m.isfile() and m.name.startswith("data/") and ".." not in m.name, m.name
    t.extractall(".")
PY
'''
HEADER = ("#!/bin/sh\n# Restore data/ from the package root. Used by ppf-stream to restore\n"
          "# the files pinned by '#ppf-stream regen' directives.\nset -e\nmkdir -p data/sample data/secret\n")


def is_pin(path):
    with open(path, "rb") as f:
        return f.read(18) == b"#ppf-stream regen "


def main(argv):
    replace = "--replace" in argv
    args = [a for a in argv if a != "--replace"]
    if not args or args[0].startswith("-"):
        print(__doc__.strip().split("\n\n")[1], file=sys.stderr)
        return 2
    pkg = pathlib.Path(args[0]).resolve()
    data = pkg / "data"
    if args[1:]:
        cases = args[1:]
    else:
        cases = [f.relative_to(data).as_posix() for f in sorted(data.rglob("*"))
                 if f.is_file() and not is_pin(f) and f.stat().st_size >= LIMIT]
    if not cases:
        print("archive: no case to archive (pinned cases must be expanded first: expand.py PACKAGE CASE)")
        return 1

    members = {}
    arc = pkg / ARCHIVE
    if arc.exists():                                     # keep what the archive already holds
        with tarfile.open(arc) as t:
            for m in t.getmembers():
                members[m.name] = t.extractfile(m).read()
    for rel in cases:
        f = data / rel
        if not f.is_file():
            print(f"archive: no such data file: data/{rel}", file=sys.stderr)
            return 1
        if is_pin(f):
            print(f"archive: data/{rel} is a pin; run expand.py PACKAGE {rel} first", file=sys.stderr)
            return 1
        members[f"data/{rel}"] = f.read_bytes()

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.GNU_FORMAT) as t:
        for name in sorted(members):
            ti = tarfile.TarInfo(name)
            ti.size, ti.mtime, ti.mode, ti.uid, ti.gid, ti.uname, ti.gname = \
                len(members[name]), 0, 0o644, 0, 0, "", ""
            t.addfile(ti, io.BytesIO(members[name]))
    arc.parent.mkdir(parents=True, exist_ok=True)
    arc.write_bytes(lzma.compress(buf.getvalue(), preset=9 | lzma.PRESET_EXTREME))

    regen = pkg / "generators" / "regen.sh"
    text = regen.read_text() if regen.exists() and not replace else HEADER
    if MARK not in text:
        regen.write_text(text.rstrip("\n") + "\n" + UNPACK)
    regen.chmod(0o755)

    for rel in cases:
        b = members[f"data/{rel}"]
        (data / rel).write_text(f"#ppf-stream regen {rel} sha256={hashlib.sha256(b).hexdigest()} size={len(b)}\n")
        (pkg / PATCHES / f"{rel}.patch").unlink(missing_ok=True)
        print(f"archive: {rel}: {len(b):,} bytes archived and pinned")
    raw = sum(len(b) for b in members.values())
    size = arc.stat().st_size
    print(f"archive: {ARCHIVE}: {len(members)} file(s), {raw:,} -> {size:,} bytes ({raw / size:.1f}x)")
    if size > LIMIT:
        print(f"archive: note: over 2 MiB; a repository size check may reject it "
              f"(GitHub warns at 50 MB per file)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
