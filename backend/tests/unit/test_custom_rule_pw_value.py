"""The PW_VALUE subject kind (design §G.10.2): catalog, readings, engine guard, pairing."""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.core.config import Settings, get_settings
from app.custom_rules import engine as engine_module
from app.custom_rules.catalog import MeasurementRule, catalog_entry, metrics_for_kind
from app.custom_rules.engine import CustomRuleEngine, _Loaded
from app.custom_rules.evaluate import FoldInput, fold_rule, interpret
from app.custom_rules.loaders import PwValueBucket, implemented_loaders, pw_value_facts
from app.custom_rules.validation import _SUBJECT_COLUMNS, CustomRuleDraft, validate_rule_draft
from app.models.custom_rule import CustomRule
from app.models.enums import CustomRuleMetric, CustomRuleSubjectKind
from app.models.property_watch import PropertyWatch, PwValue

T0 = datetime(2026, 7, 15, 10, 0, tzinfo=UTC)
FLOOR = 0.9


def _bucket(**overrides: object) -> PwValueBucket:
    base = PwValueBucket(
        bucket_start=T0,
        in_maintenance=False,
        values_measurement="EXACT",
        pinned_measurement="EXACT",
        totals_complete=True,
        total_events=100,
        parsed_events=100,
        events=None,
        value_pinned=True,
        tracked_since=T0 - timedelta(hours=1),
        tracked_until=None,
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


# ------------------------------------------------------------------------------ catalog
def test_the_catalog_entry_is_the_ips_events_shape_for_a_pinned_value() -> None:
    entry = catalog_entry(CustomRuleMetric.PW_VALUE_EVENTS)
    assert entry.subject_kind is CustomRuleSubjectKind.PW_VALUE
    assert entry.unit == "events/bucket"
    assert entry.reads == ("pw_value_metric.events",)
    assert entry.measurement is MeasurementRule.LOWER_BOUND_PROVES_INCREASE
    assert entry.is_ratio is False
    assert entry.bulk_loader == "PW_VALUE"
    assert "proves 'above' only" in entry.note
    assert metrics_for_kind(CustomRuleSubjectKind.PW_VALUE) == [CustomRuleMetric.PW_VALUE_EVENTS]
    assert "PW_VALUE" in implemented_loaders()


# --------------------------------------------------------------------- the reading rule
@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        # A row is the value's count under the bucket's measurement.
        ({"events": 7}, (7, "EXACT")),
        ({"events": 7, "pinned_measurement": "LOWER_BOUND"}, (7, "EXACT")),
        ({"events": 7, "pinned_measurement": "LOWER_BOUND", "values_measurement": "LOWER_BOUND"},
         (7, "LOWER_BOUND")),
        ({"events": 7, "value_pinned": False, "values_measurement": "LOWER_BOUND"},
         (7, "LOWER_BOUND")),
        ({"events": 7, "value_pinned": False}, (7, "EXACT")),
        # The only measured zero: pinned, EXACT pinned search, tracked, covered.
        ({}, (0, "EXACT")),
        ({"total_events": 0, "parsed_events": 0}, (0, "EXACT")),
        ({"tracked_until": T0}, (0, "EXACT")),
        ({"parsed_events": 90}, (0, "EXACT")),
    ],
)
def test_a_reading_and_the_one_measured_zero(
    changes: dict[str, object], expected: tuple[int, str]
) -> None:
    facts = pw_value_facts(_bucket(**changes), coverage_floor=FLOOR)
    assert facts is not None
    assert (facts.events, facts.dimension_measurement) == expected


@pytest.mark.parametrize(
    "changes",
    [
        {"pinned_measurement": "LOWER_BOUND"},
        {"pinned_measurement": "NOT_COLLECTED"},
        {"pinned_measurement": "NOT_REQUESTED"},
        {"value_pinned": False},
        {"value_pinned": False, "values_measurement": "EXACT"},
        {"tracked_since": None},
        {"tracked_since": T0 + timedelta(minutes=5)},
        {"tracked_until": T0 - timedelta(minutes=5)},
        {"totals_complete": False, "total_events": None, "parsed_events": None},
        {"total_events": None},
        {"parsed_events": None},
        {"parsed_events": 89},
        {"parsed_events": 0},
        # A count inside a bucket that measured nothing contradicts itself.
        {"events": 7, "value_pinned": False, "values_measurement": "NOT_COLLECTED"},
    ],
)
def test_everything_else_without_a_row_is_unmeasured_never_zero(
    changes: dict[str, object],
) -> None:
    assert pw_value_facts(_bucket(**changes), coverage_floor=FLOOR) is None


def test_a_maintenance_bucket_carries_the_flag() -> None:
    facts = pw_value_facts(_bucket(events=3, in_maintenance=True), coverage_floor=FLOOR)
    assert facts is not None and facts.in_maintenance is True


