#!/usr/bin/env python3
"""Keep a hand edit to generated data as a patch: gen -> patch -> pinned bytes.

    mkpatch.py PACKAGE secret/10_max.in EDITED_FILE

Typical use: `expand.py PACKAGE` (or copy the generated file), edit it (add a
boundary case, remove a line), then run this.  It regenerates the file without
its patch, writes the unified diff to generators/patches/secret/10_max.in.patch
(and into an option-2 validator's regen/ copy, if there is one), checks that
the patch reproduces EDITED_FILE byte for byte, and rewrites data/secret/10_max.in
as a directive pinned to the edited bytes.  Commit the patch and the directive.
"""

import difflib
import hashlib
import importlib.util
import pathlib
import shutil
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent


def load_validate():
    spec = importlib.util.spec_from_file_location("ppf_validate", HERE / "validate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def unified(old, new, name):
    """A unified diff of two byte strings, marking a missing final newline."""
    a = old.decode("latin-1").splitlines(keepends=True)
    b = new.decode("latin-1").splitlines(keepends=True)
    out = []
    for line in difflib.unified_diff(a, b, f"a/{name}", f"b/{name}", n=3):
        out.append(line)
        if not line.endswith("\n"):
            out.append("\n\\ No newline at end of file\n")
    return "".join(out).encode("latin-1")


def main():
    if len(sys.argv) != 4:
        print(__doc__.strip().split("\n\n")[1], file=sys.stderr)
        return 2
    pkg, rel, edited = pathlib.Path(sys.argv[1]).resolve(), sys.argv[2], pathlib.Path(sys.argv[3])
    v = load_validate()
    new = edited.read_bytes()

    with tempfile.TemporaryDirectory() as tmp:
        src = pathlib.Path(tmp) / "src"
        shutil.copytree(pkg, src, symlinks=True)
        (src / v.PATCH_DIR / f"{rel}.patch").unlink(missing_ok=True)
        dest = pathlib.Path(tmp) / "tree"
        v._generate(pathlib.Path(tmp), src, "generators/regen.sh", dest)
        old = (dest / "data" / rel).read_bytes()

    if old == new:
        print("mkpatch: the edited file equals the generated one; no patch needed")
        return 1
    patch = unified(old, new, rel)
    if v.apply_unified(old, patch) != new:
        print("mkpatch: the patch does not reproduce the edited file; not written", file=sys.stderr)
        return 1

    targets = [pkg / v.PATCH_DIR / f"{rel}.patch"]
    regen_copy = pkg / "output_validators" / "ppf_stream" / "regen"
    if regen_copy.is_dir():
        targets.append(regen_copy / v.PATCH_DIR / f"{rel}.patch")
    for t in targets:
        t.parent.mkdir(parents=True, exist_ok=True)
        t.write_bytes(patch)
    sha = hashlib.sha256(new).hexdigest()
    (pkg / "data" / rel).write_text(f"#ppf-stream regen {rel} sha256={sha} size={len(new)}\n")
    print(f"mkpatch: {rel}: {len(patch):,}-byte patch for a {len(new):,}-byte file; pinned sha256={sha[:12]}…")
    for t in targets:
        print(f"  wrote {t.relative_to(pkg)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
