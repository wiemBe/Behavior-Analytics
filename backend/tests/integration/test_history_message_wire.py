"""MSG-8a: the history admin page and estimate carry codes beside their sentences.

The sentences keep their English text; each coded field gains `<field>_code` and
`<field>_params`, and the rows written before the codes existed stay uncoded. A coded field
renders, through its registry template, exactly the English text the API sends beside it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.history.planner import Gap
from app.i18n.messages import MessageRef, render
from tests.integration.factories import make_instance, make_log_source
from tests.integration.test_history_backfill_api import (
    ADMIN,
    BASE,
    READER,
    STAMP,
    START,
    Client,
    catchall_bucket,
    decision,
    family_of,
    progress_body,
    rule,
    scope_row,
    waf_bucket,
    waf_endpoint,
    waf_profile,
)

pytestmark = pytest.mark.integration


def _value(value: object) -> object:
    """A wire parameter as a `render` input: nested references and lists are rebuilt."""
    if isinstance(value, dict) and "code" in value:
        return MessageRef(value["code"], {k: _value(v) for k, v in value["params"].items()})
    if isinstance(value, dict) and value.get("kind") == "list":
        return [_value(item) for item in value["items"]]
    return value


def _rendered(code: str | None, params: dict | None) -> str:
    assert code is not None
    return render(MessageRef(code, {k: _value(v) for k, v in (params or {}).items()}))


async def test_a_coded_scope_reason_travels_with_its_code_and_an_old_one_does_not(  # type: ignore[no-untyped-def]
    db_session, db_schema
) -> None:
    instance = await make_instance(db_session)
    db_session.add(decision(instance.id, "YES"))
    gap = Gap(
        START,
        START + timedelta(minutes=30),
        "search failed 3 times at the minimum width",
        "history.gap.search_failed",
        {"failures": 3},
    )
    db_session.add_all(
        [
            scope_row(
                instance.id,
                "IPS",
                "1",
                state="PARTIAL",
                finished_at=STAMP,
                reason="stopped by ayse",
                reason_i18n={"code": "history.lifecycle.stopped_by", "params": {"by": "ayse"}},
            ),
            scope_row(
                instance.id,
                "IPS",
                "2",
                state="PARTIAL",
                finished_at=STAMP,
                reason="stopped by ayse",
            ),
            scope_row(
                instance.id,
                "CATCHALL",
                "",
                state="READING",
                reason="reading",
                floor=START,
                through=START + timedelta(hours=1),
                progress=progress_body(
                    floor=START,
                    through=START + timedelta(hours=1),
                    searches=3,
                    buckets_read=12,
                    gaps=(gap, Gap(START + timedelta(hours=2), START + timedelta(hours=3), "old")),
                ),
            ),
        ]
    )
    await db_session.commit()
    console = Client(db_schema, READER).get(BASE).json()[0]
    coded, old = family_of(console, "IPS")["scopes"]
    assert coded["reason"] == "stopped by ayse"
    assert coded["reason_code"] == "history.lifecycle.stopped_by"
    assert coded["reason_params"] == {"by": "ayse"}
    assert _rendered(coded["reason_code"], coded["reason_params"]) == coded["reason"]
    assert (old["reason"], old["reason_code"], old["reason_params"]) == (
        "stopped by ayse",
        None,
        None,
    )
    reading = family_of(console, "CATCHALL")["scopes"][0]
    # A row written without a code stays uncoded.
    assert reading["reason"] == "reading"
    assert (reading["reason_code"], reading["reason_params"]) == (None, None)
    coded_gap, old_gap = reading["gaps"]
    assert coded_gap["reason_code"] == "history.gap.search_failed"
    assert coded_gap["reason_params"] == {"failures": 3}
    assert _rendered(coded_gap["reason_code"], coded_gap["reason_params"]) == coded_gap["reason"]
    assert (old_gap["reason_code"], old_gap["reason_params"]) == (None, None)
    # The later-Yes words of a scope are coded as well: a console-wide family's, and a
    # per-source scope's (it already has a scope, so a later Yes is not needed).
    assert reading["later_yes"] == "possible"
    assert (reading["later_yes_code"], reading["later_yes_params"]) == (
        "history.later_yes.possible",
        {},
    )
    assert coded["later_yes"] == "not needed: this source has a history scope"
    assert coded["later_yes_code"] == "history.later_yes.has_scope"
    assert _rendered(coded["later_yes_code"], coded["later_yes_params"]) == coded["later_yes"]


async def test_the_collection_switch_and_window_reasons_are_coded(db_session, db_schema) -> None:  # type: ignore[no-untyped-def]
    await make_instance(db_session)
    await db_session.commit()
    client = Client(
        db_schema,
        READER,
        settings_update={
            "waf_collection_enabled": True,
            "ips_collection_enabled": False,
            "waf_metric_retention_days": 30,
        },
    )
    console = client.get(BASE).json()[0]
    waf = family_of(console, "WAF")
    assert waf["collection_reason"] == "WAF_COLLECTION_ENABLED is on"
    assert waf["collection_reason_code"] == "history.family.switch_on"
    assert waf["collection_reason_params"] == {"setting": "WAF_COLLECTION_ENABLED"}
    ips = family_of(console, "IPS")
    assert ips["collection_reason"] == "IPS_COLLECTION_ENABLED is off"
    assert ips["collection_reason_code"] == "history.family.switch_off"
    phase_a = family_of(console, "PHASE_A")
    assert phase_a["collection_reason_code"] == "history.family.always_on"
    assert phase_a["collection_reason_params"] == {}
    window = waf["window"]
    assert window["reason_code"] == "history.window.reduced"
    assert _rendered(window["reason_code"], window["reason_params"]) == window["reason"]
    assert window["reason_params"]["asked"] == 28 and window["reason_params"]["days"] == 26
    assert window["reason_params"]["limits"]["kind"] == "list"
    assert phase_a["window"]["reason_code"] is None
    # What a later Yes would do is coded too, beside the English the page compares.
    assert phase_a["later_yes"] == "possible"
    assert phase_a["later_yes_code"] == "history.later_yes.possible"
    assert phase_a["later_yes_params"] == {}
    assert waf["later_yes"] is None and waf["later_yes_code"] is None


async def test_an_unavailable_window_is_coded_with_its_limits(db_session, db_schema) -> None:  # type: ignore[no-untyped-def]
    await make_instance(db_session)
    await db_session.commit()
    client = Client(
        db_schema,
        READER,
        settings_update={
            "waf_collection_enabled": True,
            "waf_metric_retention_days": 9,
            "baseline_lookback_days": 7,
        },
    )
    window = family_of(client.get(BASE).json()[0], "WAF")["window"]
    assert window["available"] is False
    assert window["reason_code"] == "history.window.unavailable"
    assert _rendered(window["reason_code"], window["reason_params"]) == window["reason"]


async def test_the_estimate_reason_and_every_assumption_carry_their_code(  # type: ignore[no-untyped-def]
    db_session, db_schema
) -> None:
    instance = await make_instance(db_session)
    waf_source = await make_log_source(db_session, instance, qradar_id=10, name="waf-1")
    catchall_source = await make_log_source(db_session, instance, qradar_id=12, name="catch-all")
    for index in (1, 2):
        await make_log_source(db_session, instance, qradar_id=index, name=f"src-{index}")
    db_session.add_all(
        [
            waf_profile(instance.id, waf_source.id),
            waf_endpoint(instance.id, waf_source.id, "/a"),
            catchall_bucket(catchall_source.id, datetime.now(UTC) - timedelta(hours=1)),
            rule(instance.id, 1),
        ]
    )
    await db_session.commit()
    client = Client(
        db_schema,
        READER,
        settings_update={
            "waf_collection_enabled": True,
            "ips_collection_enabled": False,
            "rule_match_collection_enabled": True,
        },
    )
    estimates = {
        item["family"]: item for item in client.get(f"{BASE}/estimate").json()[0]["families"]
    }
    assert set(estimates) >= {"PHASE_A", "WAF", "IPS", "CATCHALL", "RULE_MATCH"}
    for family, item in estimates.items():
        assert len(item["assumption_codes"]) == len(item["assumptions"]), family
        for text, wire in zip(item["assumptions"], item["assumption_codes"], strict=True):
            assert wire is not None, (family, text)
            assert _rendered(wire["code"], wire["params"]) == text, (family, text)
        if item["reason_code"] is not None:
            assert _rendered(item["reason_code"], item["reason_params"]) == item["reason"]
    # No count to estimate from: the reason is coded, and no number is invented.
    ips = estimates["IPS"]
    assert ips["searches"] is None
    assert ips["reason_code"] == "history.estimate.no_collection"
    assert ips["reason_params"]["reason"]["code"] == "history.family.switch_off"
    assert estimates["PHASE_A"]["reason_code"] is not None


async def test_the_decision_and_request_sentences_are_coded(db_session, db_schema) -> None:  # type: ignore[no-untyped-def]
    instance = await make_instance(db_session)
    other = await make_instance(db_session)
    db_session.add(decision(instance.id, "YES"))
    await db_session.commit()
    client = Client(db_schema, ADMIN)

    reversed_ = client.put(f"{BASE}/{instance.id}", json={"answer": "NO"})
    assert reversed_.status_code == 409
    body = reversed_.json()
    assert body["detail_code"] == "history.decide.never_reversed"
    assert _rendered(body["detail_code"], body["detail_params"]) == body["detail"]

    refused = client.post(f"{BASE}/{other.id}/start")
    assert refused.status_code == 409
    body = refused.json()
    assert body["detail_code"] == "history.request.refused"
    assert _rendered(body["detail_code"], body["detail_params"]) == body["detail"]

    started = client.post(f"{BASE}/{instance.id}/start").json()
    assert started["message_code"] == "history.request.start"
    assert _rendered(started["message_code"], started["message_params"]) == started["message"]
    stopped = client.post(f"{BASE}/{instance.id}/stop").json()
    assert stopped["message_code"] == "history.request.stop"
    assert _rendered(stopped["message_code"], stopped["message_params"]) == stopped["message"]
    # A plain read of the console carries no message, so no code either.
    plain = client.get(BASE).json()[0]
    assert (plain["message"], plain["message_code"], plain["message_params"]) == (
        None,
        None,
        None,
    )


async def test_a_source_without_a_scope_carries_the_code_of_its_later_yes(  # type: ignore[no-untyped-def]
    db_session, db_schema
) -> None:
    instance = await make_instance(db_session)
    live = await make_log_source(db_session, instance, qradar_id=1, name="waf-live")
    fresh = await make_log_source(db_session, instance, qradar_id=2, name="waf-fresh")
    db_session.add_all(
        [
            waf_profile(instance.id, live.id),
            waf_profile(instance.id, fresh.id),
            waf_bucket(live.id, START),
        ]
    )
    await db_session.commit()
    console = Client(db_schema, READER).get(BASE).json()[0]
    sources = {s["scope_key"]: s for s in family_of(console, "WAF")["sources"]}
    assert sources["1"]["later_yes_code"] == "history.later_yes.not_possible"
    assert sources["2"]["later_yes_code"] == "history.later_yes.possible"
    for source in sources.values():
        assert source["later_yes_params"] == {}
        words = _rendered(source["later_yes_code"], source["later_yes_params"])
        assert words == source["later_yes"]
