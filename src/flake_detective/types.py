"""What a flaky test is, and what distinguishes one cause from another.

"Flaky" is usually used to mean "fails sometimes", and treated as one problem with one
answer: rerun it. That is a workaround, not a diagnosis, and it hides four different faults
with four different fixes.

The classification here is **by what perturbation reveals it**, because that is the only
part that is observable. Run the same suite many times, changing exactly one thing each
time, and the thing that has to change before a test flips is the thing it depends on:

    ORDER          it passes alone and fails after another test - shared state
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
    HASH_SEED = "hash-seed"
    CLOCK = "clock"
    TIMEZONE = "timezone"
    LOCALE = "locale"
    PARALLEL = "parallel"
    ISOLATION = "isolation"
    NONDETERMINISM = "nondeterminism"
    UNKNOWN = "unknown"


FIX = {
    Cause.ORDER: "a previous test leaves state behind; isolate it or reset in a fixture",
    Cause.HASH_SEED: "something iterates a dict or set and depends on the order; sort it",
    Cause.CLOCK: "it reads the wall clock; freeze or inject the time",
    Cause.TIMEZONE: (
        "it depends on the machine's timezone; use an explicit tz instead of a naive datetime"
    ),
    Cause.LOCALE: (
        "it depends on the locale; case-folding, sorting and number formatting all "
        "change with it - pass an explicit locale or compare case-sensitively"
    ),
    Cause.ISOLATION: (
        "it only passes as part of the suite: another test creates state it needs. "
        "Move that setup into a fixture, or the test breaks the day somebody runs it "
        "on its own"
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
    """test id -> how many of this arm's runs it failed."""

    attempted: int = 0
    """Runs asked for. `runs` counts only the ones that could be scored, and the gap
    between the two is reported: an arm where pytest fell over every time has not
    looked at anything, and must not read as an arm that found nothing."""

    error: str = ""
    """Why a run could not be scored, from the first one that could not - pytest's
    exit code and the tail of what it printed."""

    @property
    def unscored(self) -> int:
        return max(self.attempted - self.runs, 0)

    def rate(self, test_id: str) -> float:
        return self.failures.get(test_id, 0) / self.runs if self.runs else 0.0

    def is_stable(self, test_id: str) -> bool:
        """Always passed or always failed - either way, not flaky under this arm."""
        n = self.failures.get(test_id, 0)
        return n == 0 or n == self.runs


@dataclass
class Flake:
    test_id: str
    cause: Cause
    evidence: str
    """What actually differed. Never a guess - the arm, and the counts."""

    rates: dict[str, float] = field(default_factory=dict)

    culprits: list[str] = field(default_factory=list)
    """For an order dependence: the earlier test(s) that leave the state behind.

    Empty unless `--localise` ran and reproduced it. The cause names the victim,
    which is the innocent half of the pair; this names the other half.
    """

    @property
    def fix(self) -> str:
        return FIX[self.cause]

    def as_row(self) -> dict:
        row = {
            "test": self.test_id,
            "cause": self.cause.value,
            "evidence": self.evidence,
            "rates": {k: round(v, 3) for k, v in self.rates.items()},
            "suggested_fix": self.fix,
        }
        if self.culprits:
            row["culprits"] = self.culprits
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

    @property
    def incomplete(self) -> list[str]:
        """Arms that were asked for and scored no runs at all."""
        return [a.name for a in self.arms if a.attempted and not a.runs]

    @property
    def ok(self) -> bool:
        """True only if tests were examined and every arm scored at least one run."""
        return not self.problem and self.total_tests > 0 and not self.incomplete

    def by_cause(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for f in self.flakes:
            out[f.cause.value] = out.get(f.cause.value, 0) + 1
        return out
