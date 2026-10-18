"""PW-6b: the preview's PW_VALUE plumbing and the 409 mapping of the rule foreign key."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.api.routes import custom_rules as rules_routes
from app.api.routes import property_watches as watch_routes
from app.custom_rules import preview
from app.custom_rules.evaluate import BucketFacts
from app.models.enums import CustomRuleMetric, CustomRuleSubjectKind
from app.services import property_watches as svc

FK = "fk_custom_rule_pw_value_id_pw_value"
KIND = CustomRuleSubjectKind.PW_VALUE


class _Session:
    """Just enough of an AsyncSession: a flush that raises, a savepoint, a rollback."""

    def __init__(self, message: str | None) -> None:
        self.message = message
        self.rolled_back = 0

    @asynccontextmanager
    async def begin_nested(self):  # type: ignore[no-untyped-def]
        yield

    async def flush(self) -> None:
        if self.message is not None:
            raise IntegrityError("STATEMENT", {}, Exception(self.message))

    async def rollback(self) -> None:
        self.rolled_back += 1


def test_the_preview_keys_a_pw_value_by_its_id() -> None:
    value = uuid.uuid4()
    other = {"log_source_id": None, "source_class": None, "waf_endpoint_id": None,
             "ips_entity_id": None}
    assert preview._subject_key(KIND, pw_value_id=value, **other) == str(value)
    assert preview._subject_key(KIND, pw_value_id=None, **other) is None


@pytest.mark.asyncio
async def test_the_preview_loads_nothing_without_a_key_or_a_coverage_floor() -> None:
    after = datetime(2026, 7, 15, 9, tzinfo=UTC)
    through = datetime(2026, 7, 15, 10, tzinfo=UTC)
    # the session is never touched: a call would fail on None
    assert await preview._load(None, KIND, None, after=after, through=through,  # type: ignore[arg-type]
                               pw_coverage_floor=0.9) == {}
    assert await preview._load(None, KIND, str(uuid.uuid4()), after=after,  # type: ignore[arg-type]
                               through=through, pw_coverage_floor=None) == {}


@pytest.mark.asyncio
async def test_a_pw_value_has_no_live_maintenance_source() -> None:
    assert await preview._maintenance_source(
        None, KIND, log_source_id=uuid.uuid4(), waf_endpoint_id=None, ips_entity_id=None  # type: ignore[arg-type]
    ) is None


def test_the_preview_reads_the_value_measurement_as_completeness() -> None:
    facts = BucketFacts(bucket_start=datetime(2026, 7, 15, 10, tzinfo=UTC), present=True,
                        dimension_measurement="LOWER_BOUND")
    assert preview._completeness(CustomRuleMetric.PW_VALUE_EVENTS, facts) == "LOWER_BOUND"
    absent = BucketFacts(bucket_start=facts.bucket_start, present=False)
    assert preview._completeness(CustomRuleMetric.PW_VALUE_EVENTS, absent) is None


@pytest.mark.asyncio
async def test_the_watch_flush_maps_only_the_rule_key(monkeypatch: pytest.MonkeyPatch) -> None:
    async def rules(_session: object, _watch: uuid.UUID, **_kw: object) -> list[svc.DependentRule]:
        return [svc.DependentRule(id=uuid.uuid4(), name="late-rule")]

    monkeypatch.setattr(svc, "dependent_rules", rules)
    session = _Session(f'violates foreign key constraint "{FK}"')
    with pytest.raises(HTTPException) as caught:
        await watch_routes._flush_refusing(session, uuid.uuid4(), "delete the watch")  # type: ignore[arg-type]
    assert caught.value.status_code == 409
    assert caught.value.detail["rules"][0]["name"] == "late-rule"  # type: ignore[index]
    assert session.rolled_back == 1  # the aborted transaction is left before the re-read

    with pytest.raises(HTTPException) as renamed:
        await watch_routes._flush_name(
            _Session(f'violates "{FK}"'), "n", watch_id=uuid.uuid4()  # type: ignore[arg-type]
        )
    assert renamed.value.status_code == 409
    assert "late-rule" in renamed.value.detail["message"]  # type: ignore[index]

    # anything else is not this task's to hide
    with pytest.raises(IntegrityError):
        await watch_routes._flush_refusing(_Session("uq_other"), uuid.uuid4(), "x")  # type: ignore[arg-type]
    with pytest.raises(IntegrityError):
        await watch_routes._flush_name(_Session("uq_other"), "n", watch_id=uuid.uuid4())  # type: ignore[arg-type]
    # the name constraint keeps its message
    with pytest.raises(HTTPException) as name:
        await watch_routes._flush_name(
            _Session("uq_property_watch_name"), "taken", watch_id=uuid.uuid4()  # type: ignore[arg-type]
        )
    assert "already exists" in name.value.detail  # type: ignore[operator]


@pytest.mark.asyncio
async def test_the_watch_flush_with_no_rule_left_still_refuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def none(_session: object, _watch: uuid.UUID, **_kw: object) -> list[svc.DependentRule]:
        return []

    monkeypatch.setattr(svc, "dependent_rules", none)
    with pytest.raises(HTTPException) as caught:
        await watch_routes._flush_refusing(_Session(FK), uuid.uuid4(), "delete the watch")  # type: ignore[arg-type]
    assert caught.value.status_code == 409
    assert caught.value.detail["rules"] == []  # type: ignore[index]
    assert "reload" in caught.value.detail["message"]  # type: ignore[index]


@pytest.mark.asyncio
async def test_the_rule_flush_maps_the_value_key_and_nothing_else() -> None:
    with pytest.raises(HTTPException) as caught:
        await rules_routes._flush_name(_Session(f'violates "{FK}"'), "rule")  # type: ignore[arg-type]
    assert caught.value.status_code == 409
    assert "no longer exists" in caught.value.detail and "reload" in caught.value.detail  # type: ignore[operator]
    with pytest.raises(HTTPException) as named:
        await rules_routes._flush_name(_Session("uq_custom_rule_name"), "rule")  # type: ignore[arg-type]
    assert "already exists" in named.value.detail  # type: ignore[operator]
    with pytest.raises(IntegrityError):
        await rules_routes._flush_name(_Session("uq_something_else"), "rule")  # type: ignore[arg-type]
    await rules_routes._flush_name(_Session(None), "rule")  # type: ignore[arg-type]
