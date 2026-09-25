"""The three arms the README used to list as missing, and their honest refusals.

An arm that cannot vary what it claims to vary is worse than an absent arm: every
run in it is a second baseline, so it finds nothing and the report reads as though
the question was asked and answered. Two of these three do nothing on Windows, and
one of those would actively misattribute, so each checks itself first.
"""

from __future__ import annotations

import sys

import pytest

from flake_detective.classify import ARM_CAUSE, classify
from flake_detective.run import LOCALES, TIMEZONES, locale_supported, tz_supported
from flake_detective.types import Arm, Cause


def test_every_arm_maps_to_a_cause():
    """An arm with no entry in ARM_CAUSE is silently classified UNKNOWN, so a whole
    perturbation would be measured and then reported as inexplicable."""
    from flake_detective.detective import ALL_ARMS

    for name in ALL_ARMS:
        assert name in ARM_CAUSE, f"arm {name!r} has no cause"


def test_every_cause_has_a_fix():
    from flake_detective.types import FIX

    for cause in Cause:
        assert cause in FIX, f"{cause} has no suggested fix"
        assert FIX[cause].strip()


def test_a_flip_in_the_new_arms_is_attributed_to_them():
    """One arm flips, the rest are stable: the cause is that arm."""
    for arm_name, expected in (
        ("timezone", Cause.TIMEZONE),
        ("locale", Cause.LOCALE),
        ("parallel", Cause.PARALLEL),
    ):
        baseline = Arm("baseline", "d", runs=4, failures={})
        culprit = Arm(arm_name, "d", runs=4, failures={"t::a": 2})
        inv = classify([baseline, culprit], ["t::a"])
        assert len(inv.flakes) == 1, arm_name
        assert inv.flakes[0].cause is expected, f"{arm_name} -> {inv.flakes[0].cause}"


def test_two_arms_flipping_is_still_unknown():
    """Under -n tests are distributed as well as reordered, so a parallel flip could
    be order dependence. Crediting parallelism would send somebody to look for a
    shared port when the problem is leaked state."""
    baseline = Arm("baseline", "d", runs=4, failures={})
    order = Arm("order", "d", runs=4, failures={"t::a": 2})
    parallel = Arm("parallel", "d", runs=4, failures={"t::a": 3})
    inv = classify([baseline, order, parallel], ["t::a"])

    assert inv.flakes[0].cause is Cause.UNKNOWN
    assert "order" in inv.flakes[0].evidence and "parallel" in inv.flakes[0].evidence


def test_the_varied_values_actually_differ():
    """An arm whose values repeat before the runs do is a baseline wearing a label."""
    assert len(set(TIMEZONES)) == len(TIMEZONES) >= 4
    assert len(set(LOCALES)) == len(LOCALES) >= 3


@pytest.mark.skipif(sys.platform != "win32", reason="the measurement below is Windows")
def test_tz_and_locale_report_themselves_unsupported_on_windows():
    """Measured, not assumed. On Windows, TZ=Pacific/Kiritimati and
    TZ=America/New_York produce the SAME local time while TZ=UTC shifts by an hour,
    and LC_ALL does not change locale.getlocale() at all."""
    assert tz_supported() is False
    assert locale_supported() is False


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX has tzset")
def test_tz_is_supported_where_tzset_exists():
    assert tz_supported() is True
