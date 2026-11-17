---
layout: default
title: Large Test Data
sort: 6
---

# Large test data

Some problems need test data that is large enough to be awkward to store.
Once a problem's `data/` directory reaches tens or hundreds of megabytes, committing it to version control stops being free, and the usual mitigations move the cost around rather than removing it.

This appendix describes a pattern that removes it, using only mechanisms the [specification](../spec/2025-09.md) already defines.
No change to the format is required and no judge system has to do anything new.

## When this applies

If a problem's test data is a few megabytes, none of this is worth the effort.
Commit the files and move on.

The pattern starts paying when one of the following is true:

- Cloning the repository is slow enough that people avoid doing it.
- Git Large File Storage quotas are being consumed by ordinary development traffic.
- The history contains many superseded versions of large files, because every revision of a large file is stored in full.
- A change to test data cannot be reviewed, because the diff is a binary blob.

The last is the one most often overlooked and the one this pattern fixes most completely.

### The cost that usually triggers it

GitHub Free and Pro accounts receive 10 GiB of Git Large File Storage bandwidth and 10 GiB of storage per month, pooled across the whole account rather than per repository.
Bandwidth is consumed by every clone, fetch, pull, and continuous integration checkout that touches a tracked file.
When the quota is exhausted and no budget is configured, LFS is blocked for the rest of the calendar month, and the symptom is that pulls return LFS _pointer files_ rather than content.
Close to a contest that looks like corrupted test data rather than like a billing event.

