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
pip install flake-detective
```

Python 3.11+, no runtime dependencies. **It needs pytest, but not in its own
environment:** it runs your suite as a subprocess with the pytest installed wherever
your tests already run, and you point it there with `--python`. If that interpreter
cannot import pytest, it stops and says so (exit status 2) rather than reporting a
clean suite.

To run the built-in benchmark (`bench`) with no `--python`, pytest has to be next to
flake-detective: `pip install flake-detective pytest`.

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

# Check the tool against a suite whose answers are known (about a minute)
flake-detective bench

# Write that suite out to read it
flake-detective fixture ./flake-fixture
```

## What it prints

The built-in benchmark's suite — written to be flaky in four specified ways — at the
default 7 runs per arm, from `flake-detective fixture ./fx` then
`flake-detective investigate ./fx --seed 0`:

```
10 tests, 4 arms, 37s
order seed 0 (--seed 0 repeats these shuffles)

  baseline   7 runs         identical conditions, repeated
  order      7 runs         the same tests, shuffled
  hashseed   7 runs         PYTHONHASHSEED varied
  clock      7 runs         the wall clock frozen at a different date each run

4 flaky tests:
    1  order
    1  hash-seed
    1  clock
    1  nondeterminism

Rates are failures over the runs that observed the test; - means an arm
never saw it pass or fail. Setup and teardown errors count as failures.

--------------------------------------------------------------------------
test                                                   baseline    order hashseed    clock
--------------------------------------------------------------------------
test_order_dependent.py::test_aaa_first_one_wins            0.0      0.4      0.0      0.0
    ORDER (direction undetermined): failed 0 of 7 baseline runs, 3 of 7 shuffled: its result depends on which tests run before it. The order arm cannot say which way - broken by another test, or relying on one - and nothing else measured it
    fix: its result depends on which tests run before it: another test either leaks state into it or creates state it relies on. --localise names that test and says which (so does --arms isolation)

..._hash_dependent.py::test_first_of_a_set_is_stable        0.0      0.0      0.7      0.0
    HASH-SEED: stable under identical repetition; failed 5 of 7 runs when PYTHONHASHSEED varied
    fix: something iterates a dict or set and depends on the order; sort it

test_clock_dependent.py::test_second_is_even                0.0      0.0      0.0      0.4
    CLOCK: stable under identical repetition; failed 3 of 7 runs when the wall clock frozen at a different date each run
    fix: it reads the wall clock; freeze or inject the time

test_nondeterministic.py::test_unseeded_random              0.4      0.4      0.6      0.9
    NONDETERMINISM: flipped with nothing changed: failed 3 of 7 identical runs
    fix: it flips with nothing changed - unseeded randomness, or a race
```

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

Measured on the built-in 10-test suite, on a 16-core Windows machine that was busy with
other work at the time (so read these as upper bounds):

| command | runs | wall time |
|---|---:|---:|
| `flake-detective bench` (default `--jobs 4`) | 28 | 38 s and 67 s, in two runs |
| `flake-detective bench --jobs 1` | 28 | 142 s |
| `flake-detective investigate ./fx` (the same suite, `--jobs 1`) | 28 | 99 s; 37 s on a later, quieter run |

The two `--jobs 1` rows do the same work; the 40-second gap between them is the machine's
load, not the tool. Most of each run is pytest starting up, so on your suite expect
roughly your normal `pytest` time, 28 times over.

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

`flake-detective bench` at 7 runs per arm: **4/4 flaky tests detected, 4/4 with the right
cause, 0 of 6 stable tests flagged.** The stable tests are what make that mean anything:
three are written to look flaky (one iterates a set, one mutates module state, one reads
the clock), and one is the other half of the order-dependent pair.

What a run count buys, from `flake-detective bench --sweep`:

| runs/arm | detection | attribution | false pos | secs |
|---:|---:|---:|---:|---:|
| 1 | 75% | **0%** | 0% | 18 |
| 2 | 100% | 100% | 0% | 26 |
| 3 | 100% | 100% | 0% | 19 |
| 5 | 100% | 100% | 0% | 22 |
| 7 | 100% | 100% | 0% | 24 |
| 11 | 100% | 100% | 0% | 30 |

Seconds are with the default `--jobs 4` on the same busy machine, and noisy: the two-run
row took longer than the three-run row. Detection at one run varies between sweeps
(earlier ones found 3 of 4 and 2 of 4, this one 3 of 4) because a single shuffle may or may not put
the order-dependent pair the wrong way round.

The zero attribution at one run is deliberate. One run per arm finds instability and
**refuses to name a cause**, because a baseline that runs once cannot flip, and a control
that cannot flip cannot rule out nondeterminism. Before that refusal existed, the one-run
pass reported the nondeterministic test as `clock` — confidently, and wrongly.

### On real suites, where every finding would be a false positive

