"""Command line: `investigate` a real suite, `bench` the classifier, `fixture` to inspect it."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from flake_detective import bench as bench_mod
from flake_detective import fixture as fixture_mod
from flake_detective import report as report_mod
from flake_detective.detective import ARMS, Options, investigate


def _say(msg: str) -> None:
    print(f"  .. {msg}", file=sys.stderr, flush=True)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="flake-detective",
        description="Find flaky tests and say what they depend on.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    inv = sub.add_parser("investigate", help="run a suite under varied conditions")
    inv.add_argument("repo", type=Path)
    inv.add_argument("target", nargs="?", default="", help="path or node id to narrow to")
    inv.add_argument(
        "--runs",
        type=int,
        default=5,
        help="runs per arm (default 5). Below 2 no cause can be established: a single "
        "baseline run cannot flip, so nondeterminism cannot be ruled out.",
    )
    inv.add_argument("--timeout", type=float, default=900.0, help="seconds per run")
    inv.add_argument(
        "--arms",
        default=",".join(ARMS),
        help=f"which perturbations to try, comma separated (default {','.join(ARMS)})",
    )
    inv.add_argument(
        "--python",
        default="",
        help="interpreter to run the target suite with (default: this one). "
        "On a real repo this is almost always its own venv.",
    )
    inv.add_argument(
        "--no-freeze-clock",
        action="store_true",
        help="do not pin the wall clock; disables the clock arm",
    )
    inv.add_argument("--json", type=Path, help="also write the findings here")
    inv.add_argument(
        "--fail-on-flake",
        action="store_true",
        help="exit 1 if any flaky test is found (for CI)",
    )
    inv.add_argument("--quiet", action="store_true")

    b = sub.add_parser("bench", help="score the classifier on a suite with known causes")
    b.add_argument("--runs", type=int, default=7)
    b.add_argument("--timeout", type=float, default=120.0)
    b.add_argument("--json", type=Path)
    b.add_argument("--quiet", action="store_true")
    b.add_argument(
        "--sweep",
        action="store_true",
        help="score at 1,2,3,5,7,11 runs per arm instead of one count",
    )

    f = sub.add_parser("fixture", help="write the benchmark suite somewhere to look at it")
    f.add_argument("into", type=Path)

    a = p.parse_args(argv)

    if a.cmd == "fixture":
        where = fixture_mod.write(a.into)
        print(f"wrote {len(fixture_mod.FILES)} files to {where}")
        for name, cause in sorted(fixture_mod.TRUTH.items()):
            print(f"  {str(cause or 'stable'):<16} {name}")
        return 0

    if a.cmd == "bench":
        say = None if a.quiet else _say
        if a.sweep:
            res = bench_mod.sweep(timeout=a.timeout, progress=say)
            print(bench_mod.sweep_text(res))
        else:
            res = bench_mod.run(runs=a.runs, timeout=a.timeout, progress=say)
            print(bench_mod.text(res))
        if a.json:
            bench_mod.write_json(res, a.json)
            print(f"\nwrote {a.json}")
        return 0

    if not a.repo.exists():
        print(f"no such path: {a.repo}", file=sys.stderr)
        return 2

    result = investigate(
        a.repo,
        a.target,
        Options(
            runs=a.runs,
            timeout=a.timeout,
            arms=tuple(x.strip() for x in a.arms.split(",") if x.strip()),
            python=a.python,
            freeze_clock=not a.no_freeze_clock,
        ),
        progress=None if a.quiet else _say,
    )
    print(report_mod.text(result))
    if a.json:
        report_mod.write_json(result, a.json)
        print(f"\nwrote {a.json}")

    if a.fail_on_flake and result.flakes:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
