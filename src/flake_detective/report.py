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

# Every cause, most actionable first. Built from the enum and checked against it, because
# a hand-kept list of five went stale when four arms were added: under --arms all the
# headline said "9 flaky tests" and listed four of them.
ORDER = [
    Cause.ORDER,
    Cause.NEEDS_TEST,
    Cause.HASH_SEED,
    Cause.CLOCK,
    Cause.TIMEZONE,
    Cause.LOCALE,
    Cause.PARALLEL,
    Cause.NONDETERMINISM,
    Cause.UNKNOWN,
]
assert set(ORDER) == set(Cause), "report.ORDER must list every Cause"


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
        if inv.interrupted:
            out.append("INTERRUPTED - nothing was examined.")
        else:
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
    if inv.seed is not None and any(a.name == "order" for a in inv.arms):
        out.append(f"order seed {inv.seed} (--seed {inv.seed} repeats these shuffles)")
    out.append("")
    if inv.interrupted:
        out.append("INTERRUPTED (Ctrl-C). What follows is from the runs that finished; arms")
        out.append("that had not started are missing, and fewer runs can see less.")
        out.append("")

    names = [a.name for a in inv.arms]
    for a in inv.arms:
        runs = f"{a.runs} runs" if not a.unscored else f"{a.runs} of {a.attempted} runs"
        if a.interrupted:
            runs += " (cut short)"
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

    # Say what was NOT searched, on both branches. A cause whose arm never ran cannot
    # be ruled out, and "1 flaky test: 1 clock" otherwise reads as the whole space
    # having been looked at. This was announced on the progress stream only, so --quiet
    # hid it and the JSON had no record of it.
    if inv.skipped_arms:
        out.append("")
        out.append("Asked for but not run on this platform, so these causes were not searched:")
        for name, reason in inv.skipped_arms:
            out.append(f"  {name:<10} {reason}")
        out.append("")

    if not inv.flakes:
        per_arm = min((a.runs for a in inv.arms if a.runs), default=0)
        if inv.interrupted:
            out.append(f"No flaky tests found in the runs that finished ({per_arm}+ per arm).")
        elif inv.incomplete:
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
        out.append("Rates are failures over the runs that observed the test; - means an arm")
        out.append("never saw it pass or fail. Setup and teardown errors count as failures.")
        out.append("")
        out.append("-" * 74)
        header = f"{'test':<54}" + "".join(f"{n[:8]:>9}" for n in names)
        out.append(header)
        out.append("-" * 74)

        rank = {c: i for i, c in enumerate(ORDER)}
        for f in sorted(inv.flakes, key=lambda f: (rank.get(f.cause, 9), f.test_id)):
            row = f"{_short(f.test_id):<54}"
            row += "".join(f"{f.rates[n]:>9.1f}" if n in f.rates else f"{'-':>9}" for n in names)
            out.append(row)
            label = f.cause.value.upper()
            if f.cause is Cause.ORDER and not f.directed:
                label += " (direction undetermined)"
            out.append(f"    {label}: {f.evidence}")
            if f.errored:
                out.append("    (some failures were errors in fixture setup or teardown)")
            if f.culprits:
                # The cause names this test; this names the other half of the pair,
                # which is usually the one worth opening.
                what = "needs" if f.cause is Cause.NEEDS_TEST else "caused by"
                if len(f.culprits) == 1:
                    out.append(f"    {what}: {f.culprits[0]}")
                else:
                    out.append(
                        f"    {what} these {len(f.culprits)} together (no single one was enough):"
                    )
                    for c in f.culprits:
                        out.append(f"      {c}")
            if f.localisation:
                out.append(f"    localise: {f.localisation['summary']}")
            out.append(f"    fix: {f.fix}")
            out.append("")

    if inv.unobserved:
        out.append("-" * 74)
        out.append(
            f"{len(inv.unobserved)} tests never passed or failed in any run (skipped, or "
            "never reached) - not judged:"
        )
        for t in inv.unobserved[:10]:
            out.append(f"  {_short(t, 68)}")
        if len(inv.unobserved) > 10:
            out.append(f"  ... and {len(inv.unobserved) - 10} more")

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
        "seed": inv.seed,
        "interrupted": inv.interrupted,
        "arms": [
            {
                "name": a.name,
                "description": a.description,
                "runs": a.runs,
                "attempted": a.attempted,
                **({"interrupted": True} if a.interrupted else {}),
                **({"error": a.error} if a.error else {}),
            }
            for a in inv.arms
        ],
        "by_cause": inv.by_cause(),
        "flakes": [f.as_row() for f in inv.flakes],
        "always_failed": inv.always_failed,
        "unobserved": inv.unobserved,
        "ok": inv.ok,
        "problem": inv.problem or None,
        "incomplete_arms": inv.incomplete,
        # Arms the user asked for that this platform cannot run. Without this a report
        # from `--arms all` listed five arms and mentioned neither timezone nor locale,
        # which reads as a completed search of every cause.
        "skipped_arms": [{"name": name, "reason": reason} for name, reason in inv.skipped_arms],
    }


def write_json(inv: Investigation, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(as_json(inv), indent=2), encoding="utf-8")
