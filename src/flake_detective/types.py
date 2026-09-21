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
    NONDETERMINISM = "nondeterminism"
    UNKNOWN = "unknown"


FIX = {
    Cause.ORDER: "a previous test leaves state behind; isolate it or reset in a fixture",
    Cause.HASH_SEED: "something iterates a dict or set and depends on the order; sort it",
    Cause.CLOCK: "it reads the wall clock; freeze or inject the time",
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

    @property
    def fix(self) -> str:
        return FIX[self.cause]

    def as_row(self) -> dict:
        return {
            "test": self.test_id,
            "cause": self.cause.value,
            "evidence": self.evidence,
            "rates": {k: round(v, 3) for k, v in self.rates.items()},
            "suggested_fix": self.fix,
        }


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

    def by_cause(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for f in self.flakes:
            out[f.cause.value] = out.get(f.cause.value, 0) + 1
        return out
