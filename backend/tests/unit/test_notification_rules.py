"""The pure notification-rule matcher (SOC-12c): every clause, no database."""

from __future__ import annotations

import typing
import uuid
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace

from app.alerts.notification_rules import RuleView, match_rules
from app.alerts.query import AlertFamily
from app.schemas.notification_rule import FamilyName

AT = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)
D1, D2, D3 = (uuid.UUID(int=i) for i in (1, 2, 3))


def alert(severity="HIGH", source_type="log_source", **context):  # type: ignore[no-untyped-def]
    return SimpleNamespace(severity=severity, source_type=source_type, context=context)


def rule(n: int = 1, **over):  # type: ignore[no-untyped-def]
    values = dict(
        id=uuid.UUID(int=100 + n), priority=100, min_severity="LOW", destination_ids=(D1,)
    )
    values.update(over)
    return RuleView(**values)


def ids(result):  # type: ignore[no-untyped-def]
    return result.destination_ids


def test_family_literal_is_exactly_alert_family() -> None:
    assert set(typing.get_args(FamilyName)) == {f.value for f in AlertFamily}


def test_empty_rule_list_matches_nothing_and_keeps_defaults() -> None:
    result = match_rules([], alert(), at=AT)
    assert result.destination_ids == () and result.stop_default is False


def test_severity_threshold_is_inclusive() -> None:
    r = rule(min_severity="HIGH")
    assert ids(match_rules([r], alert("CRITICAL"), at=AT)) == (D1,)
    assert ids(match_rules([r], alert("HIGH"), at=AT)) == (D1,)
    assert ids(match_rules([r], alert("MEDIUM"), at=AT)) == ()
    assert ids(match_rules([r], alert("INFO"), at=AT)) == ()
    assert ids(match_rules([r], alert("BOGUS"), at=AT)) == ()


def test_family_filter_and_unknown_family() -> None:
    r = rule(families=frozenset({"waf", "ips"}))
    assert ids(match_rules([r], alert(source_type="waf_endpoint"), at=AT)) == (D1,)
    assert ids(match_rules([r], alert(source_type="ips_source"), at=AT)) == (D1,)
    assert ids(match_rules([r], alert(source_type="log_source"), at=AT)) == ()
    assert ids(match_rules([r], alert(source_type="mystery"), at=AT)) == ()
    # an unknown family matches a rule with no family filter
    assert ids(match_rules([rule()], alert(source_type="mystery"), at=AT)) == (D1,)


def test_owner_and_source_class_need_a_matching_context_value() -> None:
    owner = rule(owners=frozenset({"payments"}))
    assert ids(match_rules([owner], alert(owner="payments"), at=AT)) == (D1,)
    assert ids(match_rules([owner], alert(owner="other"), at=AT)) == ()
    assert ids(match_rules([owner], alert(), at=AT)) == ()
    cls = rule(source_classes=frozenset({"FIREWALL"}))
    assert ids(match_rules([cls], alert(source_class="FIREWALL"), at=AT)) == (D1,)
    assert ids(match_rules([cls], alert(source_class="PROXY"), at=AT)) == ()
    assert ids(match_rules([cls], alert(), at=AT)) == ()


def test_group_rule_needs_an_intersecting_group_id() -> None:
    r = rule(group_ids=frozenset({7, 9}))
    assert ids(match_rules([r], alert(group_ids=[1, 9]), at=AT)) == (D1,)
    assert ids(match_rules([r], alert(group_ids=[1, 2]), at=AT)) == ()
    assert ids(match_rules([r], alert(group_ids=[]), at=AT)) == ()
    assert ids(match_rules([r], alert(), at=AT)) == ()
    assert ids(match_rules([r], alert(group_ids="9"), at=AT)) == ()
    assert ids(match_rules([r], alert(group_ids=[True]), at=AT)) == ()
    # a rule with no group filter ignores groups
    assert ids(match_rules([rule()], alert(), at=AT)) == (D1,)


def at_minute(hour: int, minute: int) -> datetime:
    return AT.replace(hour=hour, minute=minute)


def test_window_is_half_open_in_utc() -> None:
    r = rule(window_start_minute=9 * 60, window_end_minute=17 * 60)
    assert ids(match_rules([r], alert(), at=at_minute(8, 59))) == ()
    assert ids(match_rules([r], alert(), at=at_minute(9, 0))) == (D1,)
    assert ids(match_rules([r], alert(), at=at_minute(16, 59))) == (D1,)
    assert ids(match_rules([r], alert(), at=at_minute(17, 0))) == ()


def test_window_wraps_midnight() -> None:
    r = rule(window_start_minute=22 * 60, window_end_minute=6 * 60)
    assert ids(match_rules([r], alert(), at=at_minute(21, 59))) == ()
    assert ids(match_rules([r], alert(), at=at_minute(22, 0))) == (D1,)
    assert ids(match_rules([r], alert(), at=at_minute(23, 59))) == (D1,)
    assert ids(match_rules([r], alert(), at=at_minute(0, 0))) == (D1,)
    assert ids(match_rules([r], alert(), at=at_minute(5, 59))) == (D1,)
    assert ids(match_rules([r], alert(), at=at_minute(6, 0))) == ()
    assert ids(match_rules([r], alert(), at=at_minute(12, 0))) == ()


def test_window_uses_utc_for_an_aware_non_utc_time() -> None:
    r = rule(window_start_minute=9 * 60, window_end_minute=10 * 60)
    plus3 = timezone(timedelta(hours=3))
    assert ids(match_rules([r], alert(), at=datetime(2026, 11, 13, 12, 30, tzinfo=plus3))) == (D1,)
    assert ids(match_rules([r], alert(), at=datetime(2026, 11, 13, 9, 30, tzinfo=plus3))) == ()


def test_no_window_means_all_day() -> None:
    assert ids(match_rules([rule()], alert(), at=at_minute(3, 33))) == (D1,)


def test_priority_then_id_and_deduplication() -> None:
    late = rule(1, priority=20, destination_ids=(D3, D1))
    early = rule(2, priority=10, destination_ids=(D2, D1))
    tie_low = rule(3, priority=20, destination_ids=(D2,))
    result = match_rules([late, tie_low, early], alert(), at=AT)
    assert result.destination_ids == (D2, D1, D3)


def test_stop_default_from_any_matching_rule_only() -> None:
    stopper = rule(1, stop_default=True, min_severity="CRITICAL")
    plain = rule(2, destination_ids=(D2,))
    assert match_rules([stopper, plain], alert("HIGH"), at=AT).stop_default is False
    result = match_rules([stopper, plain], alert("CRITICAL"), at=AT)
    assert result.stop_default is True and result.destination_ids == (D1, D2)


def test_disabled_rule_is_skipped() -> None:
    off = rule(enabled=False, stop_default=True)
    result = match_rules([off], alert(), at=AT)
    assert result.destination_ids == () and result.stop_default is False


def test_filters_combine_with_and() -> None:
    r = rule(min_severity="HIGH", families=frozenset({"log_source"}), owners=frozenset({"a"}))
    assert ids(match_rules([r], alert("HIGH", owner="a"), at=AT)) == (D1,)
    assert ids(match_rules([r], alert("LOW", owner="a"), at=AT)) == ()
    assert ids(match_rules([r], alert("HIGH", owner="b"), at=AT)) == ()
