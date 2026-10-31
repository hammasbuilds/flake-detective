"""Compare this tool against the way flakiness is usually handled: re-running.

`pytest-rerunfailures` is what most projects actually use. It re-runs a failing test a few
times and lets it pass if any attempt passes, which makes the failure go away. This tool
runs the suite many times while **changing one thing per batch** and names what each flaky
test depends on.

The comparison is possible at all because of `flake_detective.fixture`: a suite written to
be flaky in eight specified ways, with an answer key, plus stable tests that look flaky so
false positives are measurable. Nobody knows the ground truth for somebody else's flaky
test - that is why this tool exists - so a benchmark with a key is the only place a
detection rate means anything.

What is measured, on the same generated fixture, in the same run:

  * **detection** - how many known-flaky tests each approach reports,
  * **false positives** - how many of the stable-but-flaky-looking tests each flags,
  * **cause attribution** - which this tool reports and a re-runner structurally cannot,
    so it is reported as a structural difference rather than scored as a failure.

The expected shape of the result, stated before running so it cannot be fitted
afterwards: re-running finds flakiness that reproduces **under identical conditions** and
nothing else. An order-dependent test fails again on a re-run in the same session, because
its neighbour has already run. A hash-seed-dependent test sees the same PYTHONHASHSEED in
the same process. A test that fails *alone* and passes after its neighbour never fails in a
full-suite run, so there is nothing to re-run.

    python scripts/compare_reruns.py
    python scripts/compare_reruns.py --reruns 6 --out docs/rerun-comparison.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from flake_detective.fixture import TRUTH, score, write  # noqa: E402

VENV = ROOT / ".venv-rerun"

# pytest-rerunfailures prints one `RERUN <nodeid>` per retried attempt under a section
# headed "rerun test summary info", then `FAILED <nodeid>` if the last attempt also
# failed. The summary counts are not enough: "2 failed, 14 passed, 8 rerun" does not say
# WHICH test was retried.
OUTCOME_LINE = re.compile(r"^(RERUN|FAILED|ERROR)\s+(\S+::\S+)", re.MULTILINE)
RERUN_SECTION = "rerun test summary info"


def python_in(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def ensure_baselines() -> dict[str, str]:
    python = python_in(VENV)
    if not python.exists():
        subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)
    versions: dict[str, str] = {}
    for package in ("pytest", "pytest-rerunfailures", "flaky"):
        shown = subprocess.run(
            [str(python), "-m", "pip", "show", package], capture_output=True, text=True
        )
        if shown.returncode != 0:
            subprocess.run(
                [str(python), "-m", "pip", "install", "-q", "--disable-pip-version-check", package],
                check=True,
            )
            shown = subprocess.run(
                [str(python), "-m", "pip", "show", package],
                capture_output=True,
                text=True,
                check=True,
            )
        for line in shown.stdout.splitlines():
            if line.startswith("Version:"):
                versions[package] = line.split(":", 1)[1].strip()
    return versions


def rerun_pass(suite: Path, reruns: int, repeats: int) -> tuple[set[str], set[str], list[str]]:
    """(noticed, masked, transcripts) for a re-runner over the same suite.

    **noticed**: the test was retried at all, so the re-runner saw instability. The
    generous reading, and the right one for "did it detect anything".

    **masked**: retried, and the session still ended with it passing - the failure went
    away and CI went green. That is what a re-runner is installed to do, and what a user
    experiences.

    A test retried that still fails is in `noticed` and not in `masked`: noticed and not
    helped.

    The whole suite is run `repeats` times, because a re-runner has no notion of arms and
    its own detection is a matter of luck. Running it once would be comparing this tool's
    twenty-eight runs against one.
    """
    noticed: set[str] = set()
    masked: set[str] = set()
    transcripts: list[str] = []
    for _ in range(repeats):
        proc = subprocess.run(
            [
                str(python_in(VENV)),
                "-m",
                "pytest",
                "-p",
                "no:cacheprovider",
                f"--reruns={reruns}",
                "-rR",
                "-q",
                str(suite),
            ],
            capture_output=True,
            text=True,
            cwd=suite,
        )
        text = proc.stdout + proc.stderr
        transcripts.append(text)
        if RERUN_SECTION not in text:
            continue
        tail = text.split(RERUN_SECTION, 1)[1]
        retried: set[str] = set()
        ended_badly: set[str] = set()
        for outcome, nodeid in OUTCOME_LINE.findall(tail):
            if outcome == "RERUN":
                retried.add(nodeid)
            else:
                ended_badly.add(nodeid)
        noticed |= retried
        masked |= retried - ended_badly
    return noticed, masked, transcripts


def investigate(suite: Path, seed: int) -> tuple[dict[str, str], set[str], str]:
    """This tool's own answer: test id -> cause, and which causes it looked for."""
    # --json takes a FILE. Passing "-" wrote the report to a file called "-" and left
    # stdout as the progress display, so the parse found no JSON after a full 28-run
    # investigation had already been paid for.
    with tempfile.TemporaryDirectory(prefix="fd-json-", dir=scratch_dir()) as hold:
        report = Path(hold) / "report.json"
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "flake_detective",
                "investigate",
                str(suite),
                "--seed",
                str(seed),
                "--json",
                str(report),
            ],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        if not report.exists():
            raise SystemExit(f"investigate wrote no report:\n{(proc.stderr or proc.stdout)[-800:]}")
        data = json.loads(report.read_text(encoding="utf-8"))
    # The key is "flakes", not "flaky", and the searched causes come from the arms that
    # actually ran - `searched_causes` is not a field of the report. Reading absent keys
    # scored this tool at 0 detections on a fixture it finds five flakes in.
    found = {row["test"]: row.get("cause") or "unknown" for row in data.get("flakes", [])}
    searched = searched_causes_from(data)
    return found, searched, proc.stdout


