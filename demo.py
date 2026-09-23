"""Show what flake-detective does, in one command, with nothing to set up.

    python demo.py

Runs the built-in benchmark: a set of tests that are flaky for four different
and individually diagnosable reasons - a clock dependency, hash-seed ordering,
unseeded randomness, and test-order dependence. Each one is planted, so the
tool's diagnosis can be checked against the truth rather than merely read.

Pointing a flake detector at a real suite produces a list. It does not tell you
whether the list is right. This does, because the answer is known in advance.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> int:
    print("flake-detective benchmark: four planted flakes, four causes to name.\n", flush=True)
    result = subprocess.run(
        [sys.executable, "-m", "flake_detective.cli", "bench"],
        cwd=ROOT,
        env={**__import__("os").environ, "PYTHONPATH": str(ROOT / "src")},
        check=False,
    )
    if result.returncode != 0:
        return result.returncode
    print("\nEach cause above was planted deliberately, so the diagnosis is", flush=True)
    print("checkable rather than merely plausible.\n", flush=True)
    print("Point it at your own suite with:", flush=True)
    print("    flake-detective investigate <path-to-tests>", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
