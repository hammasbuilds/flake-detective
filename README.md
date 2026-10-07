<h1 align="center">flake-detective</h1>
<p align="center"><i>A flaky test is not a problem to rerun. It is a dependency nobody declared.</i></p>

<p align="center">
  <a href="https://github.com/hammasbuilds/flake-detective/actions/workflows/ci.yml"><img src="https://github.com/hammasbuilds/flake-detective/actions/workflows/ci.yml/badge.svg" alt="ci"></a>
  <img src="https://img.shields.io/badge/python-3.11%2B-blue" alt="python">
  <img src="https://img.shields.io/badge/runtime%20deps-0-brightgreen" alt="zero dependencies">
  <a href="https://github.com/hammasbuilds/flake-detective/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="license"></a>
</p>

flake-detective runs your pytest suite many times, changing **one thing per batch of
runs** — the test order, `PYTHONHASHSEED`, the frozen wall-clock date — and reports
which change makes each flaky test flip. So instead of "`test_x` is flaky, rerun it"
you get "`test_x` passes on its own and fails after `test_y`" (or "fails on its own and
passes only after `test_y`", which needs the opposite fix), with the failure rate under
every condition printed beside the verdict.

`pytest-rerunfailures` makes a flaky test go away. This tells you why it was flaky.

## Install

```bash
pip install git+https://github.com/hammasbuilds/flake-detective
```

PyPI release coming: `pip install flake-detective` will work once it is published.

Python 3.11+, no runtime dependencies. **It needs pytest, but not in its own
environment:** it runs your suite as a subprocess with the pytest installed wherever
your tests already run, and you point it there with `--python`. If that interpreter
cannot import pytest, it stops and says so (exit status 2) rather than reporting a
clean suite.

To run the built-in benchmark (`bench`) with no `--python`, pytest has to be next to
flake-detective: `pip install git+https://github.com/hammasbuilds/flake-detective pytest`.

## Quickstart

```bash
# Your project, with its own virtualenv. Linux / macOS:
flake-detective investigate path/to/project tests --python path/to/project/.venv/bin/python

# Windows:
flake-detective investigate path\to\project tests --python path\to\project\.venv\Scripts\python.exe

# --python also accepts the venv directory itself:
flake-detective investigate path/to/project tests --python path/to/project/.venv
```

`REPO` is the directory pytest runs in; the optional `TARGET` narrows to a path or node
id inside it. If flake-detective is installed in the same environment as your tests,
leave `--python` out.

```bash
# In CI: exit 1 if anything flaky is found, 2 if the suite could not be examined
flake-detective investigate . tests --runs 5 --fail-on-flake

# Name the other test in each order dependence, and which way round it is
flake-detective investigate . tests --localise

# Repeat an earlier investigation's shuffles exactly (the report prints the seed)
flake-detective investigate . tests --seed 1234

# Check the tool against a suite whose answers are known (seconds to minutes, see What it costs)
flake-detective bench

# Write that suite out to read it
flake-detective fixture ./flake-fixture
```

## What it prints

The built-in benchmark's suite — written to be flaky in four specified ways — at the
default 7 runs per arm, from `flake-detective fixture ./fx` then
`flake-detective investigate ./fx --seed 0`:

```
==========================================================================
FLAKE DETECTIVE
==========================================================================
16 tests, 4 arms, 14s
order seed 0 (--seed 0 repeats these shuffles)

  baseline   7 runs         identical conditions, repeated
  order      7 runs         the same tests, shuffled
  hashseed   7 runs         PYTHONHASHSEED varied
  clock      7 runs         the wall clock was frozen at a different date each run

5 flaky tests:
    2  order
    1  hash-seed
    1  clock
    1  nondeterminism

Rates are failures over the runs that observed the test; - means an arm
never saw it pass or fail. Setup and teardown errors count as failures.

--------------------------------------------------------------------------
test                                                   baseline    order hashseed    clock
--------------------------------------------------------------------------
...needs_setup.py::test_needs_the_helper_to_have_run        1.0      0.4      1.0      1.0
    ORDER (direction undetermined): failed 7 of 7 baseline runs, 3 of 7 shuffled: its result depends on which tests run before it. The order arm cannot say which way - broken by another test, or relying on one - and nothing else measured it
    ! it failed in every baseline run, so clock, hashseed could not show a difference and were not ruled out. Fix the cause
      above and run again: a second cause would be invisible here.
    fix: its result depends on which tests run before it: another test either leaks state into it or creates state it relies on. --localise names that test and says which (so does --arms isolation)

test_order_dependent.py::test_aaa_first_one_wins            0.0      0.6      0.0      0.0
    ORDER (direction undetermined): failed 0 of 7 baseline runs, 4 of 7 shuffled: its result depends on which tests run before it. The order arm cannot say which way - broken by another test, or relying on one - and nothing else measured it
    fix: its result depends on which tests run before it: another test either leaks state into it or creates state it relies on. --localise names that test and says which (so does --arms isolation)

..._hash_dependent.py::test_first_of_a_set_is_stable        0.0      0.0      0.7      0.0
    HASH-SEED: stable under identical repetition; failed 5 of 7 runs when PYTHONHASHSEED varied
    fix: something iterates a dict or set and depends on the order; sort it

test_clock_dependent.py::test_second_is_even                0.0      0.0      0.0      0.4
    CLOCK: stable under identical repetition; failed 3 of 7 runs when the wall clock was frozen at a different date each run
    fix: it reads the wall clock; freeze or inject the time

test_nondeterministic.py::test_unseeded_random              0.7      0.6      0.4      0.9
    NONDETERMINISM: flipped with nothing changed: failed 5 of 7 identical runs
    fix: it flips with nothing changed - unseeded randomness, a race, or state left behind by an earlier run (a cache, a file outside tmp_path, a database row). If it failed once and then never again, look for the state first
```

`--seed 0` repeats the shuffles, so the order, hash-seed and clock rows come out the same
every time. The nondeterministic row does not (that test is unseeded on purpose), and
the wall time in the first line depends on the machine - 15 to 24 s on a 16-core Windows
machine, up to a few minutes on a heavily loaded one.

**The evidence is the shape of the row, not the label.** Three rows are zero everywhere
but one column — that is what an attribution looks like. The order row says *direction
undetermined* because only the order arm measured it; `--localise` would add "passes on
its own and fails after `test_bbb_also_appends`" (checked: 7 extra runs). The fourth is nonzero in
*every* column including the control, which is nondeterminism whatever word sits beside
it. (The nondeterministic row's exact numbers change from run to run; that is the point
of it.)

## What it costs

**Every run is a full pytest process.** An investigation costs about

    (number of arms + 1)  x  --runs  x  one run of your suite

which at the defaults (3 arms + the baseline, 7 runs) is **28 runs of your suite**, plus
one collection. A suite that takes 20 seconds takes about ten minutes. The `isolation`
arm is the exception: one pass is one pytest process *per test*.

Progress is printed per run, with an estimate of what is left:

```
  .. hashseed 3/7   (run 17 of 28, 32s so far, about 21s left)
```

Measured on the built-in 10-test suite, 2026-10-04, on a 16-core Windows machine:

| command | runs | wall time |
|---|---:|---:|
| `flake-detective bench` (default `--jobs 4`) | 28 | 5 s |
| `flake-detective bench --sweep` | 6 run counts | 26 s |
| `flake-detective investigate ./fx --seed 0` (`--jobs 1`) | 28 | 15 s; 24 s with another job running |

Load dominates: the same `bench` took 38 to 214 s on 2026-10-03 while the machine ran
other jobs. Most of each run is pytest starting up, so on your suite expect roughly your
normal `pytest` time, 28 times over.

`--jobs N` runs N pytest processes at once. It is on by default for `bench`, whose suite
shares nothing between processes, and **off by default for `investigate`**: two copies of
a real suite running side by side collide on anything it fixes in place — a file in the
repo, a port, a database name — and those collisions are exactly the flakes this tool
looks for. Raise it for a suite you know does not do that.

Cheaper ways to spend less: narrow with `TARGET`, lower `--runs` (the report prints what
that many runs can miss), or pick arms with `--arms`.

## Options

| option | default | what it does |
|---|---|---|
| `--runs N` | 7 | runs per arm. 1 sees instability but names no cause (one baseline run cannot flip); 7 misses a two-test order dependence 0.8% of the time, 5 misses it 3.1% |
| `--arms LIST` | `order,hashseed,clock` | also `timezone`, `locale`, `parallel`, `isolation`, or `all`. Unknown names are refused |
| `--python PATH` | this interpreter | the interpreter (or venv directory) your tests run with; must have pytest |
| `--jobs N` | 1 | pytest processes at once — see above before raising it |
| `--timeout S` | 900 | seconds per pytest run; a run that times out is left out and reported |
| `--localise` | off | bisect each order dependence to the other test involved, and say whether that test breaks it or it needs that test (about log2(n) extra runs each); shown in the report and the JSON |
| `--seed N` | random | seed for the order arm's shuffles; the report prints it, so any run can be repeated |
| `--fail-on-flake` | off | exit 1 if anything flaky is found |
| `--json FILE` | — | also write the findings, including any error, as JSON |
| `--no-freeze-clock` | off | do not pin the wall clock (disables the clock arm) |
| `--quiet` | off | no progress on stderr |
| `--version` | | print the version |

**Exit status:** `0` examined and (with `--fail-on-flake`) nothing flaky; `1` flaky tests
found with `--fail-on-flake`; `2` nothing — or not everything — could be examined: bad
arguments, pytest missing from the target interpreter, a collection error, no tests
collected, every test skipped, or an arm in which no run could be scored. Exit 2 always
comes with the reason, in pytest's own words where there are any, on stderr. `130`
interrupted with Ctrl-C: running pytest processes are killed and the runs that finished
are still reported, marked INTERRUPTED.

## How it works

```
  BASELINE  seed pinned, clock frozen, order fixed, repeated
      |
      +-- flips with nothing changed? --> NONDETERMINISM (nothing more can be learned)
      |
      +-- stable: now vary ONE thing per arm
              order      shuffle the tests
              hashseed   change PYTHONHASHSEED
              clock      move the frozen date
                  |
                  +-- one environment arm disagrees     --> that arm is the cause
                  +-- only order/isolation/parallel do  --> ORDER or NEEDS-OTHER-TEST
                  +-- two independent arms disagree     --> UNKNOWN, not a guess
```

| what varies | what it proves when the test flips |
|---|---|
| nothing at all | nondeterminism — unseeded randomness, or a race |
| the order | its result depends on which tests run before it (direction: see below) |
| `PYTHONHASHSEED` | something iterates a dict or set and depends on the order |
| the frozen date | it reads the wall clock |
| `TZ` (opt-in, POSIX) | it depends on the machine's timezone |
| `LANG`/`LC_ALL` (opt-in, POSIX) | it depends on the locale |
| `pytest -n 4` (opt-in, needs pytest-xdist) | tests share a fixed file, port or database |
| each test alone (opt-in) | which way an order dependence points |

**"Identical" takes work.** CPython randomises string hashing on every start, and the
clock moves while the suite runs. Both are **pinned in every arm** — seed `0`, and the
wall clock frozen — so the arm that varies one of them is the only place it varies at
all. `time.monotonic` and `perf_counter` are deliberately left alone: they measure
durations, and freezing them hangs anything that waits. pytest-randomly, if installed,
is disabled for every run, since it would shuffle the baseline.

**The frozen dates are chosen, not random**, and ordered so that even `--runs 2` or `3`
crosses a weekend and a weekday, an odd and an even second, a month end, a leap day and
a year end; later runs add a DST fall-back hour and a new year.

**Disagreement is not only a flip.** A test that passes 7/7 under one seed and fails 7/7
under the others never flips *within* an arm, and is the strongest evidence of hash
dependence there is. An arm implicates a test when it flips **or** when its failure rate
differs from the baseline's.

**The baseline is checked first and wins.** A test that flips with nothing changed also
flips when the order changes; without that precedence every arm would take credit for
it. **Two independent arms means `UNKNOWN`, never the first match** — a wrong cause sends
somebody to the wrong file, which is worse than no cause.

**Order, isolation and parallel are one family.** All three change which other tests run
before this one in the same process, so when more than one of them implicates a test that
is corroboration, not a conflict. (An environment arm — hashseed, clock, timezone, locale —
together with any other arm is still `UNKNOWN`.) A flip under `parallel` alone is credited
to parallelism: something shared between processes.

**An order dependence has a direction**, and the two have opposite fixes:

| | run alone | reported as |
|---|---|---|
| another test leaks state into it | passes | `ORDER`, with the other test under `caused by` |
| it relies on state another test creates | fails | `NEEDS-OTHER-TEST`, with the other test under `needs` |

The order arm alone cannot tell these apart — both fail in some shuffles — so without the
`isolation` arm or `--localise` the finding says `ORDER (direction undetermined)` and the
advice says how to find out, rather than guessing. `--localise` runs the test alone once
to fix the direction, then bisects for the test that breaks it or the test it needs.

**What each test did comes from pytest, not from the terminal.** A plugin injected into
every run records pytest's own setup, call and teardown reports. A fixture error counts
as a failure. A test counts only in runs where it was seen to pass or fail — a skipped
test, or one a run never reached, is not a pass. Options in your `addopts` that would stop
a run early or reorder it by history (`-x`, `--maxfail`, `--sw`, `--lf`, `--ff`, `--nf`,
and xdist's `-n` outside the parallel arm) are switched off for these runs; the rest of
your `addopts` is kept. A pytest-rerunfailures retry counts as the failure it was.

## How well it works

### On a suite with known answers

The fixture holds **8 flaky tests, one per cause the tool can name, and 8 stable ones** -
and a cause whose arm cannot run on the current machine is reported as unscoreable rather
than counted as a miss, because otherwise this number measures the platform.

`flake-detective bench` at 7 runs per arm, on Windows, with every arm (`--arms all`) and
`pytest-xdist` installed:

| | |
|---|---:|
| scoreable causes | **6** of 8 |
| detected | **6 / 6** |
| right cause | **6 / 6** |
| stable tests flagged | **0** of 8 |
| not scoreable here | `timezone`, `locale` |

Three runs on 2026-10-07 gave 6/6, 6/6, 0/8 each time, at `--jobs 4` and at `--jobs 1`.
With the default three arms it is **4 / 4** detected and attributed, 0 of 8 flagged, and
the four remaining causes are reported unscoreable: the arms that establish them did not
run.

**`--arms` was unreachable from the command line until 2026-10-07.** `bench()` had taken
the argument since the fixture gained a known positive for every arm, but no flag passed
it, so four of the eight causes could not be scored by anyone using the tool as shipped.

`timezone` and `locale` stay unscoreable on Windows, and that is measured rather than
assumed: setting `TZ` there shifts the clock without understanding zone names, and
`LANG`/`LC_ALL` do not reach the interpreter's locale at all. An arm built on either would
blame a cause that reproduces nowhere a user runs. Both report themselves skipped, with
the reason.

**`parallel` used to be the third of those, and it was this tool's own fault twice over.**
It needs `pytest-xdist`, which is now in the dev extra, so the published number is
reproducible from a plain dev install. And the fixture could not exhibit the cause it
claimed: a single test asserting that no *other copy of itself* held a marker, when
`-n` runs each test once, on one worker. There was never a second copy. At `--jobs 1` the
cause was missed outright; at the default four it was detected and called
`nondeterminism`, because four independent pytest processes collided on a marker keyed to
a path rather than to a run - so the test flipped in the *baseline*, and nondeterminism is
the right reading of a test that does that. The fixture is now a pair: a holder that takes
the marker for two seconds and asserts nothing, and a contender that only checks it,
keyed on `PYTEST_XDIST_TESTRUNUID` so the workers of one run contend while independent
pytest processes do not.

Not every run is 4/4: the nondeterministic test fails half its runs, so with probability
2/2^7 = 1.6% all seven baseline runs agree, the other arms then disagree with each other,
and it comes back `UNKNOWN` - attribution 3/4, never a wrong cause. One of six `bench` runs
on 2026-10-03 did exactly that.

**One test is 25 points of this detection rate**, which is why the 48-plant study and the
1,752-test false-positive run further down are the stronger evidence and this is the
smallest claim here. The stable tests are what make even that mean anything: three are
written to look flaky (one iterates a set, one mutates module state, one reads the clock),
one is the other half of the order-dependent pair, one is the helper the
needs-another-test fixture depends on, and one is the holder the parallel fixture
contends with.

What a run count buys. `flake-detective bench --sweep` scores one sweep; the
nondeterministic test is unseeded on purpose, so a single sweep is a draw, not the rate.
Eight sweeps on 2026-10-04 (`--sweep --json`), counting how many found all four flaky
tests and how many also named all four causes right:

| runs/arm | all 4 detected | and all 4 causes right | stable tests flagged | secs per point |
|---:|---:|---:|---:|---:|
| 1 | 0 of 8 (2-3 of 4 found) | 0 of 8 | 0 | 2-3 |
| 2 | 7 of 8 | 7 of 8 | 0 | 2-3 |
| 3 | 8 of 8 | 6 of 8 | 0 | 3-4 |
| 5 | 8 of 8 | 8 of 8 | 0 | 4-6 |
| 7 | 8 of 8 | 7 of 8 | 0 | 5-7 |
| 11 | 8 of 8 | 8 of 8 | 0 | 7-9 |

The one miss at 2 runs is detection: the nondeterministic test gave the same result in
every run of every arm, so nothing flagged it. Every other miss is that test coming back
`UNKNOWN`: it fails
about half its runs, so sometimes every baseline run agrees, the control shows no flip, and
the other arms then disagree with each other. Nothing in those sweeps was given a wrong
cause, and no stable test was ever flagged.

The zero attribution at one run is deliberate. One run per arm finds instability and
**refuses to name a cause**, because a baseline that runs once cannot flip, and a control
that cannot flip cannot rule out nondeterminism. Before that refusal existed, the one-run
pass reported the nondeterministic test as `clock` — confidently, and wrongly.

### On real suites

The wide pass below was re-measured on **2026-09-29** against the current suites (several
had grown substantially since the last pass, some by 2-10x — an active portfolio, not a
fixed benchmark). The deep pass and the planted-flake study further down were both
measured before outcomes were read from pytest's own reports (the audit fixes listed
further down); that change can only add observations, never hide one, so re-measuring
could only raise a false-positive count that has stayed at zero across two different
outcome-reading implementations now — but the deep pass and the plant study themselves
have not been re-run since, and are dated below.

Two separate passes over real, deterministic suites, at different depths. Both are
author-run: the suites are the author's own repositories, so these rows cannot be
re-run from this repository alone (the third-party study below can):

| pass | suites | tests | runs per arm | flagged as flaky |
|---|---:|---:|---:|---:|
| deep (measured before the outcome-reading refactor, not re-run) | 5 | 166 | 5 (20 runs per suite) | **0** |
| wide (re-measured 2026-09-29) | 14 scored + 1 environment crash | 1,752 | 3 (12 runs per suite) | **0** |

The fifteenth suite, devign-leakage, could not be scored this pass: pytest itself crashes
on import inside that repo's own virtual environment (`pandas.errors` hits a native stack
overflow, exit code 0xC0000FD unrelated to flake-detective) — reported honestly as "nothing
was examined," not folded into the clean total.

