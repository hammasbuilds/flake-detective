"""Print what was found, with the evidence beside every claim.

A report saying "test_foo is order-dependent" is asking to be believed. This one shows the
failure rate in every arm next to the verdict, so the reader can see the shape the claim was
read from: zeros everywhere except one column is what an attribution looks like, and a row
that is 0.4 in all four columns is nondeterminism no matter what label sits beside it.

Ordering is by how actionable the finding is, not by how confident the tool sounds. An
`UNKNOWN` goes last because it hands the reader nothing to do.
"""

from __future__ import annotations

import json
from pathlib import Path

from flake_detective.types import Cause, Investigation

ORDER = [Cause.ORDER, Cause.HASH_SEED, Cause.CLOCK, Cause.NONDETERMINISM, Cause.UNKNOWN]


def _short(test_id: str, width: int = 52) -> str:
    return test_id if len(test_id) <= width else "..." + test_id[-(width - 3) :]


def text(inv: Investigation) -> str:
    out: list[str] = []
    w = "=" * 74
    out.append(w)
    out.append("FLAKE DETECTIVE")
    out.append(w)

    if inv.problem:
        out.append("")
        out.append("NOTHING WAS EXAMINED - this is an error, not a clean result.")
        out.append("")
        out.extend(inv.problem.splitlines())
        return "\n".join(out)

    if not inv.total_tests:
        out.append("")
        out.append("No tests were collected. This is not a clean bill of health -")
        out.append("nothing was examined. Check the path and that pytest can import the suite.")
        return "\n".join(out)

    out.append(f"{inv.total_tests} tests, {len(inv.arms)} arms, {inv.seconds:.0f}s")
    out.append("")

    names = [a.name for a in inv.arms]
    for a in inv.arms:
        runs = f"{a.runs} runs" if not a.unscored else f"{a.runs} of {a.attempted} runs"
        out.append(f"  {a.name:<10} {runs:<14} {a.description}")
    out.append("")

    if inv.incomplete:
        out.append("INCOMPLETE: these arms scored no runs at all, so they examined nothing:")
        for a in inv.arms:
            if a.name in inv.incomplete:
                out.append(f"  {a.name}:")
                out.extend("    " + ln for ln in (a.error or "no reason recorded").splitlines())
        out.append("")
    partial = [a for a in inv.arms if a.unscored and a.runs]
    if partial:
        out.append("Some runs could not be scored (pytest crashed, timed out or collected")
        out.append("nothing) and were left out of the rates above:")
        for a in partial:
            first = (a.error or "").splitlines()[:1]
            reason = f" - {first[0]}" if first else ""
            out.append(f"  {a.name}: {a.unscored} of {a.attempted}{reason}")
        out.append("")

    if not inv.flakes:
        per_arm = min((a.runs for a in inv.arms if a.runs), default=0)
        if inv.incomplete:
            out.append(f"No flaky tests found by the arms that ran ({per_arm}+ runs each).")
        else:
            out.append(f"No flaky tests found across {per_arm} runs per arm.")
        out.append("A suite can still be flaky at a rate this many runs cannot see -")
        out.append("raise --runs to lower that bound.")
        if per_arm:
            # The bound, computed, rather than left for the reader to work out.
            # A test that fails in half of all runs survives n of them undetected
            # with probability 2^-n; one that fails in a tenth, 0.9^n. Both are
            # worth stating, because "nothing found" means very different things
            # at five runs and at fifty.
            out.append("")
            out.append(f"  what {per_arm} runs per arm can miss:")
            for label, p in (("fails in half of all runs", 0.5), ("fails in one run in ten", 0.1)):
                out.append(
                    f"    a test that {label:<26} is missed {(1 - p) ** per_arm:>7.1%} of the time"
                )
    else:
        counts = inv.by_cause()
        out.append(f"{len(inv.flakes)} flaky tests:")
        for c in ORDER:
            if counts.get(c.value):
                out.append(f"  {counts[c.value]:>3}  {c.value}")
        out.append("")
        out.append("-" * 74)
        header = f"{'test':<54}" + "".join(f"{n[:8]:>9}" for n in names)
        out.append(header)
        out.append("-" * 74)

        rank = {c: i for i, c in enumerate(ORDER)}
        for f in sorted(inv.flakes, key=lambda f: (rank.get(f.cause, 9), f.test_id)):
            row = f"{_short(f.test_id):<54}"
            row += "".join(f"{f.rates.get(n, 0.0):>9.1f}" for n in names)
            out.append(row)
            out.append(f"    {f.cause.value.upper()}: {f.evidence}")
            if f.culprits:
                # The cause names the victim, which is the innocent half of the
                # pair. This is the half worth opening.
                if len(f.culprits) == 1:
                    out.append(f"    caused by: {f.culprits[0]}")
                else:
                    out.append(
                        f"    caused by these {len(f.culprits)} together "
                        f"(no single one was enough):"
                    )
                    for c in f.culprits:
                        out.append(f"      {c}")
            out.append(f"    fix: {f.fix}")
            out.append("")

    if inv.always_failed:
        out.append("-" * 74)
        out.append(f"{len(inv.always_failed)} tests failed in every run - broken, not flaky:")
        for t in inv.always_failed[:10]:
            out.append(f"  {_short(t, 68)}")
        if len(inv.always_failed) > 10:
            out.append(f"  ... and {len(inv.always_failed) - 10} more")

    return "\n".join(out)


def as_json(inv: Investigation) -> dict:
    return {
        "total_tests": inv.total_tests,
        "seconds": round(inv.seconds, 1),
        "arms": [
            {
                "name": a.name,
                "description": a.description,
                "runs": a.runs,
                "attempted": a.attempted,
                **({"error": a.error} if a.error else {}),
            }
            for a in inv.arms
        ],
        "by_cause": inv.by_cause(),
        "flakes": [f.as_row() for f in inv.flakes],
        "always_failed": inv.always_failed,
        "ok": inv.ok,
        "problem": inv.problem or None,
        "incomplete_arms": inv.incomplete,
    }


def write_json(inv: Investigation, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(as_json(inv), indent=2), encoding="utf-8")
