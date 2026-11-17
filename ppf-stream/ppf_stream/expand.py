#!/usr/bin/env python3
"""Option 1: turn a package's directives back into test data, at build time.

    expand.py PACKAGE [--check]

Every `#ppf-stream regen <path> sha256=... size=...` file under PACKAGE/data is
replaced, in place, by the bytes the problem's own generator writes there, after
those bytes are checked against the pin.  The generator runs once, from a scratch
copy of the package, via `generators/regen.sh`.  The result is an ordinary
package: no interactive problem, no change for judges or contestants.

Run it in CI before `verifyproblem`, and before uploading to a contest system.
`--check` regenerates and checks every pin without writing anything.
Generated trees are cached in PPF_STREAM_CACHE (default: the system temp dir).
"""

import importlib.util
import pathlib
import shutil
import sys

HERE = pathlib.Path(__file__).resolve().parent


def load_validate():
    spec = importlib.util.spec_from_file_location("ppf_validate", HERE / "validate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    args = [a for a in sys.argv[1:] if a != "--check"]
    check = "--check" in sys.argv[1:]
    if len(args) != 1:
        print(__doc__.strip().split("\n\n")[1], file=sys.stderr)
        return 2
    pkg = pathlib.Path(args[0]).resolve()
    v = load_validate()
    found = [(f, v.read_directive(f)) for f in sorted((pkg / "data").rglob("*")) if f.is_file()]
    found = [(f, d) for f, d in found if d]
    if not found:
        print(f"expand: no directives in {pkg / 'data'}")
        return 0
    # Resolve every directive before writing any: writing changes the package,
    # which is the cache key, and would force a regeneration per file.
    bad, ready = 0, []
    for f, d in found:
        try:
            p, _ = v.resolve(d, v.regen_cache_dir(), src=pkg, script_rel="generators/regen.sh")
            ready.append((f, p))
        except RuntimeError as e:
            bad += 1
            print(f"FAIL  {f.relative_to(pkg)}  {e}")
    if bad:
        return 1
    for f, p in ready:
        if not check:
            shutil.copyfile(p, f)
        print(f"{'ok   ' if check else 'wrote'} {f.relative_to(pkg)}  ({p.stat().st_size:,} bytes, pin ok)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
