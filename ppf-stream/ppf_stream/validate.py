#!/usr/bin/env python3
"""Generic streaming output validator.

This file is the reusable part.  A problem setter does not edit it.
It turns

    submission < big.in | diff big.ans -

into

    gen ARGS | submission | judge (gen ARGS) -

where `ARGS` is the entire contents of the test case's `.in` file, so a test
case that presents gigabytes to a submission is a line of text on disk.

Drop it into `output_validator/` next to two programs you do write:

  gen     `gen ARGS` writes the test case input on standard output.
          Must be deterministic: same ARGS, same bytes, forever.

          ARGS may contain `|` to name further stages, each an executable
          beside this file, so a test case reads as a pipeline:

              --n 1000000 --digits 6 --case max-n | keep-north

          A late constraint change is then a filter added to a `.in` file
          rather than a change to a generator that has already been reviewed.
          Every stage is part of the cache key, so adding one regenerates.

  judge   an ordinary output validator, exactly as the specification defines
          it: `judge input answer feedback_dir [args] < team_output`,
          exit 42 for accepted and 43 for wrong answer.  Because it takes
          that interface and nothing else, the same `judge` also works in a
          conventional non-interactive package, and can be tested on its own.

  solution   optional.  If present, `solution < input > answer` produces the
          answer file handed to `judge`.  Omit it when `judge` recomputes the
          answer itself.

Both may be executables in any language, or scripts with a shebang.

Caching: `gen` is deterministic, so it runs once per test case and its output
is kept in a `.cache` file keyed by a digest of the arguments and of every
program involved.  Editing `gen` invalidates the cache automatically.  Set
`PPF_STREAM_CACHE` to choose where they live; the default is beside the `.in`.

Existing packages (mixed mode)
------------------------------
A problem that already has data and a generator that writes all of it does not
need a per-case `gen`.  Keep every ordinary case as it is and replace only the
large files with a one-line directive:

    #ppf-stream regen secret/10_max.in sha256=<hex> size=<bytes>

`regen/` beside this file holds a copy of what the problem's own generator
needs, and `regen/regen.sh` runs it from a scratch copy of that directory,
writing `data/...` exactly as it does in the package.  It runs once per cache;
every file it wrote is then looked up by path.  The sha256 and size pin the
bytes: a generator that does not reproduce them on this machine is a judge
error, never a silent change of test data.  A `.ans` may be a directive too.

A `.in` that is neither a directive nor (when `gen` exists) an argument line is
streamed as it is, so small cases need no change.  Without a `judge` beside
this file, `default_judge.py` compares output with the answer as the
specification's default output validator does, honouring its flags.

    validate.py --warm DATA_DIR    regenerate and check every directive, once
"""

import hashlib
import os
import pathlib
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

AC, WA, JUDGE_ERROR = 42, 43, 1
HERE = pathlib.Path(__file__).resolve().parent

# A submission that exits early leaves us writing into a dead pipe.  Dying on
# SIGPIPE would exit with a signal rather than 42 or 43, which a judge reads
# as a validator malfunction: a contest incident instead of a wrong answer.
signal.signal(signal.SIGPIPE, signal.SIG_IGN)


def note(msg):
    print(f"[ppf-stream] {msg}", file=sys.stderr)


def program(name):
    p = HERE / name
    return p if p.exists() and os.access(p, os.X_OK) else None


def stages(args):
    """Split ARGS on `|` into [[gen, ...], [filter, ...], ...]."""
    out, cur = [], []
    for a in args:
        if a == "|":
            out.append(cur)
            cur = []
        else:
            cur.append(a)
    out.append(cur)
    if not out[0]:
        raise RuntimeError("empty generator stage")
    out[0][0:0] = ["gen"]          # first stage is always `gen`
    for st in out[1:]:
        if not st:
            raise RuntimeError("empty pipeline stage")
    return out