The fourteen scored, with the suite sizes they had **when they were scored on
2026-09-29** - three have grown since, and restating today's numbers would
misdescribe what was actually examined: urdu-nlp-toolkit (576 tests), blast-radius (237),
clcuv-surveillance (203), suite-auditor (108), credit-risk-engine (108), primer-designer
(94), demand-forecast-platform (77), insurance-mlops (111), repo-surgeon (47),
docstring-drift (42), perf-hunter (42), pr-referee (37), model-serving-platform (40),
trace-to-patch (30). All share an author, so a clean sweep across them is weaker evidence
than unrelated projects would be.

A detector that cries wolf gets uninstalled in a week, so this is the number to check
before the detection rate. On its own it only shows the tool is quiet on quiet code.

### Planted in third-party suites (re-runnable)

The two studies below ran against the author's own repositories, so nobody else can
re-run them. This one can: `sh scripts/reproduce_third_party.sh` clones
[toolz](https://github.com/pytoolz/toolz) at `451af60` and
[sqlparse](https://github.com/andialbrecht/sqlparse) at `60cdc64`, plants one flake of
each cause into each, and investigates at the defaults (7 runs per arm, `--seed 0`).
Two runs on a 16-core Windows machine, one while it was busy with other jobs and one
while it was quiet:

| suite | tests | order | hash-seed | clock | nondeterminism | other tests flagged |
|---|---:|:---:|:---:|:---:|:---:|---|
| toolz, 2026-10-03 (busy) | 194 | right | right | right | right | none |
| sqlparse, 2026-10-03 (busy) | 510 | right | right | right | `UNKNOWN` | 2 distinct, in 4 of 4 runs |
| toolz, 2026-10-04 (quiet, 5 min in all) | 194 | right | right | right | right | none |
| sqlparse, 2026-10-04 | 510 | right | right | right | right | none |

16 of 16 plants detected, 15 of 16 with the right cause. The miss is the same one the
benchmark can show (below): a test that fails half the time also passes all 7 baseline
runs, or fails all 7, with probability 2/2^7 = 1.6%, and then the other arms disagree
and the answer is `UNKNOWN` rather than a guess.

The two sqlparse tests flagged on the busy day are real wall-clock flakes, not false
positives: `test_dos_prevention.py::test_nested_paren_within_cap_under_1s` and
`..._case_within_cap_under_1s` assert that a parse finishes in under one second, which a
loaded machine does not always manage - and on the quiet day it did, so they were not
flagged.

### Planted in the author's other suites (author-run, dated)

The fixture's author and the classifier's author are the same person, so the fixture
cannot fairly measure detection. So a flake of known cause was planted into twelve real
suites instead — among thirty to ninety real tests each, with their own fixtures and
conftest — using [`scripts/inject_and_score.py`](https://github.com/hammasbuilds/flake-detective/blob/main/scripts/inject_and_score.py). Measured before the outcome-reading
refactor above; not re-run since (unlike the wide pass, replaying a plant against a
suite that has since gained or lost tests is not a like-for-like re-check, so this one
is left dated rather than re-run against drifted targets):

| | |
|---|---:|
| plants scored | **48** across 12 repositories |
| detected | **47 / 48 — 98%** |
| cause named correctly | **46 / 48 — 96%** |
| findings that were **not** the planted test | **0** |

The order miss was the run count: a two-test order dependence is only exposed by
shuffles that put the culprit first, so five shuffles miss it 3.1% of the time — which is
why the default is now seven. The nondeterminism miss came back as `UNKNOWN`, not wrong.
That run used 5 runs per arm, before the default changed.

Full details: [docs/RESULTS.md](https://github.com/hammasbuilds/flake-detective/blob/main/docs/RESULTS.md).

## Scope

- **It does not prove a suite is clean.** "No flaky tests found" means nothing was seen
  in this many runs, and the report prints what that many runs can miss. A test failing
  one run in fifty is almost certainly still there after seven.
- **It is not fast.** See "What it costs" above. Every run is a real pytest
  process, by design: that is what makes the conditions controllable.
- **One cause can hide another, and the report says when.** Attribution works by
  exclusion: an arm counts when its failure rate *differs* from the baseline's. That needs
  the baseline to have room to differ. A test that fails in **every** baseline run cannot
  fail more often under any arm, so an arm sitting at the same rate was never really
  asked — measured on a test that is both order-dependent and clock-dependent, where the
  clock arm reads 1.0 because the order dependence fails it there too, and the clock
  dependence is invisible. Follow the suggested fix and the test still fails half the
  time. Those arms are now named in the report and in `masked_arms`, with
  `second_cause_possible`, so the finding is "this cause, and a second one is not ruled
  out" rather than "this cause". Finding the second one means fixing the first and running
  again; nothing here can do it in one pass.
- **The environment arms do nothing on Windows, and say so.** `TZ` only moves local time
  where `time.tzset` exists, and `LC_ALL` never reaches `locale.getlocale()` on Windows.
  Measured there, `TZ=Pacific/Kiritimati` and `TZ=America/New_York` return the *same*
  local time while `TZ=UTC` shifts by an hour, so the `timezone` and `locale` arms skip
  themselves with a message instead of producing a cause that reproduces nowhere.
- **No arm for filesystem ordering.** `os.listdir` order, case-insensitive paths and inode
  ordering are real sources of flakiness and none of them is varied.
- **`UNKNOWN` is common and stays that way.** Two independent arms disagreeing means no
  single cause was established.
- **Without `--localise` or the `isolation` arm, an order dependence has no direction.**
  The report says so (`direction undetermined`) instead of guessing which test is to blame.
- **A test that fails under every sampled condition reads as broken, not flaky**, and is
  listed separately rather than dropped.
- **The clock freeze does not reach everything.** A module that did
  `from datetime import datetime` before the plugin loaded keeps its own reference.
  Plugins load before test modules, so this is rare, and `--no-freeze-clock` exists for
  suites that break under it.
- **Durations measured with `time.time()` read as zero.** That is the freeze working. A
  test asserting an operation took measurable wall-clock time will be flagged as
  clock-dependent — correctly.
- **Only pytest.** unittest suites work as far as pytest can collect them.

## Layout

```
src/flake_detective/
  cli.py         the command line, argument checks, exit codes
  detective.py   plan the arms, run them with progress, then classify
  run.py         the arms; pins seed and clock in all of them
  observe.py     the plugin that records each test's outcome from pytest's own reports
  freeze.py      the clock-freezing plugin, the frozen dates, and why monotonic is spared
  classify.py    attribution by exclusion, and what "implicated" has to mean
  localise.py    run alone for the direction, then bisect for the other test
  report.py      every arm's rate printed beside the verdict
  fixture.py     a suite with known causes, plus decoys that look flaky
  bench.py       detection, attribution and false positives - all three or none
  types.py       the causes, and what distinguishes them
```

From source: `git clone https://github.com/hammasbuilds/flake-detective && cd flake-detective && pip install -e ".[dev]" && pytest`. That runs the 145 fast tests (plus 2 that skip on Windows; about 40 seconds); `pytest -m slow` runs the 14 end-to-end ones that put real suites through every arm, and `pytest -m "slow or not slow"` runs all 161, as CI does.

**Run it the way CI does before trusting a pass.** `addopts = "-m 'not slow'"` means a plain `pytest` deselects the end-to-end tests, and one of them sat failing through a commit for exactly that reason: `ALONE_RUNS` became 3, which costs two more probes before the bisection starts, and the probe-budget assertion that should have caught it was never run locally.

`pytest --cov=flake_detective -m "slow or not slow"` reports **87%** of statements. Deselecting the slow tests drops it to 80% and `bench.py` to 34%, which is the clearer reason to run them: the benchmark is nearly all end-to-end.

## License

MIT - see [LICENSE](https://github.com/hammasbuilds/flake-detective/blob/main/LICENSE).