def searched_causes_from(data: dict) -> set[str]:
    """Which causes an arm actually looked for.

    Delegated to `bench.searched_causes`, which reads the correspondence from
    `classify.ARM_CAUSE`. An earlier version of this function kept its own copy of that
    mapping as a fallback - which is precisely what that function's docstring warns
    against, because the two would drift apart and the copy would be the one nobody
    noticed was wrong. If it raises, that is a defect to see, not to paper over.
    """
    from types import SimpleNamespace  # noqa: PLC0415

    from flake_detective.bench import searched_causes  # noqa: PLC0415

    # It wants the investigation OBJECT (`inv.arms[i].name`); this reads the JSON report,
    # where arms are dicts. Only the container is adapted - the arm-to-cause mapping stays
    # in classify.ARM_CAUSE, which is the part that must not be copied.
    adapted = SimpleNamespace(arms=[SimpleNamespace(**arm) for arm in data.get("arms", [])])
    return set(searched_causes(adapted))


def scratch_dir() -> Path:
    """A temp parent on the same drive as this repository.

    `tempfile` follows TMPDIR, which is C: here, and these scripts write generated
    fixtures, JSON payloads and - in the sibling census scripts - whole package trees.
    C: went from 58 GB free to 31 over one day of measurements because nothing said
    which drive. CENSUS_SCRATCH overrides it, the same variable the census scripts use.
    """
    root = Path(os.environ.get("CENSUS_SCRATCH", str(ROOT.parent / "_census_scratch")))
    out = root / "tmp"
    out.mkdir(parents=True, exist_ok=True)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--reruns",
        type=int,
        default=6,
        help="attempts pytest-rerunfailures is given per failing test",
    )
    ap.add_argument(
        "--repeats",
        type=int,
        default=7,
        help="how many times the whole suite is run under the re-runner, so "
        "its detection is not decided by one roll",
    )
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "rerun-comparison.json")
    args = ap.parse_args()

    versions = ensure_baselines()
    known = {test: cause for test, cause in TRUTH.items() if cause is not None}
    stable = [test for test, cause in TRUTH.items() if cause is None]

    with tempfile.TemporaryDirectory(prefix="flake-compare-", dir=scratch_dir()) as tmp:
        suite = write(Path(tmp) / "fx")
        print(f"fixture at {suite}: {len(known)} known-flaky, {len(stable)} stable\n", flush=True)

        start = time.perf_counter()
        found, searched, _ = investigate(suite, args.seed)
        ours_seconds = time.perf_counter() - start
        ours = score(found, searched or None)
        print(f"flake-detective: {ours_seconds:.0f}s", flush=True)

        start = time.perf_counter()
        noticed, masked, _ = rerun_pass(suite, args.reruns, args.repeats)
        theirs_seconds = time.perf_counter() - start
        print(
            f"pytest-rerunfailures ({args.repeats} suite runs, --reruns="
            f"{args.reruns}): {theirs_seconds:.0f}s",
            flush=True,
        )

    # A re-runner names no cause, so it is scored on detection only. Passing it through
    # `score` with a placeholder cause would report 0% attribution as though it had tried
    # and failed; it never claims a cause at all.
    detected_known = sorted(t for t in noticed if t in known)
    masked_known = sorted(t for t in masked if t in known)
    false_positives = sorted(t for t in noticed if t in set(stable))
    scoreable = sorted(t for t, cause in known.items() if not searched or cause in searched)
    # Restricted to the causes BOTH sides can be scored on. Without this the two "4 of 4"
    # figures are over different sets: this tool cannot score needs-other-test here (the
    # isolation arm did not run), and the re-runner never retried the order-dependent test
    # at all, because the collection order never changes. Comparing the two unrestricted
    # counts would have published a tie that does not exist.
    common = set(scoreable)
    noticed_common = sorted(t for t in noticed if t in common)
    masked_common = sorted(t for t in masked if t in common)
    never_seen_common = sorted(common - set(noticed))

    result = {
        "measured": time.strftime("%Y-%m-%d"),
        "baseline_versions": versions,
        "reruns_per_failure": args.reruns,
        "suite_runs_under_the_rerunner": args.repeats,
        "seed": args.seed,
        "known_flaky": len(known),
        "stable_but_flaky_looking": len(stable),
        "causes_this_platform_can_score": scoreable,
        "flake_detective": {
            "detected": ours.get("detected"),
            "correct_cause": ours.get("correct_cause"),
            "false_positives": ours.get("false_positives"),
            "unscoreable": ours.get("unscoreable"),
            "seconds": round(ours_seconds, 1),
            "names_a_cause": True,
            "raw": ours,
        },
        "pytest_rerunfailures": {
            # The generous reading: it retried the test, so it saw instability.
            "noticed": len(detected_known),
            "noticed_tests": detected_known,
            "noticed_of_the_common_set": len(noticed_common),
            "noticed_tests_in_the_common_set": noticed_common,
            "masked_of_the_common_set": len(masked_common),
            "never_retried_in_the_common_set": never_seen_common,
            # What it is installed to do: the failure went away and CI went green.
            "masked": len(masked_known),
            "masked_tests": masked_known,
            "correct_cause": None,
            "false_positives": len(false_positives),
            "false_positive_tests": false_positives,
            "seconds": round(theirs_seconds, 1),
            "names_a_cause": False,
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=1), encoding="utf-8")

    print(f"\n  known-flaky tests this platform can score: {len(scoreable)}")
    print(
        f"  flake-detective      detected {ours.get('detected')}, "
        f"cause right {ours.get('correct_cause')}, "
        f"false positives {ours.get('false_positives')}"
    )
    print(
        f"  on the {len(scoreable)} cause(s) both sides can be scored on: "
        f"flake-detective {len(scoreable)}, re-runner noticed {len(noticed_common)}, "
        f"masked {len(masked_common)}"
    )
    if never_seen_common:
        print(f"    never retried at all: {', '.join(never_seen_common)}")
    print(
        f"  pytest-rerunfailures noticed {len(detected_known)} overall, "
        f"masked {len(masked_known)}, names no cause, "
        f"false positives {len(false_positives)}"
    )
    if detected_known:
        print(f"    retried: {', '.join(detected_known)}")
    if masked_known:
        print(f"    and let through: {', '.join(masked_known)}")
    print(f"\nwritten to {args.out.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