Verified nonprofits are eligible for [GitHub for Nonprofits](https://github.com/solutions/industry/nonprofits), which grants a free GitHub Team plan and raises both allowances to 250 GiB.
That is worth doing regardless.

## The pattern: the output validator is the generator

Make the problem [interactive](../spec/2025-09.md#problem-types).

For an interactive problem the specification says:

> when running the validator in interactive mode everything written on the validator's standard output is given to the program being validated.
> Please note that when running interactively the program will only receive the output produced by the validator and will not have direct access to the input file.

and separately:

> Note that the `input_file` will not be adjusted for either `interactive` or `multi-pass` problems; it will be the `.in` file of the test case.

Those two sentences are the whole pattern.
The submission never reads the `.in` file, and the validator does.
So the `.in` file holds _parameters_, and the validator generates the test data from them and streams it.

A test case that presents 37 MB of input to the submission is sixteen bytes on disk:

```
1000000 6 max-n
```

The validator reads those parameters, constructs the data deterministically, writes it to standard output where it becomes the submission's standard input, reads the submission's answer back, and exits `42` or `43`.
Because the validator built the data, it also knows the correct answer and can compute it while generating, so there is no separate accepted solution to run at judging time and no large `.ans` file either.
An `.ans` file must exist, since "if the `.in` file exists, then an associated `.ans` file must exist", but the specification allows the files named by `input_file` and `answer_file` to be empty.

Nothing here is an extension.
A package built this way is an ordinary conforming interactive package, and a judge system that supports interactive problems already supports it.

### What it stores

Measured on a five-case example problem:

Quantity                                       | Value
---------------------------------------------- | ------------------
Whole repository: validator, generator, submissions, harness | 18,624 bytes
Of which `data/`                               | 73 bytes
Data generated and never committed             | 48,081,388 bytes

The `data/` directory for the entire problem is smaller than this paragraph.

## Caching

Generating is much more expensive than reading.
In the example, building the largest case takes about 10 seconds against 0.018 seconds to read the same bytes from disk, and that cost would otherwise be paid on every submission.

Since the generator is deterministic, it only ever needs to run once.
Have the validator write its output to a cache file on first use and read it thereafter, keyed by a digest covering the parameters _and_ the generator sources, so that editing a generator invalidates the cache automatically.
Write to a temporary name and rename into place, so concurrent judge processes cannot observe a half-written file.

Case            | Cold  | Warm
--------------- | ----- | -----
`max-n`, 36.9 MB | 12.27 s | 2.15 s
`boundary`, 419 KB | 0.10 s | 0.04 s

The warm figures are almost entirely the submission reading and solving, not the cache read.

Where the cache may live is the one genuinely open question in this pattern.
Judge systems are free to run each validation in a fresh sandbox, in which case nothing persists and every submission pays full generation cost.
A useful convention would be for judge systems to preserve validator-written cache files across invocations of the same test case, which the specification already gestures at by allowing that "judge systems may assume that the result of running a program on a test case is deterministic" and permitting results to be reused for equivalent test cases.
Until such a convention exists, measure generation time against `validation_time`, whose typical default is 60 seconds, and keep generation comfortably inside it.

## Determinism

The data must be the same bytes every time it is generated, on every machine, for years.
Exact arithmetic is what makes this true unconditionally rather than conditional on an environment pin surviving.

### Randomness

"Seeded" is not sufficient.
Seeding fixes the bit source and says nothing about the operations built on it, and those are exactly what changes between versions.

Python's documentation guarantees only that `Random.random()` will keep producing the same sequence for a compatible seed; `randrange`, `sample`, `shuffle`, and the distribution functions are explicitly "subject to change across Python versions".
NumPy's [NEP 19](https://numpy.org/neps/nep-0019-rng-policy.html) grants strict stream compatibility to the legacy `RandomState`, but on the modern `Generator` only to `.bytes()`, `.integers()`, and `.random()`.

Vendor a small module into the problem instead, defining the bit source and every derived operation, with each algorithm written down.
A counter-mode hash works well: block _i_ is `SHA-256(key ‖ i)`, bits are consumed most significant first, and uniform integers below _n_ come from rejection sampling on the smallest covering power of two.
About a hundred lines is enough, it has no version, and there is nothing to drift.

Derive each seed from stable labels rather than advancing one generator:

```
seed = SHA-256(salt ‖ problem ‖ case ‖ index ‖ purpose)
```

With one advancing generator, inserting an item perturbs everything after it.
With derived seeds, each item is independent, growing a case leaves existing items untouched, and generation parallelises without ordering concerns.
Separate sub-streams by purpose as well as index, so changing how names are generated cannot move a coordinate.

### Exact arithmetic

Produce values with integer or rational arithmetic and emit them with explicit integer formatting.
`fractions.Fraction` is exact, and `decimal.Decimal` under an explicit `localcontext` is defined by the IBM General Decimal Arithmetic specification and is bit-identical everywhere.
Where transcendental functions are genuinely required, MPFR through `gmpy2` is correctly rounded by design with a stated precision and rounding mode, which `libm` is not.

The precise boundary is narrower than a blanket ban on floating point, since IEEE 754 mandates correct rounding for addition, subtraction, multiplication, division, and square root.
The nondeterminism lives almost entirely in `libm`.
A blanket ban is still the better team rule, because it is mechanically checkable and "floating point is fine if you are careful" is not.

### Checklist

Concern                   | Setting or rule
------------------------- | -----------------------------------------------
String hash randomisation | `PYTHONHASHSEED=0`. Set iteration order varies with it; dictionaries are insertion-ordered since 3.7 and are safe
Locale                    | `LC_ALL=C`, affecting sorting and number formatting
Time zone                 | `TZ=UTC`, and no reads of the wall clock at all
Directory order           | Sort anything derived from `os.listdir` or `glob`
Line endings              | Write in binary with explicit `\n`, no BOM, no trailing whitespace
Large integers            | Call `sys.set_int_max_str_digits(0)` explicitly
Dependencies              | Pin with hashes over the full transitive closure, not bare version pins

The integer limit is a trap that has already broken working generators.
CPython 3.11 introduced a _default_ cap of 4300 digits on integer-to-string conversion, so a generator emitting larger numbers worked on 3.10 and raises `ValueError` on 3.11.
In the example, a single 4000-digit coordinate is about 4003 digits, inside the cap with roughly seven percent headroom, while the product of two coordinates is about 8000 digits and fails.

On dependencies, pip's [repeatable installs](https://pip.pypa.io/en/stable/topics/repeatable-installs/) guidance notes that a plain pinned `requirements.txt` "trusts the locations you're fetching the packages from", which protects against upgrades but not against a package being yanked or an index disappearing.
Under exact arithmetic none of this affects the _output_; it affects only whether the environment can be rebuilt conveniently.

## Interaction hazards

Writing a validator that streams a large test case is not the same as writing one that answers queries, and three of these were found by hitting them.

### Closing standard output is not what it looks like

In Python, neither `sys.stdout.close()` nor `sys.stdout.buffer.close()` releases the descriptor.
The submission never sees end of input, it blocks waiting for more, the validator blocks waiting for an answer, and the pair hangs until the judge's wall-clock limit fires.

Use `os.close(1)` after flushing.
This was measured, not assumed: with either of the first two forms a trivial `cat` as the submission never terminates.

### Read while you write

A validator that writes the whole test case before reading anything deadlocks against any submission that emits output while still consuming input, because the pipe buffer is around 64 KiB and both sides end up blocked on writes nobody is draining.

Read the submission's output on a separate thread, started before the first write.
Streaming solutions are entirely reasonable, and rejecting them with a hang rather than a verdict is not acceptable behaviour.

### Bound what you keep, not what you read

A guard against a runaway submission must keep draining the pipe and discard the excess.
Stopping the read to enforce a cap fills the pipe, blocks the submission's writes, blocks the validator's writes behind them, and deadlocks — which is what the first version of the example did.

### Convert every failure into a verdict

A submission that exits early leaves the validator writing into a dead pipe.
Dying on `SIGPIPE` exits with a signal rather than `42` or `43`, which a judge reads as a validator malfunction: a contest incident instead of a wrong answer.
Handle it and return a verdict.

Test this deliberately.
In the example, submissions that exit immediately, emit garbage, and flood 8 MB of noise all produce a clean wrong answer.

## What you give up

The problem becomes interactive, and that is not free.

Contestants must treat it as interactive, with the flushing discipline that implies, even though the problem is semantically an ordinary read-input-produce-output problem.
Interactive problems are also supported less uniformly than plain ones across tooling.
Sample data needs `.interaction` files rather than the usual pair, since "displaying two files (input and output) typically does not meaningfully capture how solvers are expected to interact with the problem".

Generation time counts against `validation_time` unless a cache survives between invocations, and time the validator spends _waiting_ for the submission does not count, but time it spends generating does.

None of this makes the pattern wrong, but it does mean it should be reached for when data size actually justifies it, and not by default.

## The reusable part

Almost none of this is problem-specific.
The wrapper that reads parameters, runs the generator, caches its output, streams it to the submission without deadlocking, and hands the result to a checker is the same for every problem.
What a setter writes is two programs, in any language:

Program    | Contract
---------- | ---------------------------------------------------------------
`gen`      | `gen ARGS` writes the test case input on standard output, deterministically
`judge`    | An ordinary output validator: `judge input answer feedback [args] < team_output`, exit `42` or `43`
`solution` | Optional, when `judge` wants an answer file rather than recomputing

The `judge` contract is deliberately the interface the specification already defines and nothing more.
The same `judge` therefore works unchanged in a conventional non-interactive package, can be run standalone during development, and does not need to know the wrapper exists.
A companion tool that runs `gen` over every test case and writes real `.in` and `.ans` files keeps both forms reachable from one set of sources: the streaming form for the repository, the conventional form for uploading and archiving.
A working implementation of the wrapper and both tools, with the two example problems below, is in [`ppf-stream/`](../ppf-stream/).

A test case is written as a pipeline, so extra stages can be inserted after the generator:

```
--n 1000000 --digits 6 --case max-n | keep-north
```

That is what makes a late constraint change cheap.
When a constraint is narrowed days before a contest, adding a stage leaves the generator that has already been reviewed and tested untouched, and the diff is one token per test case plus one small program.
Every stage belongs to the cache key, so adding one regenerates rather than silently reusing unfiltered data.

Filters are much easier to get wrong than they look, which is the subject of the next section.

That split is what makes the classic development loop survive.

```
                submission < big.in | judge big.ans -

    becomes     gen ARGS | submission | judge (gen ARGS) -
```

Two example problems, in two languages, driven by the same unmodified wrapper:

Example     | `gen` and `judge` | `data/` on disk | Presented to the submission
----------- | ----------------- | --------------- | ---------------------------
`rangesum`  | C++               | 34 bytes        | 59,334,530 bytes
`artifacts` | Python            | 73 bytes        | 48,081,388 bytes

The C++ example uses the specification's existing `build` and `run` script convention to compile both programs, and generates about ten times faster than the Python one, which matters when no cache survives between invocations.

## Validating what was generated

The specification has judge systems run `input_validators/` against the `.in` files in `data/`.
In a streaming package those files are argument lines, not data, so validating the bytes the submission actually receives is the setter's job and belongs in preparation tooling rather than at judging time.

It is worth doing, because a filter is easy to get wrong in ways that are invisible until they are expensive.
A worked instance, from adding a `keep-north` stage to the example problem after a late ruling that latitudes are positive:

The obvious first attempt drops artifacts south of the equator and passes the header through.
The input validator rejected every case, for two independent reasons: the query box is part of the input and its lower latitude was still negative, and the header still claimed `n = 5000` while the body now held 3,928 artifacts.
Both are silent corruption; neither is visible by reading the filter.

The second attempt reflected artifacts rather than dropping them, which preserves the count and every planted boundary point, and mirrored the box when it lay wholly south, which is an exact bijection.
It still failed, because mirroring moves artifacts that were already northern into the south.

The third attempt passed.
The lesson generalises: a filter has to be format-aware, because the format has a count and a query box, and because the generator deliberately plants points exactly on and one unit in the last place either side of the box edges.
Those planted points are the only reason several test cases exist, and a filter that mangles them leaves a test set that still validates and no longer tests anything.

Which is the second half of the check.
After any late narrowing, re-judge every submission and confirm the ones under `wrong_answer/` still fail.
In the example the discriminating power survived — the floating-point submission still differs on 441 names in the `boundary` case, against 441 before the change — but nothing about the filter guaranteed that, and a filter that had narrowed coordinates instead of reflecting them would have destroyed it while leaving every validator green.

## Worked example

A problem taking `n` artifacts with hyphenated lowercase names and GPS positions, asking for every artifact inside or on the boundary of a query box, with coordinates as exact decimals of up to 4000 fractional digits.

Inclusive boundaries are what force exactness.
A double carries about 15 to 17 significant decimal digits, so a coordinate one unit in the last place outside the box rounds to the same double as the box edge.
The generator places such points deliberately.

The obvious floating-point solution is correct at six fractional digits, including on the million-artifact case, and wrong wherever precision exceeds a double:

Case            | Digits | In box, exact | Reported by float solution
--------------- | ------ | ------------- | --------------------------
`tiny`          | 6      | 3             | 3
`max-n`         | 6      | 486,338       | 486,338
`max-precision` | 4000   | 104           | 123
`mid`           | 400    | 981           | 1,223
`boundary`      | 30     | 2,310         | 2,918

Test data covering only the large case would accept it.

Reproducibility, with no container, lockfile, or environment pin:

Property                                          | Result
------------------------------------------------- | ------------------------------
Same parameters, repeated runs                     | byte-identical
Across `PYTHONHASHSEED` 0, 1, 12345, `random`      | byte-identical
Across CPython 3.10, 3.11, 3.12, 3.13              | byte-identical
Across two unrelated machines                      | byte-identical
Raising `n`                                        | existing items unchanged
Hostile submissions                                | verdict, never a judge error

## Archiving

Keep the repository, and archive a materialised package alongside it.
An immutable release artifact with a stable identifier is what lets a problem be reused: [Zenodo](https://zenodo.org) is well suited, being free, DOI-issuing, and designed for this.

Keeping both means a future maintainer can regenerate and compare against the archived bytes, which is the strongest form of the guarantee: not "we believe this is what ran", but "this is what ran, and here is the program that produces it".
