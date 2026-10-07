# Results

Two questions, and they need different evidence.

**Does it find the right cause?** Only a suite whose causes are already known can answer that,
because nobody knows the ground truth for a real flaky test — which is the reason the tool
exists. So there is a fixture, written to be flaky in specified ways.

**Does it cry wolf on real code?** A fixture cannot answer that at all. So it is also run
against five real repositories, where every finding would be a false positive.

Reproduce with `flake-detective bench --sweep` and `flake-detective investigate REPO [TARGET]`.

The fixture numbers below were re-measured on 2026-10-04 on a 16-core Windows machine
(`bench --sweep` with the default `--jobs 4`). Seconds depend heavily on what else the
machine is doing.

---

## 1. The fixture

Ten keyed tests. Four flaky, one per cause. Six stable — and three of those are written to
*look* flaky. Without the decoys, a classifier that shouted "order dependence" at every test
would post perfect detection and perfect attribution on the one cause it ever names.

| test | written to be | why it is hard |
|---|---|---|
| `test_aaa_first_one_wins` | **order** | appends to module state; passes alone, fails after its neighbour |
| `test_first_of_a_set_is_stable` | **hash-seed** | `next(iter({"alpha", "beta"}))` — two elements, so about half of seeds pass |
| `test_second_is_even` | **clock** | `int(time.time()) % 2 == 0` |
| `test_unseeded_random` | **nondeterminism** | `random.random() < 0.5`, seeded from the OS |
| `test_bbb_also_appends` | stable | the other half of the order pair; passes whatever runs first |
| `test_sorted_set_is_deterministic` | stable | iterates a set — but sorts it first |
| `test_module_state_but_cleans_up` | stable | mutates module state — and clears it in `finally` |
| `test_clock_but_only_a_duration` | stable | reads the clock — but only compares a duration |
| `test_seeded_random_is_deterministic` | stable | two seeded streams agreeing |
| `test_plain_arithmetic` | stable | nothing to trip over |

The hash test uses a **two**-element set on purpose. An eight-element one fails under nearly
every seed, which is not a flaky test — it is a broken one, and the tool correctly files it
under "failed in every run" instead. A real hash-order flake usually passes.

### Full report, 7 runs per arm

Re-run on 2026-10-04 (`flake-detective fixture ./fx`, then
`flake-detective investigate ./fx --seed 0`). The order row now says its
direction is undetermined: only the order arm measured it. `--localise` adds "passes on its
own and fails after `test_bbb_also_appends`".

```
10 tests, 4 arms, 24s
order seed 0 (--seed 0 repeats these shuffles)

  baseline   7 runs         identical conditions, repeated
  order      7 runs         the same tests, shuffled
  hashseed   7 runs         PYTHONHASHSEED varied
  clock      7 runs         the wall clock was frozen at a different date each run

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
    CLOCK: stable under identical repetition; failed 3 of 7 runs when the wall clock was frozen at a different date each run
    fix: it reads the wall clock; freeze or inject the time

test_nondeterministic.py::test_unseeded_random              0.7      1.0      0.6      0.7
    NONDETERMINISM: flipped with nothing changed: failed 5 of 7 identical runs
    fix: it flips with nothing changed - unseeded randomness, or a race
```

The four rows are the whole argument. Three are zero everywhere but one column. The fourth is
nonzero in every column *including the control*, which is what nondeterminism looks like
regardless of the label printed beside it.

---

## 2. Accuracy against run count

The nondeterministic test is unseeded on purpose, so one `bench --sweep` is one draw. Eight
sweeps on 2026-10-04 (`flake-detective bench --sweep --json FILE`, eight times):

| runs/arm | all 4 detected | and all 4 causes right | stable tests flagged | secs per point |
|---:|---:|---:|---:|---:|
| 1 | 0 of 8 (2-3 of 4 found) | 0 of 8 | 0 | 2-3 |
| 2 | 7 of 8 | 7 of 8 | 0 | 2-3 |
| 3 | 8 of 8 | 6 of 8 | 0 | 3-4 |
| 5 | 8 of 8 | 8 of 8 | 0 | 4-6 |
| 7 | 8 of 8 | 7 of 8 | 0 | 5-7 |
| 11 | 8 of 8 | 8 of 8 | 0 | 7-9 |

