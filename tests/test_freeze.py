"""The clock freeze, exercised in a real subprocess.

Patching a C type's classmethods is the sort of thing that either works or silently does
nothing, so these run the generated plugin for real rather than inspecting its source.
"""

from __future__ import annotations

import os
import subprocess
import sys

from flake_detective import freeze


def run_frozen(snippet: str, epoch: float | None) -> str:
    env = dict(os.environ)
    if epoch is not None:
        env[freeze.ENV_VAR] = repr(epoch)
        env["PYTHONPATH"] = str(freeze.plugin_dir()) + os.pathsep + env.get("PYTHONPATH", "")
        snippet = f"import {freeze.PLUGIN_NAME}\n" + snippet
    else:
        env.pop(freeze.ENV_VAR, None)
    proc = subprocess.run(
        [sys.executable, "-c", snippet], capture_output=True, text=True, env=env, check=True
    )
    return proc.stdout.strip()


def test_time_time_is_pinned():
    out = run_frozen("import time; print(time.time()); print(time.time())", 1_700_000_000.0)
    assert out.splitlines() == ["1700000000.0", "1700000000.0"]


def test_datetime_now_follows_the_freeze():
    out = run_frozen(
        "import datetime; print(datetime.datetime.now().isoformat())", freeze.BASELINE_EPOCH
    )
    assert out.startswith("2024-06-12")


def test_date_today_follows_the_freeze():
    epoch = freeze._at(2023, 11, 14, 9, 30, 0)
    out = run_frozen("import datetime; print(datetime.date.today().isoformat())", epoch)
    assert out == "2023-11-14"


def test_monotonic_is_deliberately_left_alone():
    """Freezing it would hang any suite that waits for elapsed time to pass.

    The elapsed time comes from `time.sleep`, not from a busy loop. The first
    version spun 200,000 empty iterations and asserted the clock had moved,
    which is a race: on a fast machine the loop finishes inside the clock's
    resolution, both readings are identical, and `>` is false. It passed for
    months and then failed - a timing-dependent test inside a tool for finding
    timing-dependent tests.

    A sleep of 50 ms is orders of magnitude above any monotonic resolution, so
    the only way this fails now is if freezing really has pinned the clock.
    """
    out = run_frozen(
        "import time\na = time.monotonic()\ntime.sleep(0.05)\nprint(time.monotonic() - a > 0.01)",
        freeze.BASELINE_EPOCH,
    )
    assert out == "True"


def test_a_duration_measured_with_time_time_reads_as_zero():
    """A real consequence worth stating, not a bug.

    Code timing itself with `time.time()` sees no elapsed time under the freeze. A test
    asserting a duration is *under* a budget still passes; one asserting it is *over*
    something will not, and will be reported as clock-dependent - correctly, since it is.
    """
    out = run_frozen(
        "import time\ns = time.time()\nsum(range(100000))\nprint(time.time() - s)",
        freeze.BASELINE_EPOCH,
    )
    assert float(out) == 0.0


def test_nothing_is_patched_without_the_env_var():
    out = run_frozen("import time; print(time.time() > 1_600_000_000)", None)
    assert out == "True"


def test_the_clock_epochs_are_all_distinct():
    """Two equal instants would make one clock run a duplicate of another."""
    assert len(set(freeze.CLOCK_EPOCHS)) == len(freeze.CLOCK_EPOCHS)


def test_the_clock_epochs_land_on_distinct_local_dates():
    """The boundaries have to be boundaries where the suite under test will see them.

    Named in UTC on a UTC+5 machine, the leap-day and month-boundary instants collapse onto
    the same local day five hours from any edge, and two of the seven runs become duplicates.
    """
    import datetime

    days = {datetime.date.fromtimestamp(e) for e in freeze.CLOCK_EPOCHS}
    assert len(days) == len(freeze.CLOCK_EPOCHS)


def test_the_epochs_span_more_than_one_year():
    import datetime

    years = {datetime.date.fromtimestamp(e).year for e in freeze.CLOCK_EPOCHS}
    assert len(years) >= 3


def test_the_baseline_epoch_is_not_one_of_the_varied_ones():
    assert freeze.BASELINE_EPOCH not in freeze.CLOCK_EPOCHS


def test_second_parity_varies_across_the_clock_epochs():
    """The cheapest clock dependency there is; if the epochs missed it, most would slip by."""
    parities = {int(e) % 2 for e in freeze.CLOCK_EPOCHS}
    assert parities == {0, 1}
