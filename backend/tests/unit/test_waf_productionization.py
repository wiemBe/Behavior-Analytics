"""Phase B.1 pure rules: admission, metric semantics, maintenance, profile
mapping and validation judgement, plus the REST property probe."""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.anomaly.seasonal import in_maintenance
from app.core.config import Settings, get_settings
from app.models.enums import (
    BucketCompleteness,
    DetectorSignal,
    WafFieldStatus,
    WafLogicalField,
    WafProfileValidationStatus,
)
from app.providers.dto import WafPropertyProbe, WafPropertyValue, WafSourceProbe
from app.waf.admission import AdmissionPolicy, decide_admission
from app.waf.detector import (
    WafBaselineCell,
    WafDetectionContext,
    WafPoint,
    WafThresholds,
    detect_request_rate_spike,
)
from app.waf.profile import WafFieldMapping, WafProfileError, validate_property_name
from app.waf.semantics import request_count_semantics, semantics_incompatibility
from app.waf.validation import evaluate_profile
from tests.unit.test_qradar_rest_provider import build_provider

T0 = datetime(2026, 8, 3, 19, 0, tzinfo=UTC)


# ------------------------------------------------------------------ admission
@dataclass(frozen=True)
class Cand:
    endpoint: str
    request_count: int
    method: str = "GET"
    virtual_host: str | None = None


POLICY = AdmissionPolicy(max_new_per_bucket=20, max_active=2000, min_requests=3)


class TestAdmission:
    def test_a_scanner_burst_admits_only_the_quota_by_volume(self) -> None:
        # 5,000 unseen endpoints; the 20 largest must be admitted and nothing else.
        unseen = [Cand(f"/scan/{i:05d}", request_count=5 + (i % 97)) for i in range(5000)]
        decision = decide_admission(unseen, active_count=0, policy=POLICY)
        assert len(decision.admitted) == 20
        assert decision.truncated
        assert len(decision.untracked) == 4980
        smallest_admitted = min(c.request_count for c in decision.admitted)
        assert all(c.request_count <= smallest_admitted for c in decision.over_quota)
        assert decision.untracked_requests == sum(c.request_count for c in unseen) - sum(
            c.request_count for c in decision.admitted
        )

    def test_order_of_arrival_does_not_change_the_outcome(self) -> None:
        unseen = [Cand(f"/p/{i}", request_count=10) for i in range(200)]
        shuffled = unseen[:]
        random.Random(7).shuffle(shuffled)
        a = decide_admission(unseen, active_count=0, policy=POLICY)
        b = decide_admission(shuffled, active_count=0, policy=POLICY)
        assert [c.endpoint for c in a.admitted] == [c.endpoint for c in b.admitted]

    def test_ties_break_on_host_endpoint_then_method(self) -> None:
        unseen = [
            Cand("/b", 10, "POST"),
            Cand("/b", 10, "GET"),
            Cand("/a", 10, "GET", virtual_host="z.example"),
            Cand("/a", 10, "GET"),
        ]
        policy = AdmissionPolicy(max_new_per_bucket=4, max_active=100, min_requests=1)
        admitted = decide_admission(unseen, active_count=0, policy=policy).admitted
        assert [(c.virtual_host, c.endpoint, c.method) for c in admitted] == [
            (None, "/a", "GET"),
            (None, "/b", "GET"),
            (None, "/b", "POST"),
            ("z.example", "/a", "GET"),
        ]

    def test_below_threshold_is_never_admitted_and_not_truncation(self) -> None:
        decision = decide_admission([Cand("/once", 2)], active_count=0, policy=POLICY)
        assert decision.admitted == []
        assert [c.endpoint for c in decision.below_threshold] == ["/once"]
        assert not decision.truncated

    def test_the_active_ceiling_caps_the_quota(self) -> None:
        policy = AdmissionPolicy(max_new_per_bucket=20, max_active=25, min_requests=1)
        unseen = [Cand(f"/n/{i}", 10) for i in range(30)]
        assert decide_admission(unseen, active_count=22, policy=policy).quota == 3
        at_ceiling = decide_admission(unseen, active_count=25, policy=policy)
        assert at_ceiling.quota == 0 and at_ceiling.admitted == [] and at_ceiling.truncated
        over = decide_admission(unseen, active_count=40, policy=policy)
        assert over.quota == 0


