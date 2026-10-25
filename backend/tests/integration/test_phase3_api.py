"""Phase 3 API surface: authorization, filtering, sorting and bounds.

The authorization block is a regression guard. As introduced in e733648 the
offense, rule, coverage and provider routers declared no principal dependency
at all, so under OIDC they served offence records -- parsed usernames, source
addresses, analyst assignment -- and a map of where detection coverage is
absent, to a caller with no bearer token. The guard now lives on the router, so
adding an endpoint to one of these modules cannot reintroduce the gap.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.database import get_session
from app.main import create_app
from app.models.enums import CoverageStatus, MappingSource, RuleHealthStatus
from app.models.identity import AuditLog
from app.models.rule import AnalyticsRule, DetectionCoverage, TechniqueMapping
from app.security.auth import get_principal
from app.security.rbac import Principal
from tests.integration.factories import make_instance

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        "TEST_DATABASE_URL" not in os.environ,
        reason="set TEST_DATABASE_URL to run Phase 3 API integration tests",
    ),
]

NOW = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)

ADMIN = Principal(subject="admin", permissions=frozenset({"admin:*"}))
READER = Principal(subject="reader", permissions=frozenset({"read:*"}))
NOBODY = Principal(subject="nobody", permissions=frozenset())
ALERTS_ONLY = Principal(subject="alerts", permissions=frozenset({"alert:ack"}))
RULE_EDITOR = Principal(
    subject="rule-editor", permissions=frozenset({"read:*", "rule:write"})
)
COVERAGE_EDITOR = Principal(
    subject="coverage-editor", permissions=frozenset({"read:*", "coverage:write"})
)

PHASE3_ENDPOINTS = [
    "/api/v1/offenses",
    "/api/v1/offenses/analytics",
    "/api/v1/offenses/aggregates",
    "/api/v1/rules",
    "/api/v1/rules/health-summary",
    "/api/v1/coverage/summary",
    "/api/v1/coverage/techniques",
    "/api/v1/coverage/degraded",
    "/api/v1/coverage/missing",
    "/api/v1/providers/capabilities",
]


def client_for(principal: Principal) -> TestClient:
    url = os.environ["TEST_DATABASE_URL"]
    # NullPool: TestClient runs each request in its own event loop, and a
    # pooled asyncpg connection is pinned to the loop that opened it. Pooling
    # here makes the second request in a test fail on a cross-loop future.
    engine = create_async_engine(url, future=True, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _session():
        async with maker() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    app = create_app()
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_principal] = lambda: principal
    return TestClient(app)


# ============================================================================
class TestAuthorization:
    @pytest.mark.parametrize("path", PHASE3_ENDPOINTS)
    def test_a_principal_without_read_permission_is_refused(
        self, path: str, db_schema
    ) -> None:
        assert client_for(NOBODY).get(path).status_code == 403

    @pytest.mark.parametrize("path", PHASE3_ENDPOINTS)
    def test_an_unrelated_permission_does_not_grant_access(
        self, path: str, db_schema
    ) -> None:
        """alert:ack must not imply the right to read offence records."""
        assert client_for(ALERTS_ONLY).get(path).status_code == 403

    @pytest.mark.parametrize("path", PHASE3_ENDPOINTS)
    def test_the_read_wildcard_grants_access(self, path: str, db_schema) -> None:
        """Existing read-only roles keep working; only anonymity is refused."""
        assert client_for(READER).get(path).status_code == 200

    @pytest.mark.parametrize("path", PHASE3_ENDPOINTS)
    def test_admin_retains_access(self, path: str, db_schema) -> None:
        assert client_for(ADMIN).get(path).status_code == 200

    def test_detail_routes_are_guarded_too(self, db_schema) -> None:
        """A 403 must precede the 404 -- existence is itself information."""
        denied = client_for(NOBODY)
        assert denied.get("/api/v1/offenses/999999").status_code == 403
        assert denied.get("/api/v1/offenses/999999/history").status_code == 403
        assert denied.get("/api/v1/coverage/techniques/T1059").status_code == 403

    def test_the_rule_patch_route_is_guarded(self, db_schema) -> None:
        resp = client_for(NOBODY).patch(
            "/api/v1/rules/00000000-0000-0000-0000-000000000000",
            json={"soc_notes": "x"},
        )
        assert resp.status_code == 403

    def test_every_phase3_endpoint_refuses_an_unauthorized_caller(
        self, db_schema
    ) -> None:
        """Structural guard: a new endpoint cannot ship unguarded by omission.

        Enforcement lives on the router rather than on each handler precisely so
        that this holds for endpoints nobody remembered to add to the list
        above. Driven off the OpenAPI schema so a newly added Phase 3 route is
        picked up automatically and must also refuse.
        """
        client = client_for(NOBODY)
        schema = client.app.openapi()

        prefixes = ("/api/v1/offenses", "/api/v1/rules", "/api/v1/coverage",
                    "/api/v1/providers")
        checked = 0
        for path, operations in schema["paths"].items():
            if not path.startswith(prefixes):
                continue
            # Substitute any path parameter with a syntactically valid value;
            # authorization must be decided before the value is ever looked up.
            concrete = path
            for name in ("rule_id", "technique_id", "qradar_offense_id",
                         "instance_id"):
                concrete = concrete.replace(
                    "{" + name + "}",
                    "00000000-0000-0000-0000-000000000000"
                    if name.endswith("_id") and name != "qradar_offense_id"
                    else "1",
                )
            if "{" in concrete:
                continue  # a parameter we do not know how to fill

            for method in operations:
                if method not in ("get", "post", "patch", "put", "delete"):
                    continue
                checked += 1
                resp = client.request(method.upper(), concrete, json={})
                assert resp.status_code == 403, (
                    f"{method.upper()} {concrete} returned {resp.status_code}, "
                    "not 403 -- it is not authorization-guarded"
                )

        assert checked >= len(PHASE3_ENDPOINTS)


# ============================================================================
class TestOffenseApi:
    def test_listing_is_paginated_and_bounded(self, db_schema) -> None:
        client = client_for(READER)
        resp = client.get("/api/v1/offenses", params={"limit": 3, "offset": 0})
        assert resp.status_code == 200
        body = resp.json()
        assert "items" in body
        assert len(body["items"]) <= 3

    def test_limit_beyond_the_cap_is_rejected(self, db_schema) -> None:
        resp = client_for(READER).get("/api/v1/offenses", params={"limit": 100000})
        assert resp.status_code == 422

    def test_negative_offset_is_rejected(self, db_schema) -> None:
        resp = client_for(READER).get("/api/v1/offenses", params={"offset": -1})
        assert resp.status_code == 422

    def test_magnitude_filter_is_range_checked(self, db_schema) -> None:
        assert client_for(READER).get(
            "/api/v1/offenses", params={"min_magnitude": 99}
        ).status_code == 422

    def test_unknown_offense_is_404_for_an_authorized_caller(self, db_schema) -> None:
        resp = client_for(READER).get("/api/v1/offenses/987654")
        assert resp.status_code == 404

    def test_history_of_an_unknown_offense_is_404(self, db_schema) -> None:
        resp = client_for(READER).get("/api/v1/offenses/987654/history")
        assert resp.status_code == 404

    def test_analytics_responds_on_an_empty_dataset(self, db_schema) -> None:
        """Aggregates over nothing must be nulls, not an error."""
        resp = client_for(READER).get("/api/v1/offenses/analytics")
        assert resp.status_code == 200


class TestRuleApi:
    def test_listing_is_paginated(self, db_schema) -> None:
        resp = client_for(READER).get("/api/v1/rules", params={"limit": 5})
        assert resp.status_code == 200
        assert len(resp.json()["items"]) <= 5

    def test_health_summary_responds_on_an_empty_dataset(self, db_schema) -> None:
        resp = client_for(READER).get("/api/v1/rules/health-summary")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_unknown_rule_is_404(self, db_schema) -> None:
        resp = client_for(READER).get(
            "/api/v1/rules/00000000-0000-0000-0000-000000000000"
        )
        assert resp.status_code == 404

    def test_malformed_rule_id_is_422(self, db_schema) -> None:
        assert client_for(READER).get("/api/v1/rules/not-a-uuid").status_code == 422


class TestCoverageApi:
    @pytest.mark.asyncio
    async def test_summary_reflects_persisted_rows(self, db_session) -> None:
        inst = await make_instance(db_session)
        rule = AnalyticsRule(
            instance_id=inst.id, qradar_id=1, name="R",
            health_status=RuleHealthStatus.HEALTHY,
        )
        db_session.add(rule)
        await db_session.flush()
        db_session.add(
            TechniqueMapping(
                instance_id=inst.id, technique_id="T1059", rule_id=rule.id,
                source=MappingSource.EXPLICIT, confidence=1.0,
            )
        )
        db_session.add_all(
            [
                DetectionCoverage(
                    instance_id=inst.id, technique_id="T1059",
                    status=CoverageStatus.COVERED, coverage_score=1.0,
                ),
                DetectionCoverage(
                    instance_id=inst.id, technique_id="T1003",
                    status=CoverageStatus.MISSING, coverage_score=0.0,
                ),
                DetectionCoverage(
                    instance_id=inst.id, technique_id="T1078",
                    status=CoverageStatus.DEGRADED, coverage_score=0.5,
                ),
            ]
        )
        await db_session.commit()

        client = client_for(READER)
        summary = client.get("/api/v1/coverage/summary")
        assert summary.status_code == 200

        degraded = client.get("/api/v1/coverage/degraded").json()
        missing = client.get("/api/v1/coverage/missing").json()
        assert {t["technique_id"] for t in degraded["items"]} == {"T1078"}
        assert {t["technique_id"] for t in missing["items"]} == {"T1003"}

    def test_unknown_technique_is_404(self, db_schema) -> None:
        resp = client_for(READER).get("/api/v1/coverage/techniques/T9999")
        assert resp.status_code == 404

    def test_by_rule_and_by_data_source_views_respond(self, db_schema) -> None:
        client = client_for(READER)
        assert client.get("/api/v1/coverage/by-rule").status_code == 200
        assert client.get("/api/v1/coverage/by-data-source").status_code == 200


async def make_rule(session, *, qradar_id: int = 100) -> AnalyticsRule:
    inst = await make_instance(session)
    rule = AnalyticsRule(
        instance_id=inst.id, qradar_id=qradar_id, name=f"rule-{qradar_id}",
        health_status=RuleHealthStatus.UNKNOWN,
    )
    session.add(rule)
    await session.flush()
    return rule


async def audit_rows(session, action: str) -> list[AuditLog]:
    stmt = select(AuditLog).where(AuditLog.action == action)
    return list((await session.scalars(stmt)).all())


# ============================================================================
class TestRuleWriteAuthorization:
    """PATCH /rules/{id} needs rule:write on top of the router's read:rules.

    A `read:*` principal must not be able to edit SOC-owned rule metadata, and
    authorization is decided before the rule is looked up so a forbidden caller
    cannot probe which ids exist.
    """

    @pytest.mark.asyncio
    async def test_a_read_only_principal_cannot_patch_and_the_row_is_unchanged(
        self, db_session
    ) -> None:
        rule = await make_rule(db_session)
        rule_id = str(rule.id)
        await db_session.commit()

        denied = client_for(READER)
        resp = denied.patch(f"/api/v1/rules/{rule_id}", json={"soc_notes": "tampered"})
        assert resp.status_code == 403
        assert denied.get(f"/api/v1/rules/{rule_id}").json()["soc_notes"] is None
        assert await audit_rows(db_session, "rule.update") == []

    @pytest.mark.asyncio
    async def test_a_read_only_principal_gets_403_before_404(self, db_session) -> None:
        resp = client_for(READER).patch(
            "/api/v1/rules/00000000-0000-0000-0000-000000000000",
            json={"soc_notes": "x"},
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_rule_write_grants_the_patch_and_records_the_actor(
        self, db_session
    ) -> None:
        rule = await make_rule(db_session)
        rule_id = str(rule.id)
        await db_session.commit()

        resp = client_for(RULE_EDITOR).patch(
            f"/api/v1/rules/{rule_id}",
            json={"soc_notes": "tuned", "owner": "soc-team"},
        )
        assert resp.status_code == 200
        assert resp.json()["soc_notes"] == "tuned"

        rows = await audit_rows(db_session, "rule.update")
        assert len(rows) == 1
        assert rows[0].actor == "rule-editor"
        assert rows[0].object_type == "rule"
        assert rows[0].object_id == rule_id
        assert rows[0].detail["changes"]["soc_notes"] == {"from": None, "to": "tuned"}

    @pytest.mark.asyncio
    async def test_the_rule_write_permission_does_not_grant_coverage(
        self, db_session
    ) -> None:
        rule = await make_rule(db_session)
        await db_session.commit()
        resp = client_for(RULE_EDITOR).post(
            "/api/v1/coverage/mappings",
            json={"technique_id": "T1059", "rule_id": str(rule.id)},
        )
        assert resp.status_code == 403


# ============================================================================
class TestCoverageWriteAuthorization:
    """POST /coverage/mappings needs coverage:write, not just read:coverage."""

    @pytest.mark.asyncio
    async def test_a_read_only_principal_cannot_create_a_mapping(
        self, db_session
    ) -> None:
        rule = await make_rule(db_session)
        await db_session.commit()

        resp = client_for(READER).post(
            "/api/v1/coverage/mappings",
            json={"technique_id": "T1059", "rule_id": str(rule.id)},
        )
        assert resp.status_code == 403
        assert list((await db_session.scalars(select(TechniqueMapping))).all()) == []
        assert await audit_rows(db_session, "coverage.mapping.create") == []

    @pytest.mark.asyncio
    async def test_a_read_only_principal_gets_403_before_404(self, db_session) -> None:
        # An instance exists, so without the guard an unknown rule would be 404.
        await make_rule(db_session)
        await db_session.commit()

        resp = client_for(READER).post(
            "/api/v1/coverage/mappings",
            json={
                "technique_id": "T1059",
                "rule_id": "00000000-0000-0000-0000-000000000000",
            },
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_coverage_write_creates_the_mapping_and_records_the_actor(
        self, db_session
    ) -> None:
        rule = await make_rule(db_session)
        await db_session.commit()

        resp = client_for(COVERAGE_EDITOR).post(
            "/api/v1/coverage/mappings",
            json={
                "technique_id": "T1059",
                "technique_name": "Command and Scripting Interpreter",
                "rule_id": str(rule.id),
            },
        )
        assert resp.status_code == 201
        assert resp.json()["technique_id"] == "T1059"

        rows = await audit_rows(db_session, "coverage.mapping.create")
        assert len(rows) == 1
        assert rows[0].actor == "coverage-editor"
        assert rows[0].object_type == "technique_mapping"
        assert rows[0].detail["technique_id"] == "T1059"

    @pytest.mark.asyncio
    async def test_the_coverage_write_permission_does_not_grant_rules(
        self, db_session
    ) -> None:
        rule = await make_rule(db_session)
        rule_id = str(rule.id)
        await db_session.commit()
        resp = client_for(COVERAGE_EDITOR).patch(
            f"/api/v1/rules/{rule_id}", json={"soc_notes": "x"}
        )
        assert resp.status_code == 403


class TestProviderCapabilitiesApi:
    def test_capabilities_never_leak_a_credential(self, db_schema) -> None:
        """The endpoint reports posture, not configuration."""
        body = client_for(READER).get("/api/v1/providers/capabilities").json()
        serialized = str(body).lower()
        for forbidden in ("sec_token", "password", "token", "ca_bundle", "console_host"):
            assert forbidden not in serialized

    def test_capabilities_report_the_blocked_write_tools(self, db_schema) -> None:
        body = client_for(READER).get("/api/v1/providers/capabilities").json()
        assert len(body["mcp_blocked_write_tools"]) == 25
        assert "update_offense" in body["mcp_blocked_write_tools"]
        assert "create_ariel_search" in body["mcp_blocked_write_tools"]
        # The allowlist and the blocked set must never intersect.
        assert not set(body["mcp_allowlisted_tools"]) & set(
            body["mcp_blocked_write_tools"]
        )


# ============================================================================
class TestEnumFieldJsonBodies:
    """The enum fields these routes return must serialize exactly as before.

    Handing the response models their declared enum members again (instead of
    the plain str the sanitizer used to produce) is a serializer-type fix, so
    each body here is compared whole, not field by field.
    """

    @pytest.mark.asyncio
    async def test_a_degraded_technique_item_body_is_unchanged(
        self, db_session
    ) -> None:
        inst = await make_instance(db_session)
        db_session.add(
            DetectionCoverage(
                instance_id=inst.id,
                technique_id="T1078",
                status=CoverageStatus.DEGRADED,
                coverage_score=0.5,
                degraded_rule_count=1,
            )
        )
        await db_session.commit()

        body = client_for(READER).get("/api/v1/coverage/degraded").json()
        assert body["total"] == 1
        assert body["items"] == [
            {
                "instance_id": str(inst.id),
                "technique_id": "T1078",
                "technique_name": None,
                "tactic": None,
                "status": "DEGRADED",
                "coverage_score": 0.5,
                "confidence": 1.0,
                "mapped_rule_count": 0,
                "enabled_rule_count": 0,
                "firing_rule_count": 0,
                "inferred_rule_count": 0,
                "degraded_rule_count": 1,
                "last_evaluated_at": None,
                "logic_version": 1,
                "reason": None,
            }
        ]

    @pytest.mark.asyncio
    async def test_a_rule_detail_body_reports_health_status(
        self, db_session
    ) -> None:
        rule = await make_rule(db_session)
        await db_session.commit()

        body = client_for(READER).get(f"/api/v1/rules/{rule.id}").json()
        assert body == {
            "id": str(rule.id),
            "qradar_id": 100,
            "name": "rule-100",
            "enabled": True,
            "is_building_block": False,
            "rule_type": None,
            "origin": None,
            "owner": None,
            "health_status": "UNKNOWN",
            "health_evaluated_at": None,
            "health_reason": None,
            "last_fired_at": None,
            "offense_contribution_count": 0,
            "event_contribution_count": 0,
            "mitre_techniques": [],
            "description": None,
            "categories": [],
            "average_capacity": None,
            "generates_offense": None,
            "response_actions": [],
            "qradar_created_at": None,
            "qradar_modified_at": None,
            "first_seen_at": None,
            "soc_notes": None,
            "expected_daily_firings": None,
            "health_monitoring_enabled": True,
            "dependencies": [],
        }

    @pytest.mark.asyncio
    async def test_a_created_mapping_body_reports_source(
        self, db_session
    ) -> None:
        rule = await make_rule(db_session)
        await db_session.commit()

        resp = client_for(COVERAGE_EDITOR).post(
            "/api/v1/coverage/mappings",
            json={"technique_id": "T1059", "rule_id": str(rule.id)},
        )
        assert resp.status_code == 201
        body = resp.json()
        # The id is generated on insert; only its shape is pinned here.
        assert uuid.UUID(body.pop("id"))
        assert body == {
            "technique_id": "T1059",
            "technique_name": None,
            "tactic": None,
            "rule_id": str(rule.id),
            "source": "EXPLICIT",
            "confidence": 1.0,
            "provenance": None,
        }
