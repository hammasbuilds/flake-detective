"""Plant known flakes into many externally-chosen third-party suites.

    python scripts/third_party_census.py --packages 200 [--out census.json]

docs/RESULTS.md scores plants in eight real suites. Six of the eight are our own
projects (assay-drift, blast-radius, pr-referee, repo-surgeon, suite-auditor,
trace-to-patch), and the other two - toolz and sqlparse - were picked by hand. The
objection writes itself: a detector tuned on code its author also wrote is not shown
to work on code its author has never seen.

This script fixes the sample the same way the suite-auditor census does:

  * The candidate list is **external** - top packages by download count, from
    hugovk/top-pypi-packages.
  * Inclusion is decided by **stated, outcome-blind gates**, all measured before a
    single flake is planted:
      1. the sdist ships a test directory pytest recognises,
      2. the project and its declared test dependencies install,
      3. the clean suite passes and collects a non-zero number of tests,
      4. the clean suite finishes inside --max-clean-seconds.
  * Gate 4 is the one that needs stating plainly. An investigation runs the suite
    **28 times** (four arms, seven runs each), so a suite taking two minutes clean
    takes about an hour to investigate. Capping clean time is what makes a census of
    hundreds possible at all. It biases the sample toward fast suites, it is applied
    before any planting so it cannot depend on a result, and the cap and the number
    excluded by it are reported beside the score.
  * Every exclusion is **recorded with its reason and counted**.

The score itself comes from scripts/inject_and_score.py, unchanged. This script only
decides, in the open, which suites it is run against.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOP = ("https://raw.githubusercontent.com/hugovk/top-pypi-packages/main/"
       "top-pypi-packages.min.json")
TEST_FILE = re.compile(r"^[^/]+/(?:src/)?(tests?)/(?:.*/)?test_[^/]*\.py$")


def fetch_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=60) as response:
        return json.loads(response.read().decode())


def top_packages(count: int) -> list[str]:
    data = fetch_json(TOP)
    rows = data["rows"] if isinstance(data, dict) else data
    return [r["project"] for r in rows[:count]]


def newest_sdist(package: str) -> tuple[str, str] | None:
    try:
        data = fetch_json(f"https://pypi.org/pypi/{package}/json")
    except Exception:  # noqa: BLE001
        return None
    version = data["info"]["version"]
    for f in data["releases"].get(version, []):
        if f["packagetype"] == "sdist" and f["filename"].endswith(".tar.gz"):
            return version, f["url"]
    return None


def unpack(url: str, into: Path) -> Path | None:
    try:
        with urllib.request.urlopen(url, timeout=180) as response:
            blob = response.read()
        with tarfile.open(fileobj=io.BytesIO(blob)) as tf:
            if not any(TEST_FILE.match(n) for n in tf.getnames()):
                return None
            tf.extractall(into, filter="data")
    except Exception:  # noqa: BLE001
        return None
    roots = [p for p in into.iterdir() if p.is_dir()]
    return roots[0] if len(roots) == 1 else None


def test_dir(root: Path) -> Path | None:
    for parent in (root, root / "src"):
        for name in ("tests", "test"):
            candidate = parent / name
            if candidate.is_dir() and any(candidate.rglob("test_*.py")):
                return candidate
    return None


TEST_GROUP = re.compile(r"test|dev|check|^ci$", re.I)
NOT_TEST_GROUP = re.compile(r"doc|bench|emscripten|lint|release|min|publish|typing", re.I)


def declared_groups(root: Path) -> list[str]:
    """Group names a project really declares, in a sensible install order.

    Read from pyproject.toml rather than guessed, because `uv pip install --group`
    returns 0 for a group that does not exist, so a return code cannot tell the
    difference between installed and absent.
    """
    pyproject = root / "pyproject.toml"
    if not pyproject.exists():
        return []
    try:
        import tomllib
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    names = list(data.get("dependency-groups", {}))
    wanted = [n for n in names if TEST_GROUP.search(n) and not NOT_TEST_GROUP.search(n)]
    # `test` before `dev` so the lighter set lands first, then anything it missed.
    return sorted(wanted, key=lambda n: (0 if n.lower().startswith("test") else 1, n))


def install_test_deps(root: Path, py: Path, pip) -> list[str]:
    """Install every test-ish group and extra. Returns what was actually applied."""
    applied = []
    for group in declared_groups(root):
        done = subprocess.run(["uv", "pip", "install", "-q", "--python", str(py),
                               "--group", group], cwd=root,
                              capture_output=True, text=True, timeout=900)
        if done.returncode == 0 and "was not found" not in (done.stderr or ""):
            applied.append(f"group:{group}")
    if not applied:
        for extra in ("test", "tests", "testing", "dev"):
            if pip(f"{root}[{extra}]").returncode == 0:
                applied.append(f"extra:{extra}")
                break
    for req in ("test-requirements.txt", "requirements-test.txt", "dev-requirements.txt",
                "requirements-dev.txt", "requirements/test.txt", "requirements/dev.txt"):
        path = root / req
        if path.exists() and pip("-r", str(path)).returncode == 0:
            applied.append(req)
            break
    return applied


def prepare(root: Path, package: str, version: str, tests: Path,
            cap: float) -> tuple[Path | None, str, float]:
    """A venv whose clean suite passes quickly, or the reason it does not qualify."""
    venv = root.parent / "venv"
    if subprocess.run(["uv", "venv", str(venv), "-q"], capture_output=True).returncode:
        return None, "could not create a venv", 0.0
    py = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")

    def pip(*spec: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(["uv", "pip", "install", "-q", "--python", str(py), *spec],
                              cwd=cwd, capture_output=True, text=True, timeout=900)

    base = pip(str(root), "pytest")
    if base.returncode:
        base = pip(f"{package}=={version}", "pytest")
    if base.returncode:
        last = (base.stderr or "").strip().splitlines()
        return None, f"install failed: {last[-1][:110] if last else '?'}", 0.0

    install_test_deps(root, py, pip)

    t0 = time.time()
    try:
        clean = subprocess.run(
            [str(py), "-m", "pytest", str(tests), "-q", "--no-header",
             "-p", "no:cacheprovider"],
            cwd=root, capture_output=True, text=True, timeout=max(cap * 1.5, 60))
    except subprocess.TimeoutExpired:
        limit = max(cap * 1.5, 60)
        return None, f"clean suite exceeded {limit:.0f}s, over the {cap:.0f}s cap", limit
    seconds = time.time() - t0
    text = clean.stdout + clean.stderr
    collected = max((int(m.group(1)) for m in re.finditer(r"(\d+) (?:passed|collected)", text)),
                    default=0)
    if clean.returncode != 0:
        tail = [line for line in text.strip().splitlines() if line.strip()]
        return None, f"clean suite does not pass: {tail[-1][:100] if tail else '?'}", seconds
    if collected == 0:
        return None, "clean suite passes but collects no tests", seconds
    if seconds > cap:
        return None, f"clean suite takes {seconds:.0f}s, over the {cap:.0f}s cap", seconds
    return py, f"{collected} tests in {seconds:.1f}s", seconds


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--packages", type=int, default=200)
    ap.add_argument("--max-clean-seconds", type=float, default=25.0,
                    help="a suite slower than this clean is excluded, because an "
                         "investigation runs it 28 times (25)")
    ap.add_argument("--runs", type=int, default=7)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--budget", type=int, default=1800)
    ap.add_argument("--out", default="third-party-census.json")
    args = ap.parse_args()

    names = top_packages(args.packages)
    print(f"{len(names)} packages from the external top-downloads list")
    print(f"clean-suite cap: {args.max_clean_seconds}s (an investigation runs it "
          f"{4 * args.runs} times)\n")
    out, results, started = Path(args.out), [], time.time()

    for i, name in enumerate(names, 1):
        row: dict = {"package": name}
        sd = newest_sdist(name)
        if sd is None:
            row["excluded"] = "no sdist published"
        else:
            row["version"], url = sd
            with tempfile.TemporaryDirectory(prefix="fd-census-", ignore_cleanup_errors=True) as tmp:
                root = unpack(url, Path(tmp))
                if root is None:
                    row["excluded"] = "sdist ships no test directory"
                elif (tests := test_dir(root)) is None:
                    row["excluded"] = "no test_*.py under tests/"
                else:
                    py, why, seconds = prepare(root, name, row["version"], tests,
                                               args.max_clean_seconds)
                    row["clean_run"] = why
                    row["clean_seconds"] = round(seconds, 1)
                    if py is None:
                        row["excluded"] = why
                    else:
                        report = Path(tmp) / "score.json"
                        cmd = [sys.executable, str(HERE / "inject_and_score.py"), str(root),
                               "--runs", str(args.runs), "--jobs", str(args.jobs),
                               "--python", str(py), "--json", str(report)]
                        try:
                            subprocess.run(cmd, capture_output=True, text=True,
                                           timeout=args.budget)
                            if report.exists():
                                row["score"] = json.loads(report.read_text(encoding="utf-8"))
                            else:
                                row["excluded"] = "scorer produced no report"
                        except subprocess.TimeoutExpired:
                            row["excluded"] = f"budget exceeded ({args.budget}s)"
        results.append(row)
        state = row.get("excluded", "scored")
        rate = (time.time() - started) / i
        print(f"  [{i}/{len(names)}] {name:26} {state[:44]:44} "
              f"{rate:.0f}s/pkg ~{rate * (len(names) - i) / 60:.0f}min left", flush=True)
        out.write_text(json.dumps(results, indent=1), encoding="utf-8")

    scored = [r for r in results if "score" in r]
    slow = [r for r in results if "over the" in str(r.get("excluded", ""))]
    print(f"\n{len(scored)} of {len(results)} suites scored; {len(results) - len(scored)} "
          f"excluded, of which {len(slow)} for exceeding the clean-time cap")
    print(f"written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
