"""Which earlier test leaves the state behind.

The order arm establishes that a test passes in some orders and fails in others.
That is a true statement and an unhelpful one: it names the victim, and the victim
is the one test in the pair that is innocent. Whoever picks up the report still has
to find the culprit by hand, in a suite of several hundred, which is the work the
report was supposed to save.

So: bisect. The victim fails after some set of earlier tests and passes without
them, so halve the set and run again. Each probe is one pytest invocation with an
explicit list of test ids, and the search needs about log2(n) of them - eight runs
to find one culprit among two hundred tests.

Three honest outcomes, and they are different claims:

  a single culprit        the victim passes alone and after half the suite, and
                          fails after this one test
  a combination           neither half reproduces it on its own, so no single
                          earlier test is sufficient and the trigger needs two or
                          more together. Reported as the smallest set that does
                          reproduce, not as a guess at which member matters.
  not reproducible        the victim did not fail even with every earlier test in
                          front of it. Order was not the cause, or the effect
                          needs something this search does not vary.

The third is why this returns a result object rather than a test id. A localiser
that always names something is a localiser that sometimes names the wrong thing,
and a wrong culprit sends somebody to read a file that is fine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from flake_detective.run import run_once


@dataclass
class Localisation:
    """What the bisection established about one order-dependent test."""

    victim: str
    culprits: list[str] = field(default_factory=list)
    """The smallest set of earlier tests that reproduces the failure.

    One entry is a single culprit. Several means no single test was enough and
    they are required together. Empty means the search did not reproduce it.
    """

    probes: int = 0
    """pytest invocations spent. Reported because it is the cost of this feature."""

    outcome: str = "not reproducible"
    """One of: single, combination, not reproducible, victim fails alone."""

    @property
    def found(self) -> bool:
        return bool(self.culprits)

    def describe(self) -> str:
        if self.outcome == "victim fails alone":
            return f"{self.victim} fails on its own - it is broken, not order-dependent"
        if not self.culprits:
            return f"{self.victim}: no earlier test reproduced the failure in {self.probes} runs"
        if len(self.culprits) == 1:
            return f"{self.victim} fails after {self.culprits[0]}"
        return (
            f"{self.victim} fails after these {len(self.culprits)} together "
            f"(no single one was enough): " + ", ".join(self.culprits)
        )


def _fails(repo: Path, prefix: list[str], victim: str, timeout: float, python: str) -> bool | None:
    """Run `prefix` then `victim`. True if the victim failed, None if unscoreable."""
    failed = run_once(repo, order=[*prefix, victim], timeout=timeout, python=python)
    if failed is None:
        return None
    return victim in failed


def localise(
    repo: Path,
    victim: str,
    tests: list[str],
    timeout: float = 900.0,
    python: str = "",
    max_probes: int = 24,
) -> Localisation:
    """Find the smallest set of earlier tests that makes `victim` fail."""
    out = Localisation(victim=victim)

    # Alone first. A test that fails with nothing in front of it is broken, and
    # bisecting a broken test would "find" whichever half was tried first.
    alone = _fails(repo, [], victim, timeout, python)
    out.probes += 1
    if alone is None:
        return out
    if alone:
        out.outcome = "victim fails alone"
        return out

    candidates = [t for t in tests if t != victim]
    if not candidates:
        return out

    # Then with everything. If the whole suite in front of it is not enough, there
    # is nothing here to narrow, and saying so beats halving noise for eight runs.
    whole = _fails(repo, candidates, victim, timeout, python)
    out.probes += 1
    if not whole:
        return out

    # Binary search. The invariant: `candidates` always reproduces the failure.
    while len(candidates) > 1 and out.probes < max_probes:
        mid = len(candidates) // 2
        first, second = candidates[:mid], candidates[mid:]

        first_fails = _fails(repo, first, victim, timeout, python)
        out.probes += 1
        if first_fails:
            candidates = first
            continue
        if first_fails is None:
            break

        second_fails = _fails(repo, second, victim, timeout, python)
        out.probes += 1
        if second_fails:
            candidates = second
            continue
        if second_fails is None:
            break

        # Neither half alone reproduces it, so the trigger spans both and this set
        # is already minimal for a halving search. Reporting one of them would be a
        # guess, and the whole point of this module is not to guess.
        out.culprits = candidates
        out.outcome = "combination"
        return out

    out.culprits = candidates
    out.outcome = "single" if len(candidates) == 1 else "combination"
    return out
