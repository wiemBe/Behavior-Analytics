"""MSG-7 coded alert text and custom-rule status reasons."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.custom_rules.evaluate import (
    BucketFacts,
    collection_silence_message,
    interpret,
)
from app.i18n.messages import MessageRef, render
from app.models.enums import CustomRuleMetric
from tests.unit.test_custom_rule_evaluate import _fold, _reading


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (MessageRef("alert.threshold.crossed", {"name": "Daily failures"}),
         "Daily failures crossed its threshold"),
        (MessageRef("alert.search.failure.description", {
            "name": "Daily failures", "failures": 1204, "error_type": "ConnectTimeout"
        }), "Daily failures failed 1204 consecutive runs (ConnectTimeout)"),
        (MessageRef("alert.custom_rule.description.exact_unit", {
            "name": "High traffic", "observed": 0.0, "unit": "events",
            "operator": "GT", "threshold": 1.25,
        }), "High traffic observed 0 events (GT 1.25 events)"),
        (MessageRef("alert.custom_rule.description.lower_bound_unit", {
            "name": "High traffic", "observed": 1204.5, "unit": "events",
            "operator": "GE", "threshold": 1000.0,
        }), "High traffic observed at least 1204.5 events (GE 1000 events)"),
        (MessageRef("alert.inventory.catchall_sender.reviewed", {"target": "DISMISSED"}),
         "catch-all sender dismissed"),
    ],
)
def test_alert_english_is_byte_for_byte(message: MessageRef, expected: str) -> None:
    assert render(message) == expected


@pytest.mark.parametrize(
    ("metric", "facts", "code"),
    [
        (CustomRuleMetric.EVENT_COUNT, BucketFacts(
            bucket_start=datetime(2026, 10, 29, tzinfo=UTC), present=False
        ), "custom_rule.reading.subject_missing"),
        (CustomRuleMetric.EVENT_COUNT, BucketFacts(
            bucket_start=datetime(2026, 10, 29, tzinfo=UTC), present=True,
            completeness="PARTIAL", event_count=5,
        ), "custom_rule.reading.bucket_partial"),
        (CustomRuleMetric.BLOCK_RATIO, BucketFacts(
            bucket_start=datetime(2026, 10, 29, tzinfo=UTC), present=True,
            action_measurement="LOWER_BOUND", blocked_count=2, action_events=3,
        ), "custom_rule.reading.action_denominator_lower_bound"),
        (CustomRuleMetric.SERVER_ERROR_RATIO, BucketFacts(
            bucket_start=datetime(2026, 10, 29, tzinfo=UTC), present=True,
            response_code_measurement="EXACT", server_error_count=1,
            response_code_events=0, unclassified_response_count=0,
        ), "custom_rule.reading.response_denominator_zero"),
    ],
)
def test_interpretation_reason_has_its_source_code(
    metric: CustomRuleMetric, facts: BucketFacts, code: str
) -> None:
    reading = interpret(metric, facts)
    assert reading.reason_message is not None
    assert reading.reason_message.code == code
    assert reading.reason == render(reading.reason_message)


def test_stale_reason_preserves_minute_and_unbounded_hour_english() -> None:
    watermark = datetime(2026, 10, 28, 9, 0, tzinfo=UTC)
    message = collection_silence_message(
        watermark=watermark,
        closed=datetime(2026, 10, 29, 10, 0, tzinfo=UTC),
        now=datetime(2026, 10, 29, 10, 1, 1, tzinfo=UTC),
        bound_seconds=60,
    )
    assert message is not None
    assert message.code == "custom_rule.status.stale.minute"
    assert render(message) == (
        "no bucket collected for this kind since 2026-10-28 09:00 UTC (25 h 1 min 1 s)"
    )


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"platform_enabled": False}, "custom_rule.status.platform_off"),
        ({"enabled": False}, "custom_rule.status.disabled"),
        ({"retired": True}, "custom_rule.status.subject_retired"),
        ({"family_collected": False}, "custom_rule.status.family_not_collected"),
        ({"threshold_value": float("nan"), "readings": (_reading(0, 1),)},
         "custom_rule.status.threshold_not_finite"),
        ({"operator": "XX", "readings": (_reading(0, 1),)},
         "custom_rule.status.operator_unknown"),
        ({
            "metric": CustomRuleMetric.REQUEST_COUNT,
            "operator": "LT",
            "readings": (_reading(0, 101, "LOWER_BOUND"),),
        },
         "custom_rule.status.lower_bound_cannot_prove_under"),
    ],
)
def test_fold_exit_reason_carries_code(overrides: dict[str, object], code: str) -> None:
    output = _fold(**overrides)
    assert output.status_reason_message is not None
    assert output.status_reason_message.code == code
    assert output.status_reason == render(output.status_reason_message)
