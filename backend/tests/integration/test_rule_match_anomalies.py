"""SOC-6d1: the statistics-block read, `GET /rules/match-anomalies` (design §7.1).

Rows are inserted directly. Every state of the block is driven through the real route, the
statement count through the handler on a recorded session, and the neighbouring
`/rules/statistics` page is pinned by `test_rule_statistics_golden.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.api.deps import get_settings
from app.api.routes.rules import rule_match_anomalies
from app.core.config import Settings
from app.models.enums import AnomalyState
from app.rules.matches import floor_to_window
from tests.integration.factories import make_instance
from tests.integration.test_phase3_api import NOBODY, READER, client_for
from tests.integration.test_rule_anomaly_read import add_rule, incident
from tests.integration.test_rule_match_collection import recorded_statements
from tests.integration.test_rule_statistics import add_windows

pytestmark = pytest.mark.integration

URL = "/api/v1/rules/match-anomalies"


def settings(**extra: object) -> Settings:
    return get_settings().model_copy(update={"rule_match_collection_enabled": True, **extra})


def span_end() -> datetime:
    cfg = settings()
    return floor_to_window(
        datetime.now(UTC) - timedelta(seconds=cfg.rule_match_settle_lag_seconds),
        cfg.rule_match_window_seconds,
    )


def get(principal=READER, cfg: Settings | None = None, **params):
    client = client_for(principal)
    client.app.dependency_overrides[get_settings] = lambda: cfg or settings()
    try:
        return client.get(URL, params={"days": 1, **params})
    finally:
        client.app.dependency_overrides.pop(get_settings, None)


class TestStates:
    @pytest.mark.asyncio
    async def test_collection_off_is_not_collected_with_no_list(self, db_session) -> None:
        inst = await make_instance(db_session)
        rule = await add_rule(db_session, inst, 1)
        await add_windows(db_session, inst, end=span_end())
        db_session.add(incident(rule))
        await db_session.commit()

        body = get(cfg=settings(rule_match_collection_enabled=False)).json()

        assert body["state"] == "NOT_COLLECTED"
        assert body["items"] is None and body["total"] is None and body["span"] is None

    @pytest.mark.asyncio
    async def test_no_recorded_window_is_not_collected(self, db_session) -> None:
        inst = await make_instance(db_session)
        rule = await add_rule(db_session, inst, 1)
        db_session.add(incident(rule))
        await db_session.commit()

        body = get().json()

        assert body["state"] == "NOT_COLLECTED"
        assert body["items"] is None and body["total"] is None

    @pytest.mark.asyncio
    async def test_a_complete_span_with_no_incident_is_an_empty_ok(self, db_session) -> None:
        inst = await make_instance(db_session)
        await add_windows(db_session, inst, end=span_end())
        await db_session.commit()

        body = get().json()

        assert body["state"] == "OK"
        assert body["items"] == [] and body["total"] == 0 and body["items_truncated"] is False
        assert body["span"]["complete"] is True and body["span"]["window_status"] == "complete"
        assert body["reason"] is None

    @pytest.mark.asyncio
    async def test_a_complete_span_lists_silence_and_storm_with_their_fields(
        self, db_session
    ) -> None:
        inst = await make_instance(db_session)
        quiet = await add_rule(db_session, inst, 7, name="Quiet one")
        loud = await add_rule(db_session, inst, 8, name="Loud BB", bb=True)
        await add_windows(db_session, inst, end=span_end())
        started = datetime.now(UTC) - timedelta(hours=3)
        silence = incident(
            quiet, detector="RULE_SILENCE", observed_value=0.0, expected_value=40.0,
            anomaly_start=started, detected_at=datetime.now(UTC) - timedelta(hours=2),
        )
        storm = incident(
            loud, observed_value=900.0, expected_value=90.0, state=AnomalyState.RECOVERING,
            detected_at=datetime.now(UTC) - timedelta(hours=1),
            details={"extra": {"truncated_window": False}},
        )
        db_session.add_all([silence, storm])
        await db_session.commit()

        body = get().json()

        assert body["state"] == "OK" and body["total"] == 2
        first, second = body["items"]
        assert first["anomaly_id"] == str(storm.id)
        assert first["rule_id"] == str(loud.id) and first["name"] == "Loud BB"
        assert first["qradar_id"] == 8 and first["is_building_block"] is True
        assert first["detector"] == "RULE_STORM" and first["state"] == "RECOVERING"
        assert first["observed"] == 900.0 and first["expected"] == 90.0
        assert first["row_state"] == "OK" and first["window_status"] == "complete"
        assert second["detector"] == "RULE_SILENCE" and second["observed"] == 0.0
        assert datetime.fromisoformat(second["started"]) == started

    @pytest.mark.asyncio
    async def test_an_incomplete_span_withholds_silence_and_marks_storms(
        self, db_session
    ) -> None:
        inst = await make_instance(db_session)
        quiet = await add_rule(db_session, inst, 7)
        loud = await add_rule(db_session, inst, 8)
        await add_windows(db_session, inst, end=span_end(), skip=(3,))
        db_session.add_all([incident(quiet, detector="RULE_SILENCE"), incident(loud)])
        await db_session.commit()

        body = get().json()

        assert body["state"] == "INSUFFICIENT_DATA"
        assert body["span"]["complete"] is False and body["span"]["missing_windows"] == 1
        assert body["reason"] and body["reason_code"] == "rule_statistics.window.incomplete"
        assert [i["detector"] for i in body["items"]] == ["RULE_STORM"]
        assert body["items"][0]["window_status"] == "incomplete"
        assert body["total"] == 1

    @pytest.mark.asyncio
    async def test_a_truncated_observed_window_is_named_on_its_row(self, db_session) -> None:
        inst = await make_instance(db_session)
        rule = await add_rule(db_session, inst, 8)
        other = await add_rule(db_session, inst, 9)
        await add_windows(db_session, inst, end=span_end())
        truncated = incident(rule, details={"extra": {"truncated_window": True}},
                             detected_at=datetime.now(UTC) - timedelta(hours=1))
        exact = incident(other, details={"extra": {"truncated_window": False}},
                         detected_at=datetime.now(UTC) - timedelta(hours=2))
        db_session.add_all([truncated, exact])
        await db_session.commit()

        items = {i["anomaly_id"]: i for i in get().json()["items"]}

        assert items[str(truncated.id)]["row_state"] == "TRUNCATED"
        assert items[str(exact.id)]["row_state"] == "OK"

    @pytest.mark.asyncio
    async def test_candidates_other_instances_and_old_resolved_rows_are_not_listed(
        self, db_session
    ) -> None:
        inst = await make_instance(db_session)
        elsewhere = await make_instance(db_session)
        rule = await add_rule(db_session, inst, 1)
        foreign = await add_rule(db_session, elsewhere, 1)
        await add_windows(db_session, inst, end=span_end())
        now = datetime.now(UTC)
        old = now - timedelta(days=3)
        db_session.add_all(
            [
                incident(rule, state=AnomalyState.CANDIDATE, detected_at=now),
                incident(foreign, detected_at=now),
                incident(rule, state=AnomalyState.RESOLVED, detected_at=old),
            ]
        )
        still_open = incident(rule, detected_at=old, detector="RULE_SILENCE")
        recent = incident(rule, state=AnomalyState.RESOLVED, detected_at=now)
        db_session.add_all([still_open, recent])
        await db_session.commit()

        ids = {i["anomaly_id"] for i in get(instance_id=str(inst.id)).json()["items"]}

        assert ids == {str(still_open.id), str(recent.id)}

    @pytest.mark.asyncio
    async def test_the_list_is_capped_but_the_total_counts_every_match(self, db_session) -> None:
        inst = await make_instance(db_session)
        rule = await add_rule(db_session, inst, 1)
        await add_windows(db_session, inst, end=span_end())
        db_session.add_all(
            incident(rule, detected_at=datetime.now(UTC) - timedelta(minutes=i)) for i in range(5)
        )
        await db_session.commit()

        body = get(limit=2).json()

        assert len(body["items"]) == 2 and body["total"] == 5 and body["items_truncated"] is True


class TestRoute:
    @pytest.mark.parametrize("params", [{"days": 0}, {"days": 31}, {"limit": 0}, {"limit": 201}])
    def test_out_of_range_parameters_are_422(self, params, db_schema) -> None:
        assert client_for(READER).get(URL, params=params).status_code == 422

    def test_the_route_needs_read_rules(self, db_schema) -> None:
        assert client_for(NOBODY).get(URL).status_code == 403

    @pytest.mark.asyncio
    async def test_the_literal_path_is_not_swallowed_by_the_rule_id_route(
        self, db_session
    ) -> None:
        await make_instance(db_session)
        await db_session.commit()

        resp = get()

        assert resp.status_code == 200, resp.text

    def test_no_instance_is_404_not_a_500(self, db_schema) -> None:
        assert get().status_code == 404


class TestConstantCost:
    @pytest.mark.asyncio
    async def test_statement_count_is_the_same_at_10_and_500_rules(self, db_session) -> None:
        counts: dict[int, int] = {}
        for n in (10, 500):
            inst = await make_instance(db_session)
            rules = [await add_rule(db_session, inst, i) for i in range(n)]
            await add_windows(db_session, inst, end=span_end())
            now = datetime.now(UTC)
            db_session.add_all(
                incident(r, detector="RULE_SILENCE" if i % 2 else "RULE_STORM",
                         detected_at=now - timedelta(minutes=i))
                for i, r in enumerate(rules)
            )
            await db_session.flush()
            with recorded_statements(db_session) as statements:
                body = await rule_match_anomalies(
                    db_session, settings(), instance_id=inst.id, days=1, limit=200
                )
            counts[n] = len(statements)
            cap = min(200, get_settings().api_max_page_size)
            assert body.state == "OK" and body.total == n and len(body.items) == min(n, cap)

        assert counts[10] == counts[500], counts
