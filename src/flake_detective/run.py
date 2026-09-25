"""Run a suite many times, changing exactly one thing per arm.

The whole method rests on changing **one** variable at a time. Run a suite twice with a
different seed *and* a different order, watch a test flip, and you have learned that it is
flaky and nothing about why. Vary one thing and the arm that disagrees names the dependency.

Four arms, and the baseline is not a formality:

    baseline   identical conditions, repeated - the control
    order      the same tests, shuffled
    hashseed   PYTHONHASHSEED varied, everything else identical
    clock      the wall clock frozen at a different instant each run

"Identical conditions" takes work. Two of the three variables leak in by default: the
interpreter randomises string hashing on every start, and the clock moves while the suite
runs. Both are pinned in *every* arm - seed 0 and a fixed instant - so that the arm which
varies one of them is the only place it varies at all. Without that the baseline flips
whatever the other arms would have flipped, and claims it as nondeterminism.
"""

from __future__ import annotations

import os
import random
import re
import subprocess
import sys
from pathlib import Path

from flake_detective import freeze
from flake_detective.types import Arm

# `-rf` prints "FAILED path::test[id] - AssertionError: ...". Matching `\S+` stops at the
# first space, and a parametrised node id contains them: `test_counting[(x, y)-2]` is
# captured as `test_counting[(x,`. The truncated id is stable across runs, so it never
# produced a false flake - but two different parametrisations can truncate to the *same*
# string, which would merge them and hide a flake in whichever one flipped.
_SUMMARY = re.compile(r"^(?:FAILED|ERROR)\s+(.+?)(?:\s+-\s.*)?$", re.MULTILINE)


def _env(hashseed: int, epoch: float | None) -> dict[str, str]:
    env = dict(os.environ)
    # PYTHONHASHSEED has to be set in the environment: by the time the interpreter is
    # running it is far too late to change how strings hash.
    env["PYTHONHASHSEED"] = str(hashseed)
    if epoch is not None:
        env[freeze.ENV_VAR] = repr(epoch)
        d = str(freeze.plugin_dir())
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = d + (os.pathsep + existing if existing else "")
    else:
        env.pop(freeze.ENV_VAR, None)
    return env


