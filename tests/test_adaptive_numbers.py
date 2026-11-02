"""Section 2b of docs/RESULTS.md, against `docs/adaptive.json`.

The section's claim is a *negative* one about cost and a positive one about the kind of
answer, and both halves have to hold or it says something else:

  * the sequential arm spent the same runs as the fixed arm at the floor. If a later change
    made it cheaper the section would be understating it, and if it made it dearer the
    section would be wrong. Either way the prose needs rewriting, so the equality is pinned
    rather than described.
  * all three budgets found the same flakes. Without that, a difference in run count is not
    a statement about cost at all - the stopping rule would have changed the search.
  * the two fixed arms report `undecided` as absent, not as zero. That distinction is the
    section's point, and a `0` there would make the fixed budgets look like the stronger
    result while meaning the opposite.
"""

from __future__ import annotations

import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
MEASURED = ROOT / "docs" / "adaptive.json"
RESULTS = ROOT / "docs" / "RESULTS.md"
README = ROOT / "README.md"

PUBLISHED_FLOOR = 59
PUBLISHED_RUNS = {"fixed 7": 28, "fixed 59": 236, "sequential, cap 70": 236}
PUBLISHED_FLAKES = 5
PUBLISHED_ARMS = 4


def _measured() -> dict:
    if not MEASURED.exists():
        pytest.skip("docs/adaptive.json is not present")
    return json.loads(MEASURED.read_text(encoding="utf-8"))


def _rows() -> dict:
    return {row["label"]: row for row in _measured()["rows"]}


def _repo_file(path: pathlib.Path) -> str:
    if not path.is_file():
        pytest.skip(f"{path.name} is not present - running against an installed wheel")
    return path.read_text(encoding="utf-8")


def _section() -> str:
    text = _repo_file(RESULTS)
    start = text.index("### 2b. Spending the runs sequentially instead")
    return " ".join(text[start : text.index("\n## 3. ", start)].split())


# --------------------------------------------------------------------------- the arms


def test_the_three_budgets_are_the_ones_the_section_names():
    assert set(_rows()) == set(PUBLISHED_RUNS)


def test_the_run_counts_are_the_published_ones():
    rows = _rows()
    for label, runs in PUBLISHED_RUNS.items():
        assert rows[label]["runs_total"] == runs, label
        assert len(rows[label]["runs_spent"]) == PUBLISHED_ARMS, label


def test_the_sequential_arm_cost_the_same_as_the_fixed_floor():
    """The negative claim, and the reason the middle arm exists."""
    measured = _measured()
    assert measured["sequential_cost_the_same_as_the_fixed_floor"] is True
    rows = _rows()
    assert rows["sequential, cap 70"]["runs_total"] == rows["fixed 59"]["runs_total"]


def test_the_sequential_arm_stopped_at_the_floor_rather_than_the_cap():
    measured = _measured()
    assert measured["sprt"]["runs_to_call_stable"] == PUBLISHED_FLOOR
    assert measured["sprt"]["cap"] > PUBLISHED_FLOOR
    assert measured["sequential_spent_the_floor_rather_than_the_cap"] is True
    row = _rows()["sequential, cap 70"]
    assert set(row["stopped_early"]) == set(row["runs_spent"])
    assert all(runs == PUBLISHED_FLOOR for runs in row["runs_spent"].values())


def test_all_three_budgets_found_the_same_flakes():
    """Otherwise the run counts are not a statement about cost."""
    measured = _measured()
    assert measured["findings_agree"] is True
    rows = _rows()
    assert len({tuple(row["flakes"]) for row in rows.values()}) == 1
    for label, row in rows.items():
        assert row["flake_count"] == PUBLISHED_FLAKES, label


def test_only_the_sequential_arm_can_report_undecided_tests():
    rows = _rows()
    assert rows["fixed 7"]["undecided_is_measurable"] is False
    assert rows["fixed 59"]["undecided_is_measurable"] is False
    assert rows["sequential, cap 70"]["undecided_is_measurable"] is True
    # Absent, not zero. A zero would read as "it decided everything".
    assert rows["fixed 7"]["undecided_count"] is None
    assert rows["fixed 59"]["undecided_count"] is None
    assert rows["sequential, cap 70"]["undecided_count"] == 0


def test_the_fixed_arms_carry_no_budget_block_and_the_sequential_one_does():
    rows = _rows()
    assert rows["fixed 7"]["budget"] is None
    assert rows["fixed 59"]["budget"] is None
    assert rows["sequential, cap 70"]["budget"]["runs_to_call_stable"] == PUBLISHED_FLOOR


def test_the_bounds_the_fixed_arms_get_are_computed_not_typed():
    bounds = _measured()["fixed_budget_bounds"]
    assert bounds["runs_7"] == pytest.approx(0.349, abs=5e-4)
    assert bounds[f"runs_{PUBLISHED_FLOOR}"] == pytest.approx(0.0495, abs=5e-4)
    assert bounds["runs_for_95_percent_power_against_2_percent"] == 149
    # More runs can only lower the detectable rate; a bound that moved the other way would
    # mean the power calculation had been inverted somewhere.
    assert bounds[f"runs_{PUBLISHED_FLOOR}"] < bounds["runs_7"]


# -------------------------------------------------------------------------- the prose


def test_every_figure_in_the_section_comes_from_the_artefact():
    measured = _measured()
    rows = _rows()
    section = _section()
    for value in (
        str(rows["fixed 7"]["runs_total"]),
        str(rows["fixed 59"]["runs_total"]),
        str(PUBLISHED_FLAKES),
        str(PUBLISHED_FLOOR),
        f"{measured['fixed_budget_bounds']['runs_7'] * 100:.1f}%",
        f"{measured['fixed_budget_bounds'][f'runs_{PUBLISHED_FLOOR}'] * 100:.1f}%",
        f"{measured['sprt']['p0'] * 100:.1f}%",
    ):
        assert value in section, value


def test_the_section_says_the_sequential_budget_is_not_cheaper():
    """The claim most likely to be quietly upgraded by a later edit."""
    section = _section()
    assert "not cheaper" in section
    assert "same 236 runs" in section or "same 236" in section


def test_the_section_explains_that_n_a_is_not_zero():
    section = _section()
    assert "not zero" in section
    assert "reads as stable" in section


def test_the_section_reports_the_failure_mode_of_too_small_a_cap():
    section = _section()
    assert "14 of 16" in section
    assert "40" in section


def test_the_readme_and_the_results_agree_on_the_table():
    readme = _repo_file(README)
    rows = _rows()
    for value in (
        f"| {rows['fixed 7']['runs_total']} |",
        f"| **{rows['sequential, cap 70']['runs_total']}** |",
        "`--until-decided --max-runs 70`",
    ):
        assert value in readme, value
    assert "14 of 16" in readme


def test_the_options_table_documents_the_three_new_flags():
    readme = _repo_file(README)
    for flag in ("`--until-decided`", "`--p0 RATE`", "`--p1 RATE`", "`--max-runs N`"):
        assert flag in readme, flag
