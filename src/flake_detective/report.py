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

    if not inv.total_tests:
        out.append("")
        out.append("No tests were collected. This is not a clean bill of health -")
        out.append("nothing was examined. Check the path and that pytest can import the suite.")
        return "\n".join(out)

    out.append(f"{inv.total_tests} tests, {len(inv.arms)} arms, {inv.seconds:.0f}s")
    out.append("")

    names = [a.name for a in inv.arms]
    for a in inv.arms:
        out.append(f"  {a.name:<10} {a.runs} runs   {a.description}")
    out.append("")

    if not inv.flakes:
        per_arm = inv.arms[0].runs if inv.arms else 0
        out.append(f"No flaky tests found across {per_arm} runs per arm.")
        out.append("A suite can still be flaky at a rate this many runs cannot see -")
        out.append("raise --runs to lower that bound.")
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
        "arms": [{"name": a.name, "description": a.description, "runs": a.runs} for a in inv.arms],
        "by_cause": inv.by_cause(),
        "flakes": [f.as_row() for f in inv.flakes],
        "always_failed": inv.always_failed,
    }


def write_json(inv: Investigation, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(as_json(inv), indent=2), encoding="utf-8")
