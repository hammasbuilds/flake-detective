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

What each test did is read from pytest's own reports by an injected plugin (see
`observe.py`), never scraped from the terminal. A test counts in a run only if it was
observed to pass or fail there: a test that errored in a fixture is a failure, and a test
that never ran is not a pass.
"""

from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path

from flake_detective import freeze, observe
from flake_detective.types import Arm

# Passed to every pytest invocation, collection included.
#
# `no:randomly` because pytest-randomly, where installed, shuffles the order and reseeds
# `random` on every run - including the baseline. The control would then vary the very
# thing the order arm varies, and every order-dependent test in the suite would be filed
# as nondeterminism. Blocking a plugin that is not installed is a no-op.
#
# The cache plugin stays loaded - `--lf`, `--ff` or `--sw` in a project's addopts would be
# unrecognised options without it - but writes to a throwaway directory, and the observe
# plugin switches those history-driven options off.
_COMMON = ["-p", "no:randomly", "-p", observe.PLUGIN_NAME]

# Past this many characters of node ids, the order is handed over in a file instead of
# on the command line. Windows refuses a command line over 32,767 characters, and a
# shuffled suite of a thousand tests passes that easily; the run would fail to start and
# the whole order arm would score nothing.
MAX_ARGV_CHARS = 20_000

Tick = Callable[[], None]


# --- stopping cleanly on Ctrl-C ------------------------------------------------------------
#
# Every child pytest is registered here while it runs, so an interrupt can kill all of
# them - including the ones started by worker threads, which never see KeyboardInterrupt.
_LIVE: set[subprocess.Popen] = set()
_LIVE_LOCK = threading.Lock()
_STOP = threading.Event()


def stop_all() -> None:
    """Kill every running child and refuse to start new ones until `reset()`."""
    _STOP.set()
    with _LIVE_LOCK:
        procs = list(_LIVE)
    for p in procs:
        try:
            p.kill()
        except OSError:
            pass


def reset() -> None:
    _STOP.clear()


def stopping() -> bool:
    return _STOP.is_set()


def _env(hashseed: int, epoch: float | None) -> dict[str, str]:
    env = dict(os.environ)
    # PYTHONHASHSEED has to be set in the environment: by the time the interpreter is
    # running it is far too late to change how strings hash.
    env["PYTHONHASHSEED"] = str(hashseed)
    # Output is decoded as UTF-8 here, so it has to be written as UTF-8 there. Left to
    # the locale, a Windows console writes cp1252 and a non-ASCII test id comes back as
    # a different string from the one collection returned.
    env["PYTHONIOENCODING"] = "utf-8"
    d = str(freeze.plugin_dir())
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = d + (os.pathsep + existing if existing else "")
    if epoch is not None:
        env[freeze.ENV_VAR] = repr(epoch)
    else:
        env.pop(freeze.ENV_VAR, None)
    for var in (
        observe.ORDER_ENV_VAR,
        observe.RESULTS_ENV_VAR,
        observe.EXACT_ENV_VAR,
        observe.SERIAL_ENV_VAR,
    ):
        env.pop(var, None)
    # Distributed runs only where the parallel arm asks for them.
    env[observe.SERIAL_ENV_VAR] = "1"
    return env


def _tail(text: str, lines: int = 12) -> str:
    kept = [ln for ln in (text or "").splitlines() if ln.strip()]
    return "\n".join(kept[-lines:])


def _spawn(
    cmd: list[str], repo: Path, timeout: float, env: dict[str, str] | None = None
) -> tuple[int | None, str]:
    """Run a command. Returns (returncode, or None if it never finished; its output)."""
    if _STOP.is_set():
        return None, "interrupted"
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=repo,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
    except OSError as e:
        return None, f"could not start {cmd[0]}: {e}"
    with _LIVE_LOCK:
        _LIVE.add(proc)
    deadline = time.monotonic() + timeout
    try:
        while True:
            # Short waits rather than one long one, so the main thread gets back to
            # Python often enough for Ctrl-C to be delivered promptly.
            try:
                out, err = proc.communicate(
                    timeout=min(0.5, max(deadline - time.monotonic(), 0.01))
                )
                break
            except subprocess.TimeoutExpired:
                if _STOP.is_set():
                    proc.kill()
                    proc.communicate()
                    return None, "interrupted"
                if time.monotonic() >= deadline:
                    proc.kill()
                    proc.communicate()
                    return None, (
                        f"timed out after {timeout:g}s (raise --timeout if the suite is slow)"
                    )
    except BaseException:
        # KeyboardInterrupt in the main thread: take the child down with us.
        proc.kill()
        try:
            proc.communicate(timeout=5)
        except (subprocess.TimeoutExpired, OSError, ValueError):
            pass
        raise
    finally:
        with _LIVE_LOCK:
            _LIVE.discard(proc)
    if _STOP.is_set():
        return None, "interrupted"
    return proc.returncode, (out or "") + (err or "")


def pytest_problem(python: str = "", repo: Path | None = None) -> str:
    """Why pytest cannot run in this interpreter, or "" if it can.

    Checked before anything else. Without it, a missing pytest makes every run exit 1
    with "No module named pytest", which scores as a run where nothing failed - so the
    report used to announce no flaky tests, and the benchmark 0 of 4 detected, both
    with exit status 0.
    """
    exe = python or sys.executable
    code, out = _spawn(
        [exe, "-c", "import pytest; print(pytest.__version__)"],
        repo or Path.cwd(),
        timeout=120,
    )
    if code == 0:
        return ""
    reason = _tail(out, 1) or f"exit status {code}"
    shown = f'"{exe}"' if " " in exe else exe
    return (
        f"pytest is not importable by {exe}\n"
        f"  ({reason})\n"
        "\n"
        "flake-detective does not bundle pytest: it runs your suite with the pytest\n"
        "installed in the interpreter your tests use. Either:\n"
        "  - point --python at that interpreter, e.g.\n"
        "      --python .venv/bin/python            (Linux, macOS)\n"
        "      --python .venv\\Scripts\\python.exe    (Windows)\n"
        f"  - or install pytest into this one:  {shown} -m pip install pytest\n"
        f"    (in a uv-made venv without pip:   uv pip install --python {shown} pytest)"
    )


@dataclass
class Observed:
    """What one pytest run was seen to do, per test, from pytest's own reports."""

    outcomes: dict[str, str] = field(default_factory=dict)
    """node id -> passed, failed, error or skipped. Absent means it never ran."""

    rootdir: str = ""
    collected: list[str] = field(default_factory=list)

    @property
    def failed(self) -> set[str]:
        """Failed or errored: both mean the test did not pass."""
        return {t for t, o in self.outcomes.items() if o in ("failed", "error")}

    @property
    def errored(self) -> set[str]:
        return {t for t, o in self.outcomes.items() if o == "error"}

    @property
    def passed(self) -> set[str]:
        return {t for t, o in self.outcomes.items() if o == "passed"}

    @property
    def seen(self) -> set[str]:
        """Tests observed to pass or fail. Skipped and never-run tests are not evidence."""
        return self.failed | self.passed

    def merge(self, other: Observed) -> Observed:
        return Observed({**self.outcomes, **other.outcomes}, self.rootdir or other.rootdir)


