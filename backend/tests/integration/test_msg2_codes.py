"""MSG-2: coverage and rule-statistics sentences carry their codes, stored and served."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.i18n.messages import MessageRef, render
from app.models.enums import CoverageStatus, MappingSource, RuleHealthStatus
from app.models.rule import DetectionCoverage, DetectionCoverageSnapshot, TechniqueMapping
from app.services.detection_coverage import DetectionCoverageEvaluator
from tests.integration.factories import make_instance
from tests.integration.test_phase3_api import READER, client_for
from tests.integration.test_rule_health_coverage import NOW as COVERAGE_NOW
from tests.integration.test_rule_health_coverage import make_rule
from tests.integration.test_rule_statistics import add_rule, add_windows, stats

pytestmark = pytest.mark.integration


async def _map(session, inst, rule, technique_id: str, confidence: float = 1.0) -> None:
    session.add(
        TechniqueMapping(
            instance_id=inst.id,
            technique_id=technique_id,
            technique_name="Command and Scripting Interpreter",
            tactic="Execution",
            rule_id=rule.id,
            source=MappingSource.EXPLICIT,
            confidence=confidence,
        )
    )
    await session.flush()


class TestCoverageCodes:
    @pytest.mark.asyncio
    async def test_the_evaluation_stores_the_code_beside_every_reason(self, db_session) -> None:
        inst = await make_instance(db_session)
        healthy = await make_rule(
            db_session, inst, qradar_id=1, health_status=RuleHealthStatus.HEALTHY
        )
        quiet = await make_rule(
            db_session, inst, qradar_id=2, health_status=RuleHealthStatus.INACTIVE
        )
        low = await make_rule(
            db_session, inst, qradar_id=3, health_status=RuleHealthStatus.HEALTHY
        )
        for rule in (healthy, quiet):
            await _map(db_session, inst, rule, "T1059")
        await _map(db_session, inst, low, "T1059", confidence=0.1)

        await DetectionCoverageEvaluator(db_session, clock=lambda: COVERAGE_NOW).evaluate_instance(
            inst
        )

        row = await db_session.scalar(
            select(DetectionCoverage).where(DetectionCoverage.technique_id == "T1059")
        )
        snap = await db_session.scalar(
            select(DetectionCoverageSnapshot).where(
                DetectionCoverageSnapshot.technique_id == "T1059"
            )
        )
        assert row.status == CoverageStatus.DEGRADED
        expected = {
            "code": "coverage.rollup.some_degraded",
            "params": {"working": 1, "enabled": 2, "degraded": 1},
        }
        assert row.reason_i18n == expected
        assert snap.reason_i18n == expected
        assert row.reason == "1 of 2 enabled rules are detecting; 1 are degraded."
        by_rule = {r["rule_qradar_id"]: r for r in row.evidence["rules"]}
        assert by_rule[1]["reason_code"] == "coverage.evidence.acceptable"
        assert by_rule[1]["reason_params"] == {}
        assert by_rule[2]["reason_code"] == "coverage.evidence.inactive"
        assert by_rule[3]["reason_code"] == "coverage.evidence.below_floor"
        assert by_rule[3]["reason_params"] == {"confidence": 0.1, "floor": 0.5}
        assert by_rule[3]["reason"] == (
            "Mapping confidence 0.10 is below the 0.50 floor; recorded but not counted "
            "toward coverage."
        )

    @pytest.mark.asyncio
    async def test_the_api_serves_the_code_and_old_rows_stay_uncoded(self, db_session) -> None:
        inst = await make_instance(db_session)
        rule = await make_rule(
            db_session, inst, qradar_id=1, health_status=RuleHealthStatus.HEALTHY
        )
        await _map(db_session, inst, rule, "T1059")
        evaluator = DetectionCoverageEvaluator(db_session, clock=lambda: datetime.now(UTC))
        await evaluator.evaluate_instance(inst)
        # A row written before 0035: English reason, no reference, evidence without codes.
        db_session.add(
            DetectionCoverage(
                instance_id=inst.id,
                technique_id="T1003",
                status=CoverageStatus.MISSING,
                coverage_score=0.0,
                reason="No rule is mapped to this technique.",
                evidence={"rules": [{"rule_name": "old", "reason": "The rule is disabled."}]},
            )
        )
        await db_session.commit()

        client = client_for(READER)
        page = client.get("/api/v1/coverage/techniques").json()
        by_id = {t["technique_id"]: t for t in page["items"]}
        assert by_id["T1059"]["reason"] == (
            "All 1 enabled rules mapped to this technique are detecting."
        )
        assert by_id["T1059"]["reason_code"] == "coverage.rollup.all_detecting"
        assert by_id["T1059"]["reason_params"] == {"enabled": 1}
        assert by_id["T1003"]["reason"] == "No rule is mapped to this technique."
        assert by_id["T1003"]["reason_code"] is None
        assert by_id["T1003"]["reason_params"] is None

        detail = client.get("/api/v1/coverage/techniques/T1059").json()
        assert detail["evidence"]["rules"][0]["reason_code"] == "coverage.evidence.acceptable"
        old = client.get("/api/v1/coverage/techniques/T1003").json()
        assert old["evidence"]["rules"][0] == {
            "rule_name": "old",
            "reason": "The rule is disabled.",
        }

        history = client.get("/api/v1/coverage/techniques/T1059/history").json()
        assert history[0]["reason_code"] == "coverage.rollup.all_detecting"
        assert history[0]["reason_params"] == {"enabled": 1}


class TestStatisticsCodes:
    @pytest.mark.asyncio
    async def test_a_partial_window_carries_one_code_in_three_fields(self, db_session) -> None:
        inst = await make_instance(db_session)
        await add_rule(db_session, inst, 1)
        await add_windows(db_session, inst, skip=(7,))

        result = await stats(db_session, inst)

        params = {"complete": 23, "expected": 24, "missing": 1, "truncated": 0, "failed": 0}
        assert result.window.reason == (
            "Only 23 of 24 windows in the span are complete (1 missing, 0 truncated, 0 failed)."
            " Absence from an incomplete window proves nothing."
        )
        assert result.window.reason_code == "rule_statistics.window.incomplete"
        assert result.window.reason_params == params
        assert result.silent_reason == result.window.reason
        assert result.silent_reason_code == "rule_statistics.window.incomplete"
        assert result.silent_reason_params == params
        assert result.top_reason == result.window.reason
        assert result.top_reason_code == "rule_statistics.window.incomplete"
        assert result.top_reason_params == params
        assert result.silent is None

    @pytest.mark.asyncio
    async def test_no_collection_is_coded_and_the_benchmark_sentences_are_too(
        self, db_session
    ) -> None:
        inst = await make_instance(db_session)
        await add_rule(db_session, inst, 1, capacity=2.0)

        result = await stats(db_session, inst)

        assert result.window.reason_code == "rule_statistics.window.none"
        assert result.window.reason_params == {}
        assert result.silent_reason_code == "rule_statistics.window.none"
        assert result.top_reason_code == "rule_statistics.window.none"
        cost = result.costliest_by_benchmark
        assert cost.label_code == "rule_statistics.cost.label"
        assert cost.unit_code == "rule_statistics.cost.unit"
        assert cost.label == render(MessageRef(cost.label_code))
        assert cost.unit == render(MessageRef(cost.unit_code))

    @pytest.mark.asyncio
    async def test_a_complete_span_has_no_reason_code_and_codes_the_exclusion(
        self, db_session
    ) -> None:
        inst = await make_instance(db_session)
        await add_rule(db_session, inst, 1, rule_type="FLOW")
        await add_windows(db_session, inst)

        result = await stats(db_session, inst)

        assert result.window.complete is True
        assert result.window.reason is None and result.window.reason_code is None
        assert result.window.reason_params is None
        assert result.silent_reason is None and result.silent_reason_code is None
        assert result.silent is not None
        assert result.silent.excluded_reason_code == "rule_statistics.silent.excluded"
        assert result.silent.excluded_reason_params == {}
        assert result.silent.excluded_reason == render(
            MessageRef("rule_statistics.silent.excluded")
        )

    @pytest.mark.asyncio
    async def test_the_api_serves_the_codes(self, db_session) -> None:
        inst = await make_instance(db_session)
        await add_rule(db_session, inst, 1)
        await add_windows(db_session, inst, skip=(7,))
        await db_session.commit()

        body = client_for(READER).get("/api/v1/rules/statistics").json()

        assert body["window"]["reason_code"] == "rule_statistics.window.incomplete" or (
            body["window"]["reason_code"] == "rule_statistics.window.none"
        )
        assert body["silent_reason_code"] == body["window"]["reason_code"]
        assert body["top_reason_code"] == body["window"]["reason_code"]
        assert body["costliest_by_benchmark"]["label_code"] == "rule_statistics.cost.label"
        assert body["costliest_by_benchmark"]["unit_params"] == {}
