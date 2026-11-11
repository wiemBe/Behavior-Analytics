"""One severity order for the backend (DEBT-8, extended in DEBT-8b).

`SEVERITY_RANK` and `SEVERITY_BY_RANK` live next to `Severity`; the queue, the
IPS behaviour reads and the digest read them instead of each spelling the order
out. DEBT-8b: the alert list's severity sort and notification routing read them
too. These tests pin the order itself and that no module keeps a private copy.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import cast

import pytest
from sqlalchemy.dialects import postgresql

from app.alerts import query, routing
from app.models.alert import Alert
from app.models.enums import (
    SEVERITY_BY_RANK,
    SEVERITY_RANK,
    AlertStatus,
    NotificationChannel,
    Severity,
)
from app.models.log_source import LogSourceAnomaly
from app.repositories import ips_behavior
from app.services import digest, queue


def test_every_member_has_exactly_one_rank() -> None:
    assert set(SEVERITY_RANK) == {member.value for member in Severity}
    assert len(SEVERITY_RANK) == len(Severity)


def test_ranks_start_at_one_without_gaps() -> None:
    assert sorted(SEVERITY_RANK.values()) == list(range(1, len(Severity) + 1))


def test_the_order_is_critical_first() -> None:
    ordered = [
        Severity.CRITICAL,
        Severity.HIGH,
        Severity.MEDIUM,
        Severity.LOW,
        Severity.INFO,
    ]
    ranks = [SEVERITY_RANK[member.value] for member in ordered]
    assert ranks == sorted(ranks, reverse=True)
    assert len(set(ranks)) == len(ranks)


def test_the_inverse_is_exact() -> None:
    expected = {rank: Severity(value) for value, rank in SEVERITY_RANK.items()}
    assert expected == SEVERITY_BY_RANK
    assert set(SEVERITY_BY_RANK) == set(SEVERITY_RANK.values())
    for value, rank in SEVERITY_RANK.items():
        assert SEVERITY_BY_RANK[rank].value == value


def test_the_three_modules_use_the_shared_order() -> None:
    assert ips_behavior.SEVERITY_RANK is SEVERITY_RANK
    assert ips_behavior.SEVERITY_BY_RANK is SEVERITY_BY_RANK
    assert queue.SEVERITY_RANK is SEVERITY_RANK
    assert queue.SEVERITY_BY_RANK is SEVERITY_BY_RANK
    assert digest.SEVERITY_RANK is SEVERITY_RANK
    assert digest.SEVERITY_BY_RANK is SEVERITY_BY_RANK


def test_no_module_keeps_a_private_copy() -> None:
    for module in (ips_behavior, queue, digest):
        assert not hasattr(module, "_SEVERITY_RANK"), module.__name__
        assert not hasattr(module, "_SEVERITY_BY_RANK"), module.__name__
        assert not hasattr(module, "_RANK_BY_SEVERITY"), module.__name__


def test_digest_reads_each_rank_back_as_the_same_severity() -> None:
    for member in Severity:
        assert digest._RANK_TO_SEVERITY[SEVERITY_RANK[member.value]] is member
    # Rank 0 is "no severity", which is what an unknown stored value yields; it
    # printed as INFO before DEBT-8 and still does. No member maps to it.
    assert digest._RANK_TO_SEVERITY[0] is Severity.INFO
    assert 0 not in SEVERITY_RANK.values()


def test_digest_ranks_every_stored_value_from_the_shared_order() -> None:
    statement = digest._severity_rank(LogSourceAnomaly.severity).compile(
        dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
    )
    sql = " ".join(str(statement).split())
    for value, rank in SEVERITY_RANK.items():
        assert f"'{value}' THEN {rank}" in sql
    # An unknown stored value is not INFO in the case; it is 0, "lowest".
    assert sql.endswith("ELSE 0 END")


def test_the_alert_list_and_routing_keep_no_private_order() -> None:
    """DEBT-8b: both read the shared map by name, and neither keeps a copy."""
    for module in (query, routing):
        assert module.SEVERITY_RANK is SEVERITY_RANK, module.__name__
        assert not hasattr(module, "_SEVERITY_RANK"), module.__name__


def test_alert_list_ranks_every_stored_value_from_the_shared_order() -> None:
    statement = query.SEVERITY_RANK_EXPRESSION.compile(
        dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
    )
    sql = " ".join(str(statement).split())
    assert sql.startswith("CASE alert.severity WHEN ")
    for value, rank in SEVERITY_RANK.items():
        assert f"'{value}' THEN {rank}" in sql
    assert sql.count(" THEN ") == len(Severity)
    # No `else_`: a stored value the order does not name still ranks SQL NULL,
    # exactly as the private copy did before DEBT-8b.
    assert "ELSE" not in sql.upper()


def _alert_with_severity(severity: Severity) -> Alert:
    now = datetime.now(UTC)
    return Alert(
        dedup_key="anomaly:debt8b",
        fingerprint="anomaly:debt8b",
        title="t",
        severity=severity,
        status=AlertStatus.OPEN,
        source_type="log_source",
        source_id=uuid.uuid4(),
        opened_at=now,
        first_seen_at=now,
        context={},
    )


#: The order in words, independent of `SEVERITY_RANK`: routing must agree with
#: this, not merely with itself, so a swapped rank is caught here.
_CRITICAL_FIRST = [
    Severity.CRITICAL,
    Severity.HIGH,
    Severity.MEDIUM,
    Severity.LOW,
    Severity.INFO,
]


@pytest.mark.parametrize("alert_severity", list(Severity))
@pytest.mark.parametrize("min_severity", list(Severity))
def test_routing_accepts_and_rejects_around_min_severity_for_every_pair(
    alert_severity: Severity, min_severity: Severity
) -> None:
    rule = routing.RoutingRule(
        channel=NotificationChannel.SLACK, target="hook", min_severity=min_severity
    )
    expected = _CRITICAL_FIRST.index(alert_severity) <= _CRITICAL_FIRST.index(min_severity)
    assert rule.matches(_alert_with_severity(alert_severity)) is expected


def test_routing_still_refuses_an_unknown_stored_severity() -> None:
    """An unknown value raised `KeyError` before DEBT-8b and still does."""
    alert = _alert_with_severity(Severity.HIGH)
    alert.severity = cast(Severity, "NOT_A_SEVERITY")
    rule = routing.RoutingRule(channel=NotificationChannel.SLACK, target="hook")
    with pytest.raises(KeyError):
        rule.matches(alert)