These two passes, and the planted-flake study below, were measured before outcomes were
read from pytest's own reports (the audit fixes listed further down). The change can only
add observations — errors, and ids the old parser dropped — so re-measuring could raise the
false-positive count; it has not been re-run.

Two separate passes over real, deterministic suites, at different depths:

| pass | suites | tests | runs per arm | flagged as flaky |
|---|---:|---:|---:|---:|
| deep | 5 | 166 | 5 (20 runs per suite) | **0** |
| wide | 15 | 660 | 3 (12 runs per suite) | **0** |

The wide pass includes the five suites of the deep one; a few had gained tests in between
(blast-radius 28 → 32, suite-auditor 24 → 32), which is why the counts differ. The
fifteen: clcuv-surveillance (101 tests), primer-designer (64), urdu-nlp-toolkit (57),
repo-surgeon (47), insurance-mlops (44), docstring-drift (42), perf-hunter (42),
demand-forecast-platform (41), credit-risk-engine (40), pr-referee (37), blast-radius (32),
model-serving-platform (32), suite-auditor (32), trace-to-patch (30), devign-leakage (19).
All share an author, so a clean sweep across them is weaker evidence than unrelated
projects would be.

A detector that cries wolf gets uninstalled in a week, so this is the number to check
before the detection rate. On its own it only shows the tool is quiet on quiet code.

### Planted in other people's suites

The fixture's author and the classifier's author are the same person, so the fixture
cannot fairly measure detection. So a flake of known cause was planted into twelve real
suites instead — among thirty to ninety real tests each, with their own fixtures and
conftest — using [`scripts/inject_and_score.py`](https://github.com/hammasbuilds/flake-detective/blob/main/scripts/inject_and_score.py):

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

## What this does NOT do

- **It does not prove a suite is clean.** "No flaky tests found" means nothing was seen
  in this many runs, and the report prints what that many runs can miss. A test failing
  one run in fifty is almost certainly still there after seven.
- **It is not fast.** See "What it costs" above. Every run is a real pytest
  process, by design: that is what makes the conditions controllable.
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

## Problems hit while building this

Every one of these produced a **confident, wrong answer**, and the benchmark is what
caught them.

- **The control could not control the thing it was controlling for.** The clock arm first
  waited 1.1 seconds between runs, but the baseline takes time too, so a test asserting
  `int(time.time()) % 2 == 0` flipped in the *baseline* and was filed as nondeterminism.
  The fix: freeze the clock in every arm and move the frozen instant only in the clock arm.
- **The cleanest evidence there is was invisible.** The hash-dependent test passed 7/7
  under seed 0 and failed 7/7 under seeds 1–7. It never flipped *within* an arm, and the
  classifier only looked for flips, so it reported nothing.
- **One run per arm produced a confident cause** (see above).
- **The date boundaries were not on any boundary.** Instants pinned in UTC landed, on a
  UTC+5 machine, on the local afternoon of ordinary days. They are now placed in local
  time, where the suite under test reads them.
- **The tool reported success when it had examined nothing.** An empty directory, a
  missing pytest and a collection error all printed "No flaky tests found" and exited 0;
  with pytest missing, `bench` reported "detection 0/4" as though the classifier had
  failed. Each is now exit status 2 with the reason.

An independent audit of the first release candidate found more, all of the same kind:

- **Fixture errors were invisible.** Outcomes were scraped from pytest's `-rf` summary,
  which prints `FAILED` lines only. A test whose fixture raised in every baseline run
  appeared nowhere — not as flaky, not even as broken.
- **A test that never ran counted as a pass.** With `addopts = -x`, an order-dependent test
  sitting after a random failure came back as *nondeterminism* (baseline 0.6), because in
  the runs where the earlier test failed it never ran at all.
- **A test id containing `" - "` vanished**: the summary line uses that to separate the id
  from the message.
- **A REPO below the rootdir wasted every order run.** With `mono/pytest.ini` and
  `investigate mono/pkg`, node ids came back relative to `mono` and were passed back from
  `mono/pkg`: "file or directory not found". They are now made absolute.
- **More arms gave a weaker answer.** Under `--arms all` a plain polluter/victim pair was
  `UNKNOWN (order, parallel, isolation)`, and the headline said "9 flaky tests" but listed
  four, because its list of causes predated four of the arms.
- **A test that needs another was told the other test was polluting it**, and `--localise`
  then called it "broken, not order-dependent" because it fails alone.
- **Ctrl-C printed a twenty-line traceback and threw away every finished run.**

All of these now have regression tests.

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

From source: `git clone https://github.com/hammasbuilds/flake-detective && cd flake-detective && pip install -e ".[dev]" && pytest`.

## License

MIT - see [LICENSE](https://github.com/hammasbuilds/flake-detective/blob/main/LICENSE).