def _fold(readings: tuple[object, ...], **overrides: object):  # type: ignore[no-untyped-def]
    spec = FoldInput(
        rule_id=uuid.uuid4(),
        rule_name="deny spike",
        severity="HIGH",
        metric=CustomRuleMetric.PW_VALUE_EVENTS,
        operator="GT",
        threshold_value=100.0,
        sustain_buckets=1,
        recovery_buckets=2,
        consecutive_breaching=0,
        consecutive_under=0,
        last_evaluated_bucket_start=None,
        previous_status=None,
        previous_reason=None,
        subject_kind="PW_VALUE",
        subject_label="w: DENY",
        platform_enabled=True,
        enabled=True,
        retired=False,
        retired_reason=None,
        family_collected=True,
        family_reason=None,
        breach=None,
        readings=readings,  # type: ignore[arg-type]
        now=T0 + timedelta(hours=1),
    )
    return fold_rule(replace(spec, **overrides))  # type: ignore[arg-type]


def _reading(minute: int, **changes: object):  # type: ignore[no-untyped-def]
    row = _bucket(bucket_start=T0 + timedelta(minutes=minute), **changes)
    facts = pw_value_facts(row, coverage_floor=FLOOR)
    assert facts is not None
    return interpret(CustomRuleMetric.PW_VALUE_EVENTS, facts)


def test_a_lower_bound_above_gt_opens_and_a_later_lower_bound_never_resolves() -> None:
    lower = {"values_measurement": "LOWER_BOUND", "pinned_measurement": "LOWER_BOUND"}
    opened = _fold((_reading(0, events=150, **lower),))
    assert opened.breach is not None and opened.breach.state == "OPEN"
    assert opened.breach.worst_measurement == "LOWER_BOUND"
    held = _fold(
        (_reading(5, events=1, **lower),),
        breach=opened.breach,
        consecutive_breaching=opened.consecutive_breaching,
        last_evaluated_bucket_start=opened.last_evaluated_bucket_start,
    )
    assert held.breach is not None and held.breach.state == "OPEN"
    assert held.breach.resolved_at is None and held.consecutive_under == 0


def test_a_lower_bound_with_lt_is_insufficient_and_never_opens() -> None:
    lower = {"values_measurement": "LOWER_BOUND", "pinned_measurement": "LOWER_BOUND"}
    result = _fold((_reading(0, events=1, **lower),), operator="LT", threshold_value=100.0)
    assert result.breach is None
    assert result.status == "NO_VERDICT"
    assert result.consecutive_breaching == 0 and result.consecutive_under == 0


def test_a_measured_zero_makes_an_lt_rule_usable_and_resolves_a_gt_breach() -> None:
    silent = _fold((_reading(0),), operator="LT", threshold_value=5.0)
    assert silent.breach is not None and silent.breach.worst_value == 0.0
    opened = _fold((_reading(0, events=500),))
    recovered = _fold(
        (_reading(5), _reading(10)),
        breach=opened.breach,
        consecutive_breaching=opened.consecutive_breaching,
        last_evaluated_bucket_start=opened.last_evaluated_bucket_start,
    )
    assert recovered.resolved is not None and recovered.resolved.state == "RESOLVED"


# ----------------------------------------------------------------------- the engine guard
def _settings(**overrides: object) -> Settings:
    values = get_settings().model_dump()
    values.update(overrides)
    return Settings(**values)


def _item(kind: str) -> _Loaded:
    rule = CustomRule(
        name="r",
        subject_kind=kind,
        metric="x",
        operator="GT",
        threshold_value=1.0,
        sustain_buckets=1,
        recovery_buckets=1,
        enabled=True,
        log_source_id=uuid.uuid4(),
        source_class="LINUX",
        waf_endpoint_id=uuid.uuid4(),
        ips_entity_id=uuid.uuid4(),
        pw_value_id=uuid.uuid4(),
    )
    source = SimpleNamespace(name="edge-fw", maintenance_mode=False, maintenance_until=None)
    endpoint = SimpleNamespace(
        display_name="GET /x", is_active=True, retired_at=None, log_source_id=uuid.uuid4()
    )
    entity = SimpleNamespace(
        display_name=None, value_key="443", dimension="DESTINATION_PORT", active=True,
        retired_at=None,
    )
    value = PwValue(
        watch_id=uuid.uuid4(), watch_version=1, value_key="DENY", pinned=True, active=True
    )
    watch = PropertyWatch(name="waf-action")
    return _Loaded(
        rule=rule,
        breach_row=None,
        endpoint=endpoint,  # type: ignore[arg-type]
        entity=entity,  # type: ignore[arg-type]
        source=source,  # type: ignore[arg-type]
        pw_value=value,
        pw_watch=watch,
    )


