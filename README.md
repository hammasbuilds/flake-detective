# flake-detective

We built flake-detective to find flaky tests and say what they depend on. It runs your pytest suite many times, changing **one thing per batch of
runs** — the test order, `PYTHONHASHSEED`, the frozen wall-clock date — and reports
which change makes each flaky test flip. So instead of "`test_x` is flaky, rerun it"
you get "`test_x` passes on its own and fails after `test_y`" (or "fails on its own and
passes only after `test_y`", which needs the opposite fix), with the failure rate under
every condition printed beside the verdict.

`pytest-rerunfailures` makes a flaky test go away. This tells you why it was flaky. It
is for maintainers and CI owners of Python test suites.

## What it does

- Runs your suite in arms that each vary one condition - test order, `PYTHONHASHSEED`, a
  frozen wall-clock date, and optionally timezone, locale, `pytest-xdist` parallelism or
  each test alone - next to a baseline of identical repeats.
- Names a cause only when an arm's failure rate differs from the baseline by more than
  the baseline can explain; a test that flips with nothing changed is nondeterminism, and
  two arms that disagree give `UNKNOWN` rather than a guess.
- `--localise` bisects each order dependence to the other test involved and says which
  way round it is.
- `bench` scores the tool on a built-in suite with known answers; `--json` and
  `--fail-on-flake` make it a CI step.

## Screenshots

A small browser front end drives the package for demonstrations. It is a local demo and is **not part of
the package** — the package has no web surface and no runtime dependencies. Every result on these screens
is a live call into the library.

![Overview](https://raw.githubusercontent.com/hammasbuilds/flake-detective/main/docs/screenshots/00-overview.png)
*The front end as it opens: every feature is a tab, and the page states that nothing on it is precomputed.*

![Investigate A Suite](https://raw.githubusercontent.com/hammasbuilds/flake-detective/main/docs/screenshots/01-investigate-a-suite.png)
*Each flaky test with the cause that makes it flip, and its failure rate under every condition.*

![Score The Classifier](https://raw.githubusercontent.com/hammasbuilds/flake-detective/main/docs/screenshots/02-score-the-classifier.png)
*The classifier scored on a suite whose causes are known: detected, attributed, and stable tests wrongly flagged.*

![The Suite Used](https://raw.githubusercontent.com/hammasbuilds/flake-detective/main/docs/screenshots/03-the-suite-used.png)
*The benchmark suite itself — one file per cause, so the scoring can be checked.*

## Install

```bash
pip install git+https://github.com/hammasbuilds/flake-detective
```

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

## Sample input / output

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

## Results

Full tables and every dated run: [docs/RESULTS.md](https://github.com/hammasbuilds/flake-detective/blob/main/docs/RESULTS.md).

**On the built-in suite with known answers.** The fixture holds
**8 flaky tests, one per cause the tool can name, and 8 stable ones**
(three of them written to look flaky).
`flake-detective bench --arms all` at 7 runs per arm, on Windows, with `pytest-xdist`
installed, three runs on 2026-10-07:

| | |
|---|---:|
| scoreable causes | **6** of 8 |
| detected | **6 / 6** |
| right cause | **6 / 6** |
| stable tests flagged | **0** of 8 |
| not scoreable here | `timezone`, `locale` |

With the default three arms it is 4 of 4 detected and attributed.

What a run count buys, eight sweeps on 2026-10-07 (`bench --sweep --json`):

| runs/arm | all 4 detected | and all 4 causes right | stable tests flagged | secs per point |
|---:|---:|---:|---:|---:|
| 1 | 7 of 8 | 0 of 8 | 0 | 3-4 |
| 2 | 8 of 8 | 0 of 8 | 0 | 3-4 |
| 3 | 8 of 8 | 7 of 8 | 0 | 4-5 |
| 5 | 8 of 8 | 7 of 8 | 0 | 6-8 |
| 7 | 8 of 8 | 8 of 8 | 0 | 7-8 |
| 11 | 8 of 8 | 8 of 8 | 0 | 10-13 |

**Planted in third-party suites** (re-runnable: `sh scripts/reproduce_third_party.sh`
clones toolz at `451af60` and sqlparse at `60cdc64` and plants one flake of each cause):

| suite | tests | order | hash-seed | clock | nondeterminism | other tests flagged |
|---|---:|:---:|:---:|:---:|:---:|---|
| toolz, 2026-10-03 (busy machine) | 194 | right | right | right | right | none |
| sqlparse, 2026-10-03 (busy machine) | 510 | right | right | right | `UNKNOWN` | 2, real wall-clock flakes |
| toolz, 2026-10-04 (quiet) | 194 | right | right | right | right | none |
| sqlparse, 2026-10-04 | 510 | right | right | right | right | none |

16 of 16 plants detected, 15 of 16 with the right cause. The two sqlparse tests flagged on
the busy day assert that a parse finishes in under one second.

**Planted in twelve of our own suites** (author-run, `scripts/inject_and_score.py`): 48
plants, **47 detected**, **46 with the right cause**, **0** findings on tests that were
not planted.

**False positives on real suites** (author-run):

| pass | suites | tests | runs per arm | flagged as flaky |
|---|---:|---:|---:|---:|
| deep | 5 | 166 | 5 | **0** |
| wide, 2026-09-29 | 14 | 1,752 | 3 | **0** |
| deep, 2026-10-07, at recorded revisions | 3 | 574 | 5 | **1** (a real 1.5% nondeterministic test in suite-auditor, since fixed there) |

## Good to know

- "No flaky tests found" means nothing was seen in that many runs; the report prints what
  that many runs can miss.
- A test that fails in every baseline run cannot show a difference under any arm, so
  those arms are named as not ruled out (`masked_arms`, `second_cause_possible`): fix the
  first cause and run again.
- The `timezone` and `locale` arms skip themselves on Windows, where `TZ` and `LC_ALL` do
  not reach the interpreter the way they do on Linux and macOS.
- The clock freeze makes durations measured with `time.time()` read as zero; use
  `--no-freeze-clock` for a suite that breaks under it. A module that imported `datetime`
  before the plugin loaded keeps its own reference.
- Only pytest (unittest suites as far as pytest can collect them). Filesystem ordering is
  not varied.

## Tech stack

- Python 3.11+, standard library only at runtime; two pytest plugins of its own
  (`observe.py` records outcomes from pytest's reports, `freeze.py` pins the clock)
- Your suite's own pytest, run as a subprocess through `--python`; `pytest-xdist` for the
  optional parallel arm
- uv, pytest and ruff; GitHub Actions on Ubuntu and Windows, Python 3.11 to 3.14, plus a
  wheel build that runs the suite against the installed package

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

From source: `git clone https://github.com/hammasbuilds/flake-detective && cd flake-detective && pip install -e ".[dev]" && pytest`. That runs the 151 fast tests (2 of them skip on Windows); `pytest -m slow` runs the 14 end-to-end ones that put real suites through every arm, and `pytest -m "slow or not slow"` runs all 165, as CI does.

A plain `pytest` deselects the slow end-to-end tests (`addopts = "-m 'not slow'"`); run `pytest -m "slow or not slow"` as CI does.

`pytest --cov=flake_detective -m "slow or not slow"` reports **87%** of statements. Deselecting the slow tests drops it to 80% and `bench.py` to 34%, which is the clearer reason to run them: the benchmark is nearly all end-to-end.

## Licence

MIT - see [LICENSE](https://github.com/hammasbuilds/flake-detective/blob/main/LICENSE).
