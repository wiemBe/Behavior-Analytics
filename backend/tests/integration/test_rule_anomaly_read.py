"""SOC-6d1: rule incidents on the anomalies read side (design §5.6, §7.2, §8).

The rows are inserted directly; the engine that writes them is SOC-6c's. The union arm,
the subject kind, `ActiveByFamily.rule`, the family 422 text, the digest's `RULE` spelling
and the triage plumbing (stale closer, orphan delete) are driven through the real API and
services against PostgreSQL.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.enums import (
    AnomalyFamily,
    AnomalyState,
    PwScopeKind,
    Severity,
    TriageStatus,
)
from app.models.property_watch import PropertyWatch, PwAnomaly
from app.models.rule import AnalyticsRule
from app.models.rule_anomaly import RuleAnomaly
from app.models.triage import AnomalyTriage
from app.repositories.anomaly import AnomalyRepository
from app.services.anomaly_triage_retention import delete_orphan_triage
from app.services.stale_triage import StaleTriageCloser
from tests.integration.factories import make_instance, make_log_source
from tests.integration.test_anomaly_histogram import phase_a
from tests.integration.test_phase3_api import NOBODY, READER, client_for

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)


def ago(hours: float = 1) -> datetime:
    return datetime.now(UTC) - timedelta(hours=hours)


async def add_rule(session, inst, qradar_id: int, *, name: str | None = None, bb: bool = False):
    rule = AnalyticsRule(
        instance_id=inst.id, qradar_id=qradar_id, name=name or f"rule-{qradar_id}",
        rule_type="EVENT", enabled=True, origin="USER", is_building_block=bb,
    )
    session.add(rule)
    await session.flush()
    return rule


def incident(
    rule, *, detector="RULE_STORM", state=AnomalyState.OPEN, detected_at=None, **kw
) -> RuleAnomaly:
    return RuleAnomaly(
        rule_id=rule.id, qradar_id=rule.qradar_id, is_building_block=rule.is_building_block,
        detector_type=detector, fingerprint=f"fp-{uuid.uuid4().hex[:12]}",
        severity=Severity.HIGH, state=state, detected_at=detected_at or ago(),
        details=kw.pop("details", {}),
        observed_value=kw.pop("observed_value", 900.0),
        expected_value=kw.pop("expected_value", 90.0),
        **kw,
    )


async def world(session):
    inst = await make_instance(session)
    source = await make_log_source(session, inst, qradar_id=11, name="fw-1")
    rule = await add_rule(session, inst, 501, name="Brute force", bb=False)
    block = await add_rule(session, inst, 502, name="Common BB", bb=True)
    return inst, source, rule, block


class TestUnionArm:
    @pytest.mark.asyncio
    async def test_the_union_projects_nulls_both_ways(self, db_session) -> None:
        inst, source, rule, _block = await world(db_session)
        watch = PropertyWatch(
            instance_id=inst.id, name="w-1", scope_kind=PwScopeKind.LOG_SOURCE,
            log_source_id=source.id, properties=[{"name": "x"}], pinned_values=[],
            detectors=[], watch_version=1, enabled=True,
        )
        db_session.add(watch)
        await db_session.flush()
        db_session.add_all(
            [
                phase_a(source.id, ago(2)),
                PwAnomaly(
                    watch_id=watch.id, pw_value_id=None, value_key="v",
                    detector_type="PW_VALUE_SPIKE", fingerprint=f"fp-{uuid.uuid4()}",
                    severity=Severity.HIGH, state=AnomalyState.OPEN, detected_at=ago(3),
                    watch_version=1, consecutive_buckets=3, policy_version=1, details={},
                    evidence_status="NOT_REQUESTED", observed_measurement="EXACT",
                ),
                incident(rule, detected_at=ago(1)),
            ]
        )
        await db_session.commit()

        rows, total = await AnomalyRepository(db_session).list_unified(
            limit=10, offset=0, since=ago(24), until=ago(0)
        )

        assert total == 3
        by_kind = {r["subject_kind"]: r for r in rows}
        assert set(by_kind) == {"LOG_SOURCE", "PW_VALUE", "RULE"}
        rule_cols = ("rule_id", "rule_name", "rule_qradar_id", "rule_is_building_block")
        for kind in ("LOG_SOURCE", "PW_VALUE"):
            assert all(by_kind[kind][c] is None for c in rule_cols), kind
        rule_row = by_kind["RULE"]
        assert rule_row["rule_name"] == "Brute force" and rule_row["rule_qradar_id"] == 501
        not_rule = (
            "log_source_id", "log_source_name", "waf_endpoint_id", "ips_entity_id",
            "pw_watch_id", "pw_watch_name", "pw_value_id", "pw_value_key",
        )
        assert all(rule_row[c] is None for c in not_rule)

    @pytest.mark.asyncio
    async def test_the_kind_filter_returns_only_rule_rows_with_their_subject(
        self, db_session
    ) -> None:
        _inst, source, rule, block = await world(db_session)
        db_session.add_all(
            [
                phase_a(source.id, ago(2)),
                incident(rule, detected_at=ago(1)),
                incident(block, detector="RULE_SILENCE", detected_at=ago(2)),
            ]
        )
        await db_session.commit()
        client = client_for(READER)

        both = client.get("/api/v1/anomalies", params={"limit": 50}).json()
        only = client.get("/api/v1/anomalies", params={"subject_kind": "RULE"}).json()

        assert both["total"] == 3
        assert only["total"] == 2
        assert {i["subject_kind"] for i in only["items"]} == {"RULE"}
        newest, older = only["items"]
        assert newest["rule"] == {
            "rule_id": str(rule.id), "name": "Brute force", "qradar_id": 501,
            "is_building_block": False,
        }
        assert older["rule"]["is_building_block"] is True and older["rule"]["qradar_id"] == 502
        assert newest["anomaly_type"] == "RULE_STORM"
        assert newest["observed_value"] == 900.0 and newest["expected_value"] == 90.0
        others = [i for i in both["items"] if i["subject_kind"] != "RULE"]
        assert [i["rule"] for i in others] == [None]

    @pytest.mark.asyncio
    async def test_filters_that_cannot_apply_to_a_rule_exclude_it(self, db_session) -> None:
        inst, source, rule, _block = await world(db_session)
        db_session.add_all([phase_a(source.id, ago(2)), incident(rule)])
        await db_session.commit()
        client = client_for(READER)

        by_source = client.get("/api/v1/anomalies", params={"log_source_id": str(source.id)})
        by_type = client.get("/api/v1/anomalies", params={"anomaly_type": "RULE_STORM"})
        by_silence = client.get("/api/v1/anomalies", params={"anomaly_type": "RULE_SILENCE"})
        by_instance = client.get("/api/v1/anomalies", params={"instance_id": str(inst.id)})
        other_instance = client.get(
            "/api/v1/anomalies",
            params={"subject_kind": "RULE", "instance_id": str(uuid.uuid4())},
        )

        assert {i["subject_kind"] for i in by_source.json()["items"]} == {"LOG_SOURCE"}
        assert [i["subject_kind"] for i in by_type.json()["items"]] == ["RULE"]
        assert by_silence.json()["total"] == 0
        assert by_instance.json()["total"] == 2
        assert other_instance.json()["total"] == 0

    @pytest.mark.asyncio
    async def test_a_triaged_rule_row_carries_its_triage(self, db_session) -> None:
        _inst, _source, rule, _block = await world(db_session)
        row = incident(rule)
        db_session.add(row)
        await db_session.flush()
        db_session.add(
            AnomalyTriage(
                family="rule", anomaly_id=row.id, status="IN_PROGRESS", owner="me@example.com",
                updated_by="me@example.com", version=2,
            )
        )
        await db_session.commit()

        only = client_for(READER).get("/api/v1/anomalies", params={"subject_kind": "RULE"}).json()

        assert only["items"][0]["triage"]["status"] == "IN_PROGRESS"
        assert only["items"][0]["triage"]["owner"] == "me@example.com"
        new_only = client_for(READER).get(
            "/api/v1/anomalies", params={"subject_kind": "RULE", "triage_status": "NEW"}
        )
        assert new_only.json()["total"] == 0


class TestActiveByFamily:
    @pytest.mark.asyncio
    async def test_open_rule_incidents_are_counted_and_resolved_ones_are_not(
        self, db_session
    ) -> None:
        _inst, _source, rule, block = await world(db_session)
        db_session.add_all(
            [
                incident(rule),
                incident(block, detector="RULE_SILENCE", state=AnomalyState.RECOVERING),
                incident(rule, state=AnomalyState.RESOLVED, detector="RULE_SILENCE"),
            ]
        )
        await db_session.commit()
        client = client_for(READER)

        fam = client.get("/api/v1/anomalies/summary").json()["active_by_family"]
        listed = client.get(
            "/api/v1/anomalies", params={"active_only": "true", "limit": 200}
        ).json()

        assert fam["rule"] == {"open": 1, "candidate": 0, "recovering": 1, "total": 2}
        assert sum(f["total"] for f in fam.values()) == listed["total"] == 2

    @pytest.mark.asyncio
    async def test_no_rule_incident_is_a_measured_zero(self, db_session) -> None:
        await world(db_session)
        await db_session.commit()

        fam = client_for(READER).get("/api/v1/anomalies/summary").json()["active_by_family"]

        assert fam["rule"] == {"open": 0, "candidate": 0, "recovering": 0, "total": 0}


class TestFamilyText:
    @pytest.mark.asyncio
    async def test_the_anomalies_422_lists_rule(self, db_session) -> None:
        await db_session.commit()
        resp = client_for(READER).get(f"/api/v1/anomalies/triage/nope/{uuid.uuid4()}")

        assert resp.status_code == 422
        assert resp.json()["detail"] == (
            "unknown anomaly family: expected log_source, waf, ips, property_watch or rule"
        )

    @pytest.mark.asyncio
    async def test_the_suppression_422_lists_rule(self, db_session) -> None:
        await db_session.commit()
        resp = client_for(READER).get(f"/api/v1/suppressions/for/nope/{uuid.uuid4()}")

        assert resp.status_code == 422
        assert resp.json()["detail"] == (
            "unknown anomaly family: expected log_source, waf, ips, property_watch or rule"
        )

    @pytest.mark.asyncio
    async def test_the_family_text_is_read_after_authorisation(self, db_session) -> None:
        await db_session.commit()
        resp = client_for(NOBODY).get(f"/api/v1/anomalies/triage/nope/{uuid.uuid4()}")

        assert resp.status_code == 403


class TestTriageForRules:
    @pytest.mark.asyncio
    async def test_the_stale_closer_closes_a_stale_new_rule_incident_in_triage_only(
        self, db_session
    ) -> None:
        _inst, _source, rule, _block = await world(db_session)
        old = incident(rule, detected_at=NOW - timedelta(days=5))
        young = incident(rule, detected_at=NOW - timedelta(days=1), detector="RULE_SILENCE")
        taken = incident(rule, detected_at=NOW - timedelta(days=6), detector="RULE_SILENCE")
        db_session.add_all([old, young, taken])
        await db_session.flush()
        db_session.add(
            AnomalyTriage(
                family="rule", anomaly_id=taken.id, status="IN_PROGRESS", owner="me@example.com",
                updated_by="me@example.com", version=2, updated_at=NOW - timedelta(days=6),
            )
        )
        await db_session.commit()

        old_id, taken_id, young_id = old.id, taken.id, young.id

        report = await StaleTriageCloser(db_session, days=3, clock=lambda: NOW).run()

        assert report.families["rule"] == 1
        rows = {
            t.anomaly_id: t
            for t in await db_session.scalars(
                select(AnomalyTriage).where(AnomalyTriage.family == AnomalyFamily.RULE)
            )
        }
        assert rows[old_id].status == TriageStatus.CLOSED
        assert rows[taken_id].status == TriageStatus.IN_PROGRESS
        assert young_id not in rows
        # The detector lifecycle is a separate axis: the incident is still OPEN.
        db_session.expire_all()
        assert (await db_session.get(RuleAnomaly, old_id)).state == AnomalyState.OPEN

    @pytest.mark.asyncio
    async def test_the_triage_list_filters_a_rule_family_row(self, db_session) -> None:
        _inst, _source, rule, _block = await world(db_session)
        row = incident(rule)
        db_session.add(row)
        await db_session.commit()

        resp = client_for(READER).get(
            "/api/v1/anomalies", params={"subject_kind": "RULE", "owner": "none"}
        )

        assert resp.json()["total"] == 1

    @pytest.mark.asyncio
    async def test_orphan_delete_removes_triage_of_a_deleted_rule_anomaly_only(
        self, db_session
    ) -> None:
        _inst, _source, rule, _block = await world(db_session)
        live = incident(rule)
        db_session.add(live)
        await db_session.flush()
        gone = uuid.uuid4()
        db_session.add_all(
            [
                AnomalyTriage(family="rule", anomaly_id=live.id, status="NEW",
                              updated_by="s", version=1),
                AnomalyTriage(family="rule", anomaly_id=gone, status="NEW",
                              updated_by="s", version=1),
                # Same id under another family must not be judged against rule_anomaly.
                AnomalyTriage(family="waf", anomaly_id=live.id, status="NEW",
                              updated_by="s", version=1),
            ]
        )
        await db_session.commit()

        removed = await delete_orphan_triage(db_session, AnomalyFamily.RULE, batch_size=10)

        assert removed == 1
        left = {
            (t.family, t.anomaly_id) for t in await db_session.scalars(select(AnomalyTriage))
        }
        assert left == {("rule", live.id), ("waf", live.id)}