def _pytest(
    python: str,
    args: list[str],
    repo: Path,
    timeout: float,
    env: dict[str, str],
) -> tuple[int | None, str, dict | None]:
    """Run pytest with the observe plugin. (returncode, output, the plugin's JSON or None)."""
    fd, results = tempfile.mkstemp(prefix="flake-results-", suffix=".json")
    os.close(fd)
    os.unlink(results)  # absent until the plugin writes it, so a crash is detectable
    cache = tempfile.mkdtemp(prefix="flake-cache-")
    env = {**env, observe.RESULTS_ENV_VAR: results}
    cmd = [python or sys.executable, "-m", "pytest", "-o", f"cache_dir={cache}", *args]
    try:
        code, out = _spawn(cmd, repo, timeout, env=env)
        data = None
        if os.path.exists(results):
            try:
                with open(results, encoding="utf-8") as fh:
                    data = json.load(fh)
            except (OSError, ValueError):
                data = None
        return code, out, data
    finally:
        for p in (results,):
            try:
                os.unlink(p)
            except OSError:
                pass
        shutil.rmtree(cache, ignore_errors=True)


@dataclass
class Collection:
    tests: list[str]
    returncode: int | None
    output: str
    rootdir: str = ""
    """pytest's rootdir. Node ids are relative to it, not to the directory pytest was
    started in - which is why they are made absolute before being passed back."""

    @property
    def error(self) -> str:
        """Why collection did not produce a usable list of tests, or ""."""
        if self.returncode == 0 and self.tests:
            return ""
        if self.returncode is None:
            return "pytest could not collect the suite: " + self.output
        what = {
            0: "pytest found no tests",
            1: "pytest reported failures while collecting",
            2: "pytest hit errors while collecting (an import failed, or a syntax error)",
            3: "pytest was interrupted while collecting",
            4: "pytest rejected the command line (a TARGET that does not exist, or an "
            "option in the project's addopts that needs a plugin not installed here)",
            5: "pytest found no tests",
        }.get(self.returncode, f"pytest exited with status {self.returncode}")
        tail = _tail(self.output)
        if not tail:
            return what
        return what + ":\n" + "\n".join("    " + ln for ln in tail.splitlines())


