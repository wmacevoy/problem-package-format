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
"""

import hashlib
import os
import pathlib
import signal
import subprocess
import sys
import tempfile
import threading

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


def main():
    if len(sys.argv) < 4:
        note("usage: validate input_file answer_file feedback_dir [args]")
        return JUDGE_ERROR
    input_file, answer_file, feedback_dir = sys.argv[1:4]
    extra = sys.argv[4:]

    try:
        args = pathlib.Path(input_file).read_text().split()
    except OSError as e:
        note(f"cannot read {input_file}: {e}")
        return JUDGE_ERROR

    cache_dir = os.environ.get("PPF_STREAM_CACHE") or str(pathlib.Path(input_file).parent)
    try:
        data_path, ans_path, hit = materialize(args, cache_dir)
    except (RuntimeError, OSError) as e:
        note(str(e))
        return JUDGE_ERROR
    note(f"{' '.join(args)} -> {data_path.stat().st_size} bytes, "
         f"cache {'hit' if hit else 'miss'}")

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

        judge = program("judge")
        if judge is None:
            note("no executable 'judge' beside validate.py")
            return JUDGE_ERROR
        with open(team_out, "rb") as fi:
            r = subprocess.run(
                [str(judge), str(data_path),
                 str(ans_path) if ans_path else answer_file, feedback_dir, *extra],
                stdin=fi)
    if r.returncode not in (AC, WA):
        note(f"judge exited {r.returncode}, expected 42 or 43")
        return JUDGE_ERROR
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
