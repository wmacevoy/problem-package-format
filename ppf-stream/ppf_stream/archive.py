#!/usr/bin/env python3
"""Option 3: keep data no generator can make in generators/data.tar.xz.

    archive.py PACKAGE [CASE ...] [--replace]

A shim for a generator that is broken, drifted, or missing.  Each CASE (e.g.
secret/10_max.in; default: every data file of 2 MiB or more that holds data,
not a pin) is added to the archive, and its data file becomes a
`#ppf-stream regen` pin like any generated case, so expand.py, package.py
(options 1 and 2), sync.py and patches all work unchanged.  With no CASE to
add, an existing archive is repacked (and re-split) as it is.

An archive larger than PART bytes (just under 2 MiB) is written as
generators/data.tar.xz.part-000, -001, ... so that no file passes a per-file
size limit; regen.sh joins the parts before unpacking.  The parts are slices
of one xz stream: none is useful alone, and the repository still holds the
whole archive.

generators/regen.sh unpacks the archive after the generator runs, so archived
cases override generated ones.  With no regen.sh, or with --replace (the
generator crashes), regen.sh only unpacks.  A case's patch is dropped: the
archive holds the edited bytes.  Archives are byte-for-byte reproducible.

To retire the shim when the generator is fixed: put the generator back in
regen.sh, delete the archive (all its parts), and run `sync.py PACKAGE --repin`.
"""

import hashlib
import io
import lzma
import pathlib
import re
import sys
import tarfile

LIMIT = 2 * 1024 * 1024          # cases at least this large are archived by default
PART = 2_000_000                 # largest file written: under a 2 MiB per-file limit
GEN = pathlib.Path("generators")
ARCHIVE = GEN / "data.tar.xz"
PATCHES = GEN / "patches"
MARK = "# ppf-stream option 3: unpack generators/data.tar.xz over data/"
UNPACK = f'''{MARK}
python3 - <<'PY'
import glob, io, tarfile
parts = sorted(glob.glob("generators/data.tar.xz.part-*")) or ["generators/data.tar.xz"]
blob = b"".join(open(p, "rb").read() for p in parts)
with tarfile.open(fileobj=io.BytesIO(blob), mode="r:xz") as t:
    for m in t.getmembers():
        assert m.isfile() and m.name.startswith("data/") and ".." not in m.name, m.name
    t.extractall(".")
PY
'''
BLOCK = re.compile(re.escape(MARK) + r"\npython3 - <<'PY'\n.*?\nPY\n", re.S)
HEADER = ("#!/bin/sh\n# Restore data/ from the package root. Used by ppf-stream to restore\n"
          "# the files pinned by '#ppf-stream regen' directives.\nset -e\nmkdir -p data/sample data/secret\n")


def is_pin(path):
    with open(path, "rb") as f:
        return f.read(18) == b"#ppf-stream regen "


def archive_files(pkg):
    """The archive's files, in order: the parts if split, else the one file."""
    parts = sorted((pkg / GEN).glob("data.tar.xz.part-*"))
    whole = pkg / ARCHIVE
    return parts or ([whole] if whole.exists() else [])


def read_members(pkg):
    files = archive_files(pkg)
    if not files:
        return {}
    blob = b"".join(f.read_bytes() for f in files)
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:xz") as t:
        return {m.name: t.extractfile(m).read() for m in t.getmembers()}


def write_archive(pkg, members):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.GNU_FORMAT) as t:
        for name in sorted(members):
            ti = tarfile.TarInfo(name)
            ti.size, ti.mtime, ti.mode, ti.uid, ti.gid, ti.uname, ti.gname = \
                len(members[name]), 0, 0o644, 0, 0, "", ""
            t.addfile(ti, io.BytesIO(members[name]))
    xz = lzma.compress(buf.getvalue(), preset=9 | lzma.PRESET_EXTREME)
    for f in archive_files(pkg):
        f.unlink()
    (pkg / GEN).mkdir(parents=True, exist_ok=True)
    if len(xz) <= PART:
        (pkg / ARCHIVE).write_bytes(xz)
        return [pkg / ARCHIVE], len(xz)
    out = []
    for i in range(0, len(xz), PART):
        p = pkg / GEN / f"data.tar.xz.part-{i // PART:03d}"
        p.write_bytes(xz[i:i + PART])
        out.append(p)
    return out, len(xz)


def write_regen(pkg, replace):
    regen = pkg / GEN / "regen.sh"
    text = regen.read_text() if regen.exists() and not replace else HEADER
    if BLOCK.search(text):
        text = BLOCK.sub(lambda _: UNPACK, text, count=1)      # an older unpack step
    elif MARK not in text:
        text = text.rstrip("\n") + "\n" + UNPACK
    regen.write_text(text)
    regen.chmod(0o755)


def main(argv):
    replace = "--replace" in argv
    args = [a for a in argv if a != "--replace"]
    if not args or args[0].startswith("-"):
        print(__doc__.strip().split("\n\n")[1], file=sys.stderr)
        return 2
    pkg = pathlib.Path(args[0]).resolve()
    data = pkg / "data"
    members = read_members(pkg)
    if args[1:]:
        cases = args[1:]
    else:
        cases = [f.relative_to(data).as_posix() for f in sorted(data.rglob("*"))
                 if f.is_file() and not is_pin(f) and f.stat().st_size >= LIMIT]
    if not cases and not members:
        print("archive: no case to archive (pinned cases must be expanded first: expand.py PACKAGE CASE)")
        return 1
    for rel in cases:
        f = data / rel
        if not f.is_file():
            print(f"archive: no such data file: data/{rel}", file=sys.stderr)
            return 1
        if is_pin(f):
            print(f"archive: data/{rel} is a pin; run expand.py PACKAGE {rel} first", file=sys.stderr)
            return 1
        members[f"data/{rel}"] = f.read_bytes()

    files, size = write_archive(pkg, members)
    write_regen(pkg, replace)
    for rel in cases:
        b = members[f"data/{rel}"]
        (data / rel).write_text(f"#ppf-stream regen {rel} sha256={hashlib.sha256(b).hexdigest()} size={len(b)}\n")
        (pkg / PATCHES / f"{rel}.patch").unlink(missing_ok=True)
        print(f"archive: {rel}: {len(b):,} bytes archived and pinned")
    raw = sum(len(b) for b in members.values())
    how = f"{len(files)} parts of at most {PART:,} bytes" if len(files) > 1 else files[0].name
    print(f"archive: {len(members)} file(s), {raw:,} -> {size:,} bytes ({raw / size:.1f}x), as {how}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