class _NoSql:
    """A session that fails the test if a loader sends a statement."""

    async def execute(self, *_args: object, **_kwargs: object) -> object:
        raise AssertionError("an empty subject list sent a statement")


def _unsupported(kind: str) -> list[str]:
    """What the engine lacks for `kind`: empty when it has all four branches and a loader."""
    missing: list[str] = []
    settings = _settings()
    item = _item(kind)
    if kind not in engine_module._KIND_FAMILY:
        missing.append("cadence family")
    if kind not in implemented_loaders():
        missing.append("loader")
    if kind not in {k.value for k in _SUBJECT_COLUMNS}:
        missing.append("subject column")
    _ok, reason = engine_module._family_collected(kind, settings)
    if (reason or "").startswith("no collection family"):
        missing.append("family branch")
    retired, retired_reason = engine_module._retired(item)
    if retired and (retired_reason or "").startswith("no retirement rule"):
        missing.append("retirement branch")
    try:
        engine_module._label(item)
    except RuntimeError:
        missing.append("label")
    if engine_module._subject_key(item.rule) is None:
        missing.append("subject key")
    return missing


@pytest.mark.parametrize("kind", [kind.value for kind in CustomRuleSubjectKind])
def test_every_subject_kind_has_a_loader_a_family_a_retirement_branch_and_a_label(
    kind: str,
) -> None:
    assert _unsupported(kind) == []


def test_an_unknown_subject_kind_fails_the_guard_and_is_never_judged() -> None:
    missing = _unsupported("NOT_A_KIND")
    assert {"cadence family", "loader", "family branch", "retirement branch", "label"} <= set(
        missing
    )
    assert engine_module._family_collected("NOT_A_KIND", _settings())[0] is False
    assert engine_module._retired(_item("NOT_A_KIND"))[0] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [kind.value for kind in CustomRuleSubjectKind])
async def test_every_kind_has_a_dispatching_loader_and_unknown_has_none(kind: str) -> None:
    engine = CustomRuleEngine(_NoSql(), _settings())  # type: ignore[arg-type]
    assert await engine._load_metrics(kind, [], after=T0, through=T0) == {}
    with pytest.raises(RuntimeError, match="no bulk loader"):
        await engine._load_metrics("NOT_A_KIND", [], after=T0, through=T0)


def test_the_pw_family_gate_is_off_with_the_collection_switch() -> None:
    off = engine_module._family_collected("PW_VALUE", _settings(pw_collection_enabled=False))
    assert off == (False, "pw_collection_enabled is off")
    on = engine_module._family_collected("PW_VALUE", _settings(pw_collection_enabled=True))
    assert on == (True, None)


def test_retirement_label_and_key_for_a_pinned_value() -> None:
    item = _item("PW_VALUE")
    assert engine_module._retired(item) == (False, None)
    assert engine_module._label(item) == "waf-action: DENY"
    assert engine_module._subject_key(item.rule) == str(item.rule.pw_value_id)
    item.pw_value.pinned = False  # type: ignore[union-attr]
    assert engine_module._retired(item) == (True, "the value is no longer pinned")
    item.pw_value.pinned = True  # type: ignore[union-attr]
    item.pw_value.active = False  # type: ignore[union-attr]
    assert engine_module._retired(item) == (True, "the value is retired")
    item.pw_value = None
    assert engine_module._retired(item) == (True, "the subject no longer exists")


# ------------------------------------------------------------------------------ pairing
def _draft(**overrides: object) -> CustomRuleDraft:
    values: dict[str, object] = {
        "subject_kind": CustomRuleSubjectKind.PW_VALUE,
        "metric": "pw_value_events",
        "operator": "GT",
        "threshold_value": 500.0,
        "sustain_buckets": 3,
        "recovery_buckets": 2,
        "pw_value_id": uuid.uuid4(),
    }
    values.update(overrides)
    return CustomRuleDraft(**values)  # type: ignore[arg-type]


def test_a_pw_value_rule_pairs_with_pw_value_id_and_its_metric_only() -> None:
    assert validate_rule_draft(_draft()) == []
    assert any("pw_value_id" in r for r in validate_rule_draft(_draft(pw_value_id=None)))
    assert any(
        "log_source_id must stay empty" in r
        for r in validate_rule_draft(_draft(log_source_id=uuid.uuid4()))
    )
    assert any(
        "pw_value_id must stay empty" in r
        for r in validate_rule_draft(
            _draft(subject_kind=CustomRuleSubjectKind.LOG_SOURCE, metric="event_count",
                   log_source_id=uuid.uuid4())
        )
    )
    assert any("measures a" in r for r in validate_rule_draft(_draft(metric="events")))