# ------------------------------------------------------------------ semantics
SEMANTICS_BASE = {
    "aggregation": "SUM(eventcount)",
    "normalizer_version": 1,
    "profile_version": 1,
}


class TestSemantics:
    @pytest.mark.parametrize(
        "change",
        [
            {"aggregation": "COUNT(*)"},
            {"aggregation": None},
            {"normalizer_version": 2},
            {"profile_version": 2},
        ],
    )
    def test_every_component_changes_the_identity(self, change: dict) -> None:
        assert request_count_semantics(**SEMANTICS_BASE) != request_count_semantics(
            **{**SEMANTICS_BASE, **change}
        )

    def test_the_identity_is_deterministic_and_bounded(self) -> None:
        identity = request_count_semantics(**SEMANTICS_BASE)
        assert identity == request_count_semantics(**SEMANTICS_BASE)
        assert len(identity) <= 160

    def test_matching_identities_are_compatible(self) -> None:
        identity = request_count_semantics(**SEMANTICS_BASE)
        assert semantics_incompatibility(identity, identity) is None

    @pytest.mark.parametrize(
        ("bucket", "baseline", "fragment"),
        [
            (None, None, "bucket carries no"),
            (None, "x", "bucket carries no"),
            ("x", None, "rebuild required"),
            ("x", "y", "rebuild required"),
        ],
    )
    def test_anything_else_fails_closed(
        self, bucket: str | None, baseline: str | None, fragment: str
    ) -> None:
        reason = semantics_incompatibility(bucket, baseline)
        assert reason is not None and fragment in reason

    def test_an_incompatible_baseline_yields_insufficient_data_not_a_verdict(self) -> None:
        point = WafPoint(
            bucket_start=T0, bucket_end=T0 + timedelta(minutes=5),
            completeness=BucketCompleteness.COMPLETE, request_count=900,
            endpoint_coverage_ratio=1.0,
        )
        cell = WafBaselineCell(
            median=100, mad=5, p05=90, p95=110, sample_count=30, is_reliable=True, completeness=1
        )
        ev = detect_request_rate_spike(
            WafDetectionContext(
                point=point, baseline=cell, thresholds=WafThresholds(),
                baseline_incompatibility="rebuild required",
            )
        ).evidence
        assert ev.signal is DetectorSignal.UNKNOWN
        assert ev.insufficient_data is True
        assert ev.reason == "rebuild required"


# ---------------------------------------------------------------- maintenance
class TestMaintenanceRule:
    until = T0 + timedelta(minutes=12)

    def test_off_is_never_maintenance(self) -> None:
        assert not in_maintenance(T0, maintenance_mode=False, maintenance_until=None)

    def test_open_ended_maintenance_covers_everything(self) -> None:
        assert in_maintenance(
            T0 + timedelta(days=400), maintenance_mode=True, maintenance_until=None
        )

    def test_a_bucket_the_window_ends_inside_is_in_maintenance(self) -> None:
        # [12:10, 12:15) partially overlaps a window ending 12:12.
        assert in_maintenance(
            T0 + timedelta(minutes=10), maintenance_mode=True, maintenance_until=self.until
        )

    def test_a_bucket_starting_exactly_at_the_end_is_in_maintenance(self) -> None:
        assert in_maintenance(self.until, maintenance_mode=True, maintenance_until=self.until)

    def test_the_first_bucket_starting_after_the_end_is_not(self) -> None:
        assert not in_maintenance(
            T0 + timedelta(minutes=15), maintenance_mode=True, maintenance_until=self.until
        )