def fingerprint(args):
    """Cache key: the arguments, plus every byte of every program that can
    affect the output -- including every filter stage, so adding a filter
    invalidates the cache rather than silently reusing unfiltered data."""
    h = hashlib.sha256()
    h.update(b"ppf-stream/2\x1f" + "\x1f".join(args).encode() + b"\x1f")
    names = [st[0] for st in stages(args)] + ["solution"]
    for name in names:
        p = program(name)
        if p is None:
            h.update(b"-")
            continue
        for f in sorted(x for x in ([p] if p.is_file() else p.rglob("*")) if x.is_file()):
            h.update(f.name.encode())
            h.update(hashlib.sha256(f.read_bytes()).digest())
    # A sibling lib/ directory is the usual home for shared generator code.
    lib = HERE / "lib"
    if lib.is_dir():
        for f in sorted(x for x in lib.rglob("*") if x.is_file()):
            h.update(f.name.encode())
            h.update(hashlib.sha256(f.read_bytes()).digest())
    return h.hexdigest()


def materialize(args, cache_dir):
    """Return (input path, answer path or None, cache hit).

    Runs `gen`, and `solution` if present, writing both into the cache.
    """
    key = fingerprint(args)
    base = pathlib.Path(cache_dir) / key if cache_dir else None
    gen_path = base.with_suffix(".in.cache") if base else None
    ans_path = base.with_suffix(".ans.cache") if base else None

    if gen_path is not None and gen_path.exists():
        return gen_path, (ans_path if ans_path.exists() else None), True

    chain = stages(args)
    for st in chain:
        if program(st[0]) is None:
            raise RuntimeError(f"no executable '{st[0]}' beside validate.py")

    tmpdir = pathlib.Path(cache_dir or tempfile.mkdtemp())
    tmp_in = tmpdir / f".{key}.in.part"

    # Stages are chained through FILES, not pipes.  A stage whose standard
    # input is redirected from a regular file gets a *seekable* stdin, so a
    # filter that must rewrite a record count can read once to count, seek
    # back to zero, and read again to emit -- one generator run, no temporary
    # storage of its own, and no buffering the whole case in memory.
    #
    # Chaining through pipes instead would force such a filter either to hold
    # everything in memory or to have the generator run a second time.  The
    # generator is the expensive part by a wide margin: generating runs at a
    # few MB/s while re-reading a file runs at a couple of GB/s, so paying an
    # extra pass over a file is hundreds of times cheaper than paying an
    # extra pass over the generator.
    stage_out = []
    prev_path = None
    try:
        for i, st in enumerate(chain):
            last = i == len(chain) - 1
            out_path = tmp_in if last else tmpdir / f".{key}.s{i}.part"
            fi = open(prev_path, "rb") if prev_path else subprocess.DEVNULL
            try:
                with open(out_path, "wb") as fo:
                    r = subprocess.run([str(program(st[0])), *st[1:]],
                                       stdin=fi, stdout=fo, stderr=subprocess.PIPE)
            finally:
                if prev_path:
                    fi.close()
            if r.returncode != 0:
                raise RuntimeError(
                    f"{st[0]} failed ({r.returncode}): {r.stderr.decode()[:400]}")
            if not last:
                stage_out.append(out_path)
            prev_path = out_path
    finally:
        for f in stage_out:                 # intermediates are transient
            try:
                f.unlink()
            except OSError:
                pass

    tmp_ans = None
    sol = program("solution")
    if sol is not None:
        tmp_ans = tmpdir / f".{key}.ans.part"
        with open(tmp_in, "rb") as fi, open(tmp_ans, "wb") as fo:
            r = subprocess.run([str(sol)], stdin=fi, stdout=fo, stderr=subprocess.PIPE)
        if r.returncode != 0:
            raise RuntimeError(f"solution failed ({r.returncode}): {r.stderr.decode()[:400]}")

    if gen_path is None:
        return tmp_in, tmp_ans, False
    # Rename into place so a concurrent judge never sees a partial file.
    os.replace(tmp_in, gen_path)
    if tmp_ans is not None:
        os.replace(tmp_ans, ans_path)
    return gen_path, (ans_path if tmp_ans is not None else None), False


MARKER = b"#ppf-stream"
REGEN_ENV = {"PYTHONHASHSEED": "0", "LC_ALL": "C", "TZ": "UTC"}


