# flake-detective next to the way flakiness is usually handled

Most projects do not diagnose a flaky test. They install
[`pytest-rerunfailures`](https://github.com/pytest-dev/pytest-rerunfailures) and retry it
until it passes. That is the real alternative, so it is the one to measure against.

The comparison is only possible because of the benchmark fixture: a suite written to be
flaky in eight specified ways, **with an answer key**, plus stable tests that look flaky so
false positives are measurable. Nobody knows the ground truth for somebody else's flaky
test - that is the reason this tool exists - so a suite with a key is the only place a
detection rate means anything.

Measured 2026-10-29 on the same generated fixture in the same run: `pytest` 9.2.0,
`pytest-rerunfailures` 16.7, `--reruns=6`, and the whole suite run **7 times** under the
re-runner so its detection is not decided by one roll. Every number is in
[`rerun-comparison.json`](rerun-comparison.json), written by `scripts/compare_reruns.py`;
`tests/test_rerun_comparison.py` checks this page against it.

## The result

Four of the eight known causes can be scored on this platform - the timezone and locale
arms report themselves unavailable on Windows and `parallel` needs pytest-xdist in the
target environment, so three known positives cannot be found here at all and are excluded
from both sides rather than counted as misses.

| | flake-detective | pytest-rerunfailures |
|---|---:|---:|
| detected, of the 4 both sides can be scored on | **4** | **3** noticed, **2** masked |
| **cause named correctly** | **4 of 4** | **names no cause** |
| false positives (of 8 stable-but-flaky-looking tests) | **0** | **0** |
| seconds | 21 | 4 |

The restriction matters and is easy to get wrong - I did, before checking. Both tools
touched four tests, and **not the same four**: the re-runner retried `needs-other-test`,
which this tool cannot score here, and it never retried the order-dependent test at all.
Comparing the two unrestricted counts would have published a tie that does not exist.

## What the re-runner saw, read two ways

Before running it I wrote down that a re-runner would find only the flakiness that
reproduces under identical conditions. That was half right, and the half that was wrong is
worth keeping: a test retried by `--reruns` was retried *because it failed*, and "failed"
is not the same signal as "flaky".

| | tests | which |
|---|---:|---|
| **noticed** - retried at all, so instability was seen | 4 | clock, hash-seed, needs-other-test, nondeterminism |
| **masked** - retried and the session ended green | **2** | clock, nondeterminism |
| **never retried** - of the four scoreable here | **1** | **order** |

The two it masked are the two that flip *within a session*. Two more it retried six times
each and they failed every time, so what a user sees is a **red build with a failing
test**, not a flake; in the sessions where they happen to pass, nothing is reported at all.

And the order-dependent test it never saw. `--reruns` retries a test in the order it
already ran, so a flake that depends on the order is invisible to it by construction - not
missed, unreachable.

So the honest statement is not "a re-runner finds less". It is that **a re-runner's signal
does not separate a flaky test from a broken one**: it retries both, silently green-lights
the ones that flip, leaves the ones that do not looking like ordinary failures, and cannot
reach the ones whose cause is something it never varies. Neither outcome says what the test
depends on.

## What the cause column buys

`test_first_of_a_set_is_stable` is the clearest case. Under the re-runner it fails in some
sessions and passes in others, and within any one session it fails all seven attempts. This
tool's baseline arm runs it 7 times under **identical** conditions, where it never fails,
and then once per varied `PYTHONHASHSEED`, where it does:

```
test_hash_dependent.py::test_first_of_a_set_is_stable
  cause     : hash-seed
  evidence  : stable under identical repetition; failed when PYTHONHASHSEED varied
  rates     : baseline 0.00  order 0.00  hashseed 0.43  clock 0.00
```

The rate per arm *is* the diagnosis: 0.00 under repetition and 0.43 when one named thing
changes. A re-runner has one configuration and no arms, so this sentence is not a thing it
could produce.

## What this does not show

- **A re-runner is not a diagnostic tool and does not claim to be.** It exists to keep CI
  green, and on that measure it worked: two red builds became green in five seconds. Every
  column it is missing here is a column it never advertised.
- **Speed is not comparable, and the gap is the point.** 21 seconds against 4 is 28 runs
  against 7; the arms are the cost of knowing why. A fair reading is cost-per-answer, not
  cost.
- **One fixture, sixteen tests, one platform.** The suite is written by this project, which
  is the only way to have an answer key and also the obvious objection to it: the fixture
  and the classifier were written by the same hand. Four of the eight causes are
  unscoreable here, so the headline rests on four known positives and eight known
  negatives.
- **`--reruns=6` and 7 suite runs are choices I made.** More reruns would mask more; fewer
  would mask less. The values are in the artefact.
- **Nothing here measures real repositories.** The third-party census in
  [RESULTS.md](RESULTS.md) is this tool run on real suites, where no answer key exists -
  which is why the detection rate lives here and the real-world evidence lives there.