# -------------------------------------------------------------------- profile
class TestMapping:
    @pytest.mark.parametrize(
        "name",
        ['URL"', "URL, x", "SUM(eventcount)", "", " URL", "a" * 65, "url;DROP", "u\nrl"],
    )
    def test_anything_but_a_plain_property_name_is_refused(self, name: str) -> None:
        with pytest.raises(WafProfileError):
            validate_property_name(name)

    def test_the_lab_names_are_accepted_verbatim(self) -> None:
        for name in ("URL", "HTTP_Method", "WAF_ResponseCode", "WAF Request URL"):
            assert validate_property_name(name) == name

    def test_the_mapping_refuses_an_unsafe_optional_name(self) -> None:
        with pytest.raises(WafProfileError):
            WafFieldMapping(url="URL", method="HTTP_Method", action="x)")

    def test_url_and_method_have_no_default(self) -> None:
        with pytest.raises(TypeError):
            WafFieldMapping()  # type: ignore[call-arg]

    def test_a_mapped_virtual_host_joins_the_collection_fields(self) -> None:
        plain = WafFieldMapping(url="URL", method="HTTP_Method")
        hosted = WafFieldMapping(url="URL", method="HTTP_Method", virtual_host="Host")
        assert WafLogicalField.VIRTUAL_HOST not in plain.collection_fields()
        assert WafLogicalField.VIRTUAL_HOST in hosted.collection_fields()
        assert plain.property_for(WafLogicalField.ACTION) is None


# ----------------------------------------------------------------- validation
MAPPING = WafFieldMapping(
    url="URL", method="HTTP_Method", action="WAF_Action", user_agent="WAF_USERAGENT"
)
SECRET_URL = "/api/v1/report?token=do-not-store"


def probe(name: str, values: list[tuple[str, int]], **kw) -> WafPropertyProbe:
    return WafPropertyProbe(
        property_name=name,
        populated_events=kw.pop("populated", sum(c for _, c in values)),
        distinct_values=len(values),
        values=[WafPropertyValue(value=v, events=c) for v, c in values],
        queries=[f"Q {name}"],
        **kw,
    )


def source_probe(*props: WafPropertyProbe, total: int | None = 120, **kw) -> WafSourceProbe:
    return WafSourceProbe(
        qradar_log_source_id=312, window_start=T0, window_end=T0 + timedelta(minutes=15),
        total_events=total, properties=list(props), **kw,
    )


GOOD = [
    probe("URL", [(SECRET_URL, 70), ("/portal/login", 50)]),
    probe("HTTP_Method", [("GET", 83), ("POST", 37)]),
    probe("WAF_Action", [("ALLOW", 78), ("DENY", 42)]),
    probe("WAF_USERAGENT", [("Mozilla/5.0 (secret-agent)", 120)]),
]


def judge(*props: WafPropertyProbe, found: bool = True, **kw):  # type: ignore[no-untyped-def]
    return evaluate_profile(
        MAPPING, source_probe(*props, **kw), log_source_found=found, min_ratio=0.9
    )


