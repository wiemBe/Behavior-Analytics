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
_CORE_LIST = re.compile(r"\*\*Core \(production path\)\*\*, 38 checks: ([^.\n]+)\.")


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


def test_the_core_checks_are_the_same_38_on_both_pages() -> None:
    guide_core = [
        check_id for check_id, heading in _guide_headings() if "[core]" in heading
    ]
    results_core = [
        check_id for check_id, core in _result_rows() if core == "yes"
    ]
    assert len(guide_core) == 38
    assert results_core == guide_core


def test_the_core_summary_line_lists_exactly_the_core_checks() -> None:
    match = _CORE_LIST.search(RESULTS.read_text(encoding="utf-8"))
    assert match is not None, "the results page has no core summary line"
    listed = [entry.removeprefix("MT-") for entry in re.findall(r"MT-\d+", match.group(1))]
    assert len(listed) == 38
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