Every cause missed in a sweep that found all four was the nondeterministic test reported as
`UNKNOWN`; the one detection miss at 2 runs was that test giving the same result in every
run of every arm. No test was ever given a wrong cause.

- **detection** — of the four flaky tests, how many were reported at all
- **attribution** — of those, how many got the right cause
- **false positives** — of the six stable tests, how many were reported as flaky

All three or none. A tool reporting every test as `nondeterminism` scores 100% detection; one
reporting nothing scores zero false positives.

**The zero at one run is the point.** One run per arm still detects some of the four - two or three in these sweeps, depending on whether a single shuffle happens to reverse the order pair - because
an arm landing on a different failure rate than the baseline is evidence even from a single
run each. What it cannot do is *attribute*: a baseline that runs once cannot flip, so
nondeterminism can never be excluded, and every finding is `UNKNOWN`.

That refusal was added after the sweep caught the alternative. The earlier version reported
the nondeterministic test as `clock` at one run per arm — it passed in the single baseline run
and failed in the single clock run, satisfying every rule. Confident, and wrong.

**A few runs per arm is enough here.** That is a property of this fixture, whose flaky
tests fail 30–70% of the time. A test failing one run in twenty needs far more: the chance of
*seeing* a flip in `n` runs is `1 - (19/20)^n`, which is 23% at 5 runs and 43% at 11. The
run count is a bound on what can be seen, not a quality setting.

---

## 3. Five real repositories

Measured before outcomes were read from pytest's own reports (the round-2 audit fixes);
not re-run since. The new reader only adds observations - errors, and ids the old parser
dropped - so a re-run could only find more, not fewer.

**The revision matters, and three of these rows used not to carry one.** The table below
used to say "suite-auditor, 24 tests, none" and "blast-radius, 28 tests, none". Those
repositories now have 130 and 284 tests, so the rows were about revisions nobody had
recorded while reading as though they were about the current suites. Every investigation
records its target's git revision now, and the three that could be re-run were, on
2026-10-07, at 5 runs per arm:

