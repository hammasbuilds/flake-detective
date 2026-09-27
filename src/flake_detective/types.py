"""What a flaky test is, and what distinguishes one cause from another.

"Flaky" is usually used to mean "fails sometimes", and treated as one problem with one
answer: rerun it. That is a workaround, not a diagnosis, and it hides several different
faults with different fixes.

The classification here is **by what perturbation reveals it**, because that is the only
part that is observable. Run the same suite many times, changing exactly one thing each
time, and the thing that has to change before a test flips is the thing it depends on:

    ORDER          another test leaks state into it: it fails after that test runs
    NEEDS_TEST     it relies on state another test creates, and fails without it
    HASH_SEED      it depends on dict or set iteration order
    CLOCK          it depends on the wall clock
    NONDETERMINISM it flips with nothing changed at all

The last is the residue: everything that flips under identical conditions. Unseeded
randomness lives there, and so does genuine concurrency, and the tool does not pretend to
tell those apart - it says so instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Cause(StrEnum):
    ORDER = "order"
    NEEDS_TEST = "needs-other-test"
    HASH_SEED = "hash-seed"
    CLOCK = "clock"
    TIMEZONE = "timezone"
    LOCALE = "locale"
    PARALLEL = "parallel"
    NONDETERMINISM = "nondeterminism"
    UNKNOWN = "unknown"


# An order dependence has a direction, and the two directions have opposite fixes. When the
# evidence does not establish which it is, the advice says so instead of guessing.
ORDER_UNDIRECTED_FIX = (
    "its result depends on which tests run before it: another test either leaks state "
    "into it or creates state it relies on. --localise names that test and says which "
    "(so does --arms isolation)"
)

FIX = {
    Cause.ORDER: (
        "another test leaves state behind that breaks this one; reset that state in a "
        "fixture, or stop the other test leaking it"
    ),
    Cause.NEEDS_TEST: (
        "it only passes when another test has run first and created state it needs. "
        "Move that setup into a fixture, or the test breaks the day somebody runs it "
        "on its own"
    ),
    Cause.HASH_SEED: "something iterates a dict or set and depends on the order; sort it",
    Cause.CLOCK: "it reads the wall clock; freeze or inject the time",
    Cause.TIMEZONE: (
        "it depends on the machine's timezone; use an explicit tz instead of a naive datetime"
    ),
    Cause.LOCALE: (
        "it depends on the locale; case-folding, sorting and number formatting all "
        "change with it - pass an explicit locale or compare case-sensitively"
    ),
    Cause.PARALLEL: (
        "it fails when tests share a process pool - a file, port, database or temp "
        "path is fixed rather than per-worker"
    ),
    Cause.NONDETERMINISM: "it flips with nothing changed - unseeded randomness, or a race",
    Cause.UNKNOWN: "it flipped, but no single perturbation explains it",
}


@dataclass
class Arm:
    """One way of running the suite, repeated."""

    name: str
    description: str
    runs: int = 0
    failures: dict[str, int] = field(default_factory=dict)
    """test id -> how many of this arm's runs it failed (a setup or teardown error counts)."""

    attempted: int = 0
    """Runs asked for. `runs` counts only the ones that could be scored, and the gap
    between the two is reported: an arm where pytest fell over every time has not
    looked at anything, and must not read as an arm that found nothing."""

    error: str = ""
    """Why a run could not be scored, from the first one that could not - pytest's
    exit code and the tail of what it printed."""

    observed: dict[str, int] = field(default_factory=dict)
    """test id -> how many of this arm's runs it was actually seen to pass or fail in.

    A run can finish without running every test - a skip, a module that failed to import
    under --continue-on-collection-errors, a run stopped early - and a test that did not
    run is not a pass. Rates are over the runs that observed the test, never over all runs.

    Only consulted when `tracked` is set, as it is for every arm built from real runs.
    Hand-built arms in the unit tests leave it unset, and every scored run is then taken
    to have observed every test.
    """

    tracked: bool = False
    """`observed` holds real per-test observation counts. Kept separate from "is
    `observed` empty", because an arm in which every test was skipped has an empty
    `observed` too - and must report every test as unobserved, not as passing."""

    errors: dict[str, int] = field(default_factory=dict)
    """test id -> runs in which it errored in setup or teardown (included in `failures`)."""

    interrupted: bool = False
    """Ctrl-C arrived while this arm was running; only the runs that finished count."""

    @property
    def unscored(self) -> int:
        return max(self.attempted - self.runs, 0)

    def seen(self, test_id: str) -> int:
        """Runs in which this test was observed to pass or fail."""
        if not self.tracked:
            return self.runs
        return self.observed.get(test_id, 0)

    def rate(self, test_id: str) -> float:
        n = self.seen(test_id)
        return self.failures.get(test_id, 0) / n if n else 0.0

    def is_stable(self, test_id: str) -> bool:
        """Always passed or always failed - either way, not flaky under this arm."""
        n = self.failures.get(test_id, 0)
        return n == 0 or n == self.seen(test_id)


