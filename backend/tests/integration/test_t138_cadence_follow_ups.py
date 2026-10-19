"""T-138: per-family cadence defaults, cadence-independent first run and NOT_APPLICABLE,
honest property-watch statuses."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.catchall.keyed_rows import parse_catchall_rows  # noqa: F401
from app.collectors.catchall_collector import CatchallCollector
from app.collectors.geo_collector import GeoCollector
from app.core.config import get_settings
from app.custom_rules.engine import (
    _FLEET_CLASS,
    _IPS_ENTITY,
    _IPS_SOURCE,
    _LOG_SOURCE,
    _PW_VALUE,
    _WAF_ENDPOINT,
    CustomRuleEngine,
)
from app.models.enums import PwWatchStatus, SearchCadence, SearchFamily
from app.models.search_cadence import SearchCadenceSetting
from app.providers.base import ProviderError
from app.providers.mock import MockQRadarProvider, geo_key
from tests.integration.factories import make_instance
from tests.integration.pw_engine_support import STEP, T0, make_rig
from tests.integration.test_property_watches_api import PREFIX
from tests.integration.test_pw_collection import (
    add_watch,
    buckets_of,
    collect,
    pin_cadence,
    provider_with,
    reload,
)
from tests.integration.test_pw_reenable import _route_watch, _watch_row

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

NOW = datetime(2026, 10, 13, 12, 10, tzinfo=UTC)


# ------------------------------------------------- custom-rules cadence fallback (item 5)
async def test_the_custom_rules_engine_falls_back_to_each_familys_default(db_session) -> None:
    """No cadence row: WAF and IPS wait 15 minutes, everything else 5."""
    engine = CustomRuleEngine(db_session, get_settings())
    kinds = [_LOG_SOURCE, _FLEET_CLASS, _WAF_ENDPOINT, _IPS_ENTITY, _IPS_SOURCE, _PW_VALUE]
    assert await engine._cadence_seconds(kinds) == {
        _LOG_SOURCE: 300,
        _FLEET_CLASS: 300,
        _WAF_ENDPOINT: 900,
        _IPS_ENTITY: 900,
        _IPS_SOURCE: 900,
        _PW_VALUE: 300,
    }


async def test_a_stored_cadence_row_beats_the_custom_rules_fallback(db_session) -> None:
    db_session.add(
        SearchCadenceSetting(
            family=SearchFamily.WAF_COLLECTION.value,
            cadence_seconds=int(SearchCadence.ONE_HOUR),
            updated_at=NOW,
        )
    )
    await db_session.flush()
    engine = CustomRuleEngine(db_session, get_settings())
    got = await engine._cadence_seconds([_WAF_ENDPOINT, _IPS_ENTITY])
    assert got == {_WAF_ENDPOINT: 3600, _IPS_ENTITY: 900}


# ------------------------------------------------------------- first run (item 2)
async def test_the_catchall_first_run_looks_back_twelve_buckets_at_the_default_cadence(
    db_session,
) -> None:
    from tests.integration.test_catchall_collection import Script, row, setup, steady

    inst, _source, _ = await setup(db_session)
    provider = Script(steady([row("fw-01", 50)]))
    settings = get_settings().model_copy(update={"search_cadence_max_searches_per_tick": 50})
    report = await CatchallCollector(
        db_session, provider, settings=settings, clock=lambda: NOW
    ).collect(inst)
    assert report.bootstrap and report.buckets_complete == 12


async def test_the_geo_first_run_looks_back_twelve_buckets_at_the_default_cadence(
    db_session,
) -> None:
    from tests.integration.test_geo_collection import Script, steady

    inst = await make_instance(db_session)
    provider = Script(steady([{"geo": geo_key("US"), "events": 40}]))
    settings = get_settings().model_copy(update={"search_cadence_max_searches_per_tick": 50})
    report = await GeoCollector(
        db_session, provider, settings=settings, clock=lambda: NOW
    ).collect(inst)
    assert report.buckets_complete == 24  # 12 buckets x two directions


# --------------------------------------------------------------- NOT_APPLICABLE (item 2)
def _unparsed_provider() -> MockQRadarProvider:
    provider = MockQRadarProvider(seed=1)
    provider.pw_script = lambda key, names: [{"values": dict.fromkeys(names), "events": 40}]
    return provider


async def test_not_applicable_needs_twelve_consecutive_buckets_counted_across_ticks(
    db_session,
) -> None:
    """At the 5-minute default one tick holds one bucket: eleven ticks do not flag it."""
    instance = await make_instance(db_session)
    start = datetime(2026, 10, 13, 9, 0, tzinfo=UTC)
    watch = await add_watch(db_session, instance, through=start - STEP)
    provider = _unparsed_provider()
    for k in range(11):
        await collect(db_session, instance, provider, now=start + STEP * k + timedelta(minutes=7))
        await reload(db_session, watch)
        assert watch.last_status is None, k
    await collect(db_session, instance, provider, now=start + STEP * 11 + timedelta(minutes=7))
    await reload(db_session, watch)
    assert watch.last_status == PwWatchStatus.NOT_APPLICABLE.value


async def test_one_parsed_bucket_restarts_the_not_applicable_count(db_session) -> None:
    instance = await make_instance(db_session)
    start = datetime(2026, 10, 13, 9, 0, tzinfo=UTC)
    watch = await add_watch(db_session, instance, through=start - STEP)
    bad, good = _unparsed_provider(), provider_with({"alice": 10})
    for k in range(14):
        provider = good if k == 5 else bad
        await collect(db_session, instance, provider, now=start + STEP * k + timedelta(minutes=7))
        await reload(db_session, watch)
        assert watch.last_status is None, k  # never 12 unparsed buckets in a row


async def test_a_failed_attempt_is_retried_and_the_count_continues_over_it(db_session) -> None:
    instance = await make_instance(db_session)
    start = datetime(2026, 10, 13, 9, 0, tzinfo=UTC)
    watch = await add_watch(db_session, instance, through=start - STEP)
    provider = _unparsed_provider()
    for k in range(10):
        await collect(db_session, instance, provider, now=start + STEP * k + timedelta(minutes=7))
    provider.pw_fault = lambda s: ProviderError("boom")
    await collect(db_session, instance, provider, now=start + STEP * 10 + timedelta(minutes=7))
    provider.pw_fault = None
    for k in (10, 11):
        await collect(db_session, instance, provider, now=start + STEP * k + timedelta(minutes=7))
    await reload(db_session, watch)
    # the held bucket was collected on the retry, so buckets 0..11 are 12 in a row
    assert watch.last_status == PwWatchStatus.NOT_APPLICABLE.value


async def test_twelve_buckets_in_one_tick_still_flag_not_applicable(db_session) -> None:
    await pin_cadence(db_session, 3600)
    instance = await make_instance(db_session)
    start = datetime(2026, 10, 13, 9, 0, tzinfo=UTC)
    watch = await add_watch(db_session, instance, through=start - STEP)
    await collect(
        db_session, instance, _unparsed_provider(), now=start + timedelta(hours=1, minutes=7)
    )
    await reload(db_session, watch)
    assert watch.last_status == PwWatchStatus.NOT_APPLICABLE.value


# -------------------------------------------------- a failed search is not OK (item 3)
async def test_a_failed_latest_search_is_stale_and_a_later_bucket_returns_to_ok(
    db_session,
) -> None:
    rig = await make_rig(db_session)
    await rig.ticks(0, 3)
    assert (await rig.reload_watch()).last_status == PwWatchStatus.OK.value
    rig.fail = {3}
    await rig.tick(3)
    watch = await rig.reload_watch()
    assert watch.last_status == PwWatchStatus.STALE.value
    assert "latest search failed" in (watch.last_status_reason or "")
    assert "NOT_COLLECTED" in (watch.last_status_reason or "")
    # still failing: it stays stale, it does not slip back to OK
    await rig.tick(3)
    assert (await rig.reload_watch()).last_status == PwWatchStatus.STALE.value
    rig.fail = set()
    await rig.tick(3)
    watch = await rig.reload_watch()
    assert watch.last_status == PwWatchStatus.OK.value
    assert watch.last_status_reason == "the latest collected bucket was evaluated"
    assert [b.values_measurement for b in await buckets_of(db_session, watch)][-1] == "EXACT"


# --------------------------------------------------------- DISABLED (item 3)
async def test_disabling_says_disabled_and_re_enabling_clears_it(db_session, db_schema) -> None:
    client, created = await _route_watch(db_session, db_schema)
    watch = await _watch_row(db_session, created["id"])
    watch.collected_through = T0 - STEP
    watch.evaluated_through = T0 - STEP
    watch.last_status = PwWatchStatus.OK
    watch.last_status_reason = "the latest collected bucket was evaluated"
    watch.enabled = True
    await db_session.commit()

    disabled = client.patch(
        f"{PREFIX}/{created['id']}",
        json={"expected_updated_at": watch.updated_at.isoformat(), "enabled": False},
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["status"] == "DISABLED"
    watch = await _watch_row(db_session, created["id"])
    assert watch.last_status == PwWatchStatus.DISABLED.value
    assert watch.last_status_reason == "disabled by detection_engineer"

    enabled = client.patch(
        f"{PREFIX}/{created['id']}",
        json={"expected_updated_at": disabled.json()["updated_at"], "enabled": True},
    )
    assert enabled.status_code == 200, enabled.text
    watch = await _watch_row(db_session, created["id"])
    assert watch.last_status is None and watch.last_status_reason is None
    # T-136 stays: the watermarks are reset as before
    assert watch.collected_through is None and watch.evaluated_through == T0 - STEP


async def test_a_rename_keeps_the_status_of_an_enabled_watch(db_session, db_schema) -> None:
    client, created = await _route_watch(db_session, db_schema)
    watch = await _watch_row(db_session, created["id"])
    watch.enabled = True
    watch.last_status = PwWatchStatus.OK
    await db_session.commit()
    renamed = client.patch(
        f"{PREFIX}/{created['id']}",
        json={
            "expected_updated_at": watch.updated_at.isoformat(),
            "name": f"r-{uuid.uuid4().hex[:6]}",
        },
    )
    assert renamed.status_code == 200, renamed.text
    watch = await _watch_row(db_session, created["id"])
    assert watch.last_status == PwWatchStatus.OK.value
