"""Breakdown collection and detection through the scheduled WAF source path."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.enums import AnomalyState, WafAnomalyType, WafTelemetryStatus
from app.models.waf import (
    WafAnomaly,
    WafEndpointBaseline,
    WafEndpointMetric,
    WafSourceBucket,
)
from app.providers.dto import WafBreakdownBatch, WafBreakdownGroup
from app.waf.baseline import BLOCK_RATIO_METRIC
from app.waf.profile import WafFieldMapping
from tests.integration import test_waf_productionization as production

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

AUTHORED = WafFieldMapping(
    url="URL",
    method="HTTP_Method",
    action="WAF_Action",
    response_code="WAF_ResponseCode",
    blocked_actions=("DENY",),
    allowed_actions=("ALLOW",),
)
ACTION_ONLY = WafFieldMapping(
    url="URL",
    method="HTTP_Method",
    action="WAF_Action",
    blocked_actions=("DENY",),
    allowed_actions=("ALLOW",),
)

Row = tuple[str, str, str, int]


class ScheduledBreakdownProvider(production.StubWafProvider):
    """The production-path stub plus shaped breakdown results by bucket."""

    def __init__(self, batches=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(batches)
        self.breakdowns: dict[tuple[str, object], list[Row]] = {}
        self.breakdown_calls: list[dict] = []

    def serve(self, kind: str, start, rows: list[Row]) -> None:  # type: ignore[no-untyped-def]
        self.breakdowns[(kind, start)] = rows

    async def get_waf_breakdown(self, **kw):  # type: ignore[no-untyped-def, override]
        self.breakdown_calls.append(kw)
        rows = self.breakdowns.get((kw["breakdown"], kw["window_start"]), [])
        return WafBreakdownBatch(
            breakdown=kw["breakdown"],
            qradar_log_source_id=kw["qradar_log_source_id"],
            window_start=kw["window_start"],
            window_end=kw["window_end"],
            groups=[
                WafBreakdownGroup(
                    raw_url=url,
                    raw_method=method,
                    raw_value=value,
                    request_count=count,
                )
                for url, method, value, count in rows
            ],
            query=f"STUB {kw['breakdown']}",
            ariel_searches=1,
        )


def settings(**overrides):  # type: ignore[no-untyped-def]
    values = {
        "waf_outcome_collection_enabled": True,
        "waf_client_collection_enabled": True,
    }
    values.update(overrides)
    return production.cfg(**values)


def serve_all(provider: ScheduledBreakdownProvider, start) -> None:  # type: ignore[no-untyped-def]
    login = ("/portal/login", "POST")
    provider.serve("ACTION", start, [(*login, "DENY", 20), (*login, "ALLOW", 80)])
    provider.serve(
        "RESPONSE_CODE",
        start,
        [(*login, "200", 80), (*login, "403", 15), (*login, "500", 5)],
    )
    provider.serve(
        "SOURCE_IP",
        start,
        [(*login, "198.51.100.1", 60), (*login, "203.0.113.2", 40)],
    )


async def collected_rows(db_session, source):  # type: ignore[no-untyped-def]
    bucket = await db_session.get(WafSourceBucket, (source.id, production.T0))
    metric = await db_session.scalar(select(WafEndpointMetric))
    assert bucket is not None and metric is not None
    return bucket, metric


async def test_scheduled_run_collects_the_plan_and_reports_it(db_session) -> None:
    configured = settings()
    provider = ScheduledBreakdownProvider(
        [production.batch(production.T0, [("/portal/login", "POST", 100)])]
    )
    serve_all(provider, production.T0)
    profile, source = await production.source_with_profile(
        db_session, provider, configured, mapping=AUTHORED
    )

    report = await production.run_at(
        db_session, profile, provider, configured, production.T0
    )
    bucket, metric = await collected_rows(db_session, source)

    assert report["planned_breakdowns"] == ["ACTION", "RESPONSE_CODE", "SOURCE_IP"]
    assert report["skipped_breakdowns"] == {}
    assert {call["breakdown"] for call in provider.breakdown_calls} == {
        "ACTION",
        "RESPONSE_CODE",
        "SOURCE_IP",
    }
    assert report["ariel_searches"] == 3
    assert bucket.action_measurement == WafTelemetryStatus.EXACT
    assert bucket.response_code_measurement == WafTelemetryStatus.EXACT
    assert bucket.client_measurement == WafTelemetryStatus.EXACT
    assert (metric.action_events, metric.blocked_count) == (100, 20)
    assert (metric.response_code_events, metric.client_error_count, metric.server_error_count) == (
        100,
        15,
        5,
    )
    assert (metric.client_events, metric.top1_client_requests, metric.client_sources) == (
        100,
        60,
        2,
    )


async def test_scheduled_run_with_breakdown_settings_off_issues_no_search(db_session) -> None:
    configured = settings(
        waf_outcome_collection_enabled=False,
        waf_client_collection_enabled=False,
    )
    provider = ScheduledBreakdownProvider(
        [production.batch(production.T0, [("/portal/login", "POST", 100)])]
    )
    profile, source = await production.source_with_profile(
        db_session, provider, configured, mapping=AUTHORED
    )

    report = await production.run_at(
        db_session, profile, provider, configured, production.T0
    )
    bucket, metric = await collected_rows(db_session, source)

    assert report["planned_breakdowns"] == []
    assert set(report["skipped_breakdowns"]) == {"ACTION", "RESPONSE_CODE", "SOURCE_IP"}
    assert provider.observation_calls == 1
    assert provider.breakdown_calls == []
    assert report["ariel_searches"] == 0
    assert bucket.action_measurement == WafTelemetryStatus.NOT_COLLECTED
    assert bucket.response_code_measurement == WafTelemetryStatus.NOT_COLLECTED
    assert bucket.client_measurement == WafTelemetryStatus.NOT_COLLECTED
    assert metric.action_events is None
    assert metric.response_code_events is None
    assert metric.client_events is None


async def test_missing_action_map_skips_only_action_and_reports_the_reason(db_session) -> None:
    configured = settings()
    provider = ScheduledBreakdownProvider(
        [production.batch(production.T0, [("/portal/login", "POST", 100)])]
    )
    serve_all(provider, production.T0)
    profile, source = await production.source_with_profile(
        db_session, provider, configured, mapping=production.LAB_MAPPING
    )

    report = await production.run_at(
        db_session, profile, provider, configured, production.T0
    )
    bucket, _metric = await collected_rows(db_session, source)

    assert report["planned_breakdowns"] == ["RESPONSE_CODE", "SOURCE_IP"]
    assert set(report["skipped_breakdowns"]) == {"ACTION"}
    assert "BLOCKED" in report["skipped_breakdowns"]["ACTION"]
    assert {call["breakdown"] for call in provider.breakdown_calls} == {
        "RESPONSE_CODE",
        "SOURCE_IP",
    }
    assert bucket.action_measurement == WafTelemetryStatus.NOT_COLLECTED
    assert bucket.response_code_measurement == WafTelemetryStatus.EXACT
    assert bucket.client_measurement == WafTelemetryStatus.EXACT


async def test_scheduled_cycles_train_and_open_a_block_ratio_incident(db_session) -> None:
    configured = settings(
        waf_client_collection_enabled=False,
        anomaly_open_after_intervals=2,
        baseline_min_samples=4,
        waf_ratio_min_denominator=10,
        waf_ratio_min_numerator=2,
        waf_ratio_min_increase=0.10,
        waf_ratio_min_multiple=2.0,
    )
    starts = [production.T0 + production.STEP * index for index in range(6)]
    provider = ScheduledBreakdownProvider(
        [production.batch(start, [("/portal/login", "POST", 100)]) for start in starts]
    )
    for index, start in enumerate(starts):
        blocked = 10 if index < 4 else 70
        provider.serve(
            "ACTION",
            start,
            [
                ("/portal/login", "POST", "DENY", blocked),
                ("/portal/login", "POST", "ALLOW", 100 - blocked),
            ],
        )
    profile, _source = await production.source_with_profile(
        db_session, provider, configured, mapping=ACTION_ONLY
    )

    reports = [
        await production.run_at(db_session, profile, provider, configured, start)
        for start in starts
    ]

    baseline = await db_session.scalar(
        select(WafEndpointBaseline).where(
            WafEndpointBaseline.metric_name == BLOCK_RATIO_METRIC
        )
    )
    incident = await db_session.scalar(
        select(WafAnomaly).where(
            WafAnomaly.detector_type == WafAnomalyType.WAF_BLOCK_RATIO_SPIKE
        )
    )
    assert {report["status"] for report in reports} == {"ok"}
    assert baseline is not None and baseline.is_reliable
    assert baseline.median == pytest.approx(0.10)
    assert incident is not None
    assert incident.state == AnomalyState.OPEN
    assert incident.observed_value == pytest.approx(0.70)
