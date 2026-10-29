"""The custom-rule engine stores each status reason with its message reference (MSG-7)."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.custom_rules.engine import CustomRuleEngine
from app.i18n.messages import MessageRef, render
from app.models.custom_rule import CustomRule
from tests.integration.factories import add_metric, make_instance, make_log_source, utc
from tests.integration.test_custom_rules import _rule, _settings

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_stored_reason_and_its_reference_are_written_together(db_session) -> None:
    instance = await make_instance(db_session)
    loud = await make_log_source(db_session, instance, qradar_id=82, name="loud")
    quiet = await make_log_source(db_session, instance, qradar_id=83, name="quiet")
    await add_metric(db_session, loud, utc(h=10, mi=5), event_count=10)
    quiet_rule = _rule("quiet-source", quiet, sustain_buckets=1)
    db_session.add_all([quiet_rule, _rule("loud-source", loud, sustain_buckets=1)])
    await db_session.flush()
    settings = _settings()

    await CustomRuleEngine(db_session, settings, clock=lambda: utc(h=10)).run()
    await CustomRuleEngine(db_session, settings, clock=lambda: utc(h=10, mi=12)).run()

    assert quiet_rule.last_status == "NO_VERDICT"
    expected = render(MessageRef("custom_rule.reading.subject_missing"))
    assert quiet_rule.last_status_reason == expected
    assert quiet_rule.last_status_reason_i18n == {
        "code": "custom_rule.reading.subject_missing",
        "params": {},
    }
    assert quiet_rule.last_status_reason_code == "custom_rule.reading.subject_missing"
    assert quiet_rule.last_status_reason_params == {}
    loud_row = (
        await db_session.execute(select(CustomRule).where(CustomRule.name == "loud-source"))
    ).scalar_one()
    assert loud_row.last_status == "OK"
    # A status with no reason carries no reference either.
    assert loud_row.last_status_reason is None
    assert loud_row.last_status_reason_i18n is None
