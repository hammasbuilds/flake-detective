"""What is "no flip in seven runs" worth? The power of a rerun budget.

Every arm of an investigation is a hypothesis test run by hand: repeat the suite `n` times
with one thing changed, and if the test flipped - passed in one run and failed in another -
the arm implicates it. Nothing in the tool says what the *absence* of a flip means, and the
absence is the common case. A report that lists four flaky tests is also, silently, a claim
that every other test in the suite is stable, and that claim has never carried a bound.

It needs one, because the bound is not reassuring. A test that fails with probability `p`
on each run flips within `n` runs with probability

    1 - (1-p)^n - p^n

- it has to not-always-pass and not-always-fail. At the default seven runs that is 98% for
a coin-flip test, 52% for one failing one run in ten, and 13% for one failing one run in
fifty. So "seven runs, no flip" rules out the obvious and almost nothing else, and the
honest form of a clean report is "no flake above about such-and-such a rate", with the rate
printed.

This matters for a measurement already in this repository. `docs/sweep.json` sweeps the
budget against the planted fixture suite and finds detection reaching 1.0 at **two** runs
per arm, which reads as "two is enough". The fixture's flakes fail about half the time, and
at p = 0.5 two runs really do have a 50% chance each and three runs 75%. The curve is a
fact about the fixture's flake rates rather than about the budget, and generalising it to a
suite whose flakes fire once in fifty runs would be wrong by two orders of magnitude in the
budget. `research/power.py` prints both curves side by side.

## Spending the budget where the evidence is thin

A fixed budget spends the same runs on a test that flipped in run two as on one that has
passed sixty times, which is the wrong way round. Wald's sequential probability ratio test
(*Sequential Analysis*, 1947) is the standard answer: accumulate the log-likelihood ratio
between "stable enough" and "flaky enough" after each run and stop as soon as either
hypothesis is decisively favoured, with the type-I and type-II error rates chosen in
advance rather than discovered afterwards.

For a per-run failure probability, testing `p <= p0` against `p >= p1`, each failure adds
`log(p1/p0)` and each pass adds `log((1-p1)/(1-p0))`. Crossing `log((1-beta)/alpha)`
accepts "flaky"; crossing `log(beta/(1-alpha))` accepts "stable"; between them the answer
is "keep running".

Two things this does not do. It does not make a cheap budget detect a rare flake - the
expected number of runs to separate p = 0.02 from p = 0.001 is large however it is spent,
and SPRT reports that honestly instead of stopping early and calling it stable. And it is a
test about one test's failure rate, so it bounds the *flip* criterion; an arm that
implicates a test by a difference in failure rate between arms rather than by a flip is a
two-sample question and is not covered here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# The default budget, so the power statements and the CLI cannot drift apart.
DEFAULT_RUNS = 7

# The rates the reports quote. Chosen to span what the flaky-test literature reports in
# practice - from a coin flip down to the once-in-fifty failures that survive in suites
# for years precisely because a handful of reruns never sees them.
REFERENCE_RATES = (0.5, 0.25, 0.1, 0.05, 0.02, 0.01)

# What counts as confident, for "the smallest rate this budget could have found".
DEFAULT_POWER = 0.95


def flip_probability(rate: float, runs: int) -> float:
    """Chance of seeing both a pass and a failure in `runs` runs, at per-run `rate`.

    This is the power of one arm against a flake of that rate, under the flip criterion.
    Zero for a single run, which is not a quibble: with `--runs 1` an arm cannot flip
    anything by construction, and a budget sweep that starts at one run is measuring
    something other than detection there.
    """
    if not 0.0 <= rate <= 1.0:
        raise ValueError(f"a failure rate must be in [0, 1], got {rate}")
    if runs < 1:
        raise ValueError(f"a budget must be at least one run, got {runs}")
    if runs == 1:
        return 0.0
    return 1.0 - (1.0 - rate) ** runs - rate**runs


def observed_probability(rate: float, runs: int) -> float:
    """Chance of seeing at least one failure in `runs` runs, at per-run `rate`.

    A different question from `flip_probability`, and the distinction decides which
    number a report should print.

    * `observed_probability` asks whether the flake was noticed **at all**. Its complement
      is the case that makes a clean report wrong: the test passed every single run and
      looks like an ordinary passing test.
    * `flip_probability` asks whether the arm saw both outcomes, which is what lets a
      cause be *attributed*. It also counts an all-failures arm as no flip, and that is
      not a dangerous miss - a test failing in every run is reported as a failing test,
      not overlooked.

    The two differ only by `rate ** runs`, so they coincide for a rare flake and part
    company for a frequent one: at a coin-flip rate and seven runs, 0.8% of arms see no
    failure and another 0.8% see nothing but failures.
    """
    if not 0.0 <= rate <= 1.0:
        raise ValueError(f"a failure rate must be in [0, 1], got {rate}")
    if runs < 1:
        raise ValueError(f"a budget must be at least one run, got {runs}")
    return 1.0 - (1.0 - rate) ** runs


def runs_for_power(rate: float, power: float = DEFAULT_POWER) -> int | None:
    """Smallest budget whose flip probability at `rate` reaches `power`.

    `None` when no finite budget does, which happens for `rate` of exactly 0 or 1: a test
    that always passes or always fails never flips, and no number of runs changes that.
    """
    if not 0.0 < power < 1.0:
        raise ValueError(f"a power must be in (0, 1), got {power}")
    if rate in (0.0, 1.0):
        return None
    runs = 2
    # The flip probability is monotone in `runs` and tends to 1, so this terminates; the
    # ceiling is a guard against a caller asking for a power of 0.999999 at rate 1e-9 and
    # getting a loop instead of an answer.
    while runs <= 100_000:
        if flip_probability(rate, runs) >= power:
            return runs
        runs += 1
    return None


def detectable_rate(runs: int, power: float = DEFAULT_POWER) -> float | None:
    """The rate nearest to zero that `runs` runs would find with probability `power`.

    This is the number a clean report should quote: not "no flakes" but "no flake failing
    more often than this, with this confidence". `None` when the budget cannot reach the
    requested power at any rate - which is every budget below the one that reaches it at
    0.5, the easiest rate there is.
    """
    if flip_probability(0.5, runs) < power:
        return None
    # Bisect on [tiny, 0.5]. Monotone in the rate over that interval: the closer to a coin
    # flip, the easier to catch.
    lo, hi = 1e-9, 0.5
    if flip_probability(lo, runs) >= power:
        return lo
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if flip_probability(mid, runs) >= power:
            hi = mid
        else:
            lo = mid
    return hi


@dataclass(frozen=True)
class Sprt:
    """A sequential test of `p <= p0` against `p >= p1` on a test's failure rate."""

    p0: float
    p1: float
    alpha: float = 0.05
    beta: float = 0.05

    def __post_init__(self) -> None:
        if not 0.0 < self.p0 < self.p1 < 1.0:
            raise ValueError(f"need 0 < p0 < p1 < 1, got p0={self.p0}, p1={self.p1}")
        if not 0.0 < self.alpha < 1.0 or not 0.0 < self.beta < 1.0:
            raise ValueError("alpha and beta must be in (0, 1)")
        if self.alpha + self.beta >= 1.0:
            # Wald's boundaries cross over and the test accepts both hypotheses at once.
            raise ValueError("alpha + beta must be below 1 or the boundaries invert")

    @property
    def accept_flaky(self) -> float:
        """Upper boundary: cross it and `p >= p1` is accepted."""
        return math.log((1.0 - self.beta) / self.alpha)

    @property
    def accept_stable(self) -> float:
        """Lower boundary: cross it and `p <= p0` is accepted."""
        return math.log(self.beta / (1.0 - self.alpha))

    def log_ratio(self, failures: int, runs: int) -> float:
        """The accumulated log-likelihood ratio after `failures` of `runs`."""
        if failures < 0 or runs < 0 or failures > runs:
            raise ValueError(f"{failures} failures of {runs} runs is not possible")
        passes = runs - failures
        return failures * math.log(self.p1 / self.p0) + passes * math.log(
            (1.0 - self.p1) / (1.0 - self.p0)
        )

    def decide(self, failures: int, runs: int) -> str:
        """`"flaky"`, `"stable"`, or `"undecided"` - keep running.

        `"undecided"` is a real answer and the one a fixed budget cannot give. A report
        that printed "stable" for a test the evidence had not reached would be making the
        claim this module exists to bound.
        """
        ratio = self.log_ratio(failures, runs)
        if ratio >= self.accept_flaky:
            return "flaky"
        if ratio <= self.accept_stable:
            return "stable"
        return "undecided"

    def runs_to_call_stable(self) -> int:
        """Consecutive passes needed before `p <= p0` is accepted.

        The cheapest path to a verdict, and the one that answers "how many runs does a
        clean test need?". Every pass moves the ratio by a fixed negative amount, so this
        is a division rather than a search.
        """
        step = math.log((1.0 - self.p1) / (1.0 - self.p0))
        return math.ceil(self.accept_stable / step)

    def runs_to_call_flaky(self) -> int:
        """Consecutive failures needed before `p >= p1` is accepted."""
        step = math.log(self.p1 / self.p0)
        return math.ceil(self.accept_flaky / step)

    def expected_runs(self, rate: float) -> float | None:
        """Expected runs before a verdict at true rate `rate`, or `None` where undefined.

        Wald's approximation, which divides by the per-run drift of the log-likelihood
        ratio. The drift passes through zero somewhere between `p0` and `p1`, so inside
        that interval the expression is 0/0 and the approximation says nothing. It is
        returned as `None` there rather than as a number: an earlier version let the
        division through and reported **1.0 run** at a 0.5% rate, sitting between 164 at
        0.1% and 64 at 2%, because the numerator vanishes when the boundaries are
        symmetric. A non-monotone expected cost with a minimum of one run in the middle of
        the indifference region is not a near-miss, it is the opposite of the truth, and
        nothing but reading the row would have caught it. `worst_case_runs` covers that
        interval with the expression that is valid there.

        At a rate of exactly 0 or 1 the run count is not random at all - every run goes
        the same way - so the exact value is returned rather than the approximation,
        which runs about 10% low for that case.
        """
        if not 0.0 <= rate <= 1.0:
            raise ValueError(f"a failure rate must be in [0, 1], got {rate}")
        if rate == 0.0:
            return float(self.runs_to_call_stable())
        if rate == 1.0:
            return float(self.runs_to_call_flaky())
        if self.p0 < rate < self.p1:
            return None
        drift = rate * math.log(self.p1 / self.p0) + (1.0 - rate) * math.log(
            (1.0 - self.p1) / (1.0 - self.p0)
        )
        if abs(drift) < 1e-12:
            return None
        reaches_flaky = 1.0 - self.beta if rate >= self.p1 else self.alpha
        expected = (
            reaches_flaky * self.accept_flaky + (1.0 - reaches_flaky) * self.accept_stable
        ) / drift
        return max(expected, 1.0)

    @property
    def indifference_rate(self) -> float:
        """The failure rate at which this test has no preferred direction."""
        up = math.log(self.p1 / self.p0)
        down = math.log((1.0 - self.p1) / (1.0 - self.p0))
        return -down / (up - down)

    def worst_case_runs(self) -> float:
        """Expected runs at the rate where the drift is zero - the test's worst case.

        Inside the indifference region the ratio wanders with no trend, and the standard
        result for a driftless walk between two absorbing barriers is `-A*B / E[z^2]`,
        with `A` and `B` the boundaries and `z` the per-run increment. This is the number
        that says what the test really costs, and it is larger than anything
        `expected_runs` reports on either side of it.
        """
        up = math.log(self.p1 / self.p0)
        down = math.log((1.0 - self.p1) / (1.0 - self.p0))
        rate = self.indifference_rate
        second_moment = rate * up**2 + (1.0 - rate) * down**2
        return -self.accept_flaky * self.accept_stable / second_moment


