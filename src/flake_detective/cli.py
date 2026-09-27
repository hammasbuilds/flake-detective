"""Command line: `investigate` a real suite, `bench` the classifier, `fixture` to inspect it.

Exit status, for every subcommand:

    0   ran, and (with --fail-on-flake) found nothing flaky
    1   flaky tests found and --fail-on-flake was given
    2   nothing could be examined, or not all of it: bad arguments, pytest missing
        from the target interpreter, a collection error, no tests, or an arm where
        no run could be scored. Never a clean result.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from flake_detective import __version__
from flake_detective import bench as bench_mod
from flake_detective import fixture as fixture_mod
from flake_detective import report as report_mod
from flake_detective.detective import ALL_ARMS, ARMS, EXTRA_ARMS, Options, investigate

EXIT_OK, EXIT_FLAKY, EXIT_ERROR = 0, 1, 2

EPILOG = """\
exit status: 0 ran cleanly; 1 flaky tests found with --fail-on-flake;
2 nothing (or not everything) could be examined - bad arguments, pytest
missing, a collection error, no tests, or an arm with no scoreable run.
"""


class _Say:
    """Progress on stderr. Per-run lines overwrite each other on a terminal and are
    printed one per line elsewhere, so a CI log still shows the run count moving."""

    def __init__(self) -> None:
        self.tty = sys.stderr.isatty()
        self.pending = 0  # width of a transient line still on screen

    def __call__(self, msg: str, transient: bool = False) -> None:
        line = f"  .. {msg}"
        if transient and self.tty:
            pad = max(self.pending - len(line), 0)
            print("\r" + line + " " * pad, end="", file=sys.stderr, flush=True)
            self.pending = len(line)
            return
        if self.pending:
            print(file=sys.stderr)
            self.pending = 0
        print(line, file=sys.stderr, flush=True)

    def done(self) -> None:
        if self.pending:
            print(file=sys.stderr, flush=True)
            self.pending = 0


def _positive_int(text: str) -> int:
    try:
        n = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a whole number, got {text!r}") from None
    if n < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1, got {n}")
    return n


def _positive_float(text: str) -> float:
    try:
        x = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a number of seconds, got {text!r}") from None
    if x <= 0:
        raise argparse.ArgumentTypeError(f"must be more than 0, got {text}")
    return x


def _arms(text: str) -> tuple[str, ...]:
    names = [x.strip().lower() for x in text.split(",") if x.strip()]
    if names == ["all"]:
        return ALL_ARMS
    if not names:
        raise argparse.ArgumentTypeError(f"name at least one arm: {', '.join(ALL_ARMS)}")
    unknown = [x for x in names if x not in ALL_ARMS]
    if unknown:
        raise argparse.ArgumentTypeError(
            f"unknown arm(s): {', '.join(unknown)}. Valid: {', '.join(ALL_ARMS)} (or 'all')"
        )
    return tuple(dict.fromkeys(names))


def _resolve_python(text: str) -> tuple[str, str]:
    """(absolute interpreter path, or "" for this one; error message or "")."""
    if not text:
        return "", ""
    p = Path(text).expanduser()
    if p.is_dir():
        # A venv directory is what people usually have to hand. Accept it.
        for rel in ("Scripts/python.exe", "bin/python", "bin/python3", "python.exe"):
            if (p / rel).is_file():
                return str((p / rel).resolve()), ""
        return "", f"--python {text}: a directory, but no Scripts/python.exe or bin/python in it"
    if p.is_file():
        # Absolute, because the suite runs with the repo as its working directory and
        # a relative path would be resolved from there instead of from here.
        return str(p.resolve()), ""
    if os.sep not in text and "/" not in text:
        import shutil

        found = shutil.which(text)
        if found:
            return found, ""
    return "", f"--python {text}: no such interpreter"


def _runs_help(default: int) -> str:
    # Every literal % in argparse help has to be written %%: help strings are
    # %-formatted, and a bare one crashed --help on Python 3.11-3.13.
    return (
        f"runs per arm (default {default}). 1 finds instability but can name no cause: a "
        "single baseline run cannot flip, so nondeterminism cannot be ruled out. Seven "
        "because a two-test order dependence is exposed by about half of all shuffles, "
        "so five runs miss it 3.1%% of the time and seven 0.8%%."
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="flake-detective",
        description="Find flaky tests in a pytest suite and say what they depend on: "
        "test order, hash seed, the clock, or nothing at all.",
        epilog=EPILOG,
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="COMMAND")

    inv = sub.add_parser(
        "investigate",
        help="run a suite under varied conditions and name each flaky test's cause",
        description="Run a pytest suite repeatedly, changing one thing per arm, and report "
        "which change makes each flaky test flip. Every run is a full pytest process: the "
        "cost is about (arms + 1) x runs x one run of the suite.",
        epilog=EPILOG,
    )
    inv.add_argument(
        "repo", type=Path, metavar="REPO", help="the project directory (pytest runs here)"
    )
    inv.add_argument(
        "target",
        nargs="?",
        default="",
        metavar="TARGET",
        help="a path or node id inside REPO to narrow to, e.g. tests/ or tests/test_x.py",
    )
    inv.add_argument("--runs", type=_positive_int, default=7, help=_runs_help(7))
    inv.add_argument(
        "--timeout",
        type=_positive_float,
        default=900.0,
        help="seconds allowed per pytest run (default 900)",
    )
    inv.add_argument(
        "--arms",
        type=_arms,
        default=ARMS,
        help=(
            f"perturbations to try, comma separated (default {','.join(ARMS)}). "
            f"Also available: {','.join(EXTRA_ARMS)}, or 'all'. Each costs a full set of "
            "runs; isolation costs one process per test per run. timezone and locale "
            "skip themselves where the variable does not reach the interpreter (Windows)."
        ),
    )
    inv.add_argument(
        "--python",
        default="",
        metavar="PATH",
        help="the interpreter your tests run with, or its venv directory "
        "(default: the one running flake-detective). Needs pytest installed. Usually "
        ".venv/bin/python, or .venv\\Scripts\\python.exe on Windows.",
    )
    inv.add_argument(
        "--jobs",
        type=_positive_int,
        default=1,
        help="pytest processes to run at once (default 1). Faster, but only for a suite "
        "that does not share fixed files, ports or databases between processes - "
        "concurrent runs would otherwise collide and create the very flakes being looked for.",
    )
    inv.add_argument(
        "--no-freeze-clock",
        action="store_true",
        help="do not pin the wall clock; disables the clock arm",
    )
    inv.add_argument("--json", type=Path, metavar="FILE", help="also write the findings here")
    inv.add_argument(
        "--fail-on-flake",
        action="store_true",
        help="exit 1 if any flaky test is found (for CI)",
    )
    inv.add_argument(
        "--localise",
        action="store_true",
        help="bisect each order dependence to name the earlier test that causes it "
        "(costs about log2(n) extra runs per order-dependent test)",
    )
    inv.add_argument("--quiet", action="store_true", help="no progress on stderr")

    b = sub.add_parser(
        "bench",
        help="score the classifier on a built-in suite whose causes are known",
        description="Investigate a built-in suite with one planted flake per cause, and "
        "score detection, attribution and false positives against the answer key. Needs "
        "pytest in the interpreter that runs it (see --python).",
        epilog=EPILOG,
    )
    b.add_argument("--runs", type=_positive_int, default=7, help="runs per arm (default 7)")
    b.add_argument(
        "--timeout", type=_positive_float, default=120.0, help="seconds per run (default 120)"
    )
    b.add_argument(
        "--python",
        default="",
        metavar="PATH",
        help="interpreter with pytest installed to run the fixture with "
        "(default: the one running flake-detective)",
    )
    b.add_argument(
        "--jobs",
        type=_positive_int,
        default=min(4, os.cpu_count() or 1),
        help="pytest processes at once (default: up to 4). Safe here: the fixture shares "
        "nothing between processes.",
    )
    b.add_argument("--json", type=Path, metavar="FILE", help="also write the scores here")
    b.add_argument("--quiet", action="store_true", help="no progress on stderr")
    b.add_argument(
        "--sweep",
        action="store_true",
        help="score at 1,2,3,5,7,11 runs per arm instead of one count",
    )

    f = sub.add_parser(
        "fixture",
        help="write the benchmark suite to a directory to read it",
        description="Write the benchmark's suite to DIR and list each test's planted cause.",
    )
    f.add_argument("into", type=Path, metavar="DIR")
    return p


def main(argv: list[str] | None = None) -> int:
    p = build_parser()
    a = p.parse_args(argv)

    if a.cmd == "fixture":
        if a.into.exists() and not a.into.is_dir():
            print(f"error: {a.into} exists and is not a directory", file=sys.stderr)
            return EXIT_ERROR
        where = fixture_mod.write(a.into)
        print(f"wrote {len(fixture_mod.FILES)} files to {where}")
        for name, cause in sorted(fixture_mod.TRUTH.items()):
            print(f"  {str(cause or 'stable'):<16} {name}")
        return EXIT_OK

    python, err = _resolve_python(a.python)
    if err:
        print(f"error: {err}", file=sys.stderr)
        return EXIT_ERROR

    say = None if a.quiet else _Say()

    if a.cmd == "bench":
        if a.sweep:
            res = bench_mod.sweep(timeout=a.timeout, progress=say, python=python, jobs=a.jobs)
            out = bench_mod.sweep_text(res)
        else:
            res = bench_mod.run(
                runs=a.runs, timeout=a.timeout, progress=say, python=python, jobs=a.jobs
            )
            out = bench_mod.text(res)
        if say:
            say.done()
        print(out)
        if a.json:
            bench_mod.write_json(res, a.json)
            print(f"\nwrote {a.json}")
        return EXIT_ERROR if "error" in res else EXIT_OK

    if not a.repo.exists():
        print(f"error: no such directory: {a.repo}", file=sys.stderr)
        return EXIT_ERROR
    if not a.repo.is_dir():
        print(
            f"error: {a.repo} is a file. REPO is the project directory; to narrow to one "
            f"file, pass it as TARGET:\n"
            f"  flake-detective investigate {a.repo.parent or '.'} {a.repo.name}",
            file=sys.stderr,
        )
        return EXIT_ERROR
    if a.runs == 1:
        print(
            "warning: with --runs 1 a flip can be seen but no cause can be named - one "
            "baseline run cannot rule out nondeterminism. Use 2 or more (default 7).",
            file=sys.stderr,
        )

    result = investigate(
        a.repo,
        a.target,
        Options(
            runs=a.runs,
            timeout=a.timeout,
            arms=a.arms,
            python=python,
            jobs=a.jobs,
            freeze_clock=not a.no_freeze_clock,
            localise=a.localise,
        ),
        progress=say,
    )
    if say:
        say.done()
    print(report_mod.text(result))
    if a.json:
        report_mod.write_json(result, a.json)
        print(f"\nwrote {a.json}")

    if not result.ok:
        return EXIT_ERROR
    if a.fail_on_flake and result.flakes:
        return EXIT_FLAKY
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
