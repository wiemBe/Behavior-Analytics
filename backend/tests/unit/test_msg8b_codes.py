"""MSG-8b: the cron floor, the manual-run budget refusal and a run's fixed error sentences.

Every converted site renders, through the registry, the sentence the old code built. The legacy
sentences below are written out by hand (not rebuilt from the registry), so a template edit fails
here. Free text (an exception's or Ariel's own words) never gets a code.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import HTTPException

from app.api.routes.searches import _cron_floor_refusal
from app.i18n.messages import MessageRef, render
from app.i18n.refusal import CodedHTTPException
from app.services.aql_validator import AQLValidationError, validate_aql
from app.services.ariel_errors import (
    ArielSearchFailed,
    ArielTimeout,
    ResultParsingError,
    failure_ref,
)
from app.services.ariel_permit import ArielPermitUnavailable
from app.services.scheduled_search import ScheduledSearchService
from app.services.search_scheduler import CronFloorError, check_cron_floor

NOW = datetime(2026, 6, 1, tzinfo=UTC)
T0 = datetime(2026, 10, 29, 10, 0, tzinfo=UTC)
T1 = datetime(2026, 10, 29, 10, 2, tzinfo=UTC)

#: code, params, the sentence the old code built.
PINS: list[tuple[str, dict[str, Any], str]] = [
    (
        "search.cron.below_floor",
        {
            "cron": repr("*/2 * * * *"),
            "timezone": "UTC",
            "gap": 120,
            "previous": T0,
            "next": T1,
            "min_seconds": 300,
        },
        "schedule_cron '*/2 * * * *' (UTC) fires 120 seconds apart "
        "(2026-10-29T10:00:00+00:00 then 2026-10-29T10:02:00+00:00); "
        "a search must fire at least 300 seconds apart",
    ),
    (
        "search.cron.below_floor",
        {
            "cron": repr("* * * * *"),
            "timezone": "Europe/Istanbul",
            "gap": 59.5,
            "previous": T0,
            "next": T1,
            "min_seconds": 300,
        },
        "schedule_cron '* * * * *' (Europe/Istanbul) fires 59.5 seconds apart "
        "(2026-10-29T10:00:00+00:00 then 2026-10-29T10:02:00+00:00); "
        "a search must fire at least 300 seconds apart",
    ),
    (
        "search.cron.invalid",
        {"cron": repr("not a cron"), "timezone": "UTC", "error": "Wrong number of fields"},
        "schedule_cron 'not a cron' (UTC) is not a valid cron expression: Wrong number of fields",
    ),
    (
        "search.cron.no_future_fire",
        {"cron": repr("0 0 30 2 *"), "timezone": "UTC"},
        "schedule_cron '0 0 30 2 *' (UTC) has no future fire time",
    ),
    ("search.cron.floor_missed", {}, "schedule_cron is below the floor"),
    (
        "search.budget.full",
        {"held": MessageRef("search.budget.held_of", {"held": 6, "cap": 6}), "waited": 30.0},
        "the Ariel search budget is full (6 of 6 in flight); waited 30 s; "
        "nothing was sent to QRadar",
    ),
    (
        "search.budget.full",
        {"held": MessageRef("search.budget.held_of", {"held": 0, "cap": 1}), "waited": 0.0},
        "the Ariel search budget is full (0 of 1 in flight); waited 0 s; "
        "nothing was sent to QRadar",
    ),
    (
        "search.budget.full",
        {"held": MessageRef("search.budget.held_cap", {"cap": 12}), "waited": 2.5},
        "the Ariel search budget is full (cap 12 in flight); waited 2.5 s; "
        "nothing was sent to QRadar",
    ),
    ("search.run.failed", {}, "Ariel search failed"),
    ("search.run.timeout", {"timeout": 1}, "search exceeded timeout of 1s"),
    ("search.run.timeout", {"timeout": 900}, "search exceeded timeout of 900s"),
    (
        "search.run.capacity",
        {"waited": 30.0, "instance_cap": 3, "global_cap": 6},
        "No Ariel permit became available within 30 seconds (per-console cap 3, global cap 6)",
    ),
    (
        "search.run.capacity",
        {"waited": 0.5, "instance_cap": 1, "global_cap": 1204},
        "No Ariel permit became available within 0.5 seconds (per-console cap 1, global cap 1204)",
    ),
    ("search.run.no_aql_execution", {}, "configured provider cannot execute AQL"),
    (
        "search.run.parse_failed",
        {"detail": "bad column"},
        "failed to fetch/parse results: bad column",
    ),
]


@pytest.mark.parametrize(("code", "params", "legacy"), PINS)
def test_the_registry_renders_the_legacy_sentence(
    code: str, params: dict[str, Any], legacy: str
) -> None:
    assert render(MessageRef(code, params)) == legacy


@pytest.mark.parametrize(
    ("gap", "legacy"),
    [
        (0, "0"),
        (1, "1"),
        (299, "299"),
        (299.5, "299.5"),
        (59.999, "59.999"),
        (0.0004, "0"),
        (120.1234, "120.123"),
        (60.0, "60"),
    ],
)
def test_the_gap_prints_as_the_old_helper_did(gap: float, legacy: str) -> None:
    ref = MessageRef(
        "search.cron.below_floor",
        {
            "cron": "'x'", "timezone": "UTC", "gap": gap, "previous": T0, "next": T1,
            "min_seconds": 300,
        },
    )
    assert f"fires {legacy} seconds apart" in render(ref)


def _legacy_gap_text(gap: float) -> str:
    # The helper the f-string used (`_gap_seconds_text`), written out here as the reference.
    if gap == int(gap):
        return str(int(gap))
    return f"{gap:.3f}".rstrip("0").rstrip(".")


@pytest.mark.parametrize("gap", [0.0, 0.25, 59.0, 59.9996, 120.5, 299.999, 1e6 + 0.1])
def test_the_gap_style_equals_the_legacy_helper(gap: float) -> None:
    ref = MessageRef(
        "search.cron.below_floor",
        {
            "cron": "'x'", "timezone": "UTC", "gap": gap, "previous": T0, "next": T1,
            "min_seconds": 300,
        },
    )
    assert f"fires {_legacy_gap_text(gap)} seconds apart" in render(ref)


# --- the cron floor -------------------------------------------------------------------------


def test_a_too_fast_cron_carries_its_code_and_the_old_sentence() -> None:
    outcome = check_cron_floor("*/2 * * * *", "UTC", now=NOW)
    assert not outcome.accepted
    assert outcome.ref is not None and outcome.ref.code == "search.cron.below_floor"
    assert outcome.reason == (
        "schedule_cron '*/2 * * * *' (UTC) fires 120 seconds apart "
        "(2026-06-01T00:00:00+00:00 then 2026-06-01T00:02:00+00:00); "
        "a search must fire at least 300 seconds apart"
    )
    assert outcome.reason == render(outcome.ref)
    assert outcome.ref.params["gap"] == 120
    assert outcome.ref.params["min_seconds"] == 300


def test_an_accepted_cron_has_no_code() -> None:
    outcome = check_cron_floor("*/5 * * * *", "UTC", now=NOW)
    assert outcome.accepted and outcome.reason is None and outcome.ref is None


def test_an_invalid_cron_carries_its_code() -> None:
    outcome = check_cron_floor("not a cron", "UTC", now=NOW)
    assert outcome.ref is not None and outcome.ref.code == "search.cron.invalid"
    assert outcome.reason is not None
    assert outcome.reason.startswith(
        "schedule_cron 'not a cron' (UTC) is not a valid cron expression: "
    )
    assert outcome.reason == render(outcome.ref)
    assert outcome.ref.params["cron"] == "'not a cron'"


def test_a_cron_with_no_future_fire_carries_its_code() -> None:
    outcome = check_cron_floor("0 0 30 2 *", "UTC", now=NOW)
    assert outcome.ref is not None and outcome.ref.code == "search.cron.no_future_fire"
    assert outcome.reason == "schedule_cron '0 0 30 2 *' (UTC) has no future fire time"


def test_the_service_refusal_carries_the_code_to_the_422() -> None:
    service = ScheduledSearchService(None)  # type: ignore[arg-type]
    with pytest.raises(CronFloorError) as caught:
        asyncio.run(service._refuse_fast_cron("*/2 * * * *", "UTC"))
    assert caught.value.ref is not None and caught.value.ref.code == "search.cron.below_floor"
    refusal = _cron_floor_refusal(caught.value)
    assert isinstance(refusal, CodedHTTPException)
    assert refusal.status_code == 422
    assert refusal.detail == str(caught.value)
    body = refusal.body()
    assert body["detail_code"] == "search.cron.below_floor"
    assert body["detail_params"]["previous"].endswith("+00:00")


def test_an_error_with_no_code_stays_a_plain_422() -> None:
    refusal = _cron_floor_refusal(CronFloorError("some other sentence"))
    assert type(refusal) is HTTPException
    assert refusal.detail == "some other sentence"


# --- a run's fixed error sentences -----------------------------------------------------------


def test_the_permit_refusal_text_is_unchanged_and_coded() -> None:
    exc = ArielPermitUnavailable(30, 3, 6)
    assert str(exc) == (
        "No Ariel permit became available within 30 seconds "
        "(per-console cap 3, global cap 6)"
    )
    assert exc.waited_seconds == 30 and exc.instance_cap == 3 and exc.global_cap == 6
    assert failure_ref(exc) == MessageRef(
        "search.run.capacity", {"waited": 30, "instance_cap": 3, "global_cap": 6}
    )
    assert str(ArielPermitUnavailable(2.5, 1, 2)) == (
        "No Ariel permit became available within 2.5 seconds (per-console cap 1, global cap 2)"
    )


def test_coded_failures_carry_their_ref_and_their_text() -> None:
    timeout = ArielTimeout(MessageRef("search.run.timeout", {"timeout": 300}))
    assert str(timeout) == "search exceeded timeout of 300s"
    assert failure_ref(timeout) == MessageRef("search.run.timeout", {"timeout": 300})
    failed = ArielSearchFailed(MessageRef("search.run.failed"))
    assert str(failed) == "Ariel search failed"
    assert failure_ref(failed) == MessageRef("search.run.failed")
    parse = ResultParsingError(MessageRef("search.run.parse_failed", {"detail": "boom"}))
    assert str(parse) == "failed to fetch/parse results: boom"
    assert failure_ref(parse) is not None


@pytest.mark.parametrize(
    "exc",
    [
        ArielSearchFailed("unknown search id"),
        ArielSearchFailed("one; two"),
        ArielTimeout("t"),
        ResultParsingError("p"),
        RuntimeError("search exceeded timeout of 300s"),
        ValueError("anything"),
    ],
)
def test_free_text_gets_no_code(exc: BaseException) -> None:
    # Even text that equals a coded sentence has no code unless the exception was built from one.
    assert failure_ref(exc) is None


def test_a_ref_that_cannot_reproduce_the_text_is_dropped() -> None:
    exc = ArielTimeout(MessageRef("search.run.timeout", {"timeout": 300}))
    exc.args = ("search exceeded timeout of 301s",)
    assert failure_ref(exc) is None


def test_the_defensive_revalidation_is_coded_when_every_reason_is() -> None:
    with pytest.raises(AQLValidationError) as caught:
        validate_aql("SELECT * FROM events; DROP TABLE x")
    exc = caught.value
    ref = failure_ref(exc)
    assert ref is not None and ref.code == "search.run.validation"
    assert render(ref) == str(exc) == "; ".join(exc.reasons)
    assert len(exc.reasons) >= 2


def test_a_validation_error_with_a_plain_reason_gets_no_code() -> None:
    assert failure_ref(AQLValidationError(["plain", MessageRef("search.aql.empty")])) is None
    assert failure_ref(AQLValidationError([])) is None