def power_table(
    runs: int = DEFAULT_RUNS,
    rates: tuple[float, ...] = REFERENCE_RATES,
    power: float = DEFAULT_POWER,
) -> list[dict]:
    """One row per reference rate: this budget's power, and the budget that would do."""
    return [
        {
            "rate": rate,
            # Both criteria, because they answer different questions and the gap between
            # them is the whole reason the report prints two columns. See
            # `observed_probability`.
            "flip_power_at_this_budget": round(flip_probability(rate, runs), 4),
            "observed_power_at_this_budget": round(observed_probability(rate, runs), 4),
            "never_failed_once": round((1.0 - rate) ** runs, 4),
            "runs_for_target_flip_power": runs_for_power(rate, power),
        }
        for rate in rates
    ]


def caveat(runs: int, power: float = DEFAULT_POWER) -> str:
    """One line for a report that found nothing, with the bound in it."""
    rate = detectable_rate(runs, power)
    if rate is None:
        return (
            f"{runs} runs per arm cannot reach {power:.0%} confidence against any flake "
            "rate - not even a test that fails half the time"
        )
    # Two lines, because the report that prints this is 74 columns wide and a single
    # sentence of this length wrapped raggedly in the middle of the number.
    return (
        f"{runs} runs per arm reaches {power:.0%} confidence only against a flake\n"
        f"  failing more than {rate:.1%} of the time; a rarer one probably did not flip"
    )
