"""The reason beside a rule's health verdict, through the real app.

`GET /rules` and `GET /rules/{id}` carry `health_reason`: the reason of the snapshot
written at the rule's own `health_evaluated_at`. A reason that belongs to another
evaluation is never shown beside the verdict, and the page reads every reason in one
statement whatever its size.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.enums import RuleHealthStatus
from app.models.rule import AnalyticsRule, RuleHealthSnapshot
from tests.integration.factories import make_instance
from tests.integration.test_behavior_bulk_reads import reader_client
from tests.integration.test_rule_match_collection import recorded_statements

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        "TEST_DATABASE_URL" not in os.environ,
        reason="set TEST_DATABASE_URL to run the rule health reason tests",
    ),
]

EVALUATED = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
GRACE = "The rule is 3.0 days old, inside the 7-day grace period."


async def _rule(session, inst, qradar_id: int, *, evaluated_at, status) -> AnalyticsRule:
    rule = AnalyticsRule(
        instance_id=inst.id,
        qradar_id=qradar_id,
        name=f"rule-{qradar_id:04d}",
        rule_type="EVENT",
        enabled=True,
        is_building_block=False,
        health_status=status,
        health_evaluated_at=evaluated_at,
    )
    session.add(rule)
    await session.flush()
    return rule


def _snapshot(rule, at, status, reason) -> RuleHealthSnapshot:
    return RuleHealthSnapshot(
        rule_id=rule.id, evaluated_at=at, status=status, reason=reason
    )


async def _seed(session):
    inst = await make_instance(session)
    matched = await _rule(
        session, inst, 1, evaluated_at=EVALUATED, status=RuleHealthStatus.INSUFFICIENT_DATA
    )
    stale = await _rule(
        session, inst, 2, evaluated_at=EVALUATED, status=RuleHealthStatus.INACTIVE
    )
    bare = await _rule(session, inst, 3, evaluated_at=None, status=RuleHealthStatus.UNKNOWN)
    no_reason = await _rule(
        session, inst, 4, evaluated_at=EVALUATED, status=RuleHealthStatus.HEALTHY
    )
    session.add_all(
        [
            # The verdict shown was written at EVALUATED: its own reason is the one.
            _snapshot(matched, EVALUATED, RuleHealthStatus.INSUFFICIENT_DATA, GRACE),
            _snapshot(
                matched, EVALUATED - timedelta(hours=1), RuleHealthStatus.HEALTHY, "older"
            ),
            # The latest snapshot is a later evaluation than the verdict on the rule.
            _snapshot(
                stale, EVALUATED + timedelta(hours=1), RuleHealthStatus.NOISY, "newer one"
            ),
            _snapshot(
                stale, EVALUATED - timedelta(hours=1), RuleHealthStatus.HEALTHY, "older one"
            ),
            _snapshot(no_reason, EVALUATED, RuleHealthStatus.HEALTHY, None),
        ]
    )
    await session.commit()
    return matched, stale, bare, no_reason


@pytest.mark.asyncio
async def test_reason_is_the_one_written_with_the_verdict(db_session, db_schema) -> None:
    matched, stale, bare, no_reason = await _seed(db_session)
    api = reader_client(db_schema)

    page = api.get("/api/v1/rules")
    assert page.status_code == 200
    by_id = {item["id"]: item for item in page.json()["items"]}
    assert by_id[str(matched.id)]["health_reason"] == GRACE
    # A later or earlier snapshot never stands in for the shown verdict's.
    assert by_id[str(stale.id)]["health_reason"] is None
    # Never evaluated, so nothing to look up.
    assert by_id[str(bare.id)]["health_reason"] is None
    # Evaluated, snapshot present, but it recorded no reason.
    assert by_id[str(no_reason.id)]["health_reason"] is None

    detail = api.get(f"/api/v1/rules/{matched.id}").json()
    assert detail["health_reason"] == GRACE
    assert api.get(f"/api/v1/rules/{stale.id}").json()["health_reason"] is None
    assert api.get(f"/api/v1/rules/{bare.id}").json()["health_reason"] is None


@pytest.mark.asyncio
async def test_reason_is_sanitised_like_the_other_text(db_session, db_schema) -> None:
    inst = await make_instance(db_session)
    rule = await _rule(
        db_session, inst, 1, evaluated_at=EVALUATED, status=RuleHealthStatus.INACTIVE
    )
    db_session.add(
        _snapshot(rule, EVALUATED, RuleHealthStatus.INACTIVE, "quiet <script>x()</script> 4 d")
    )
    await db_session.commit()

    reason = reader_client(db_schema).get(f"/api/v1/rules/{rule.id}").json()["health_reason"]

    assert "<script>" not in reason and "quiet" in reason


@pytest.mark.asyncio
async def test_the_list_reads_every_reason_in_one_statement(db_session, db_schema) -> None:
    """The same statements, including one snapshot read, for page sizes 2 and 12."""
    inst = await make_instance(db_session)
    for i in range(1, 13):
        rule = await _rule(
            db_session, inst, i, evaluated_at=EVALUATED, status=RuleHealthStatus.INACTIVE
        )
        db_session.add(_snapshot(rule, EVALUATED, RuleHealthStatus.INACTIVE, f"reason {i}"))
    await db_session.commit()

    api = reader_client(db_schema)
    async with async_sessionmaker(api.engine, expire_on_commit=False)() as anchor:
        with recorded_statements(anchor) as statements:
            response = api.get("/api/v1/rules", params={"limit": 2})
        assert response.status_code == 200
        assert len(response.json()["items"]) == 2
        small = len(statements)
        assert small == 5, statements
        small_snapshot = [s for s in statements if "rule_health_snapshot" in s]
        assert len(small_snapshot) == 1, small_snapshot

        with recorded_statements(anchor) as statements:
            response = api.get("/api/v1/rules", params={"limit": 12})
        assert response.status_code == 200
        items = response.json()["items"]
        assert len(items) == 12
        assert all(i["health_reason"] for i in items)
        assert len(statements) == small, (small, statements)
        assert len([s for s in statements if "rule_health_snapshot" in s]) == 1


@pytest.mark.asyncio
async def test_a_page_with_no_evaluated_rule_reads_no_snapshots(
    db_session, db_schema
) -> None:
    inst = await make_instance(db_session)
    await _rule(db_session, inst, 1, evaluated_at=None, status=RuleHealthStatus.UNKNOWN)
    await db_session.commit()
    api = reader_client(db_schema)
    async with async_sessionmaker(api.engine, expire_on_commit=False)() as anchor:
        with recorded_statements(anchor, only="rule_health_snapshot") as statements:
            response = api.get("/api/v1/rules")
    assert response.status_code == 200
    assert statements == []
    assert uuid.UUID(response.json()["items"][0]["id"])