def collect_detailed(
    repo: Path, target: str = "", timeout: float = 300.0, python: str = ""
) -> Collection:
    """Every test id the suite contains, without running any of them - and if none, why."""
    args = ["--collect-only", "-q", "--no-header", *_COMMON]
    if target:
        args.append(target)
    code, out, data = _pytest(python, args, repo, timeout, _env(0, None))
    tests: list[str] = []
    rootdir = ""
    if data:
        tests = list(data.get("collected") or [])
        rootdir = data.get("rootdir") or ""
    elif code == 0:
        code = -1
        out = "the flake-detective plugin reported nothing:\n" + out
    return Collection(tests, code, out, rootdir)


def collect(repo: Path, target: str = "", timeout: float = 300.0, python: str = "") -> list[str]:
    """Every test id the suite contains. Empty if collection failed for any reason."""
    c = collect_detailed(repo, target, timeout, python)
    return [] if c.error else c.tests


def _as_arg(test_id: str, rootdir: str) -> str:
    """A node id as a command-line argument that means the same thing from any directory.

    Node ids are relative to pytest's rootdir, and pytest resolves arguments relative to
    the directory it runs in. When REPO is a subfolder of the project - `mono/pkg` with
    the pytest.ini in `mono` - the two differ, and every id passed back named a file that
    does not exist: "file or directory not found: pkg/tests/test_m.py::test_a".
    """
    if not rootdir:
        return test_id
    path, sep, rest = test_id.partition("::")
    return os.path.join(rootdir, path) + sep + rest


def run_observed(
    repo: Path,
    target: str = "",
    order: list[str] | None = None,
    hashseed: int = 0,
    epoch: float | None = None,
    timeout: float = 900.0,
    python: str = "",
    extra_env: dict[str, str] | None = None,
    extra_args: list[str] | None = None,
    rootdir: str = "",
) -> tuple[Observed | None, str]:
    """(what each test did, or None if the run could not be scored; and why not)."""
    args = ["-q", "--no-header", "--tb=no", *_COMMON]
    if epoch is not None:
        args += ["-p", freeze.PLUGIN_NAME]
    if rootdir:
        args.append(f"--rootdir={rootdir}")
    if extra_args:
        args += extra_args

    env = {**_env(hashseed, epoch), **(extra_env or {})}
    order_file = None
    if order and sum(len(t) + 1 + len(rootdir) for t in order) > MAX_ARGV_CHARS:
        # Too long for a command line: collect as usual and let the plugin keep exactly
        # these items, in this order. Same tests, same order, no argv limit - and the
        # ids are matched as pytest produces them, so the rootdir does not matter.
        fd, order_file = tempfile.mkstemp(prefix="flake-order-", suffix=".txt")
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(order))
        env[observe.ORDER_ENV_VAR] = order_file
        env[observe.EXACT_ENV_VAR] = "1"
        if target:
            args.append(target)
    elif order:
        args.extend(_as_arg(t, rootdir) for t in order)
    elif target:
        args.append(target)

    try:
        code, out, data = _pytest(python, args, repo, timeout, env)
    finally:
        if order_file:
            try:
                os.unlink(order_file)
            except OSError:
                pass

    if code is None:
        return None, out
    if code == 5:
        return None, "pytest collected nothing (exit status 5)"
    if code not in (0, 1):
        # 2 is an internal error or a collection error, 3 an interrupt, 4 a usage error:
        # a run that fell over is not evidence that every test in it failed.
        return None, f"pytest exited with status {code}:\n{_tail(out)}"
    if data is None:
        return None, f"pytest exited {code} but the flake-detective plugin wrote nothing:\n" + (
            _tail(out)
        )
    return Observed(dict(data.get("outcomes") or {}), data.get("rootdir") or ""), ""


