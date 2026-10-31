"""The README's census figures are read from docs/census-summary.json, and must add up.

Before this file the thousand-package census was an artefact no test opened and no
document quoted: the measurement finished and published nothing, and nothing would have
noticed if the next run moved every number.

Four things are checked, because each has already been wrong once here or in a sibling
repository:

- the figures in the README are the ones in the summary;
- the exclusion table's rows sum to the number of packages considered, so a reason cannot
  be added to the census and left out of the table;
- every rate is reported over the arms that ran, not the packages scored - eight packages
  collected tests and ran none, and averaging their four misses into each cause published
  94.9% for order where its measured arms give 99.4%;
- a cause that measured nothing has no rate, rather than 0%.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
SUMMARY = ROOT / "docs" / "census-summary.json"

pytestmark = pytest.mark.skipif(
    not (README.exists() and SUMMARY.exists()),
    reason="the README or the census summary is absent from this checkout",
)


def _summary() -> dict:
    return json.loads(SUMMARY.read_text(encoding="utf-8"))


def _readme() -> str:
    return README.read_text(encoding="utf-8")


def test_the_exclusion_table_accounts_for_every_package() -> None:
    r"""The rows must sum to `considered`, with the scored row included.

    The sibling check in suite-auditor failed on its first run because its pattern could
    not see the bold **110** row and summed the exclusions alone - the one row the table
    exists to show was the one row the check could not see. Hence `\**` on both sides.
    """
    s = _summary()
    table = _readme().split("| | packages |")[1].split("\n\n")[0]
    found = [
        int(m.replace(",", ""))
        for line in table.splitlines()
        for m in re.findall(r"\*{0,2}([\d,]+)\*{0,2}\s*\|\s*$", line)
    ]
    assert found, "the census exclusion table has no numeric rows"
    considered = s["considered"]
    assert considered in found, f"the table does not state the {considered} considered"
    rows = [n for n in found if n != considered]
    assert sum(rows) == considered, (
        f"the table's rows sum to {sum(rows)}, not the {considered} packages considered - "
        f"an exclusion reason is in the census and missing from the table, or counted twice"
    )
    assert s["scored"] in rows, "the scored row is not in the table"
    assert sum(s["exclusions"].values()) == s["excluded"]
    assert s["scored"] + s["excluded"] == considered


def test_the_readme_quotes_the_summarys_per_cause_figures() -> None:
    s = _summary()
    text = _readme()
    for name, c in s["causes"].items():
        row = [line for line in text.splitlines() if line.startswith(f"| {name} |")]
        assert row, f"the per-cause table has no row for {name}"
        numbers = re.findall(r"([\d,]+)\s*(?:\(([\d.]+)%\))?", row[0])
        stated = [int(n.replace(",", "")) for n, _ in numbers if n]
        assert c["measured"] in stated, (
            f"{name}: the README does not state measured={c['measured']}"
        )
        assert c["found"] in stated, f"{name}: the README does not state found={c['found']}"
        assert c["cause_right"] in stated, (
            f"{name}: the README does not state cause_right={c['cause_right']}"
        )


def test_every_rate_is_over_the_arms_that_ran() -> None:
    s = _summary()
    for name, c in s["causes"].items():
        assert c["measured"] + c["unmeasured"] == c["arms"]
        assert c["found"] <= c["measured"], (
            f"{name}: more detections than arms that ran - a detection was counted on an "
            f"arm that scored zero runs, which is how rfc3987-syntax reported a hit with "
            f"no run behind it"
        )
        assert c["cause_right"] <= c["found"]
        if c["measured"]:
            assert c["found_share"] == round(c["found"] / c["measured"], 4)
            assert c["cause_right_share"] == round(c["cause_right"] / c["measured"], 4)
        else:
            assert c["found_share"] is None, (
                f"{name} measured no arms, so it has no rate; 0% would read as "
                f"'this cause is never detected'"
            )
    assert s["measured_arms"] == sum(c["measured"] for c in s["causes"].values())


def test_the_collateral_figures_are_consistent() -> None:
    """The headline total is dominated by two packages, so the median must be published."""
    s = _summary()
    assert s["packages_collateral_in_every_arm"] <= s["packages_with_collateral"] <= s["scored"]
    assert s["arms_with_collateral"] <= s["measured_arms"]
    if s["packages_with_collateral"]:
        assert s["collateral_per_affected_arm_median"] is not None, (
            "collateral_tests alone cannot be published: wcmatch and platformdirs carry "
            "most of it, so a mean describes neither them nor the median suite"
        )
        assert s["largest_collateral_packages"], "the outliers must be named beside the total"


def test_the_summary_is_the_reduction_of_a_census_that_exists() -> None:
    """The summary must be derivable, not hand-written.

    docs/third-party-census.json is gitignored, so this runs only where it is present -
    but where it is, the committed summary must equal what the script produces from it.
    """
    census = ROOT / "docs" / "third-party-census.json"
    if not census.exists():
        pytest.skip("the raw census is not present (it is gitignored)")
    import sys

    sys.path.insert(0, str(ROOT / "scripts"))
    import census_summary

    rows = json.loads(census.read_text(encoding="utf-8"))
    assert census_summary.summarise(rows) == _summary(), (
        "docs/census-summary.json is not what scripts/census_summary.py produces from "
        "docs/third-party-census.json - regenerate it"
    )


def test_the_three_kinds_of_shortfall_are_separated() -> None:
    """`cause_right` over `measured` mixes "not detected" with "detected and misnamed".

    A README sentence written from the difference 667 - 651 called all 16 of them wrong
    causes. They are 2 plants never detected, 13 reported as `unknown` - seen to flip,
    cause declined - and exactly 1 wrong cause, a nondeterminism plant reported as
    `clock`. The three say different things about the tool, and the worst of them is the
    rarest, so publishing only the total understated it.
    """
    s = _summary()
    assert (
        s["cause_right"] + s["cause_unknown"] + s["cause_wrong"] + s["not_detected"]
        == s["measured_arms"]
    ), "the four outcomes must account for every arm that ran"
    assert s["found"] == s["measured_arms"] - s["not_detected"]
    assert s["cause_right"] + s["cause_unknown"] + s["cause_wrong"] == s["found"]
    assert len(s["wrong_causes"]) == len(
        {(w["planted"], w["reported"]) for w in s["wrong_causes"]}
    ), "a planted/reported pair is listed twice"
    assert sum(w["arms"] for w in s["wrong_causes"]) == s["cause_wrong"]
    text = _readme()
    for figure in (
        f"| detected and named correctly | {s['cause_right']} |",
        f"| detected, cause reported `unknown` | {s['cause_unknown']} |",
        f"| not detected at all | {s['not_detected']} |",
    ):
        assert figure in text, f"the README does not state {figure!r}"
    assert f"**{s['cause_wrong']}**" in text
    for cohort in s["causes"].values():
        assert (
            cohort["cause_right"]
            + cohort["cause_unknown"]
            + cohort["cause_wrong"]
            + cohort["not_detected"]
            == cohort["measured"]
        )
