"""Phase B.1.5 pure rules: measurement semantics, lower-bound verdicts, sparse
reconstruction, the capability contract, and the QRadar rejection probe."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.anomaly.capabilities import WAF_DETECTORS, waf_verdict_scope
from app.core.config import Settings, get_settings
from app.models.enums import (
    BucketCompleteness,
    DetectorSignal,
    WafAnomalyType,
    WafMeasurement,
)
from app.providers.dto import WafRejectionObservation
from app.waf.baseline import clean_samples
from app.waf.detector import (
    WafBaselineCell,
    WafDetectionContext,
    WafPoint,
    WafThresholds,
    detect_request_rate_spike,
)
from app.waf.engine import WAF_DETECTOR_FUNCTIONS
from app.waf.measurement import (
    WafMeasurementRequirement,
    WafVerdictScope,
    default_measurement,
    verdict_scope,
)
from app.waf.observations import BucketFacts, RowFacts, endpoint_series, observe
from tests.unit.test_qradar_rest_provider import build_provider

T0 = datetime(2026, 8, 3, 19, 0, tzinfo=UTC)
STEP = timedelta(seconds=300)
SEM = "request_count/v1;agg=SUM(eventcount);normalizer/v1;profile/v1"
EXACT, LOWER = WafMeasurement.EXACT, WafMeasurement.LOWER_BOUND
COMPLETE, PARTIAL = BucketCompleteness.COMPLETE, BucketCompleteness.PARTIAL


# ------------------------------------------------------------ measurement scope
class TestVerdictScope:
    @pytest.mark.parametrize(
        ("requirement", "measurement", "expected"),
        [
            (WafMeasurementRequirement.EXACT, EXACT, WafVerdictScope.FULL),
            (WafMeasurementRequirement.EXACT, LOWER, WafVerdictScope.NONE),
            (WafMeasurementRequirement.EXACT, None, WafVerdictScope.NONE),
            (WafMeasurementRequirement.LOWER_BOUND_PROVES_INCREASE, EXACT, WafVerdictScope.FULL),
            (
                WafMeasurementRequirement.LOWER_BOUND_PROVES_INCREASE,
                LOWER,
                WafVerdictScope.INCREASE_ONLY,
            ),
            (WafMeasurementRequirement.LOWER_BOUND_PROVES_INCREASE, None, WafVerdictScope.NONE),
        ],
    )
    def test_matrix(self, requirement, measurement, expected) -> None:  # type: ignore[no-untyped-def]
        decision = verdict_scope(requirement, measurement)
        assert decision.scope is expected
        assert (decision.reason is None) is (expected is WafVerdictScope.FULL)

    def test_only_a_complete_observation_is_assumed_exact(self) -> None:
        assert default_measurement(COMPLETE) is EXACT
        # Unknown, not LOWER_BOUND: lower-bound reasoning must be recorded, never assumed.
        assert default_measurement(PARTIAL) is None
        assert default_measurement(BucketCompleteness.MISSING) is None

    def test_the_spike_detector_declares_lower_bound_increase(self) -> None:
        spec = WAF_DETECTORS[WafAnomalyType.WAF_REQUEST_RATE_SPIKE]
        assert spec.measurement is WafMeasurementRequirement.LOWER_BOUND_PROVES_INCREASE
        scope = waf_verdict_scope(WafAnomalyType.WAF_REQUEST_RATE_SPIKE, LOWER)
        assert scope.scope is WafVerdictScope.INCREASE_ONLY

    def test_every_executable_detector_has_a_spec_and_a_declared_requirement(self) -> None:
        assert set(WAF_DETECTOR_FUNCTIONS) <= set(WAF_DETECTORS)
        for t in WAF_DETECTOR_FUNCTIONS:
            spec = WAF_DETECTORS[t]
            assert spec.measurement is not None or spec.inputs, t
            assert all(i.requirement is not None for i in spec.inputs), t


# ------------------------------------------------------------ lower-bound detector
def spike_ctx(
    requests: int,
    *,
    completeness: BucketCompleteness = PARTIAL,
    measurement: WafMeasurement | None = LOWER,
    scope: WafVerdictScope | None = WafVerdictScope.INCREASE_ONLY,
    coverage: float = 0.3,
    median: float = 100.0,
    mad: float = 10.0,
    thresholds: WafThresholds | None = None,
) -> WafDetectionContext:
    return WafDetectionContext(
        point=WafPoint(
            bucket_start=T0, bucket_end=T0 + STEP, completeness=completeness,
            request_count=requests, endpoint_coverage_ratio=coverage,
            distinct_client_count=None, measurement=measurement,
        ),
        baseline=WafBaselineCell(
            median=median, mad=mad, p05=median * 0.8, p95=median * 1.3,
            sample_count=20, is_reliable=True, completeness=1.0,
        ),
        thresholds=thresholds or WafThresholds(),
        verdict_scope=scope,
        scope_reason="grouping was truncated" if scope is not WafVerdictScope.FULL else None,
    )


class TestLowerBoundSpike:
    def test_a_lower_bound_above_every_guard_proves_the_spike(self) -> None:
        verdict = detect_request_rate_spike(spike_ctx(900))
        assert verdict.signal is DetectorSignal.ANOMALOUS
        ev = verdict.evidence
        assert ev.observed_value == 900
        assert ev.extra["request_count_measurement"] == "LOWER_BOUND"
        # Never displayed as an exact count.
        assert ev.reason.startswith("at least 900 requests (a lower bound")

    @pytest.mark.parametrize("requests", [0, 5, 110, 150, 199])
    def test_a_lower_bound_that_fails_a_guard_is_never_healthy(self, requests: int) -> None:
        verdict = detect_request_rate_spike(spike_ctx(requests))
        assert verdict.signal is not DetectorSignal.HEALTHY
        assert verdict.evidence.insufficient_data
        assert "lower bound" in verdict.evidence.reason

    def test_low_coverage_does_not_veto_a_proven_increase(self) -> None:
        # The unattributed events are exactly what the bound leaves out.
        assert detect_request_rate_spike(spike_ctx(900, coverage=0.05)).is_anomalous

    def test_a_partial_point_without_a_granted_scope_gets_no_verdict(self) -> None:
        verdict = detect_request_rate_spike(spike_ctx(900, scope=None))
        assert verdict.evidence.insufficient_data
        verdict = detect_request_rate_spike(spike_ctx(900, measurement=None, scope=None))
        assert verdict.evidence.insufficient_data

    def test_a_missing_bucket_gets_no_verdict_even_with_a_scope(self) -> None:
        verdict = detect_request_rate_spike(
            spike_ctx(900, completeness=BucketCompleteness.MISSING)
        )
        assert verdict.evidence.insufficient_data

    def test_an_exact_count_is_unchanged_by_the_scope_machinery(self) -> None:
        exact = spike_ctx(
            110, completeness=COMPLETE, measurement=EXACT, scope=WafVerdictScope.FULL,
            coverage=1.0,
        )
        assert detect_request_rate_spike(exact).signal is DetectorSignal.HEALTHY

    def test_monotonicity_property(self) -> None:
        """If a lower bound L proves a spike, every true count T >= L is a spike.

        This is the whole justification for lower-bound verdicts, so it is
        checked over randomized baselines, thresholds and bounds, including the
        degenerate-MAD fallback and a zero-median baseline.
        """
        rng = random.Random(20260916)
        proven = 0
        for _ in range(4000):
            median = rng.choice([0.0, 1.0, 5.0, 40.0, 100.0, 1000.0]) * rng.random()
            mad = rng.choice([0.0, rng.random() * median + 0.01])
            thresholds = WafThresholds(
                deviation_z=rng.uniform(1.0, 6.0),
                spike_ratio=rng.uniform(1.1, 4.0),
                min_absolute_delta_requests=rng.uniform(0.0, 200.0),
                min_bucket_requests=rng.uniform(0.0, 100.0),
            )
            lower = rng.randrange(0, 3000)
            truth = lower + rng.randrange(0, 3000)
            bound = detect_request_rate_spike(
                spike_ctx(lower, median=median, mad=mad, thresholds=thresholds)
            )
            exact = detect_request_rate_spike(
                spike_ctx(
                    truth, completeness=COMPLETE, measurement=EXACT,
                    scope=WafVerdictScope.FULL, coverage=1.0, median=median, mad=mad,
                    thresholds=thresholds,
                )
            )
            assert bound.signal is not DetectorSignal.HEALTHY
            if bound.is_anomalous:
                proven += 1
                assert exact.is_anomalous, (lower, truth, median, mad, thresholds)
        assert proven > 500  # the property was actually exercised


# ------------------------------------------------------------ sparse reconstruction
def bucket(
    start: datetime,
    *,
    measurement: WafMeasurement | None = EXACT,
    sparse: bool = True,
    maintenance: bool = False,
    semantics: str | None = SEM,
    coverage: float = 1.0,
) -> BucketFacts:
    return BucketFacts(
        bucket_start=start, bucket_seconds=300,
        completeness=COMPLETE if measurement is EXACT else PARTIAL,
        endpoint_coverage_ratio=coverage, metric_semantics=semantics,
        in_maintenance=maintenance, measurement=measurement, sparse=sparse,
    )


def row(start: datetime, count: int, *, completeness=COMPLETE, semantics=None) -> RowFacts:  # type: ignore[no-untyped-def]
    return RowFacts(
        bucket_start=start, bucket_seconds=300, request_count=count, distinct_client_count=3,
        completeness=completeness, endpoint_coverage_ratio=1.0, metric_semantics=semantics,
        normalizer_version=1,
    )


class TestObserve:
    def test_absent_from_an_exact_sparse_bucket_while_tracked_is_a_measured_zero(self) -> None:
        obs = observe(None, bucket(T0), tracked=True, normalizer_version=1)
        assert obs is not None and obs.inferred
        assert (obs.request_count, obs.measurement, obs.is_complete) == (0, EXACT, True)
        assert obs.distinct_client_count == 0
        assert obs.metric_semantics == SEM

    def test_absent_from_a_lower_bound_bucket_is_unknown_not_zero(self) -> None:
        obs = observe(None, bucket(T0, measurement=LOWER), tracked=True, normalizer_version=1)
        assert obs is not None and obs.measurement is LOWER and not obs.is_complete
        assert obs.distinct_client_count is None

    def test_absence_says_nothing_without_tracking_a_record_or_sparse_storage(self) -> None:
        assert observe(None, bucket(T0), tracked=False, normalizer_version=1) is None
        assert observe(None, None, tracked=True, normalizer_version=1) is None
        assert observe(None, bucket(T0, sparse=False), tracked=True, normalizer_version=1) is None

    def test_a_row_inherits_its_bucket_identity_and_measurement(self) -> None:
        obs = observe(row(T0, 40), bucket(T0, measurement=LOWER), tracked=True,
                      normalizer_version=1)
        assert obs is not None and not obs.inferred
        assert (obs.metric_semantics, obs.measurement) == (SEM, LOWER)
        own = observe(row(T0, 40, semantics="legacy"), bucket(T0), tracked=True,
                      normalizer_version=1)
        assert own is not None and own.metric_semantics == "legacy"

    def test_a_row_without_a_bucket_record_is_exact_only_when_complete(self) -> None:
        complete = observe(row(T0, 40), None, tracked=True, normalizer_version=1)
        partial = observe(row(T0, 40, completeness=PARTIAL), None, tracked=True,
                          normalizer_version=1)
        assert complete is not None and complete.measurement is EXACT
        assert complete.in_maintenance is None and complete.metric_semantics is None
        assert partial is not None and partial.measurement is None


class TestSeriesAndTraining:
    def _clean(self, series, end):  # type: ignore[no-untyped-def]
        return clean_samples(
            series, end=end, semantics=SEM, min_coverage=0.9, spans=[],
            maintenance_rule=lambda _s: False,
        )

    def test_sparse_history_trains_exactly_what_dense_history_did(self) -> None:
        """The same traffic stored densely (B.1) and sparsely (B.1.5) trains the same samples."""
        rng = random.Random(7)
        starts = [T0 + STEP * i for i in range(300)]
        truth = [rng.choice([0, 0, 0, rng.randrange(1, 200)]) for _ in starts]
        kinds = [rng.choice(["exact"] * 8 + ["lower", "maintenance", "missing"]) for _ in starts]
        end = starts[-1] + STEP

        dense_rows, dense_buckets, sparse_rows, sparse_buckets = [], {}, [], {}
        for s, count, kind in zip(starts, truth, kinds, strict=True):
            if kind == "missing":
                continue  # a failed collection: no record, no row, in either world
            measurement = LOWER if kind == "lower" else EXACT
            completeness = COMPLETE if measurement is EXACT else PARTIAL
            maintenance = kind == "maintenance"
            dense_buckets[s] = bucket(s, measurement=measurement, sparse=False,
                                      maintenance=maintenance)
            sparse_buckets[s] = bucket(s, measurement=measurement, sparse=True,
                                       maintenance=maintenance)
            # B.1 wrote every tracked endpoint, zero or not, with the bucket's completeness.
            dense_rows.append(row(s, count, completeness=completeness, semantics=SEM))
            if count:
                sparse_rows.append(row(s, count, completeness=completeness))

        def series(rows, buckets):  # type: ignore[no-untyped-def]
            return endpoint_series(rows, buckets, tracked_at=lambda _s: True,
                                   normalizer_version=1, start=starts[0], end=end)

        dense = self._clean(series(dense_rows, dense_buckets), end)
        sparse = self._clean(series(sparse_rows, sparse_buckets), end)
        assert dense[0] == sparse[0]
        assert dense[1].as_dict() == sparse[1].as_dict()
        assert dense[2] == sparse[2]
        assert dense[1].incomplete and dense[1].maintenance and 0.0 in [v for _, v in dense[0]]

    def test_only_buckets_inside_the_tracking_window_contribute(self) -> None:
        starts = [T0 + STEP * i for i in range(6)]
        buckets = {s: bucket(s) for s in starts}
        rows = [row(starts[0], 50), row(starts[4], 60)]
        window = (starts[3], starts[4])
        out = endpoint_series(
            rows, buckets, tracked_at=lambda s: window[0] <= s <= window[1],
            normalizer_version=1, start=starts[0], end=starts[-1] + STEP,
        )
        # The row from before the window (an earlier tracking period) is dropped
        # together with the zeros that belonged with it.
        assert [(o.bucket_start, o.request_count, o.inferred) for o in out] == [
            (starts[3], 0, True), (starts[4], 60, False),
        ]

    def test_a_lower_bound_zero_is_excluded_as_incomplete(self) -> None:
        obs = observe(None, bucket(T0, measurement=LOWER), tracked=True, normalizer_version=1)
        samples, tally, candidates = self._clean([obs], T0 + STEP)
        assert samples == [] and tally.incomplete == 1 and candidates == 1


# ------------------------------------------------------------ rejection probe
class TestRejectionInterpretation:
    @staticmethod
    def obs(outcome: str, status: int | None = None) -> WafRejectionObservation:
        return WafRejectionObservation(
            property_name="p", outcome=outcome, create_http_status=status, query="q",
            search_created=outcome.startswith("SEARCH"),
        )

    @pytest.mark.parametrize(
        ("missing", "code"),
        [
            (("REJECTED_AT_CREATE", 422), "CONFIRMED_422"),
            (("REJECTED_AT_CREATE", 400), "REJECTED_WITH_OTHER_STATUS"),
            (("SEARCH_FAILED", None), "REJECTED_ASYNCHRONOUSLY"),
            (("SEARCH_COMPLETED", None), "ACCEPTED_SILENTLY"),
            (("TIMED_OUT", None), "INCONCLUSIVE"),
            (("CREATE_FAILED", 503), "INCONCLUSIVE"),
        ],
    )
    def test_every_observed_behaviour_has_a_stated_meaning(self, missing, code) -> None:  # type: ignore[no-untyped-def]
        from app.cli.waf import interpret_rejection

        got, sentence = interpret_rejection(self.obs(*missing), self.obs("SEARCH_COMPLETED"))
        assert got == code and sentence

    def test_nothing_is_concluded_when_the_control_fails(self) -> None:
        from app.cli.waf import interpret_rejection

        code, _ = interpret_rejection(
            self.obs("REJECTED_AT_CREATE", 422), self.obs("REJECTED_AT_CREATE", 422)
        )
        assert code == "INCONCLUSIVE_CONTROL_FAILED"


class TestRestRejectionProbe:
    @staticmethod
    def handler(create: dict[str, int], terminal: dict[str, str]):  # type: ignore[no-untyped-def]
        created: list[str] = []
        results_fetched: list[str] = []

        def handle(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if request.method == "POST" and path.endswith("/ariel/searches"):
                q = request.url.params["query_expression"]
                name = next(n for n in create if f'"{n}"' in q)
                if create[name] != 201:
                    return httpx.Response(create[name], json={"message": "echo " + q})
                created.append(name)
                return httpx.Response(201, json={"search_id": name, "status": "WAIT"})
            if path.endswith("/results"):
                results_fetched.append(path)
                return httpx.Response(200, json={"events": [{"v": "SECRET-VALUE"}]})
            name = path.rsplit("/", 1)[-1]
            return httpx.Response(200, json={"status": terminal[name], "record_count": 7})

        return handle, created, results_fetched

    @pytest.mark.asyncio
    async def test_outcomes_are_recorded_as_observed_and_no_result_is_fetched(self) -> None:
        handle, created, fetched = self.handler(
            {"Gone": 422, "Odd": 400, "Late": 201, "URL": 201},
            {"Late": "ERROR", "URL": "COMPLETED"},
        )
        provider, _ = build_provider(handle, page_size=50, max_pages=1)
        out = await provider.observe_property_rejection(
            qradar_log_source_id=312, window_start=T0, window_end=T0 + timedelta(minutes=15),
            property_names=["Gone", "Odd", "Late", "URL"],
        )
        by = {o.property_name: o for o in out}
        assert (by["Gone"].outcome, by["Gone"].create_http_status) == ("REJECTED_AT_CREATE", 422)
        assert (by["Odd"].outcome, by["Odd"].create_http_status) == ("REJECTED_AT_CREATE", 400)
        assert (by["Late"].outcome, by["Late"].search_status) == ("SEARCH_FAILED", "ERROR")
        assert (by["URL"].outcome, by["URL"].record_count) == ("SEARCH_COMPLETED", 7)
        # Only accepted creates became searches, and no result page was ever read.
        assert created == ["Late", "URL"]
        assert fetched == []
        assert all("echo" not in o.model_dump_json() for o in out)
        for o in out:
            assert "logsourceid = 312" in o.query and "START " in o.query and "LIMIT 1" in o.query


# ------------------------------------------------------------ settings
def test_the_permit_namespace_must_not_be_the_collector_namespace() -> None:
    base = get_settings().model_dump()
    base.update(waf_ariel_permit_lock_namespace=base["collection_advisory_lock_namespace"])
    with pytest.raises(ValueError, match="WAF_ARIEL_PERMIT_LOCK_NAMESPACE"):
        Settings(**base)


@pytest.mark.asyncio
async def test_the_budget_is_not_enforced_without_postgres() -> None:
    from app.services.waf_ariel_budget import WafArielBudget

    budget = WafArielBudget(None, get_settings())
    assert not budget.enforced
    async with budget.permit() as permit:
        assert permit is not None and permit.slot is None