class TestValidation:
    def test_a_sound_mapping_validates(self) -> None:
        result = judge(*GOOD)
        assert result.status is WafProfileValidationStatus.VALID
        assert result.field_status(WafLogicalField.URL) is WafFieldStatus.AVAILABLE

    def test_unmapped_is_its_own_status_not_zero_or_failure(self) -> None:
        result = judge(*GOOD)
        vhost = next(f for f in result.fields if f.logical is WafLogicalField.VIRTUAL_HOST)
        assert vhost.status is WafFieldStatus.NOT_MAPPED
        assert vhost.populated_events is None and vhost.populated_ratio is None

    def test_a_rejected_required_property_invalidates(self) -> None:
        rejected = WafPropertyProbe(property_name="URL", rejected=True, queries=["Q"])
        result = judge(rejected, *GOOD[1:])
        assert result.status is WafProfileValidationStatus.INVALID
        assert result.field_status(WafLogicalField.URL) is WafFieldStatus.PROPERTY_REJECTED

    def test_a_rejected_optional_property_is_reported_but_does_not_invalidate(self) -> None:
        rejected = WafPropertyProbe(property_name="WAF_Action", rejected=True)
        result = judge(GOOD[0], GOOD[1], rejected, GOOD[3])
        assert result.status is WafProfileValidationStatus.VALID
        assert result.field_status(WafLogicalField.ACTION) is WafFieldStatus.PROPERTY_REJECTED

    def test_a_successful_query_with_no_values_is_unpopulated_not_available(self) -> None:
        empty = probe("HTTP_Method", [], populated=0)
        result = judge(GOOD[0], empty, *GOOD[2:])
        assert result.field_status(WafLogicalField.METHOD) is WafFieldStatus.UNPOPULATED
        assert result.status is WafProfileValidationStatus.INVALID

    def test_low_coverage_on_a_required_field_invalidates(self) -> None:
        sparse = probe("URL", [("/portal/login", 60)])
        result = judge(sparse, *GOOD[1:])
        assert result.field_status(WafLogicalField.URL) is WafFieldStatus.INSUFFICIENT_COVERAGE

    def test_implausible_values_are_not_available(self) -> None:
        nonsense = probe("HTTP_Method", [("BREW", 100), ("GET", 20)])
        result = judge(GOOD[0], nonsense, *GOOD[2:])
        assert result.field_status(WafLogicalField.METHOD) is WafFieldStatus.IMPLAUSIBLE
        assert result.status is WafProfileValidationStatus.INVALID

    def test_no_events_is_inconclusive_not_valid_or_invalid(self) -> None:
        result = judge(*GOOD, total=0)
        assert result.status is WafProfileValidationStatus.INCONCLUSIVE
        assert result.field_status(WafLogicalField.URL) is WafFieldStatus.NO_EVENTS

    def test_a_failed_query_is_inconclusive(self) -> None:
        failed = WafPropertyProbe(property_name="URL", error="ProviderUnavailableError: timeout")
        result = judge(failed, *GOOD[1:])
        assert result.status is WafProfileValidationStatus.INCONCLUSIVE
        assert result.field_status(WafLogicalField.URL) is WafFieldStatus.QUERY_FAILED

    def test_a_missing_log_source_invalidates(self) -> None:
        result = judge(*GOOD, found=False)
        assert result.status is WafProfileValidationStatus.INVALID
        assert "does not exist" in result.reasons[0]

    def test_the_report_carries_no_request_values(self) -> None:
        report = json.dumps(judge(*GOOD).as_report())
        assert SECRET_URL not in report
        assert "secret-agent" not in report
        # Bounded enumerations are the only values disclosed.
        assert '"DENY"' in report and '"POST"' in report


# ---------------------------------------------------------------- REST probe
def _probe_handler(rejected_property: str | None = None):  # type: ignore[no-untyped-def]
    queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path.endswith("/ariel/searches"):
            q = request.url.params["query_expression"]
            if rejected_property and f'"{rejected_property}"' in q:
                return httpx.Response(422, json={"message": "Invalid property"})
            queries.append(q)
            return httpx.Response(201, json={"search_id": f"s-{len(queries)}", "status": "WAIT"})
        if path.endswith("/results"):
            q = queries[int(path.split("/")[-2].removeprefix("s-")) - 1]
            if '"total_events"' in q:
                rows = [{"total_events": 120.0}]
            elif '"populated"' in q:
                rows = [{"populated": 120.0, "distinct_values": 2.0}]
            else:
                rows = [{"value": "GET", "events": 83.0}, {"value": "POST", "events": 37.0}]
            return httpx.Response(200, json={"events": rows})
        if "/ariel/searches/s-" in path:
            return httpx.Response(200, json={"status": "COMPLETED", "progress": 100})
        raise AssertionError(f"unexpected {request.method} {request.url}")

    return handler, queries


