"""DOC-10: the manual test guide and its results page carry the same checks, in order.

The two documents are read as files: a new check is a heading in `docs/MANUAL-TEST.md` and a
row in `docs/MANUAL-TEST-RESULTS.md`, and the core path is the `[core]` headings there and the
`yes` cells here. A check added to one page only, or a core count that moves, fails these tests.
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[3]
GUIDE = ROOT / "docs" / "MANUAL-TEST.md"
RESULTS = ROOT / "docs" / "MANUAL-TEST-RESULTS.md"

_HEADING = re.compile(r"(?m)^#### MT-(\d+)[^\n]*$")
_ROW = re.compile(r"(?m)^\| MT-(\d+) \|([^|]*)\|")
_CORE_LIST = re.compile(r"\*\*Core \(production path\)\*\*, 42 checks: ([^.\n]+)\.")


def _guide_headings() -> list[tuple[str, str]]:
    return [(match.group(1), match.group(0)) for match in _HEADING.finditer(
        GUIDE.read_text(encoding="utf-8")
    )]


def _result_rows() -> list[tuple[str, str]]:
    return [(match.group(1), match.group(2).strip()) for match in _ROW.finditer(
        RESULTS.read_text(encoding="utf-8")
    )]


def test_the_guide_and_the_results_page_list_the_same_checks_in_the_same_order() -> None:
    guide = [check_id for check_id, _heading in _guide_headings()]
    results = [check_id for check_id, _core in _result_rows()]
    assert len(guide) == len(set(guide)), "a guide heading repeats an id"
    assert len(results) == len(set(results)), "a results row repeats an id"
    assert results == guide


def test_the_core_checks_are_the_same_42_on_both_pages() -> None:
    guide_core = [
        check_id for check_id, heading in _guide_headings() if "[core]" in heading
    ]
    results_core = [
        check_id for check_id, core in _result_rows() if core == "yes"
    ]
    assert len(guide_core) == 42
    assert results_core == guide_core


def test_the_core_summary_line_lists_exactly_the_core_checks() -> None:
    match = _CORE_LIST.search(RESULTS.read_text(encoding="utf-8"))
    assert match is not None, "the results page has no core summary line"
    listed = [entry.removeprefix("MT-") for entry in re.findall(r"MT-\d+", match.group(1))]
    assert len(listed) == 42
    assert set(listed) == {check_id for check_id, core in _result_rows() if core == "yes"}


def test_every_core_cell_is_yes_or_empty() -> None:
    cells = {core for _check_id, core in _result_rows()}
    assert cells <= {"yes", ""}


def test_mt_143_is_in_both_pages_and_not_core() -> None:
    guide = GUIDE.read_text(encoding="utf-8")
    sign_in = guide[guide.index("### 3. Sign-in") : guide.index("### 4. Language")]
    assert "#### MT-143 — A request right after sign-in is accepted (FIX-23)" in sign_in
    assert "[core]" not in sign_in[sign_in.index("#### MT-143") :].split("\n", 1)[0]
    results = dict(_result_rows())
    assert results["143"] == ""


_NEW_IN_0115 = [str(number) for number in range(144, 159)]


def test_the_fifteen_0115_checks_are_in_both_pages_and_only_two_are_core() -> None:
    """REL-15f: MT-144 to MT-158 cover the 0.1.15 changes; two of them are core."""
    guide = dict(_guide_headings())
    results = dict(_result_rows())
    for check_id in _NEW_IN_0115:
        assert check_id in guide, f"MT-{check_id} is not in the guide"
        assert check_id in results, f"MT-{check_id} is not on the results page"
    core = {check_id for check_id in _NEW_IN_0115 if "[core]" in guide[check_id]}
    assert core == {"144", "145"}
    assert {check_id for check_id in _NEW_IN_0115 if results[check_id] == "yes"} == core
    for check_id in ("146", "155", "157"):
        assert "lab only" in guide[check_id] and "(writes)" in guide[check_id]


def test_the_guide_is_the_0115_to_0116_guide() -> None:
    """REL-16a: the guide is retargeted to 0.1.15 -> 0.1.16 (migrations 0061 to 0063)."""
    guide = GUIDE.read_text(encoding="utf-8")
    assert "### 2. Upgrade 0.1.15 → 0.1.16" in guide
    assert "This section upgrades a 0.1.15 site to 0.1.16" in guide
    assert "`Upgrade complete: 0.1.15 -> 0.1.16.`" in guide
    for pair in ("0060 -> 0061", "0061 -> 0062", "0062 -> 0063"):
        assert f"Running upgrade {pair}" in guide
    assert "Running upgrade 0059 -> 0060" not in guide
    assert "a `Running upgrade` line here is a Fail" not in guide
    assert "git checkout v0.1.14" not in guide
    results = RESULTS.read_text(encoding="utf-8")
    assert "## 2. Upgrade 0.1.15 → 0.1.16" in results
    assert "Migration head after (`0063`)" in results


_NEW_IN_0116 = [str(number) for number in range(159, 172)]


def test_the_thirteen_0116_checks_are_in_both_pages_and_two_are_core() -> None:
    """REL-16a: MT-159 to MT-171 cover the 0.1.16 changes; the core grew from 40 to 42."""
    guide = dict(_guide_headings())
    results = dict(_result_rows())
    for check_id in _NEW_IN_0116:
        assert check_id in guide, f"MT-{check_id} is not in the guide"
        assert check_id in results, f"MT-{check_id} is not on the results page"
    core = {check_id for check_id in _NEW_IN_0116 if "[core]" in guide[check_id]}
    assert core == {"159", "160"}
    assert {check_id for check_id in _NEW_IN_0116 if results[check_id] == "yes"} == core
    for check_id in ("163", "165", "171"):
        assert "lab only" in guide[check_id] and "(writes)" in guide[check_id]
