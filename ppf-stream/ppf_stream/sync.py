#!/usr/bin/env python3
"""Keep pinned test data pinned, and turn hand edits into patches automatically.

    sync.py PACKAGE              restore expanded files to pins; patch edited ones
    sync.py PACKAGE --repin [CASE ...]
                                 after a generator change: re-pin every pinned file
                                 (and pin each named CASE, e.g. secret/30_new.in)
    sync.py --staged             what the pre-commit hook runs: sync every package
                                 with a staged change to pinned data, then stage
                                 the pins and patches
    sync.py --guard BASE         CI: fail if a file pinned at BASE is committed
                                 at HEAD as data instead of a pin

A file under PACKAGE/data whose committed (HEAD) version is a
`#ppf-stream regen` pin, but which now holds data (because expand.py wrote it,
or a judge edited it), is handled like this:
  - bytes match the pin: the pin is written back, nothing else changes;
  - bytes differ: the generator's own output for that file (without any patch)
    is the base, generators/patches/<case>.patch becomes the diff from it to the
    edited bytes, and the pin is rewritten to the edited bytes.
A deleted case also loses its patch.  Edits never enter Git history as data.
"""

import hashlib
import importlib.util
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
PIN = b"#ppf-stream regen "


def load(name):
    spec = importlib.util.spec_from_file_location(f"ppf_{name}", HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


v = load("validate")
unified = load("mkpatch").unified


def git(*args, cwd=".", check=True):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True)
    if check and r.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.decode().strip()}")
    return r


def committed(path, rev="HEAD"):
    """The bytes of `path` at `rev`, or None."""
    r = git("show", f"{rev}:./{path.name}", cwd=path.parent, check=False)
    return r.stdout if r.returncode == 0 else None


def pin_text(rel, data):
    return f"#ppf-stream regen {rel} sha256={hashlib.sha256(data).hexdigest()} size={len(data)}\n".encode()


def parse_pin(text):
    f = text.decode("ascii", "replace").split()
    d = dict(kv.partition("=")[::2] for kv in f[3:])
    return f[2], d.get("sha256"), int(d.get("size", -1))


def patch_targets(pkg, rel):
    out = [pkg / v.PATCH_DIR / f"{rel}.patch"]
    copy = pkg / "output_validators" / "ppf_stream" / "regen"
    if copy.is_dir():                                   # an option-2 package
        out.append(copy / v.PATCH_DIR / f"{rel}.patch")
    return out


def raw_tree(pkg, edited):
    """The generator's output with no patches applied, from a pinned copy of pkg."""
    with tempfile.TemporaryDirectory() as tmp:
        src = pathlib.Path(tmp) / "pkg"
        shutil.copytree(pkg, src, symlinks=True)
        for f, rel, old, _ in edited:
            (src / "data" / rel).write_bytes(old)
        shutil.rmtree(src / v.PATCH_DIR, ignore_errors=True)
        tree, _ = v.regen_tree(v.regen_cache_dir(), src=src, script_rel="generators/regen.sh")
        return tree


def sync(pkg):
    """Restore or patch every unpinned file of pkg.  Returns the paths changed."""
    changed, edited = [], []
    for f in sorted((pkg / "data").rglob("*")):
        if not f.is_file() or v.read_directive(f):
            continue
        old = committed(f)
        if not old or not old.startswith(PIN):
            continue                                    # an ordinary data file
        rel = f.relative_to(pkg / "data").as_posix()
        new = f.read_bytes()
        _, sha, size = parse_pin(old)
        if hashlib.sha256(new).hexdigest() == sha and len(new) == size:
            f.write_bytes(old)
            print(f"sync: {rel}: unchanged, pin restored")
            changed.append(f)
        else:
            edited.append((f, rel, old, new))

    if edited:
        tree = raw_tree(pkg, edited)
        for f, rel, old, new in edited:
            base = (tree / "data" / rel).read_bytes()
            for t in patch_targets(pkg, rel):
                if base == new:
                    t.unlink(missing_ok=True)
                    continue
                patch = unified(base, new, rel)
                if v.apply_unified(base, patch) != new:
                    raise RuntimeError(f"{rel}: the patch does not reproduce the edit")
                t.parent.mkdir(parents=True, exist_ok=True)
                t.write_bytes(patch)
            f.write_bytes(pin_text(rel, new))
            changed += [f, *patch_targets(pkg, rel)]
            what = "edit matches the generator, patch removed" if base == new else \
                f"{(pkg / v.PATCH_DIR / f'{rel}.patch').stat().st_size:,}-byte patch"
            print(f"sync: {rel}: {what}; pinned to the edited bytes ({len(new):,} bytes)")

    for p in sorted((pkg / v.PATCH_DIR).rglob("*.patch")) if (pkg / v.PATCH_DIR).is_dir() else []:
        rel = p.relative_to(pkg / v.PATCH_DIR).as_posix()[: -len(".patch")]
        if not (pkg / "data" / rel).exists():
            for t in patch_targets(pkg, rel):
                t.unlink(missing_ok=True)
                changed.append(t)
            print(f"sync: {rel}: case deleted, patch removed")
    return changed