class TestRestProbe:
    @pytest.mark.asyncio
    async def test_a_422_is_a_rejection_and_other_properties_are_still_probed(self) -> None:
        handler, queries = _probe_handler(rejected_property="URL")
        provider, _ = build_provider(handler, page_size=50, max_pages=1)
        result = await provider.probe_waf_properties(
            qradar_log_source_id=312, window_start=T0,
            window_end=T0 + timedelta(minutes=15), property_names=["URL", "HTTP_Method"],
        )
        url, method = result.properties
        assert url.rejected and url.values == []
        assert not method.rejected and method.populated_events == 120
        assert [v.value for v in method.values] == ["GET", "POST"]
        assert result.total_events == 120
        # Every probe query is bounded and scoped.
        for q in queries:
            assert "logsourceid = 312" in q and "START " in q and "STOP " in q
            assert "COUNT(*)" not in q

    @pytest.mark.asyncio
    async def test_an_unsafe_name_is_rejected_without_a_query(self) -> None:
        handler, queries = _probe_handler()
        provider, _ = build_provider(handler, page_size=50, max_pages=1)
        result = await provider.probe_waf_properties(
            qradar_log_source_id=312, window_start=T0,
            window_end=T0 + timedelta(minutes=15), property_names=['URL" OR 1=1'],
        )
        assert result.properties[0].rejected
        assert all('OR 1=1' not in q for q in queries)

    @pytest.mark.asyncio
    async def test_the_window_is_bounded_to_one_hour_before_any_io(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("no request expected")

        provider, _ = build_provider(handler)
        with pytest.raises(ValueError, match="one-hour"):
            await provider.probe_waf_properties(
                qradar_log_source_id=312, window_start=T0,
                window_end=T0 + timedelta(hours=2), property_names=["URL"],
            )


def test_metric_retention_must_exceed_the_lookback_when_waf_collection_runs() -> None:
    base = {**get_settings().model_dump(), "waf_collection_enabled": True}
    with pytest.raises(ValueError, match="WAF_METRIC_RETENTION_DAYS"):
        Settings(**{**base, "baseline_lookback_days": 28, "waf_metric_retention_days": 28})
    assert Settings(**{**base, "baseline_lookback_days": 28, "waf_metric_retention_days": 29})


def test_a_deployment_without_waf_collection_is_not_refused_over_a_waf_setting() -> None:
    """Phase A deployments set long lookbacks; a WAF default must not stop them booting."""
    settings = Settings(**{**get_settings().model_dump(), "baseline_lookback_days": 60})
    assert settings.waf_collection_enabled is False


@pytest.mark.asyncio
async def test_retention_refuses_to_delete_inside_the_baseline_lookback() -> None:
    from app.services.waf_retention import WafRetention

    settings = Settings(**{**get_settings().model_dump(), "baseline_lookback_days": 60})
    # No session is needed: the guard returns before touching the database.
    report = await WafRetention(None, settings=settings).run()  # type: ignore[arg-type]
    assert report.deleted == {}
    assert report.skipped_reason is not None
    assert "BASELINE_LOOKBACK_DAYS" in report.skipped_reason


# ------------------------------------------------------------------ scheduling
def test_waf_tasks_are_registered_and_scheduled_from_settings() -> None:
    from app.workers import tasks  # noqa: F401  -- registers the task shims
    from app.workers.celery_app import celery_app

    assert {"dispatch_waf_collection", "collect_waf_source", "apply_waf_retention"} <= set(
        celery_app.tasks
    )
    schedule = celery_app.conf.beat_schedule
    settings = get_settings()
    # Ticks every minute: the WAF_COLLECTION cadence gate now decides when a run happens.
    assert schedule["dispatch-waf-collection"]["schedule"] == 60.0
    assert schedule["apply-waf-retention"]["schedule"] == float(
        settings.waf_retention_interval_seconds
    )


@pytest.mark.asyncio
async def test_dispatch_does_nothing_while_waf_collection_is_disabled() -> None:
    from app.workers.tasks import dispatch_waf_collection

    assert get_settings().waf_collection_enabled is False
    sent: list[str] = []
    assert await dispatch_waf_collection(enqueue=sent.append) == {
        "status": "disabled",
        "dispatched": 0,
    }
    assert sent == []


def test_recorded_failure_text_never_carries_a_foreign_message() -> None:
    from app.providers.base import ProviderUnavailableError
    from app.services.waf_pipeline import sanitized_error

    assert (
        sanitized_error(ProviderUnavailableError("QRadar request timed out"))
        == "ProviderUnavailableError: QRadar request timed out"
    )
    # A database error can echo statement parameters, which may hold request
    # values; only its class survives.
    assert sanitized_error(RuntimeError("INSERT ... '/login?token=secret'")) == "RuntimeError"