def collect(repo: Path, target: str = "", timeout: float = 300.0, python: str = "") -> list[str]:
    """Every test id the suite contains, without running any of them."""
    cmd = [
        python or sys.executable,
        "-m",
        "pytest",
        "--collect-only",
        "-q",
        "--no-header",
        "-p",
        "no:cacheprovider",
    ]
    if target:
        cmd.append(target)
    try:
        proc = subprocess.run(
            cmd, cwd=repo, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (subprocess.TimeoutExpired, OSError):
        return []
    out: list[str] = []
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if "::" in line and not line.startswith(("=", "-", "ERROR", "FAILED")):
            out.append(line)
    return out


def run_once(
    repo: Path,
    target: str = "",
    order: list[str] | None = None,
    hashseed: int = 0,
    epoch: float | None = None,
    timeout: float = 900.0,
    python: str = "",
    extra_env: dict[str, str] | None = None,
    extra_args: list[str] | None = None,
) -> set[str] | None:
    """The set of test ids that failed. None if the run could not be scored at all."""
    cmd = [
        python or sys.executable,
        "-m",
        "pytest",
        "-q",
        "--no-header",
        "-p",
        "no:cacheprovider",
        "--tb=no",
        "-rf",
    ]
    if epoch is not None:
        cmd += ["-p", freeze.PLUGIN_NAME]
    if extra_args:
        cmd += extra_args
    cmd.extend(order if order else ([target] if target else []))

    try:
        proc = subprocess.run(
            cmd,
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**_env(hashseed, epoch), **(extra_env or {})},
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None

    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode == 5:  # nothing collected
        return None
    if proc.returncode not in (0, 1):
        # 2 is an internal error, 3 an interrupt: a run that fell over is not evidence
        # that every test in it failed.
        return None
    return set(_SUMMARY.findall(out))


def _tally(arm: Arm, failed: set[str] | None) -> None:
    if failed is None:
        return
    arm.runs += 1
    for t in failed:
        arm.failures[t] = arm.failures.get(t, 0) + 1


def baseline_arm(
    repo: Path, target: str, runs: int, timeout: float, epoch: float | None, python: str = ""
) -> Arm:
    """Identical conditions, repeated. Anything that flips here is nondeterministic."""
    arm = Arm("baseline", "identical conditions, repeated")
    for _ in range(runs):
        _tally(arm, run_once(repo, target, hashseed=0, epoch=epoch, timeout=timeout, python=python))
    return arm


def order_arm(
    repo: Path,
    target: str,
    tests: list[str],
    runs: int,
    timeout: float,
    epoch: float | None,
    seed: int = 0,
    python: str = "",
) -> Arm:
    """The same tests, shuffled. Flips here mean one test leaves state for another."""
    arm = Arm("order", "the same tests, shuffled")
    rnd = random.Random(seed)
    for _ in range(runs):
        shuffled = list(tests)
        rnd.shuffle(shuffled)
        _tally(
            arm,
            run_once(
                repo,
                target,
                order=shuffled,
                hashseed=0,
                epoch=epoch,
                timeout=timeout,
                python=python,
            ),
        )
    return arm


def hashseed_arm(
    repo: Path, target: str, runs: int, timeout: float, epoch: float | None, python: str = ""
) -> Arm:
    """A different PYTHONHASHSEED each run, everything else identical."""
    arm = Arm("hashseed", "PYTHONHASHSEED varied")
    for i in range(runs):
        _tally(
            arm,
            run_once(repo, target, hashseed=i + 1, epoch=epoch, timeout=timeout, python=python),
        )
    return arm


def clock_arm(repo: Path, target: str, runs: int, timeout: float, python: str = "") -> Arm:
    """The wall clock frozen at a different instant each run.

    Not "wait a second and run again" - that was the first attempt, and it could not
    distinguish a clock-dependent test from a nondeterministic one, because the baseline
    also takes time to run. Freezing makes the date a controlled variable like any other.
    """
    arm = Arm("clock", "the wall clock frozen at a different date each run")
    for i in range(runs):
        epoch = freeze.CLOCK_EPOCHS[i % len(freeze.CLOCK_EPOCHS)]
        _tally(arm, run_once(repo, target, hashseed=0, epoch=epoch, timeout=timeout, python=python))
    return arm


# Timezones chosen to disagree as much as possible about what "today" is: two of
# them are on different calendar days for most of any given UTC day, and one keeps
# a half-hour offset, which catches code that assumes offsets are whole hours.
TIMEZONES = ("UTC", "Pacific/Kiritimati", "Pacific/Niue", "Asia/Kolkata", "America/New_York")

# C is the fallback everywhere. tr_TR is the classic: Turkish has a dotless i, so
# "I".lower() is not "i" and any case-insensitive comparison written with .lower()
# behaves differently. A test that passes under C and fails under tr_TR is telling
# you about a real bug that ships to Turkish users.
LOCALES = ("C", "en_US.UTF-8", "tr_TR.UTF-8", "de_DE.UTF-8")


def tz_supported(python: str = "") -> bool:
    """Does setting TZ actually move this interpreter's idea of local time?

    Only where `time.tzset` exists, which means POSIX. Measured on Windows with
    CPython 3.12, setting TZ is worse than inert:

        TZ=UTC                 -> 03:46, tzname ('Pakistan Standard Time', ...)
        TZ=Pacific/Kiritimati  -> 04:46, tzname ('Pakistan Standard Time', ...)
        TZ=America/New_York    -> 04:46, tzname ('Pakistan Standard Time', ...)

    The Olson names are not understood at all, so two supposedly opposite
    timezones give the same answer, while TZ=UTC shifts by an hour. An arm on top
    of that can still flip a test - and would then blame "timezone" for something
    that does not reproduce anywhere the user runs it. Skipping is the honest move.
    """
    try:
        proc = subprocess.run(
            [python or sys.executable, "-c", "import time, sys; sys.exit(0 if hasattr(time, 'tzset') else 1)"],
            capture_output=True, timeout=60, check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return proc.returncode == 0


def locale_supported(python: str = "") -> bool:
    """Do LANG and LC_ALL reach this interpreter's locale at all?

    Probed rather than assumed from the platform, because the question is whether
    the environment variable arrives - and that depends on the C runtime, not on
    sys.platform. On Windows it does not: locale.getlocale() reads
    ('English_United States', '1252') under LC_ALL=C, tr_TR.UTF-8 and de_DE.UTF-8
    alike, so the arm varies nothing and every run is a second baseline.
    """
    probe = "import locale, sys; print(locale.setlocale(locale.LC_ALL))"
    seen = set()
    for name in ("C", "de_DE.UTF-8"):
        env = dict(os.environ)
        env["LANG"] = env["LC_ALL"] = name
        try:
            proc = subprocess.run(
                [python or sys.executable, "-c", probe],
                capture_output=True, encoding="utf-8", errors="replace",
                timeout=60, check=False, env=env,
            )
        except (subprocess.TimeoutExpired, OSError):
            return False
        seen.add((proc.stdout or "").strip())
    return len(seen) > 1


def timezone_arm(
    repo: Path,
    target: str,
    runs: int,
    timeout: float,
    epoch: float | None,
    python: str = "",
) -> Arm:
    """The same tests in a different timezone each run.

    Separate from the clock arm on purpose. The clock arm moves the DATE; this one
    keeps the instant and moves where you are standing, which is what breaks a
    naive datetime. Varying both at once would leave nothing to attribute to.
    """
    arm = Arm("timezone", "the machine's timezone varied")
    for i in range(runs):
        _tally(
            arm,
            run_once(
                repo, target, hashseed=0, epoch=epoch, timeout=timeout, python=python,
                extra_env={"TZ": TIMEZONES[i % len(TIMEZONES)]},
            ),
        )
    return arm


def locale_arm(
    repo: Path,
    target: str,
    runs: int,
    timeout: float,
    epoch: float | None,
    python: str = "",
) -> Arm:
    """The same tests under a different locale each run."""
    arm = Arm("locale", "the locale varied")
    for i in range(runs):
        name = LOCALES[i % len(LOCALES)]
        _tally(
            arm,
            run_once(
                repo, target, hashseed=0, epoch=epoch, timeout=timeout, python=python,
                extra_env={"LANG": name, "LC_ALL": name},
            ),
        )
    return arm


def xdist_available(repo: Path, python: str = "") -> bool:
    """Is pytest-xdist importable in the interpreter that will run the suite?

    Checked rather than assumed. Without it `-n` is an unrecognised argument,
    pytest exits 4, and every run in the arm scores as unscoreable - which reads
    in the report as an arm that found nothing rather than an arm that never ran.
    """
    try:
        proc = subprocess.run(
            [python or sys.executable, "-c", "import xdist"],
            cwd=repo, capture_output=True, timeout=60, check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return proc.returncode == 0


def parallel_arm(
    repo: Path,
    target: str,
    runs: int,
    timeout: float,
    epoch: float | None,
    python: str = "",
    workers: int = 4,
) -> Arm:
    """The same tests spread across worker processes.

    Catches what no reordering can: two tests that each want the same fixed
    resource - a port, a temp path, a database name, a file in the repo root - and
    got away with it while they ran one after another.

    Note what this arm does NOT control. Under -n, tests are distributed rather
    than merely reordered, so a flip here could in principle be order dependence
    instead. The order arm exists to rule that out: a test that flips in both is
    reported as UNKNOWN rather than credited to parallelism.
    """
    arm = Arm("parallel", f"the suite spread across {workers} worker processes")
    for _ in range(runs):
        _tally(
            arm,
            run_once(
                repo, target, hashseed=0, epoch=epoch, timeout=timeout, python=python,
                extra_args=["-n", str(workers)],
            ),
        )
    return arm
