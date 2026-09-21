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

_SUMMARY = re.compile(r"^(?:FAILED|ERROR)\s+(\S+)", re.M)


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
    cmd.extend(order if order else ([target] if target else []))

    try:
        proc = subprocess.run(
            cmd,
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_env(hashseed, epoch),
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
