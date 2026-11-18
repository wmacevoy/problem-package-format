# ppf-stream — reusable pieces for problems with large test data

The idea in one line:

```
                submission < big.in | judge big.ans -

    becomes     gen ARGS | submission | judge (gen ARGS) -
```

`ARGS` is the entire contents of the test case's `.in` file, so a test case
that presents 59 MB to a submission is 20 bytes on disk. The problem is
declared `interactive`, which is what lets the output validator supply the
submission's input; nothing else about the package format changes and no
judge system needs to do anything new.

## What you write, and what you get

`ppf_stream/validate.py` is generic and is never edited. Drop it into
`output_validator/` beside two programs, in any language:

Program    | Contract
---------- | ----------------------------------------------------------------
`gen`      | `gen ARGS` writes the test case input on standard output. Must be deterministic
`judge`    | An ordinary output validator: `judge input answer feedback [args] < team_output`, exit `42` or `43`
`solution` | Optional. `solution < input > answer` if `judge` wants an answer file
filters    | Optional extra stages named in the `.in` file after a `|`

The `judge` contract is deliberately the specification's existing output
validator interface and nothing more. That means the same `judge` also works
in a conventional non-interactive package, can be run standalone, and does
not have to know this component exists.

## Tools

```
ppf_stream/validate.py         the generic validator: gen -> filters -> stream -> judge
ppf_stream/materialize.py      run the pipeline for every case, emit a conventional package
ppf_stream/verify.py           setter-side gate: input validation + every submission
ppf_stream/run_interactive.py  local harness, so you can test without a judge system
ppf_stream/convert.py          an existing package (or its LFS pointers) to pinned directives
ppf_stream/expand.py           option 1: restore pinned data at build time
ppf_stream/default_judge.py    the spec's default output validator, as a program
ppf_stream/input_guard.py      lets existing input validators accept directives
ppf_stream/mkpatch.py          keep a hand edit to generated data as a patch
ppf_stream/sync.py             turn edited or expanded data back into pins + patches
ppf_stream/package.py          build the upload: expanded, or option 2 (interactive)
ppf_stream/archive.py          option 3: a tar.xz shim for a broken or missing generator
hooks/pre-commit               runs sync.py on staged changes (git config core.hooksPath)
```

`materialize.py` is what keeps both worlds reachable: the streaming form for
the repository, the conventional form for uploading and archiving, from one
set of sources.

    python3 ppf_stream/run_interactive.py examples/rangesum examples/rangesum/submissions/accepted/sol.py
    python3 ppf_stream/materialize.py examples/rangesum /tmp/out

## Late constraint changes

A test case is a pipeline, written in its `.in` file:

    --n 1000000 --digits 6 --case max-n | keep-north

When a constraint changes days before a contest, the move is to add a stage
rather than edit a generator that has already been reviewed and tested. The
diff is one token per test case plus one new small program, and every stage
is part of the cache key, so adding one regenerates automatically.

### The record count

Most competitive-programming formats put a record count in the header, and
that count is what makes them non-composable: a filter that drops records
must rewrite it, and one that forgets is silently wrong until an input
validator or a contestant finds out.

One answer is to run the generator twice — once to count, once to emit. It
works and it is expensive, because the generator is the costly part. Measured
on the 36.9 MB case:

Approach                                     | Cost
-------------------------------------------- | -------
`gen`                                        | 16.74 s
one bare read of the generated file          | 0.03 s
`gen` then filter, stages chained via files  | 21.89 s
`gen` twice                                  | 38.63 s
`gen` four times, judge side regenerating    | 77.25 s

So stages are chained through **files, not pipes**. A stage whose standard
input is redirected from a regular file gets a *seekable* stdin, so a filter
can read once to count, seek back to zero, and read again to emit: one
generator run, no temporary file of its own, nothing held in memory.

`recfilter.run()` does that for you. The setter writes a per-record function
and the count is handled:

    from recfilter import run
    run(header_lines=2, setup=setup, record=record, prologue=prologue,
        preserves_count=True)

`preserves_count` says the filter emits one record per record, which skips
the counting pass entirely and makes it single-pass again — 3.23 s rather
than 5.15 s on that case. A filter that maps rather than drops does not need
to be told the new count, because it is the old one. That is one more reason
to prefer reflecting over dropping.

Within a single judging run the generator already runs once, not twice: the
same materialised file is streamed to the submission *and* handed to `judge`.
The cache is what removes the cost across submissions; sharing the file is
what removes it within one.

Run `verify.py` afterwards. It generates every case through the full
pipeline, hands the result to `input_validators/`, and judges every
submission. Both halves matter:

- A filter is much easier to get wrong than it looks, and the input
  validator is what catches it.
- A narrowing can quietly destroy the reason a test case existed. If
  `wrong_answer/` starts passing, the test set has stopped working even
  though nothing failed.

## Caching

`gen` is deterministic, so it runs once per test case. Its output is kept in
a `.cache` file keyed by a digest of the arguments and of every byte of
`gen`, `solution`, and `lib/`, so editing a generator invalidates the cache
automatically. Files are renamed into place, so a concurrent judge never sees
a partial one. Set `PPF_STREAM_CACHE` to relocate them.

Whether those files survive between invocations is up to the judge system.
Where they do, generation is paid once; where they do not, it is paid per
submission and must fit inside `validation_time`.

## Existing packages: two options

A problem that already has its data, and a generator that writes all of it,
does not need rewriting. `convert.py` replaces each large file (or each Git LFS
pointer) with a one-line directive pinned to the file's sha256 and size,

    #ppf-stream regen secret/10_max.in sha256=<hex> size=<bytes>

and records the problem's own regeneration command in `generators/regen.sh`.
Small cases stay as they are. Then either:

