"""Which other test an order-dependent test depends on - and in which direction.

The order arm establishes that a test passes in some orders and fails in others. That is a
true statement and an unhelpful one. Whoever picks up the report still has to find the other
test by hand, in a suite of several hundred, which is the work the report was supposed to
save. And "depends on order" has two opposite meanings:

    broken by another test   it passes on its own and fails after test X, which leaves
                             state behind. The fix is in X, or in a fixture that resets.
    needs another test       it fails on its own and passes after test X, which creates
                             state it relies on. The fix is in this test's setup.

So: run it alone first. That one run says which direction to search in. Then bisect: put
the other tests in front of it, halve the set, and keep the half that still reproduces what
the whole set did - the failure, for a test that is broken by another; the pass, for one
that needs another. Each probe is one pytest invocation with an explicit list of test ids,
and the search needs about log2(n) of them - eight runs among two hundred tests.

Honest outcomes, and they are different claims:

  single          one test, on its own in front, reproduces it
  combination     no half reproduces it, so no single test is sufficient; reported as the
                  smallest set that does, not as a guess at which member matters
  not reproducible  even every other test in front did not change the result. Order was
                  not the cause here, or the effect needs something this search does not
                  vary (a test that must run *after* it, a different process)

A localiser that always names something is a localiser that sometimes names the wrong thing,
and a wrong answer sends somebody to read a file that is fine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from flake_detective.run import run_observed


@dataclass
class Localisation:
    """What the bisection established about one order-dependent test."""

    victim: str
    culprits: list[str] = field(default_factory=list)
    """The smallest set of other tests that reproduces the effect when run before it.

    One entry is a single test. Several means no single test was enough and they are
    required together. Empty means the search did not reproduce it.
    """

    probes: int = 0
    """pytest invocations spent. Reported because it is the cost of this feature."""

    outcome: str = "not reproducible"
    """One of: single, combination, not reproducible."""

    direction: str = ""
    """"broken" (passes alone, fails after the culprits), "needs" (fails alone, passes
    after them), or "" if the first run could not be scored."""

    @property
    def found(self) -> bool:
        return bool(self.culprits)

    def describe(self) -> str:
        n = f"{self.probes} run{'s' if self.probes != 1 else ''}"
        if not self.direction:
            return f"{self.victim}: could not be run on its own ({n})"
        if not self.culprits:
            if self.direction == "needs":
                return (
                    f"{self.victim} fails on its own, and still fails with every other "
                    f"test run before it - no enabling test found ({n})"
                )
            return (
                f"{self.victim} passes on its own, and still passes with every other "
                f"test run before it - no culprit found ({n})"
            )
        verb = "passes only after" if self.direction == "needs" else "fails after"
        alone = "fails on its own" if self.direction == "needs" else "passes on its own"
        if len(self.culprits) == 1:
            return f"{self.victim} {alone} and {verb} {self.culprits[0]} ({n})"
        return (
            f"{self.victim} {alone} and {verb} these {len(self.culprits)} together "
            f"(no single one was enough): " + ", ".join(self.culprits) + f" ({n})"
        )

    def as_dict(self) -> dict:
        return {
            "outcome": self.outcome,
            "direction": {"broken": "broken by another test", "needs": "needs another test"}.get(
                self.direction, "unknown"
            ),
            "tests": self.culprits,
            "probes": self.probes,
            "summary": self.describe(),
        }


def _fails(
    repo: Path,
    prefix: list[str],
    victim: str,
    timeout: float,
    python: str,
    epoch: float | None,
    target: str,
    rootdir: str,
) -> bool | None:
    """Run `prefix` then `victim`. True if the victim failed, None if it was not observed."""
    obs, _ = run_observed(
        repo,
        target,
        order=[*prefix, victim],
        hashseed=0,
        epoch=epoch,
        timeout=timeout,
        python=python,
        rootdir=rootdir,
    )
    if obs is None or victim not in obs.seen:
        return None
    return victim in obs.failed


def localise(
    repo: Path,
    victim: str,
    tests: list[str],
    timeout: float = 900.0,
    python: str = "",
    max_probes: int = 24,
    epoch: float | None = None,
    target: str = "",
    rootdir: str = "",
) -> Localisation:
    """Find the smallest set of other tests whose running first changes `victim`'s result.

    `epoch` pins the clock as the other arms do, so the bisection runs under the same
    conditions the order dependence was observed in. `rootdir` is pytest's rootdir from
    collection, which the node ids are relative to.
    """
    out = Localisation(victim=victim)

    def probe(prefix: list[str]) -> bool | None:
        out.probes += 1
        return _fails(repo, prefix, victim, timeout, python, epoch, target, rootdir)

    # Alone first: it fixes the direction of the search.
    alone = probe([])
    if alone is None:
        return out
    out.direction = "needs" if alone else "broken"
    # What the prefix must reproduce: a failure if it passes alone, a pass if it fails.
    want = not alone

    candidates = [t for t in tests if t != victim]
    if not candidates:
        return out

    # Then with everything. If the whole suite in front of it does not change the
    # result, there is nothing here to narrow, and saying so beats halving noise.
    if probe(candidates) is not want:
        return out

    # Binary search. The invariant: `candidates` in front reproduces `want`.
    while len(candidates) > 1 and out.probes < max_probes:
        mid = len(candidates) // 2
        first, second = candidates[:mid], candidates[mid:]

        r = probe(first)
        if r is want:
            candidates = first
            continue
        if r is None:
            break

        r = probe(second)
        if r is want:
            candidates = second
            continue
        if r is None:
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
