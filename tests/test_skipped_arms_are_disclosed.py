"""A cause whose arm never ran must be named, not silently left out."""

from __future__ import annotations

import json

from flake_detective.report import as_json, text
from flake_detective.types import Arm, Investigation


def _investigation() -> Investigation:
    inv = Investigation(arms=[Arm(name="baseline", description="2 identical runs", attempted=2, runs=2)], total_tests=3)
    inv.skipped_arms = [
        ("timezone", "TZ does not move local time on this platform"),
        ("locale", "LANG and LC_ALL do not reach the locale on this platform"),
    ]
    return inv


def test_the_json_records_arms_that_never_ran() -> None:
    """`--arms all` produced a JSON naming five arms and neither timezone nor locale.

    The skip was announced on the progress stream only, so `--quiet` hid it and the
    durable record had no trace - the words "timezone" and "locale" appeared nowhere.
    Anything built on that file reads five arms as a completed search, and a
    locale-dependent flaky test stays invisible with nothing saying its cause was never
    looked for.
    """
    payload = as_json(_investigation())
    assert "skipped_arms" in payload
    names = [entry["name"] for entry in payload["skipped_arms"]]
    assert names == ["timezone", "locale"]
    for entry in payload["skipped_arms"]:
        assert entry["reason"], "a skipped arm must say why"
    # The whole point is that the words are now findable in the durable output.
    dumped = json.dumps(payload)
    assert "timezone" in dumped
    assert "locale" in dumped


def test_the_text_report_says_which_causes_were_not_searched() -> None:
    """It has to appear in the report body, not only in the progress output."""
    rendered = text(_investigation())
    assert "not searched" in rendered
    assert "timezone" in rendered
    assert "locale" in rendered
    assert "TZ does not move local time" in rendered


def test_nothing_is_added_when_every_arm_ran() -> None:
    """No skips means no extra section - the usual report must stay unchanged."""
    inv = Investigation(arms=[Arm(name="baseline", description="2 identical runs", attempted=2, runs=2)], total_tests=3)
    assert as_json(inv)["skipped_arms"] == []
    assert "not searched" not in text(inv)