@dataclass
class Flake:
    test_id: str
    cause: Cause
    evidence: str
    """What actually differed. Never a guess - the arm, and the counts."""

    rates: dict[str, float] = field(default_factory=dict)

    culprits: list[str] = field(default_factory=list)
    """For an order dependence: the other test(s) involved, found by `--localise`.

    For ORDER, the test(s) that leave the state behind; for NEEDS_TEST, the test(s)
    that create the state it relies on. Empty unless `--localise` found them.
    """

    directed: bool = True
    """False for an ORDER finding whose direction was not established. The order arm
    alone cannot tell "broken by another test" from "needs another test": both fail in
    some shuffles and pass in others."""

    localisation: dict | None = None
    """What `--localise` did for this test: outcome, probes, and a one-line summary."""

    errored: bool = False
    """Some of its failures were errors in fixture setup or teardown, not in the test."""

    @property
    def fix(self) -> str:
        if self.cause is Cause.ORDER and not self.directed:
            return ORDER_UNDIRECTED_FIX
        return FIX[self.cause]

    def as_row(self) -> dict:
        row: dict = {
            "test": self.test_id,
            "cause": self.cause.value,
            "evidence": self.evidence,
            "rates": {k: round(v, 3) for k, v in self.rates.items()},
            "suggested_fix": self.fix,
        }
        if self.cause is Cause.ORDER:
            row["direction"] = "broken by another test" if self.directed else "undetermined"
        if self.errored:
            row["errored_in_fixture"] = True
        if self.culprits:
            row["needs" if self.cause is Cause.NEEDS_TEST else "caused_by"] = self.culprits
        if self.localisation:
            row["localisation"] = self.localisation
        return row


@dataclass
class Investigation:
    arms: list[Arm] = field(default_factory=list)
    flakes: list[Flake] = field(default_factory=list)
    total_tests: int = 0
    always_failed: list[str] = field(default_factory=list)
    """Tests that failed in every run of every arm.

    Not flaky - just broken, and reported separately. A tool that folded them into the
    flake count would inflate it with tests whose behaviour is perfectly consistent.
    """

    seconds: float = 0.0

    problem: str = ""
    """Why nothing could be examined, when that is what happened: pytest missing from
    the interpreter, a collection error, no tests, or a baseline where no run could be
    scored. Set means the investigation failed - it is never a clean result."""

    seed: int | None = None
    """The order arm's shuffle seed; `--seed` with this value repeats the same shuffles."""

    unobserved: list[str] = field(default_factory=list)
    """Tests collected but never seen to pass or fail in any run: skipped, or never ran."""

    interrupted: bool = False
    """Ctrl-C stopped the investigation; what is here is from the runs that finished."""

    @property
    def incomplete(self) -> list[str]:
        """Arms that were asked for and scored no runs at all."""
        return [a.name for a in self.arms if a.attempted and not a.runs]

    @property
    def ok(self) -> bool:
        """True only if tests were examined and every arm scored at least one run."""
        return (
            not self.problem
            and not self.interrupted
            and self.total_tests > 0
            and not self.incomplete
        )

    def by_cause(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for f in self.flakes:
            out[f.cause.value] = out.get(f.cause.value, 0) + 1
        return out