| repo | revision | tests | flaky | broken | seconds |
|---|---|---:|---|---|---:|
| [blast-radius](https://github.com/hammasbuilds/blast-radius) | `1884140` | 284 | none | none | 2,399 |
| [assay-drift](https://github.com/hammasbuilds/assay-drift) | `9a1089b` | 160 | none | none | 131 |
| [suite-auditor](https://github.com/hammasbuilds/suite-auditor) | `805a4bb` | 130 | **1** | none | 827 |

**suite-auditor's one is the useful row.** It came back `ORDER`, 0 of 5 baseline runs
against 1 of 5 shuffled - and running that assertion 200 times alone shows it fails 1.5%
of the time from its own use of `random.randint`. It is nondeterminism; the order arm
happened to be the one that saw it. Both halves were fixed: the guard in suite-auditor
that should have recognised `random.randint`, and the rule here that let one discordant
observation name a cause. A re-run at `c113418` or later finds nothing.

The older rows, kept because they cannot be re-run here (these repositories are not
checked out on this machine) and marked as what they are - measurements of revisions that
were not recorded:

| repo | revision | tests | flaky | broken | seconds |
|---|---|---:|---|---|---:|
| [repo-surgeon](https://github.com/hammasbuilds/repo-surgeon) | not recorded | 47 | none | none | 46 |
| [pr-referee](https://github.com/hammasbuilds/pr-referee) | not recorded | 37 | none | none | 61 |
| [trace-to-patch](https://github.com/hammasbuilds/trace-to-patch) | not recorded | 30 | none | none | 12 |

These suites are not trivial to hold still — four of the five shell out to subprocesses,
install packages, or write to temporary directories, which is exactly where order and clock
dependence tend to hide. Running each one 20 times under shuffled order, seven hash seeds and
seven frozen dates turned up nothing.

**What that is worth, precisely:** the clock freeze did not break any of them, the order
shuffle did not break any of them, and no test in any of them is flaky at a rate 20 runs would
see. It is not a proof of determinism, and the report says "no flaky tests found across 5 runs
per arm" rather than "no flaky tests", because those are different claims.

---

## 3b. Two third-party suites, re-runnable

`sh scripts/reproduce_third_party.sh` - toolz `451af60`, sqlparse `60cdc64`, one plant per
cause, 7 runs per arm, 4 jobs, `--seed 0`. Run 2026-10-03, 40 minutes in total on a
shared 16-core Windows machine.

| suite | plant | reported | other tests flagged | seconds |
|---|---|---|---|---:|
| toolz | order | order | - | 308 |
| toolz | hash-seed | hash-seed | - | 205 |
| toolz | clock | clock | - | 185 |
| toolz | nondeterminism | nondeterminism | - | 299 |
| sqlparse | order | order | `..._case_within_cap_under_1s` | 431 |
| sqlparse | hash-seed | hash-seed | `..._paren_within_cap_under_1s`, `..._case_within_cap_under_1s` | 416 |
| sqlparse | clock | clock | `..._case_within_cap_under_1s` | 264 |
| sqlparse | nondeterminism | unknown | `..._case_within_cap_under_1s` | 290 |

Run again 2026-10-04 on the same machine while it was quiet: 5 minutes in total, 8 of 8
plants reported with the right cause, nothing else flagged.

| suite | plant | reported | other tests flagged | seconds |
|---|---|---|---|---:|
| toolz | order | order | - | 18 |
| toolz | hash-seed | hash-seed | - | 15 |
| toolz | clock | clock | - | 16 |
| toolz | nondeterminism | nondeterminism | - | 16 |
| sqlparse | order | order | - | 44 |
| sqlparse | hash-seed | hash-seed | - | 64 |
| sqlparse | clock | clock | - | 74 |
| sqlparse | nondeterminism | nondeterminism | - | 57 |

The sqlparse tests flagged alongside the plants on the busy day are in
`tests/test_dos_prevention.py` and assert that a pathological parse finishes in under one
second of wall time - genuinely load-dependent, and not flagged on the quiet day. The nondeterminism plant fails half its runs; when all seven baseline
runs happen to agree (1.6%) the other arms disagree and the verdict is `UNKNOWN`.

## 4. What the numbers do not say

- **100% on a ten-test fixture is a small claim.** It is one test per cause. What makes it
  worth anything is the decoys and the failures it started from — 75% detection and 67%
  attribution on the first run, which is how three of the four bugs below were found.
- **The fixture was wrong twice.** A benchmark is only as trustworthy as its answer key. One
  "stable" decoy asserted a float literal from one Python build and always failed; the
  hash-dependent test used an eight-element set and failed under nearly every seed. Both are
  now checked by actually running them, in `tests/test_fixture.py`.
- **The timezone, locale, parallel and isolation arms are not in this sweep.** They are
  opt-in and the fixture has no test aimed at them; their behaviour is covered by unit tests,
  not by a scored benchmark. timezone and locale skip themselves on Windows.
- **The real-repo runs are my own repositories.** They share an author, a style and zero
  runtime dependencies, so a clean sweep across them is weaker evidence than five unrelated
  projects would be.

## Bugs this benchmark caught

All four produced a confident, wrong answer, and none raised an error.

| what it reported | what was true |
|---|---|
| clock-dependent test → `nondeterminism` | the baseline could not hold the clock still, because running a suite takes time |
| hash-dependent test → nothing at all | 7/7 passing under one seed and 7/7 failing under another never "flips" within an arm |
| nondeterministic test → `clock`, at 1 run/arm | a baseline that cannot flip is not a control |
| two of seven clock runs → duplicates | instants pinned in UTC, boundaries evaluated in local time |
