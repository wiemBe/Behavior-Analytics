"""Dashboard layout validation (UR.5.5): every bound at its limit and one past it. No DB."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from pydantic import ValidationError

from app.schemas.dashboard import (
    DashboardCreate,
    DashboardLayout,
    DashboardPatch,
    LayoutWidget,
    upgrade_layout,
)
from app.services.dashboards import copy_name, widget_count


def widget(index: int = 0, **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": f"w_{index:04d}", "type": "kpi.total_eps", "x": 0, "y": index, "w": 3, "h": 3,
        "range": None, "config": {},
    }
    base.update(over)
    return base


def layout(*widgets: dict[str, Any], **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"v": 1, "range": "24h", "widgets": list(widgets)}
    base.update(over)
    return base


def accepts(value: dict[str, Any]) -> bool:
    try:
        DashboardLayout.model_validate(value)
    except ValidationError:
        return False
    return True


def test_a_minimal_and_an_empty_layout_are_valid() -> None:
    assert accepts(layout(widget()))
    assert accepts(layout())


class TestWidgetCount:
    def test_24_widgets_are_valid_and_25_are_not(self) -> None:
        assert accepts(layout(*[widget(i) for i in range(24)]))
        assert not accepts(layout(*[widget(i) for i in range(25)]))


class TestGeometry:
    @pytest.mark.parametrize(
        ("over", "ok"),
        [
            ({"x": 0}, True), ({"x": -1}, False),
            ({"x": 11, "w": 1}, True), ({"x": 12, "w": 1}, False),
            ({"w": 1}, True), ({"w": 0}, False),
            ({"x": 0, "w": 12}, True), ({"w": 13}, False),
            ({"x": 6, "w": 6}, True), ({"x": 6, "w": 7}, False),  # x + w <= 12
            ({"x": 11, "w": 2}, False),
            ({"y": 0}, True), ({"y": -1}, False), ({"y": 1000}, True),
            ({"h": 1}, True), ({"h": 0}, False),
            ({"h": 24}, True), ({"h": 25}, False),
        ],
    )
    def test_bounds(self, over: dict[str, Any], ok: bool) -> None:
        assert accepts(layout(widget(**over))) is ok

    @pytest.mark.parametrize("bad", ["3", 3.5, True, None])
    def test_geometry_is_a_strict_integer(self, bad: Any) -> None:
        assert not accepts(layout(widget(w=bad)))


class TestIdentity:
    @pytest.mark.parametrize(
        ("value", "ok"),
        [
            ("w_abcd", True), ("w_abcdefghijkl", True), ("w_abc", False),
            ("w_abcdefghijklm", False), ("x_abcd", False), ("w_ABCD", False),
            ("w_ab-d", False), ("", False),
        ],
    )
    def test_id_pattern(self, value: str, ok: bool) -> None:
        assert accepts(layout(widget(id=value))) is ok

    def test_ids_are_unique_within_a_layout(self) -> None:
        assert not accepts(layout(widget(0, id="w_same"), widget(1, id="w_same")))

    @pytest.mark.parametrize(
        ("value", "ok"),
        [
            ("a", True), ("a" * 64, True), ("a" * 65, False), ("", False),
            ("kpi.total_eps", True), ("KPI", False), ("a-b", False), ("a b", False),
        ],
    )
    def test_type_pattern(self, value: str, ok: bool) -> None:
        assert accepts(layout(widget(type=value))) is ok

    def test_an_unknown_widget_type_is_valid_and_stored_as_is(self) -> None:
        parsed = DashboardLayout.model_validate(layout(widget(type="future.hologram")))
        assert parsed.model_dump(mode="json")["widgets"][0]["type"] == "future.hologram"


class TestRange:
    @pytest.mark.parametrize("value", ["1h", "24h", "7d"])
    def test_the_three_ranges(self, value: str) -> None:
        assert accepts(layout(widget(range=value), range=value))

    @pytest.mark.parametrize("value", ["30d", "", "24H", None])
    def test_other_dashboard_ranges_are_refused(self, value: Any) -> None:
        assert not accepts(layout(range=value))

    def test_a_widget_range_is_null_or_one_of_the_three(self) -> None:
        assert accepts(layout(widget(range=None)))
        assert not accepts(layout(widget(range="30d")))


class TestConfigAndSize:
    def config_of(self, size: int) -> dict[str, Any]:
        """A config whose compact serialization is exactly `size` bytes."""
        overhead = len('{"k":""}')
        return {"k": "a" * (size - overhead)}

    def test_config_of_4096_bytes_is_valid_and_4097_is_not(self) -> None:
        assert accepts(layout(widget(config=self.config_of(4096))))
        assert not accepts(layout(widget(config=self.config_of(4097))))

    def test_config_must_be_an_object(self) -> None:
        assert not accepts(layout(widget(config=[1])))
        assert not accepts(layout(widget(config="x")))

    def test_the_whole_layout_is_at_most_64_kb(self) -> None:
        # 24 widgets x 4 KB configs is over 64 KB; 15 of them fit, 24 do not.
        big = self.config_of(4096)
        assert accepts(layout(*[widget(i, config=big) for i in range(15)]))
        assert not accepts(layout(*[widget(i, config=big) for i in range(24)]))


class TestUnknownKeys:
    def test_top_level(self) -> None:
        assert not accepts(layout(extra=1))

    def test_in_a_widget(self) -> None:
        assert not accepts(layout(widget(extra=1)))

    def test_missing_required_keys(self) -> None:
        assert not accepts({"v": 1, "widgets": []})
        assert not accepts({"v": 1, "range": "24h"})


class TestVersion:
    @pytest.mark.parametrize("version", [0, 2, 99, "1", True])
    def test_an_unknown_v_is_refused(self, version: Any) -> None:
        assert not accepts(layout(v=version))

    def test_v_one_is_accepted(self) -> None:
        assert accepts(layout(v=1))


class TestUpgradeLayout:
    def test_version_one_is_the_identity(self) -> None:
        stored = layout(widget(type="future.hologram", config={"a": [1, {"b": 2}]}))
        before = copy.deepcopy(stored)
        assert upgrade_layout(stored) == before

    @pytest.mark.parametrize("version", [0, 2, None])
    def test_an_unknown_version_is_refused(self, version: Any) -> None:
        stored = layout()
        stored["v"] = version
        with pytest.raises(ValueError, match="not supported"):
            upgrade_layout(stored)


class TestRequests:
    def test_name_is_trimmed_and_bounded_to_80(self) -> None:
        body = DashboardCreate.model_validate({"name": "  Main  ", "layout": layout()})
        assert body.name == "Main"
        assert DashboardCreate.model_validate({"name": "n" * 80, "layout": layout()})
        for bad in ("", "   ", "n" * 81):
            with pytest.raises(ValidationError):
                DashboardCreate.model_validate({"name": bad, "layout": layout()})

    def test_patch_needs_a_field_and_forbids_nulls_for_name_and_visibility(self) -> None:
        for bad in ({}, {"name": None}, {"visibility": None}, {"visibility": "TEAM"}):
            with pytest.raises(ValidationError):
                DashboardPatch.model_validate(bad)
        assert DashboardPatch.model_validate({"description": None}).description is None

    def test_a_widget_model_forbids_unknown_keys(self) -> None:
        with pytest.raises(ValidationError):
            LayoutWidget.model_validate(widget(extra=True))


class TestCopyName:
    def test_first_copy_then_numbered(self) -> None:
        assert copy_name("Main", set()) == "Copy of Main"
        assert copy_name("Main", {"Copy of Main"}) == "Copy of Main (2)"
        assert copy_name("Main", {"Copy of Main", "Copy of Main (2)"}) == "Copy of Main (3)"

    def test_the_whole_name_fits_80_characters(self) -> None:
        name = "n" * 80
        first = copy_name(name, set())
        assert len(first) == 80 and first.startswith("Copy of n")
        second = copy_name(name, {first})
        assert len(second) == 80 and second.endswith(" (2)")
        third = copy_name(name, {first, second})
        assert len(third) == 80 and third.endswith(" (3)") and third != second

    def test_widget_count(self) -> None:
        assert widget_count(layout(widget(0), widget(1))) == 2
        assert widget_count(layout()) == 0
