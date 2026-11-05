"""The alert list's filter vocabulary: escaping, validation and family mapping."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.alerts.query import (
    MAX_Q_LENGTH,
    AlertFamily,
    AlertFilters,
    AlertSort,
    SortOrder,
    alert_family,
    escape_like,
    normalise_q,
)
from app.models.alert import Alert
from app.models.email import EMAIL_ALERT_TYPES
from app.models.enums import Severity


@pytest.mark.parametrize(
    ("raw", "escaped"),
    [
        ("volume", "volume"),
        ("100%", "100\\%"),
        ("a_b", "a\\_b"),
        ("back\\slash", "back\\\\slash"),
        ("%_\\", "\\%\\_\\\\"),
    ],
)
def test_escape_like_neutralises_every_metacharacter(raw: str, escaped: str) -> None:
    assert escape_like(raw) == escaped


def test_normalise_q_keeps_the_text_and_drops_surrounding_space() -> None:
    assert normalise_q(None) is None
    assert normalise_q("  volume drop ") == "volume drop"
    assert normalise_q("x" * MAX_Q_LENGTH) == "x" * MAX_Q_LENGTH


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_normalise_q_refuses_a_blank_search(blank: str) -> None:
    with pytest.raises(ValueError, match="blank"):
        normalise_q(blank)


def test_normalise_q_refuses_more_than_the_bound() -> None:
    with pytest.raises(ValueError, match="at most"):
        normalise_q("x" * (MAX_Q_LENGTH + 1))


@pytest.mark.parametrize(
    ("source_type", "family"),
    [
        ("log_source", AlertFamily.LOG_SOURCE),
        ("waf_endpoint", AlertFamily.WAF),
        ("ips_entity", AlertFamily.IPS),
        ("ips_source", AlertFamily.IPS),
        ("pw_value", AlertFamily.PROPERTY_WATCH),
        ("catchall_sender", AlertFamily.CATCHALL_SENDER),
        ("custom_rule", AlertFamily.CUSTOM_RULE),
        ("scheduled_search", AlertFamily.SCHEDULED_SEARCH),
        ("qradar_instance", AlertFamily.QRADAR_INSTANCE),
    ],
)
def test_alert_family_maps_every_producer(source_type: str, family: AlertFamily) -> None:
    assert alert_family(source_type) == family


def test_alert_family_invents_nothing_for_an_unknown_producer() -> None:
    assert alert_family("something_new") is None


def test_the_family_map_covers_exactly_the_alert_producers_the_app_sends() -> None:
    """The email allowlist and the family map name the same producers.

    When a producer is added to one list and not the other, a family filter
    would silently drop its alerts. Pinning the two lists together makes that a
    failing test instead.
    """
    assert {alert_family(source_type) for source_type in EMAIL_ALERT_TYPES} == set(
        AlertFamily
    )


def test_no_filter_and_the_default_order_keep_the_previous_query() -> None:
    """The default order is still `opened_at desc, id desc`, and nothing else runs."""
    assert AlertFilters().clauses() == []
    default = [str(part) for part in AlertFilters().order_by(AlertSort.CREATED_AT, SortOrder.DESC)]
    assert default == [
        str(Alert.opened_at.desc()),
        str(Alert.id.desc()),
    ]
    assert str(select(Alert).order_by(*AlertFilters().order_by(
        AlertSort.CREATED_AT, SortOrder.DESC
    ))) == str(
        select(Alert).order_by(Alert.opened_at.desc(), Alert.id.desc())
    )


def test_every_filter_contributes_exactly_one_clause() -> None:
    import uuid
    from datetime import UTC, datetime

    from app.models.enums import AlertStatus

    filters = AlertFilters(
        status=AlertStatus.OPEN,
        severities=(Severity.HIGH,),
        log_source_ids=(uuid.uuid4(),),
        family=AlertFamily.WAF,
        since=datetime(2026, 1, 1, tzinfo=UTC),
        until=datetime(2026, 1, 2, tzinfo=UTC),
        title_contains="drop",
    )
    assert len(filters.clauses()) == 7