def read_directive(path):
    """The directive in `path` as {'kind', 'path', 'sha256', 'size'}, or None."""
    try:
        with open(path, "rb") as f:
            head = f.read(len(MARKER))
            if head != MARKER:
                return None
            line = (head + f.readline()).decode("ascii", "replace").split()
    except OSError:
        return None
    if len(line) < 3 or line[1] != "regen":
        raise RuntimeError(f"{path}: unknown directive: {' '.join(line)[:200]}")
    d = {"kind": "regen", "path": line[2]}
    for kv in line[3:]:
        k, _, v = kv.partition("=")
        d[k] = v
    return d


def tree_digest(root):
    h = hashlib.sha256(b"ppf-stream-regen/1\x1f")
    for f in sorted(x for x in root.rglob("*") if x.is_file()):
        h.update(str(f.relative_to(root)).encode() + b"\x1f")
        h.update(hashlib.sha256(f.read_bytes()).digest())
    return h.hexdigest()


def regen_tree(cache_dir, src=None, script_rel="regen.sh"):
    """Run the regen script once in a scratch copy of `src`; return (tree, cache hit).

    `src` defaults to regen/ beside this file (option 2, at judge time); a build
    step passes the package itself and generators/regen.sh (option 1)."""
    src = pathlib.Path(src) if src else HERE / "regen"
    script = src / script_rel
    if not script.is_file():
        raise RuntimeError(f"no {script_rel} in {src}")
    cache = pathlib.Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    dest = cache / f"regen-{tree_digest(src)[:32]}"
    if (dest / ".complete").exists():
        return dest, True

    # Generate in a DETACHED worker, behind a lock.  A judge may kill this
    # validator (the submission's wall clock runs while it waits); if the
    # generator died with it, a generator slower than the wall limit would be
    # killed on every case and the cache would never fill.  Detached, the work
    # survives, and every later call waits for the one run in progress.
    lock = dest.with_name(dest.name + ".lock")
    failed = dest.with_name(dest.name + ".failed")
    for _ in range(2):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            subprocess.Popen([sys.executable, str(pathlib.Path(__file__).resolve()), "--regen-worker",
                              str(cache), str(src), script_rel, str(dest)],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        except FileExistsError:
            pass                                   # a run is already in progress
        while True:
            if (dest / ".complete").exists():
                return dest, False
            if failed.exists():
                raise RuntimeError(failed.read_text()[-800:])
            try:
                age = time.time() - lock.stat().st_mtime
            except FileNotFoundError:
                break                              # worker ended without a result: retry
            if age > STALE_LOCK_SECONDS:
                lock.unlink(missing_ok=True)       # a worker that died with its machine
                break
            time.sleep(0.2)
    raise RuntimeError("regeneration did not complete")


STALE_LOCK_SECONDS = 2 * 3600


def regen_worker(cache, src, script_rel, dest):
    """The detached half of regen_tree: generate, then publish or record failure."""
    cache, src, dest = pathlib.Path(cache), pathlib.Path(src), pathlib.Path(dest)
    lock = dest.with_name(dest.name + ".lock")
    failed = dest.with_name(dest.name + ".failed")
    try:
        _generate(cache, src, script_rel, dest)
    except Exception as e:                         # recorded for every waiting caller
        failed.write_text(str(e))
    finally:
        lock.unlink(missing_ok=True)
    return 0


def _generate(cache, src, script_rel, dest):
    tmp = pathlib.Path(tempfile.mkdtemp(prefix=".regen-", dir=cache))
    try:
        work = tmp / "pkg"
        shutil.copytree(src, work, symlinks=True)
        r = subprocess.run(["sh", script_rel], cwd=work, stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           env=dict(os.environ, **REGEN_ENV))
        if r.returncode != 0:
            raise RuntimeError(f"regen.sh failed ({r.returncode}): {r.stdout.decode()[-600:]}")
        apply_patches(work)
        (work / ".complete").write_text("")
        try:
            os.replace(work, dest)     # atomic; a concurrent winner keeps its copy
        except OSError:
            if not (dest / ".complete").exists():
                raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return dest


PATCH_DIR = pathlib.Path("generators") / "patches"
HUNK = re.compile(rb"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def apply_patches(work):
    """gen -> patch: apply generators/patches/<path under data>.patch to data/<path>.

    A hand edit on top of generated data (a boundary case added, a line removed)
    is kept as a small unified diff instead of as the whole file.  The pin is
    of the patched bytes, so a patch that stops applying cleanly, because the
    generator changed under it, is an error like any other mismatch."""
    root = work / PATCH_DIR
    if not root.is_dir():
        return []
    done = []
    for p in sorted(root.rglob("*.patch")):
        rel = p.relative_to(root).with_suffix("")          # secret/10_max.in
        target = work / "data" / rel
        if not target.is_file():
            raise RuntimeError(f"patch {p.relative_to(work)}: generator did not write data/{rel}")
        try:
            target.write_bytes(apply_unified(target.read_bytes(), p.read_bytes()))
        except ValueError as e:
            raise RuntimeError(f"patch {p.relative_to(work)} does not apply to data/{rel}: {e}")
        done.append(rel)
    return done


def apply_unified(old_bytes, patch_bytes):
    """Apply one file's unified diff exactly: no fuzz, no offsets."""
    old = old_bytes.splitlines(keepends=True)
    lines = patch_bytes.splitlines(keepends=True)
    out, i, k = [], 0, 0
    same = lambda a, b: a.rstrip(b"\r\n") == b.rstrip(b"\r\n")
    while k < len(lines):
        m = HUNK.match(lines[k])
        if not m:
            k += 1                                  # ---/+++ headers, anything before a hunk
            continue
        start = int(m.group(1))
        n_old = int(m.group(2)) if m.group(2) is not None else 1
        n_new = int(m.group(4)) if m.group(4) is not None else 1
        pos = start - 1 if n_old else start         # a pure insertion goes after line `start`
        if pos < i or pos > len(old):
            raise ValueError(f"hunk at line {start} out of order or past the end")
        out.extend(old[i:pos])
        i = pos
        k += 1
        # Consume exactly the hunk's counted lines, so a data line that happens
        # to start with "-- " or "++ " is never mistaken for a file header.
        while k < len(lines) and (n_old > 0 or n_new > 0 or lines[k][:1] == b"\\"):
            line, tag = lines[k], lines[k][:1]
            if tag == b"\\":                        # "\ No newline at end of file"
                if out and lines[k - 1][:1] == b"+":
                    out[-1] = out[-1].rstrip(b"\r\n")
            elif tag in (b" ", b"-"):
                if i >= len(old) or not same(old[i], line[1:]):
                    raise ValueError(f"line {i + 1} does not match the patch's context")
                if tag == b" ":
                    out.append(old[i])
                    n_new -= 1
                i += 1
                n_old -= 1
            elif tag == b"+":
                out.append(line[1:])
                n_new -= 1
            else:
                raise ValueError(f"unexpected line in hunk: {line[:40]!r}")
            k += 1
    out.extend(old[i:])
    return b"".join(out)


def resolve(directive, cache_dir, src=None, script_rel="regen.sh"):
    """Path to the bytes a directive names, checked against its pin."""
    tree, hit = regen_tree(cache_dir, src, script_rel)
    p = tree / "data" / directive["path"]
    if not p.is_file():
        raise RuntimeError(f"regen did not write data/{directive['path']}")
    if "size" in directive and p.stat().st_size != int(directive["size"]):
        raise RuntimeError(f"data/{directive['path']}: regenerated {p.stat().st_size} bytes, "
                           f"pinned {directive['size']}: the generator does not reproduce it here")
    if "sha256" in directive:
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
        if h.hexdigest() != directive["sha256"]:
            raise RuntimeError(f"data/{directive['path']}: sha256 differs from its pin: "
                               "the generator does not reproduce it here")
    return p, hit


def regen_cache_dir():
    return os.environ.get("PPF_STREAM_CACHE") or os.path.join(tempfile.gettempdir(), "ppf-stream")


def warm(data_dir):
    """Regenerate and check every directive under data_dir; print one line each."""
    bad = 0
    for f in sorted(pathlib.Path(data_dir).rglob("*")):
        if not f.is_file():
            continue
        try:
            d = read_directive(f)
            if d is None:
                continue
            p, _ = resolve(d, regen_cache_dir())
            print(f"ok    {f}  ({p.stat().st_size:,} bytes)")
        except RuntimeError as e:
            bad += 1
            print(f"FAIL  {f}  {e}")
    return 1 if bad else 0


def stream_and_collect(data_path, team_out_path):
    """Write the test case to the submission while reading its answer back.

    Both halves must happen at once.  Writing everything first and only then
    reading deadlocks against any submission that emits output while still
    consuming input, because the pipe buffer is about 64 KiB.
    """
    broke = False
    with open(team_out_path, "wb") as sink:
        def reader():
            while True:
                chunk = sys.stdin.buffer.read(1 << 16)
                if not chunk:
                    return
                sink.write(chunk)      # spill to disk: never bound by memory

        t = threading.Thread(target=reader, daemon=True)
        t.start()
        try:
            with open(data_path, "rb") as fi:
                while True:
                    chunk = fi.read(1 << 16)
                    if not chunk:
                        break
                    sys.stdout.buffer.write(chunk)
            sys.stdout.buffer.flush()
            # os.close(1), NOT sys.stdout.close() and NOT sys.stdout.buffer.close():
            # neither of those releases the descriptor, so the submission never
            # sees end of input and both processes block until the judge's
            # wall-clock limit fires.  Measured, not assumed.
            os.close(1)
        except BrokenPipeError:
            broke = True
        t.join()
    return broke


def prepare(input_file, answer_file):
    """(bytes to stream, answer for the judge or None to use answer_file, note)."""
    ans_path = None
    try:
        ans_dir = read_directive(answer_file)
        if ans_dir is not None:
            ans_path, _ = resolve(ans_dir, regen_cache_dir())
        d = read_directive(input_file)
        if d is not None:
            p, hit = resolve(d, regen_cache_dir())
            return p, ans_path, f"regen {d['path']} -> {p.stat().st_size} bytes, cache {'hit' if hit else 'miss'}"
        if program("gen") is None:
            return pathlib.Path(input_file), ans_path, None     # an ordinary case
        args = pathlib.Path(input_file).read_text().split()
        cache_dir = os.environ.get("PPF_STREAM_CACHE") or str(pathlib.Path(input_file).parent)
        p, gen_ans, hit = materialize(args, cache_dir)
        return p, ans_path or gen_ans, f"{' '.join(args)} -> {p.stat().st_size} bytes, cache {'hit' if hit else 'miss'}"
    except OSError as e:
        raise RuntimeError(str(e))


def judge_command():
    judge = program("judge")
    if judge is not None:
        return [str(judge)]
    default = HERE / "default_judge.py"
    if default.is_file():
        return [sys.executable, str(default)]
    return None


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--warm":
        return warm(sys.argv[2])
    if len(sys.argv) == 6 and sys.argv[1] == "--regen-worker":
        return regen_worker(*sys.argv[2:6])
    if len(sys.argv) < 4:
        note("usage: validate input_file answer_file feedback_dir [args]  |  validate --warm DATA_DIR")
        return JUDGE_ERROR
    input_file, answer_file, feedback_dir = sys.argv[1:4]
    extra = sys.argv[4:]

    try:
        data_path, ans_path, msg = prepare(input_file, answer_file)
    except RuntimeError as e:
        note(str(e))
        return JUDGE_ERROR
    if msg:
        note(msg)

    with tempfile.TemporaryDirectory() as work:
        team_out = pathlib.Path(work) / "team_output"
        broke = stream_and_collect(data_path, team_out)
        if broke and team_out.stat().st_size == 0:
            try:
                (pathlib.Path(feedback_dir) / "judgemessage.txt").write_text(
                    "submission closed its input before the test case was delivered\n")
            except OSError:
                pass
            return WA

        judge = judge_command()
        if judge is None:
            note("no executable 'judge' and no default_judge.py beside validate.py")
            return JUDGE_ERROR
        with open(team_out, "rb") as fi:
            r = subprocess.run(
                [*judge, str(data_path),
                 str(ans_path) if ans_path else answer_file, feedback_dir, *extra],
                stdin=fi)
    if r.returncode not in (AC, WA):
        note(f"judge exited {r.returncode}, expected 42 or 43")
        return JUDGE_ERROR
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