def run_once_detailed(
    repo: Path,
    target: str = "",
    order: list[str] | None = None,
    hashseed: int = 0,
    epoch: float | None = None,
    timeout: float = 900.0,
    python: str = "",
    extra_env: dict[str, str] | None = None,
    extra_args: list[str] | None = None,
    rootdir: str = "",
) -> tuple[set[str] | None, str]:
    """(the failed or errored test ids, or None if the run could not be scored; and why not)."""
    obs, why = run_observed(
        repo, target, order, hashseed, epoch, timeout, python, extra_env, extra_args, rootdir
    )
    return (None if obs is None else obs.failed), why


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
    rootdir: str = "",
) -> set[str] | None:
    """The set of test ids that failed or errored. None if the run could not be scored."""
    return run_once_detailed(
        repo, target, order, hashseed, epoch, timeout, python, extra_env, extra_args, rootdir
    )[0]


def _tally(arm: Arm, obs: Observed) -> None:
    arm.runs += 1
    arm.tracked = True
    for t in obs.seen:
        arm.observed[t] = arm.observed.get(t, 0) + 1
    for t in obs.failed:
        arm.failures[t] = arm.failures.get(t, 0) + 1
    for t in obs.errored:
        arm.errors[t] = arm.errors.get(t, 0) + 1


def execute(
    arm: Arm,
    runs: list[list[dict]],
    jobs: int = 1,
    tick: Tick | None = None,
) -> Arm:
    """Score an arm. Each run is one or more pytest calls whose observations are merged.

    Almost every run is a single pytest invocation; an isolation pass is one per test.
    The invocations are independent processes, so with `jobs` above one they go
    through a pool - which is sound only for a suite whose tests do not share a fixed
    file, port or database across processes. That is the caller's call, and it is off
    by default.

    Ctrl-C stops the arm: running children are killed, and the runs that had already
    finished are still scored, with `arm.interrupted` set.
    """
    units = [(i, kw) for i, calls in enumerate(runs) for kw in calls]
    results: dict[int, list[Observed | None]] = {i: [] for i in range(len(runs))}

    def one(unit: tuple[int, dict]) -> tuple[int, tuple[Observed | None, str]]:
        i, kw = unit
        return i, run_observed(**kw)

    casualties: set[int] = set()
    casualty_why: list[str] = []

    def record(i: int, res: tuple[Observed | None, str]) -> None:
        obs, why = res
        if stopping():
            return
        if obs is None and ("KeyboardInterrupt" in why or why == "interrupted"):
            # On a console Ctrl-C reaches the child pytest too, and it can exit a moment
            # before this process notices. That run is a casualty of the interrupt, not
            # a run that could not be scored; it is dropped if the interrupt follows.
            casualties.add(i)
            casualty_why.append(why)
        elif obs is None and why and not arm.error:
            arm.error = why
        results[i].append(obs)
        if tick:
            tick()

    try:
        if jobs <= 1 or len(units) <= 1:
            for u in units:
                record(*one(u))
        else:
            pool = ThreadPoolExecutor(max_workers=jobs)
            try:
                pending = {pool.submit(one, u) for u in units}
                while pending:
                    # Polled, not a blocking wait: on Windows an untimed wait on a lock
                    # does not return for Ctrl-C.
                    done, pending = wait(pending, timeout=0.5, return_when=FIRST_COMPLETED)
                    for fut in done:
                        record(*fut.result())
            except BaseException:
                stop_all()
                raise
            finally:
                pool.shutdown(wait=True, cancel_futures=True)
    except KeyboardInterrupt:
        stop_all()
        arm.interrupted = True

    finished = [i for i in range(len(runs)) if len(results[i]) == len(runs[i])]
    if arm.interrupted:
        finished = [i for i in finished if i not in casualties]
    elif casualty_why and not arm.error:
        # Not ours: a test in the suite raised KeyboardInterrupt itself.
        arm.error = casualty_why[0]
    arm.attempted += len(runs) if not arm.interrupted else len(finished)
    for i in finished:
        scored = [r for r in results[i] if r is not None]
        # One unscoreable test does not invalidate an isolation pass, but a pass where
        # nothing could be scored is not evidence and must not count as a run.
        if scored:
            merged = scored[0]
            for r in scored[1:]:
                merged = merged.merge(r)
            _tally(arm, merged)
    return arm