- **Option 1, at build time** (`--mode build`): `expand.py PACKAGE` restores the
  data before `verifyproblem` or an upload. Judges and contestants see an
  ordinary problem; something must run the expand step.
- **Option 2, on the fly** (`--mode interactive`): the problem becomes
  interactive in name only. `validate.py` regenerates once per cache, checks
  every pin, and streams each case; `default_judge.py` is the specification's
  default output validator; `input_guard.py` lets the existing input
  validators accept directives. No contest system changes.

In both, bytes that do not match their pin are an error, never a silent change
of test data.

**Hand edits as patches (gen -> patch -> pin).** Generated data is often
touched up by hand: a boundary case added, a line removed. Keep the edit, not
the file: `mkpatch.py PKG secret/10_max.in EDITED` writes a unified diff to
`generators/patches/secret/10_max.in.patch` and re-pins the case to the edited
bytes. Regeneration applies the patch before checking the pin, in both options;
a patch that no longer applies, because the generator changed, is an error.
Five edits to a 2.6 MB case made a 360-byte patch.

**Automatic patches.** A judge should not have to run `mkpatch.py` by hand.
With `git config core.hooksPath <ppf-stream>/hooks`, the judge runs
`expand.py PKG secret/10_max.in`, edits the file, and commits: the hook
(`sync.py --staged`) finds data files whose committed version is a pin, puts
the pin back where the bytes are unchanged, and, where they were edited, writes
the patch against the generator's own output and re-pins to the edited bytes.
A deleted case loses its patch; an edit that returns to the generator's output
drops it. `sync.py PKG --repin` re-pins after a generator change, and
`sync.py --guard BASE` fails CI when pinned data is committed as data.

**Option 2 and end of input.** An interactive harness may keep the submission's
input open until the validator exits (problemtools' `interactive` does, by
design). A submission that reads until end of input (`sys.stdin.read()`,
`while (cin >> x)`) then never finishes. Under such a harness, option 2 suits
only problems whose input says how much to read, or contests that state it.

    python3 ppf_stream/convert.py PKG --mode build --regen "python3 generators/gen.py"
    python3 ppf_stream/expand.py PKG --check
    python3 PKG/output_validators/ppf_stream/validate.py --warm PKG/data   # option 2

## Examples

Two problems, two languages, the same unmodified wrapper.

Example     | `gen` / `judge` | `data/` on disk | Presented to submission
----------- | --------------- | --------------- | -----------------------
`rangesum`  | C++             | 34 bytes        | 59,334,530 bytes
`artifacts` | Python          | 73 bytes        | 48,081,388 bytes

`rangesum` is deliberately trivial — sum and count the even values — and
exists to show that swapping in `gen.cc` and `judge.cc` is the whole job. Its
`output_validator/build` compiles both, using the specification's existing
build and run script convention. Its rejected submission accumulates in a
32-bit `int`, which only fails once the sum wraps.

`artifacts` is the harder one: report every artifact inside or on the
boundary of a lat-lon box, with coordinates as exact decimals of up to 4000
fractional digits. Inclusive boundaries cannot be decided in double
precision, so the rejected submission is the obvious floating-point one. It
is correct at six fractional digits, including on the million-artifact case,
and at thirty digits it reports 2,918 artifacts where the answer is 2,310,
differing on 441 names.

## Determinism

`gen` must produce the same bytes forever, on any machine. Both examples do
this the same way, and neither needs a container or a lockfile:

- Exact integer arithmetic only. No floating point anywhere in generation.
- The random number generator is written out in the problem rather than
  imported. Python guarantees only that `Random.random()` is stable across
  versions, and NumPy's NEP 19 makes similar exceptions; `randrange`,
  `shuffle`, and `sample` carry no guarantee at all.
- Seeds are derived, not advanced: `SHA-256(salt ‖ case ‖ index ‖ purpose)`
  in the Python example, domain-separated splitmix64 in the C++ one. Adding a
  test case therefore does not perturb any other, and growing one leaves the
  items it already had untouched.
- `PYTHONHASHSEED=0`, `LC_ALL=C`, `TZ=UTC`, binary writes, explicit `\n`.

The `artifacts` generator produces byte-identical output across CPython 3.10,
3.11, 3.12 and 3.13, across four `PYTHONHASHSEED` values, and across two
unrelated machines.

## Three deadlocks the wrapper already handles

Each of these was found by hitting it, and each is why `validate.py` is worth
sharing rather than rewriting per problem.

**Closing standard output.** Neither `sys.stdout.close()` nor
`sys.stdout.buffer.close()` releases the descriptor in Python. The submission
never sees end of input and both sides hang until the wall-clock limit. Use
`os.close(1)`. Verified with `cat` as the submission: it never terminates.

**Read while you write.** Writing the whole test case before reading anything
deadlocks against any submission that emits output while still consuming
input, because the pipe buffer is about 64 KiB. The wrapper reads on a
separate thread started before the first write, and spills to disk so memory
is not a bound.

**Bound what you keep, not what you read.** A guard against a runaway
submission must keep draining and discard the excess. Stopping the read to
enforce a cap fills the pipe, blocks the submission, blocks the validator
behind it, and deadlocks.

`SIGPIPE` is also converted into a verdict rather than a signal exit, since
exiting on a signal reads as a validator malfunction — a contest incident
instead of a wrong answer. Submissions that exit immediately, emit garbage,
or flood megabytes of noise all produce a clean wrong answer.

## What this costs

The problem becomes interactive. Contestants carry the flushing discipline
that implies for what is otherwise an ordinary batch problem, samples need
`.interaction` files, and interactive support is less uniform across tooling.
Generation time counts against `validation_time` when no cache survives.

Reach for this when data size justifies it, not by default.
