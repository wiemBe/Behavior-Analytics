"""MSG-3: rule-health verdict reasons carry their codes, stored and served."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.i18n.messages import MessageRef, render
from app.models.enums import RuleHealthStatus
from app.models.rule import AnalyticsRule, RuleHealthSnapshot
from tests.integration.factories import make_instance
from tests.integration.test_phase3_api import READER, client_for
from tests.integration.test_rule_health_coverage import (
    NOW,
    evaluator,
    make_rule,
    snapshots_for,
)

pytestmark = pytest.mark.integration

# A dependency clause whose QRadar name carries markup: the reason text and the `name`
# inside the params must both leave the API sanitised.
NAME = "<script>BB</script> Admin Hosts"
CLEAN_NAME = "BB Admin Hosts"
DEGRADED = MessageRef(
    "rule_health.dependency_degraded",
    {
        "dependencies": [
            MessageRef(
                "rule_health.dependency.disabled",
                {"label": MessageRef("rule_health.label.named", {"name": NAME})},
            ),
            MessageRef(
                "rule_health.dependency.not_observed",
                {"label": MessageRef("rule_health.label.building_block", {"ref": "78"})},
            ),
        ]
    },
)


async def _snap(
    session,
    rule: AnalyticsRule,
    evaluated_at: datetime,
    *,
    reason: str | None,
    reason_i18n: dict | None,
    shown: bool = True,
) -> None:
    """One snapshot row; `shown` moves the rule's badge to this evaluation."""
    session.add(
        RuleHealthSnapshot(
            rule_id=rule.id,
            evaluated_at=evaluated_at,
            status=RuleHealthStatus.DEPENDENCY_DEGRADED,
            reason=reason,
            reason_i18n=reason_i18n,
        )
    )
    if shown:
        rule.health_evaluated_at = evaluated_at
        rule.health_status = RuleHealthStatus.DEPENDENCY_DEGRADED
    await session.flush()


class TestRuleHealthCodes:
    @pytest.mark.asyncio
    async def test_an_evaluation_stores_the_code_beside_the_reason(self, db_session) -> None:
        inst = await make_instance(db_session)
        rule = await make_rule(
            db_session,
            inst,
            enabled=False,
            health_status=RuleHealthStatus.DISABLED,
        )

        await evaluator(db_session).evaluate_instance(inst)

        (snap,) = await snapshots_for(db_session, rule)
        assert snap.reason == "The rule is disabled in QRadar, so it cannot produce detections."
        assert snap.reason_i18n == {"code": "rule_health.disabled", "params": {}}
        assert snap.reason_code == "rule_health.disabled"
        assert snap.reason_params == {}

    @pytest.mark.asyncio
    async def test_the_wire_serves_the_code_from_the_same_row_as_the_text(
        self, db_session
    ) -> None:
        inst = await make_instance(db_session)
        rule = await make_rule(db_session, inst, qradar_id=1)
        shown_at = NOW
        # A newer snapshot exists, but the rule's badge still names the older evaluation:
        # the served reason must come from the row the verdict came from.
        await _snap(
            db_session,
            rule,
            shown_at,
            reason=render(DEGRADED),
            reason_i18n=DEGRADED.to_wire(),
        )
        await _snap(
            db_session,
            rule,
            shown_at + timedelta(hours=1),
            reason="The rule is disabled in QRadar, so it cannot produce detections.",
            reason_i18n={"code": "rule_health.disabled", "params": {}},
            shown=False,
        )
        await db_session.commit()

        client = client_for(READER)
        page = client.get("/api/v1/rules").json()
        assert page["items"][0]["health_reason"] == (
            f"The rule cannot fire reliably: {CLEAN_NAME} is disabled; "
            "building block 78 has not been observed. "
            "Its silence is not evidence about the rule itself."
        )
        item = page["items"][0]
        assert item["health_reason_code"] == "rule_health.dependency_degraded"
        dependencies = item["health_reason_params"]["dependencies"]
        assert [clause["code"] for clause in dependencies["items"]] == [
            "rule_health.dependency.disabled",
            "rule_health.dependency.not_observed",
        ]
        # The QRadar name inside the params is sanitised like the text, the nested
        # reference included; a ref the registry does not allow stays as it was stored.
        assert dependencies["items"][0]["params"]["label"] == {
            "code": "rule_health.label.named",
            "params": {"name": CLEAN_NAME},
        }
        assert dependencies["items"][1]["params"]["label"] == {
            "code": "rule_health.label.building_block",
            "params": {"ref": "78"},
        }

        detail = client.get(f"/api/v1/rules/{rule.id}").json()
        assert detail["health_reason"] == item["health_reason"]
        assert detail["health_reason_code"] == item["health_reason_code"]
        assert detail["health_reason_params"] == item["health_reason_params"]

        history = client.get(f"/api/v1/rules/{rule.id}/health-history?days=365").json()
        newest = history[0]
        assert newest["evaluated_at"] == "2026-07-20T13:00:00Z"
        assert newest["reason_code"] == "rule_health.disabled"
        assert newest["reason_params"] == {}
        coded = history[1]
        assert coded["reason_code"] == "rule_health.dependency_degraded"
        label = coded["reason_params"]["dependencies"]["items"][0]["params"]["label"]
        assert label["params"]["name"] == CLEAN_NAME

    @pytest.mark.asyncio
    async def test_a_snapshot_without_a_code_keeps_its_english_and_no_code(
        self, db_session
    ) -> None:
        inst = await make_instance(db_session)
        rule = await make_rule(db_session, inst, qradar_id=1)
        await _snap(
            db_session,
            rule,
            NOW,
            reason="The rule is disabled in QRadar, so it cannot produce detections.",
            reason_i18n=None,
        )
        await db_session.commit()

        client = client_for(READER)
        item = client.get("/api/v1/rules").json()["items"][0]
        assert item["health_reason"] == (
            "The rule is disabled in QRadar, so it cannot produce detections."
        )
        assert item["health_reason_code"] is None
        assert item["health_reason_params"] is None
        history = client.get(f"/api/v1/rules/{rule.id}/health-history?days=365").json()
        assert history[0]["reason_code"] is None
        assert history[0]["reason_params"] is None

    @pytest.mark.asyncio
    async def test_a_corrupt_reference_fails_closed_to_the_text(self, db_session) -> None:
        inst = await make_instance(db_session)
        rule = await make_rule(db_session, inst, qradar_id=1)
        # Not the shape the evaluator writes: a code with no params, as a hand-edited
        # row could hold. The reader must not pair that code with this text.
        await _snap(
            db_session,
            rule,
            NOW,
            reason="The rule is disabled in QRadar, so it cannot produce detections.",
            reason_i18n={"code": "rule_health.grace"},
        )
        await db_session.commit()

        client = client_for(READER)
        item = client.get("/api/v1/rules").json()["items"][0]
        assert item["health_reason"] == (
            "The rule is disabled in QRadar, so it cannot produce detections."
        )
        assert item["health_reason_code"] is None
        assert item["health_reason_params"] is None