def _call(repo: Path, target: str, timeout: float, python: str, **kw) -> list[dict]:
    return [dict(repo=repo, target=target, timeout=timeout, python=python, **kw)]


def baseline_arm(
    repo: Path,
    target: str,
    runs: int,
    timeout: float,
    epoch: float | None,
    python: str = "",
    jobs: int = 1,
    tick: Tick | None = None,
) -> Arm:
    """Identical conditions, repeated. Anything that flips here is nondeterministic."""
    arm = Arm("baseline", "identical conditions, repeated")
    plan = [_call(repo, target, timeout, python, hashseed=0, epoch=epoch) for _ in range(runs)]
    return execute(arm, plan, jobs, tick)


def order_arm(
    repo: Path,
    target: str,
    tests: list[str],
    runs: int,
    timeout: float,
    epoch: float | None,
    seed: int = 0,
    python: str = "",
    jobs: int = 1,
    tick: Tick | None = None,
    rootdir: str = "",
) -> Arm:
    """The same tests, shuffled. Flips here mean one test's result depends on another's.

    `rootdir` is pytest's rootdir from collection; node ids are relative to it."""
    arm = Arm("order", "the same tests, shuffled")
    rnd = random.Random(seed)
    plan = []
    for _ in range(runs):
        shuffled = list(tests)
        rnd.shuffle(shuffled)
        plan.append(
            _call(
                repo,
                target,
                timeout,
                python,
                order=shuffled,
                hashseed=0,
                epoch=epoch,
                rootdir=rootdir,
            )
        )
    return execute(arm, plan, jobs, tick)


def hashseed_arm(
    repo: Path,
    target: str,
    runs: int,
    timeout: float,
    epoch: float | None,
    python: str = "",
    jobs: int = 1,
    tick: Tick | None = None,
) -> Arm:
    """A different PYTHONHASHSEED each run, everything else identical."""
    arm = Arm("hashseed", "PYTHONHASHSEED varied")
    plan = [_call(repo, target, timeout, python, hashseed=i + 1, epoch=epoch) for i in range(runs)]
    return execute(arm, plan, jobs, tick)


def clock_arm(
    repo: Path,
    target: str,
    runs: int,
    timeout: float,
    python: str = "",
    jobs: int = 1,
    tick: Tick | None = None,
) -> Arm:
    """The wall clock frozen at a different instant each run.

    Not "wait a second and run again" - that was the first attempt, and it could not
    distinguish a clock-dependent test from a nondeterministic one, because the baseline
    also takes time to run. Freezing makes the date a controlled variable like any other.
    """
    arm = Arm("clock", "the wall clock was frozen at a different date each run")
    plan = [
        _call(
            repo,
            target,
            timeout,
            python,
            hashseed=0,
            epoch=freeze.CLOCK_EPOCHS[i % len(freeze.CLOCK_EPOCHS)],
        )
        for i in range(runs)
    ]
    return execute(arm, plan, jobs, tick)


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
            [
                python or sys.executable,
                "-c",
                "import time, sys; sys.exit(0 if hasattr(time, 'tzset') else 1)",
            ],
            capture_output=True,
            timeout=60,
            check=False,
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
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                check=False,
                env=env,
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
    jobs: int = 1,
    tick: Tick | None = None,
) -> Arm:
    """The same tests in a different timezone each run.

    Separate from the clock arm on purpose. The clock arm moves the DATE; this one
    keeps the instant and moves where you are standing, which is what breaks a
    naive datetime. Varying both at once would leave nothing to attribute to.
    """
    arm = Arm("timezone", "the machine's timezone varied")
    plan = [
        _call(
            repo,
            target,
            timeout,
            python,
            hashseed=0,
            epoch=epoch,
            extra_env={"TZ": TIMEZONES[i % len(TIMEZONES)]},
        )
        for i in range(runs)
    ]
    return execute(arm, plan, jobs, tick)


