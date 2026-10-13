"""Rule statistics: counts, silence, top lists, match rate, benchmark, API guard."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.models.rule import (
    AnalyticsRule,
    RuleCapacitySample,
    RuleMatchMetric,
    RuleMatchWindow,
    RuleMatchWindowStatus,
)
from app.services.rule_statistics import SILENT_ITEM_CAP, RuleStatisticsService
from tests.integration.factories import make_instance
from tests.integration.test_phase3_api import ADMIN, NOBODY, READER, client_for
from tests.integration.test_rule_match_collection import recorded_statements

pytestmark = pytest.mark.integration

#: 12:10 with a 300 s settle lag and 3,600 s windows: the span ends at 12:00.
NOW = datetime(2026, 10, 12, 12, 10, tzinfo=UTC)
END = NOW.replace(minute=0)
HOUR = timedelta(hours=1)
SPAN_1D = 24 * 3600


def service(session, **overrides) -> RuleStatisticsService:
    return RuleStatisticsService(session, get_settings().model_copy(update=overrides))


async def add_rule(
    session, inst, qradar_id, *, rule_type="EVENT", enabled=True, origin="USER", bb=False,
    capacity=None, name=None,
) -> AnalyticsRule:
    rule = AnalyticsRule(
        instance_id=inst.id, qradar_id=qradar_id, name=name or f"r{qradar_id}",
        rule_type=rule_type, enabled=enabled, origin=origin, is_building_block=bb,
        average_capacity=capacity,
    )
    session.add(rule)
    await session.flush()
    return rule


async def add_windows(
    session, inst, *, end=END, count=24, status=RuleMatchWindowStatus.COMPLETE, skip=()
) -> None:
    for i in range(1, count + 1):
        if i in skip:
            continue
        session.add(
            RuleMatchWindow(
                instance_id=inst.id, window_start=end - HOUR * i, window_seconds=3600,
                status=status, rows_returned=1, row_cap=2000, unknown_ids=0, attempts=1,
                query_version="v1", collected_at=end,
            )
        )
    await session.flush()


async def add_match(session, rule, *, hours_back=1, records=1, events=1) -> None:
    session.add(
        RuleMatchMetric(
            rule_id=rule.id, bucket_start=END - HOUR * hours_back, bucket_seconds=3600,
            match_records=records, matched_events=events,
        )
    )
    await session.flush()


async def stats(session, inst, *, days=1, top=10):
    return await service(session).compute(inst.id, days=days, top=top, now=NOW)


class TestCounts:
    @pytest.mark.asyncio
    async def test_counts_group_by_state_type_origin_and_building_block(self, db_session) -> None:
        inst = await make_instance(db_session)
        await add_rule(db_session, inst, 1)
        await add_rule(db_session, inst, 2)
        await add_rule(db_session, inst, 3, enabled=False)
        await add_rule(db_session, inst, 4, rule_type="COMMON", origin="SYSTEM")
        await add_rule(db_session, inst, 5, bb=True, origin="SYSTEM")
        other = await make_instance(db_session)
        await add_rule(db_session, other, 6)

        result = await stats(db_session, inst)

        rows = {
            (r.enabled, r.rule_type, r.origin, r.is_building_block): r.count
            for r in result.counts
        }
        assert rows == {
            (True, "EVENT", "USER", False): 2,
            (False, "EVENT", "USER", False): 1,
            (True, "COMMON", "SYSTEM", False): 1,
            (True, "EVENT", "SYSTEM", True): 1,
        }


class TestWindow:
    @pytest.mark.asyncio
    async def test_no_collection_is_not_measured_not_zero(self, db_session) -> None:
        inst = await make_instance(db_session)
        await add_rule(db_session, inst, 1)

        result = await stats(db_session, inst)

        assert result.window.complete is False
        assert result.window.complete_windows == 0
        assert result.window.measured_seconds == 0
        assert "disabled or has not run" in result.window.reason
        assert result.silent is None and result.silent_reason == result.window.reason
        assert result.top_by_matches is None and result.top_by_events is None
        assert result.top_reason == result.window.reason

    @pytest.mark.asyncio
    async def test_a_complete_span_reports_its_arithmetic(self, db_session) -> None:
        inst = await make_instance(db_session)
        await add_windows(db_session, inst)

        w = (await stats(db_session, inst)).window

        assert (w.expected_windows, w.complete_windows) == (24, 24)
        assert (w.span_start, w.span_end) == (END - 24 * HOUR, END)
        assert (w.span_seconds, w.measured_seconds, w.window_seconds) == (SPAN_1D, SPAN_1D, 3600)
        assert w.complete is True and w.reason is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "kwargs, missing, truncated",
        [
            ({"skip": (5,)}, 1, 0),
            ({"status": RuleMatchWindowStatus.TRUNCATED}, 0, 24),
        ],
    )
    async def test_an_incomplete_span_is_not_complete(
        self, db_session, kwargs, missing, truncated
    ) -> None:
        inst = await make_instance(db_session)
        await add_windows(db_session, inst, **kwargs)

        w = (await stats(db_session, inst)).window

        assert w.complete is False
        assert (w.missing_windows, w.truncated_windows) == (missing, truncated)
        assert "proves nothing" in w.reason


class TestSilent:
    @pytest.mark.asyncio
    async def test_an_incomplete_span_lists_no_silent_rule(self, db_session) -> None:
        """The completeness check: one missing window and no silence is proven."""
        inst = await make_instance(db_session)
        await add_rule(db_session, inst, 1)
        await add_windows(db_session, inst, skip=(7,))

        result = await stats(db_session, inst)

        assert result.silent is None
        assert result.silent_reason is not None and "23 of 24" in result.silent_reason

    @pytest.mark.asyncio
    async def test_a_complete_span_lists_an_event_rule_without_matches(self, db_session) -> None:
        inst = await make_instance(db_session)
        quiet = await add_rule(db_session, inst, 1, name="quiet")
        busy = await add_rule(db_session, inst, 2)
        await add_match(db_session, busy, hours_back=20)
        await add_windows(db_session, inst)

        silent = (await stats(db_session, inst)).silent

        assert [r.rule_id for r in silent.items] == [quiet.id]
        assert silent.total == 1 and silent.items_truncated is False

    @pytest.mark.asyncio
    async def test_a_match_outside_the_span_does_not_break_the_silence(self, db_session) -> None:
        inst = await make_instance(db_session)
        old = await add_rule(db_session, inst, 1)
        await add_match(db_session, old, hours_back=30)
        await add_windows(db_session, inst)

        silent = (await stats(db_session, inst)).silent

        assert [r.rule_id for r in silent.items] == [old.id]

    @pytest.mark.asyncio
    async def test_common_flow_and_offense_rules_are_never_silent_and_are_counted(
        self, db_session
    ) -> None:
        inst = await make_instance(db_session)
        event = await add_rule(db_session, inst, 1)
        for qid, kind in ((2, "COMMON"), (3, "FLOW"), (4, "OFFENSE"), (5, None)):
            await add_rule(db_session, inst, qid, rule_type=kind)
        await add_rule(db_session, inst, 6, rule_type="COMMON")
        await add_windows(db_session, inst)

        silent = (await stats(db_session, inst)).silent

        assert [r.rule_id for r in silent.items] == [event.id]
        assert silent.excluded_count == 5
        by_type = {e.rule_type: (e.evidence, e.count) for e in silent.excluded}
        assert by_type == {
            None: ("NOT_MEASURED", 1),
            "COMMON": ("LOWER_BOUND", 2),
            "FLOW": ("NOT_MEASURED", 1),
            "OFFENSE": ("NOT_MEASURED", 1),
        }
        assert "flows" in silent.excluded_reason

    @pytest.mark.asyncio
    async def test_a_disabled_rule_is_never_silent_nor_excluded(self, db_session) -> None:
        inst = await make_instance(db_session)
        await add_rule(db_session, inst, 1, enabled=False)
        await add_rule(db_session, inst, 2, enabled=False, rule_type="COMMON")
        await add_windows(db_session, inst)

        silent = (await stats(db_session, inst)).silent

        assert silent.items == [] and silent.total == 0 and silent.excluded_count == 0

    @pytest.mark.asyncio
    async def test_the_type_test_matches_match_evidence_on_case_and_padding(
        self, db_session
    ) -> None:
        inst = await make_instance(db_session)
        padded = await add_rule(db_session, inst, 1, rule_type=" event ")
        await add_windows(db_session, inst)

        silent = (await stats(db_session, inst)).silent

        assert [r.rule_id for r in silent.items] == [padded.id]

    @pytest.mark.asyncio
    async def test_the_silent_list_is_capped_but_counts_everything(self, db_session) -> None:
        inst = await make_instance(db_session)
        db_session.add_all(
            AnalyticsRule(
                instance_id=inst.id, qradar_id=i, name=f"r{i:04d}", rule_type="EVENT",
                enabled=True, is_building_block=False,
            )
            for i in range(SILENT_ITEM_CAP + 3)
        )
        await add_windows(db_session, inst)

        silent = (await stats(db_session, inst)).silent

        assert len(silent.items) == SILENT_ITEM_CAP
        assert silent.total == SILENT_ITEM_CAP + 3 and silent.items_truncated is True


class TestTopLists:
    @pytest.mark.asyncio
    async def test_ordering_rate_and_lower_bound(self, db_session) -> None:
        inst = await make_instance(db_session)
        many_records = await add_rule(db_session, inst, 1, name="many-records")
        many_events = await add_rule(db_session, inst, 2, name="many-events")
        common = await add_rule(db_session, inst, 3, rule_type="COMMON", name="common")
        await add_match(db_session, many_records, hours_back=1, records=500, events=600)
        await add_match(db_session, many_records, hours_back=2, records=500, events=600)
        await add_match(db_session, many_events, hours_back=1, records=10, events=9000)
        await add_match(db_session, common, hours_back=3, records=5, events=5)
        await add_windows(db_session, inst)

        result = await stats(db_session, inst)

        assert [r.name for r in result.top_by_matches] == [
            "many-records", "many-events", "common",
        ]
        assert [r.name for r in result.top_by_events] == [
            "many-events", "many-records", "common",
        ]
        first = result.top_by_matches[0]
        assert (first.match_records, first.matched_events) == (1000, 1200)
        assert first.match_rate_per_second == pytest.approx(1200 / SPAN_1D)
        assert {r.name: r.lower_bound for r in result.top_by_events} == {
            "many-events": False, "many-records": False, "common": True,
        }

    @pytest.mark.asyncio
    async def test_top_limits_the_lists_and_ties_break_by_name(self, db_session) -> None:
        inst = await make_instance(db_session)
        for qid, name in ((1, "b"), (2, "a"), (3, "c")):
            await add_match(db_session, await add_rule(db_session, inst, qid, name=name))
        await add_windows(db_session, inst)

        result = await stats(db_session, inst, top=2)

        assert [r.name for r in result.top_by_events] == ["a", "b"]

    @pytest.mark.asyncio
    async def test_the_rate_uses_only_complete_windows(self, db_session) -> None:
        """A half-collected span must not read as a low rate."""
        inst = await make_instance(db_session)
        rule = await add_rule(db_session, inst, 1)
        await add_match(db_session, rule, hours_back=1, events=3600)
        await add_match(db_session, rule, hours_back=2, events=999_999)  # TRUNCATED window
        await add_windows(db_session, inst, count=2)
        session = db_session
        window = await session.scalar(
            select(RuleMatchWindow).where(RuleMatchWindow.window_start == END - 2 * HOUR)
        )
        window.status = RuleMatchWindowStatus.TRUNCATED
        await session.flush()

        result = await stats(db_session, inst)

        assert result.window.complete is False
        assert result.window.measured_seconds == 3600
        (row,) = result.top_by_events
        assert row.matched_events == 3600
        assert row.match_rate_per_second == pytest.approx(1.0)
        assert "proves nothing" in result.top_reason  # partial: stated, lists still shown

    @pytest.mark.asyncio
    async def test_another_instances_matches_are_not_mixed_in(self, db_session) -> None:
        inst = await make_instance(db_session)
        other = await make_instance(db_session)
        await add_match(db_session, await add_rule(db_session, other, 1))
        await add_windows(db_session, inst)
        await add_windows(db_session, other)

        assert (await stats(db_session, inst)).top_by_events == []


class TestBenchmark:
    @pytest.mark.asyncio
    async def test_lowest_nonzero_first_and_no_benchmark_is_counted(self, db_session) -> None:
        inst = await make_instance(db_session)
        await add_rule(db_session, inst, 1, capacity=900.0, name="cheap")
        await add_rule(db_session, inst, 2, capacity=0.4, name="costliest")
        await add_rule(db_session, inst, 3, capacity=12.0, name="middle")
        await add_rule(db_session, inst, 4, capacity=0.0)
        await add_rule(db_session, inst, 5, capacity=None)
        await add_rule(db_session, inst, 6, capacity=0.1, enabled=False)

        section = (await stats(db_session, inst, top=2)).costliest_by_benchmark

        assert [(r.name, r.average_capacity) for r in section.items] == [
            ("costliest", 0.4), ("middle", 12.0),
        ]
        assert section.no_benchmark_count == 2
        assert "not a firing rate" in section.label
        assert section.unit

    @pytest.mark.asyncio
    async def test_the_benchmark_needs_no_collected_window(self, db_session) -> None:
        inst = await make_instance(db_session)
        await add_rule(db_session, inst, 1, capacity=5.0)

        result = await stats(db_session, inst)

        assert result.window.complete is False
        assert len(result.costliest_by_benchmark.items) == 1


class TestConstantCost:
    @pytest.mark.asyncio
    async def test_statement_count_is_the_same_at_20_and_600_rules(self, db_session) -> None:
        counts: dict[int, int] = {}
        for n in (20, 600):
            inst = await make_instance(db_session)
            db_session.add_all(
                AnalyticsRule(
                    instance_id=inst.id, qradar_id=i, name=f"r{i}",
                    rule_type="EVENT" if i % 3 else "COMMON", enabled=bool(i % 5),
                    is_building_block=False, average_capacity=float(i),
                )
                for i in range(n)
            )
            await db_session.flush()
            await add_windows(db_session, inst)
            rules = (
                await db_session.scalars(
                    select(AnalyticsRule).where(AnalyticsRule.instance_id == inst.id)
                )
            ).all()
            for rule in rules[: n // 2]:
                await add_match(db_session, rule, records=2, events=3)
            with recorded_statements(db_session) as statements:
                result = await stats(db_session, inst, top=50)
            counts[n] = len(statements)
            assert result.window.complete and len(result.top_by_events) == min(50, n // 2)

        assert counts[20] == counts[600], counts


def _api_params(**extra) -> dict:
    return {"days": 1, "top": 5, **extra}


class TestApi:
    @pytest.mark.parametrize(
        "params",
        [{"days": 0}, {"days": 31}, {"top": 0}, {"top": 51}, {"days": "x"}, {"instance_id": "x"}],
    )
    def test_out_of_range_parameters_are_422(self, params, db_schema) -> None:
        resp = client_for(READER).get("/api/v1/rules/statistics", params=params)
        assert resp.status_code == 422

    def test_the_bounds_themselves_are_accepted(self, db_schema) -> None:
        # No instance registered: 404 proves validation passed, and is not a 500.
        for params in ({"days": 1, "top": 1}, {"days": 30, "top": 50}):
            resp = client_for(READER).get("/api/v1/rules/statistics", params=params)
            assert resp.status_code == 404

    @pytest.mark.parametrize(
        "path",
        ["/api/v1/rules/statistics", f"/api/v1/rules/{uuid.uuid4()}/capacity-history"],
    )
    def test_the_router_guard_refuses_a_caller_without_read_rules(self, path, db_schema) -> None:
        assert client_for(NOBODY).get(path).status_code == 403

    @pytest.mark.asyncio
    async def test_the_statistics_route_is_not_swallowed_by_the_rule_id_route(
        self, db_session
    ) -> None:
        inst = await make_instance(db_session)
        await add_rule(db_session, inst, 1, capacity=3.0)
        await db_session.commit()

        resp = client_for(READER).get("/api/v1/rules/statistics")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["window"]["complete"] is False
        assert body["silent"] is None and body["top_by_events"] is None
        assert body["costliest_by_benchmark"]["items"][0]["average_capacity"] == 3.0

    @pytest.mark.asyncio
    async def test_a_complete_span_end_to_end(self, db_session) -> None:
        now = datetime.now(UTC)
        settings = get_settings()
        end = datetime.fromtimestamp(
            (int((now - timedelta(seconds=settings.rule_match_settle_lag_seconds)).timestamp())
             // 3600) * 3600,
            tz=UTC,
        )
        inst = await make_instance(db_session)
        quiet = await add_rule(db_session, inst, 1, name="quiet")
        loud = await add_rule(db_session, inst, 2, name="loud")
        await add_windows(db_session, inst, end=end)
        db_session.add(
            RuleMatchMetric(
                rule_id=loud.id, bucket_start=end - HOUR, bucket_seconds=3600,
                match_records=1, matched_events=7200,
            )
        )
        await db_session.commit()

        resp = client_for(ADMIN).get("/api/v1/rules/statistics", params=_api_params())

        body = resp.json()
        assert resp.status_code == 200, resp.text
        assert body["window"]["complete"] is True
        assert [r["rule_id"] for r in body["silent"]["items"]] == [str(quiet.id)]
        assert body["top_by_events"][0]["match_rate_per_second"] == pytest.approx(7200 / 86400)


class TestCapacityHistory:
    @pytest.mark.asyncio
    async def test_newest_first_and_capped_at_100(self, db_session) -> None:
        inst = await make_instance(db_session)
        rule = await add_rule(db_session, inst, 1)
        db_session.add_all(
            RuleCapacitySample(
                rule_id=rule.id, capacity_timestamp=NOW - timedelta(days=i),
                average_capacity=float(i), base_capacity=float(i), base_host_id=53,
                recorded_at=NOW,
            )
            for i in range(105)
        )
        await db_session.commit()

        resp = client_for(READER).get(f"/api/v1/rules/{rule.id}/capacity-history")

        body = resp.json()
        assert resp.status_code == 200 and len(body) == 100
        stamps = [row["capacity_timestamp"] for row in body]
        assert stamps == sorted(stamps, reverse=True)
        assert body[0]["average_capacity"] == 0.0

    @pytest.mark.asyncio
    async def test_a_rule_without_samples_is_an_empty_list_and_an_unknown_rule_404(
        self, db_session
    ) -> None:
        inst = await make_instance(db_session)
        rule = await add_rule(db_session, inst, 1)
        await db_session.commit()

        assert client_for(READER).get(
            f"/api/v1/rules/{rule.id}/capacity-history"
        ).json() == []
        assert client_for(READER).get(
            f"/api/v1/rules/{uuid.uuid4()}/capacity-history"
        ).status_code == 404

