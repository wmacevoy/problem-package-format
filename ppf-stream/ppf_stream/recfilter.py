"""Helper for record-oriented filters, so nobody has to get the count right.

Most competitive-programming input formats begin with a record count.  That
count is what makes such a format non-composable: a filter that drops or adds
records must rewrite the header, and a filter that forgets to is silently
wrong in a way that is invisible until an input validator or a contestant
finds it.

`run()` handles it.  The setter writes a per-record function and nothing else:

    from recfilter import run

    def keep(rec, ctx):
        ...
        return [rec]        # or [] to drop, or several to expand

    run(header_lines=2, record=keep, prologue=fix_header)

How it avoids paying for a second generation:

  Stages are chained through files, so standard input is seekable.  `run()`
  reads once to count what the filter will emit, seeks back to zero, and
  reads again to write it.  One generator run, no temporary file, and no
  holding the whole test case in memory.

  If standard input turns out not to be seekable -- because something ran the
  filter from a pipe -- it falls back to spooling to a temporary file, which
  is still far cheaper than regenerating.
"""

import io
import os
import sys
import tempfile

__all__ = ["run", "reopen"]


def _seekable_stdin():
    try:
        sys.stdin.buffer.seek(0)
        return True
    except (OSError, io.UnsupportedOperation, ValueError):
        return False


def reopen():
    """Return a fresh reader positioned at byte zero, spooling if needed."""
    if _seekable_stdin():
        return sys.stdin.buffer
    tmp = tempfile.TemporaryFile()
    while True:
        chunk = sys.stdin.buffer.read(1 << 20)
        if not chunk:
            break
        tmp.write(chunk)
    tmp.seek(0)
    return tmp


def run(header_lines, record, prologue=None, setup=None,
        preserves_count=False, count_line=0, count_field=0):
    """Two-pass record filter.

    header_lines  number of leading lines that are not records
    setup         optional f(header: list[bytes], ctx) called at the start of
                  each pass, before any record is seen
    record        f(line: bytes, ctx: dict) -> list[bytes] of output lines
    prologue      optional f(header: list[bytes], ctx, n_out) -> list[bytes],
                  called once the new record count is known
    preserves_count  set when the filter emits exactly one record per input
                  record.  The counting pass is then skipped entirely, which
                  is the common case and the cheapest one: a filter that maps
                  rather than drops does not need to be told the new count
                  because it is the old one
    count_line    which header line holds the record count
    count_field   which whitespace-separated field of that line holds it

    `ctx` is a dict shared across both passes and both callbacks, which is
    where a filter keeps whatever it learned from the header -- the query box,
    the precision, and so on.
    """
    src = reopen()
    header = [src.readline() for _ in range(header_lines)]
    ctx = {"header": header, "pass": 1}
    if setup is not None:
        setup(header, ctx)

    # Pass one: count only.  Output is discarded; nothing is retained.
    # Skipped when the filter has declared that it preserves cardinality,
    # which turns the whole thing back into a single streaming pass.
    if preserves_count:
        n_out = int(header[count_line].split()[count_field])
    else:
        n_out = 0
        for line in src:
            if line.strip():
                n_out += len(record(line, ctx))

    # Pass two: emit.  Same callback, same ctx, so a filter that derived
    # state from the header in pass one still has it.
    try:
        src.seek(0)
    except (OSError, io.UnsupportedOperation, ValueError):
        raise RuntimeError("input is not re-readable; run this filter from a file")
    header = [src.readline() for _ in range(header_lines)]
    ctx.clear()                      # each pass starts from the same state
    ctx.update({"header": header, "pass": 2})
    if setup is not None:
        setup(header, ctx)

    out = sys.stdout.buffer
    if prologue is not None:
        new_header = prologue(header, ctx, n_out)
    else:
        f = header[count_line].split()
        f[count_field] = str(n_out).encode()
        new_header = list(header)
        new_header[count_line] = b" ".join(f) + b"\n"
    for line in new_header:
        out.write(line)

    for line in src:
        if line.strip():
            for r in record(line, ctx):
                out.write(r)
    out.flush()
    try:
        os.close(1)
    except OSError:
        pass