def locale_arm(
    repo: Path,
    target: str,
    runs: int,
    timeout: float,
    epoch: float | None,
    python: str = "",
    jobs: int = 1,
    tick: Tick | None = None,
) -> Arm:
    """The same tests under a different locale each run."""
    arm = Arm("locale", "the locale varied")
    plan = []
    for i in range(runs):
        name = LOCALES[i % len(LOCALES)]
        plan.append(
            _call(
                repo,
                target,
                timeout,
                python,
                hashseed=0,
                epoch=epoch,
                extra_env={"LANG": name, "LC_ALL": name},
            )
        )
    return execute(arm, plan, jobs, tick)


def xdist_available(repo: Path, python: str = "") -> bool:
    """Is pytest-xdist importable in the interpreter that will run the suite?

    Checked rather than assumed. Without it `-n` is an unrecognised argument,
    pytest exits 4, and every run in the arm scores as unscoreable - which reads
    in the report as an arm that found nothing rather than an arm that never ran.
    """
    try:
        proc = subprocess.run(
            [python or sys.executable, "-c", "import xdist"],
            cwd=repo,
            capture_output=True,
            timeout=60,
            check=False,
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
    jobs: int = 1,
    tick: Tick | None = None,
) -> Arm:
    """The same tests spread across worker processes.

    Catches what no reordering can: two tests that each want the same fixed
    resource - a port, a temp path, a database name, a file in the repo root - and
    got away with it while they ran one after another.

    Note what this arm does NOT control. Under -n, tests are distributed rather
    than merely reordered, so a flip here could in principle be order dependence
    instead. The classifier reads it that way: a test the order or isolation arm also
    implicates is reported as an order dependence, with this arm as corroboration, and
    only a flip seen here alone is credited to parallelism.
    """
    arm = Arm("parallel", f"the suite was spread across {workers} worker processes")
    plan = [
        _call(
            repo,
            target,
            timeout,
            python,
            hashseed=0,
            epoch=epoch,
            extra_args=["-n", str(workers)],
            extra_env={observe.SERIAL_ENV_VAR: "0"},
        )
        for _ in range(runs)
    ]
    return execute(arm, plan, jobs, tick)


def isolation_arm(
    repo: Path,
    target: str,
    runs: int,
    timeout: float,
    epoch: float | None,
    python: str = "",
    tests: list[str] | None = None,
    jobs: int = 1,
    tick: Tick | None = None,
    rootdir: str = "",
) -> Arm:
    """Every test run ALONE, in its own process.

    The order arm shuffles the suite, which finds a test that breaks when its
    neighbour runs first. It cannot find the opposite and equally real failure: a test
    that only passes *because* of what ran before it. Such a test is green in every
    ordering and red the moment somebody runs it on its own - `pytest path::name`,
    which is what everybody does while debugging something else.

    One "run" of this arm is one pass over the whole suite, one process per test, so a
    pass costs n invocations rather than one. That is the reason it is opt-in and the
    reason it is honest to say so: on a forty-test suite at three runs it is a hundred
    and twenty pytest starts.

    Read it against the baseline:

        passes in the suite, fails alone   it depends on state another test creates
        fails in the suite, passes alone   another test is leaking state into it
                                           (the order arm sees this one too)
    """
    arm = Arm("isolation", "each test was run alone in its own process")
    if not tests:
        return arm
    plan = [
        [
            _call(
                repo, target, timeout, python, order=[t], hashseed=0, epoch=epoch, rootdir=rootdir
            )[0]
            for t in tests
        ]
        for _ in range(runs)
    ]
    return execute(arm, plan, jobs, tick)