def repin(pkg, extra):
    sync(pkg)
    tree, _ = v.regen_tree(v.regen_cache_dir(), src=pkg, script_rel="generators/regen.sh")
    n = 0
    for f in sorted((pkg / "data").rglob("*")):
        d = v.read_directive(f) if f.is_file() else None
        if not d:
            continue
        rel = f.relative_to(pkg / "data").as_posix()
        out = tree / "data" / rel
        if not out.is_file():
            print(f"repin: {rel}: the generator no longer writes it; delete the pin to drop the case")
            continue
        new = pin_text(rel, out.read_bytes())
        if new != f.read_bytes():
            f.write_bytes(new)
            n += 1
            print(f"repin: {rel}: re-pinned")
    for rel in extra:
        out = tree / "data" / rel
        if not out.is_file():
            raise RuntimeError(f"{rel}: the generator does not write data/{rel}")
        (pkg / "data" / rel).parent.mkdir(parents=True, exist_ok=True)
        (pkg / "data" / rel).write_bytes(pin_text(rel, out.read_bytes()))
        n += 1
        print(f"repin: {rel}: pinned")
    print(f"repin: {n} pin(s) changed")


def package_of(path):
    for d in path.parents:
        if (d / "problem.yaml").is_file():
            return d
    return None


def staged():
    top = pathlib.Path(git("rev-parse", "--show-toplevel").stdout.decode().strip())
    names = git("diff", "--cached", "--name-only", "--diff-filter=MD", "-z", cwd=top).stdout
    pkgs = set()
    for n in filter(None, names.decode().split("\0")):
        p = top / n
        if "/data/" in f"/{n}" and (committed(p) or b"").startswith(PIN):
            pkg = package_of(p)
            if pkg and (pkg / "generators" / "regen.sh").is_file():
                pkgs.add(pkg)
    for pkg in sorted(pkgs):
        changed = sync(pkg)
        changed = [c for c in changed if c.exists() or committed(c) is not None]
        if changed:
            git("add", "-A", "--", *[str(c) for c in changed], cwd=top)
    if pkgs and git("diff", "--cached", "--quiet", cwd=top, check=False).returncode == 0:
        print("sync: nothing left to commit: the expanded data matched its pins")
        return 1                                        # don't make an empty commit
    return 0


def guard(base):
    names = git("diff", "--name-only", "--diff-filter=M", "-z", base, "HEAD").stdout
    bad = []
    for n in filter(None, names.decode().split("\0")):
        old = git("show", f"{base}:{n}", check=False).stdout
        new = git("show", f"HEAD:{n}", check=False).stdout
        if old.startswith(PIN) and not new.startswith(PIN):
            bad.append(n)
    for n in bad:
        print(f"::error file={n}::pinned test data committed as data; run "
              f"`python3 {os.path.relpath(__file__)} <problem>` (or use the pre-commit "
              f"hook) and commit the pin and patch instead")
    return 1 if bad else 0


def main(argv):
    try:
        if argv[:1] == ["--staged"]:
            return staged()
        if argv[:1] == ["--guard"] and len(argv) == 2:
            return guard(argv[1])
        if len(argv) >= 1 and not argv[0].startswith("-"):
            pkg = pathlib.Path(argv[0]).resolve()
            if not (pkg / "generators" / "regen.sh").is_file():
                print(f"sync: {pkg} has no generators/regen.sh", file=sys.stderr)
                return 2
            if argv[1:2] == ["--repin"]:
                repin(pkg, argv[2:])
            elif len(argv) == 1:
                sync(pkg)
            else:
                raise SystemExit(__doc__)
            return 0
    except RuntimeError as e:
        print(f"sync: {e}", file=sys.stderr)
        return 1
    print(__doc__.strip().split("\n\n")[1], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
