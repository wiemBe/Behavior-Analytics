"""JDBC flow truth table, independent inventory/connection axes and safe API shapes."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.jdbc.flow import (
    FlowResult,
    LastObservation,
    connection_status,
    flow_state,
    inventory_freshness,
    observation_stale,
    target_bucket,
)
from app.jdbc.parameters import FIELDS
from app.models.enums import BucketCompleteness, SearchFamily, default_search_cadence
from app.schemas import jdbc

NOW = datetime(2026, 11, 20, 12, 2, tzinfo=UTC)
TARGET = (NOW - timedelta(minutes=7), NOW - timedelta(minutes=2))
LIVE = dict(row_present=True, completeness=BucketCompleteness.COMPLETE, origin="live", count=9)


def project(**overrides: Any) -> FlowResult:
    facts = dict(enabled=True, monitored=True, target=TARGET, **LIVE)
    facts.update(overrides)
    return flow_state(**facts)


@pytest.mark.parametrize(
    ("facts", "state", "measurement", "count"),
    [
        ({"enabled": False}, "DISABLED_IN_QRADAR", "NOT_COLLECTED", None),
        ({}, "RECEIVING", "EXACT", 9),
        ({"count": 0}, "NO_ATTRIBUTED_EVENTS", "EXACT", 0),
        ({"completeness": BucketCompleteness.PARTIAL}, "NOT_MEASURED", "PARTIAL", None),
        ({"fleet_empty": True, "count": 0}, "NOT_MEASURED", "PARTIAL", None),
        ({"row_present": False, "truncated_attempt": True}, "NOT_MEASURED", "TRUNCATED", None),
        ({"row_present": False}, "NOT_MEASURED", "NOT_COLLECTED", None),
        ({"completeness": BucketCompleteness.MISSING}, "NOT_MEASURED", "NOT_COLLECTED", None),
        ({"completeness": None}, "NOT_MEASURED", "NOT_COLLECTED", None),
        ({"origin": "history"}, "NOT_MEASURED", "NOT_COLLECTED", None),
        ({"origin": None}, "NOT_MEASURED", "NOT_COLLECTED", None),
        ({"origin": "unknown"}, "NOT_MEASURED", "NOT_COLLECTED", None),
        ({"monitored": False, "row_present": False}, "NOT_MEASURED", "NOT_COLLECTED", None),
        ({"monitored": False}, "RECEIVING", "EXACT", 9),
        ({"count": None}, "NOT_MEASURED", "NOT_COLLECTED", None),
        ({"count": -1}, "NOT_MEASURED", "NOT_COLLECTED", None),
        ({"count": True}, "NOT_MEASURED", "NOT_COLLECTED", None),
        ({"count": 2.5}, "NOT_MEASURED", "NOT_COLLECTED", None),
        ({"truncated_attempt": True}, "NOT_MEASURED", "TRUNCATED", None),
        ({"enabled": False, "truncated_attempt": True},
         "DISABLED_IN_QRADAR", "NOT_COLLECTED", None),
        ({"origin": "history", "truncated_attempt": True},
         "NOT_MEASURED", "NOT_COLLECTED", None),
        ({"origin": "history", "fleet_empty": True},
         "NOT_MEASURED", "NOT_COLLECTED", None),
    ],
)
def test_flow_table(facts: dict, state: str, measurement: str, count: int | None) -> None:
    result = project(**facts)
    assert (result.state, result.measurement, result.count) == (state, measurement, count)
    assert (result.target_start, result.target_end) == TARGET


def test_success_zero_not_receiving() -> None:
    assert connection_status("SUCCESS")[0] == "CONNECTED"
    assert project(count=0).state == "NO_ATTRIBUTED_EVENTS"
    assert project(count=0).count == 0


def test_warn_not_empty_table() -> None:
    assert connection_status("WARN") == ("WARNING", None)


def test_error_positive_both_visible() -> None:
    assert connection_status("ERROR") == ("CONNECTION_ERROR", None)
    assert project().state == "RECEIVING"


@pytest.mark.parametrize("raw", ["NA", None, "unknown", "success", "", "password=sentinel"])
def test_unknown_status_is_not_reported(raw: str | None) -> None:
    assert connection_status(raw, enabled=True) == (None, None)


@pytest.mark.parametrize("enabled", [False, True])
def test_disabled_status_is_independent_of_enabled(enabled: bool) -> None:
    assert connection_status("DISABLED", enabled=enabled) == (
        "DISABLED_STATUS", "enabled_with_disabled_status" if enabled else None
    )
    assert project(enabled=enabled).state == ("RECEIVING" if enabled else "DISABLED_IN_QRADAR")


def test_missing_target_no_old_positive_fallback() -> None:
    old = LastObservation(*(t - timedelta(days=1) for t in TARGET), NOW - timedelta(days=1), 999,
                          BucketCompleteness.COMPLETE, "live")
    result = project(row_present=False, last_observation=old)
    assert (result.state, result.measurement, result.count) == (
        "NOT_MEASURED", "NOT_COLLECTED", None
    )
    assert result.last_observation is old
    with pytest.raises(FrozenInstanceError):
        result.count = 999
    with pytest.raises(FrozenInstanceError):
        old.count = 1


@pytest.mark.parametrize("facts", [
    {"completeness": BucketCompleteness.PARTIAL}, {"truncated_attempt": True},
    {"origin": "history"}, {"fleet_empty": True},
])
def test_partial_truncated_history_not_exact(facts: dict) -> None:
    assert project(**facts).measurement != "EXACT"
    assert project(**facts).count is None


@pytest.mark.parametrize("state", ["COMPLETE", "PARTIAL", "FAILED", "NOT_COLLECTED"])
def test_stale_inventory_does_not_veto_receiving(state: Any) -> None:
    inventory = inventory_freshness(NOW - timedelta(days=30), state, NOW, cadence_seconds=900)
    assert inventory.stale
    assert inventory.last_attempt_state == state
    assert project().state == "RECEIVING"


def test_disabled_wins_over_stale_inventory() -> None:
    assert inventory_freshness(None, "FAILED", NOW, cadence_seconds=900).stale
    assert project(enabled=False).state == "DISABLED_IN_QRADAR"
    assert project(enabled=False).count is None


def test_unmonitored_no_row_is_not_collected() -> None:
    result = project(monitored=False, row_present=False)
    assert (result.state, result.measurement, result.count) == (
        "NOT_MEASURED", "NOT_COLLECTED", None
    )


@pytest.mark.parametrize(("seconds", "cadence", "width", "settle", "end_offset"), [
    (0, 300, 300, 0, 0), (-1, 300, 300, 0, -300),
    (0, 300, 300, 120, -300), (119, 300, 300, 120, -300),
    (120, 300, 300, 120, 0), (0, 900, 60, 0, 0),
    (-1, 900, 60, 0, -900), (120, 3600, 600, 120, 0),
])
def test_cadence_boundaries_custom_and_settle(
    seconds: int, cadence: int, width: int, settle: int, end_offset: int
) -> None:
    boundary = NOW - timedelta(minutes=2)
    end = boundary + timedelta(seconds=end_offset)
    assert target_bucket(boundary + timedelta(seconds=seconds), cadence_seconds=cadence,
                         width_seconds=width, anchor=settle) == (
        end - timedelta(seconds=width), end
    )


def test_default_volume_cadence_and_utc_normalization() -> None:
    local = NOW.astimezone(timezone(timedelta(hours=3)))
    cadence = int(default_search_cadence(SearchFamily.PHASE_A_VOLUME))
    assert target_bucket(local, cadence_seconds=cadence, width_seconds=300, anchor=120) == TARGET
    result = project(target=tuple(t.astimezone(local.tzinfo) for t in TARGET))
    assert result.target_start.tzinfo is UTC


@pytest.mark.parametrize("age,stale", [(899, False), (900, False), (901, True)])
def test_observation_stale_boundary(age: int, stale: bool) -> None:
    assert observation_stale(NOW - timedelta(seconds=age), NOW,
                             cadence_seconds=300, width_seconds=300) is stale
    assert observation_stale(None, NOW, cadence_seconds=300, width_seconds=300)


@pytest.mark.parametrize("cadence,width", [(900, 60), (300, 600)])
def test_observation_stale_custom(cadence: int, width: int) -> None:
    boundary = NOW - timedelta(seconds=2 * cadence + width)
    assert not observation_stale(boundary, NOW, cadence_seconds=cadence, width_seconds=width)
    assert observation_stale(boundary - timedelta(seconds=1), NOW,
                             cadence_seconds=cadence, width_seconds=width)


@pytest.mark.parametrize("age,stale", [(1799, False), (1800, False), (1801, True)])
def test_inventory_freshness_boundary(age: int, stale: bool) -> None:
    result = inventory_freshness(NOW - timedelta(seconds=age), "FAILED", NOW, cadence_seconds=900)
    assert result.stale is stale
    assert result.reason_code == ("inventory_stale" if stale else None)
    missing = inventory_freshness(None, "NOT_COLLECTED", NOW, cadence_seconds=900)
    assert missing.stale
    assert missing.reason_code == "inventory_not_collected"


@pytest.mark.parametrize("cadence,width,anchor", [(0, 300, 0), (300, 0, 0), (300, 300, -1)])
def test_invalid_target_timing(cadence: int, width: int, anchor: int) -> None:
    with pytest.raises(ValueError):
        target_bucket(NOW, cadence_seconds=cadence, width_seconds=width, anchor=anchor)


@pytest.mark.parametrize("cadence,width", [(0, 300), (300, 0)])
def test_invalid_stale_timing(cadence: int, width: int) -> None:
    with pytest.raises(ValueError):
        observation_stale(NOW, NOW, cadence_seconds=cadence, width_seconds=width)


def test_invalid_inventory_timing() -> None:
    with pytest.raises(ValueError):
        inventory_freshness(NOW, "COMPLETE", NOW, cadence_seconds=0)


def test_naive_clocks_are_rejected() -> None:
    naive = NOW.replace(tzinfo=None)
    with pytest.raises(ValueError, match="timezone-aware"):
        target_bucket(naive, cadence_seconds=300, width_seconds=300, anchor=0)
    with pytest.raises(ValueError, match="timezone-aware"):
        observation_stale(naive, NOW, cadence_seconds=300, width_seconds=300)
    with pytest.raises(ValueError, match="timezone-aware"):
        project(target=(naive, NOW))


def config_data(**changes: Any) -> dict:
    return dict(support="VERIFIED", state="NOT_COLLECTED", observed_at=None,
                last_success_at=None, stale=True, safe_fields=None, field_states={}) | changes


def source_data() -> dict:
    return dict(
        id=UUID(int=1), qradar_id=562, name="JDBC source", protocol_type_id=8,
        protocol_name="JDBC", config=config_data(),
        qradar=dict(enabled=True, status="CONNECTED", inventory_last_complete_at=NOW,
                    inventory_last_attempt_at=NOW, inventory_state="COMPLETE",
                    inventory_stale=False),
        volume=dict(measurement="NOT_COLLECTED", target_start=TARGET[0], target_end=TARGET[1],
                    bucket_seconds=300, event_count=None, average_eps=None, collected_at=None,
                    stale=True), flow=dict(state="NOT_MEASURED"), monitoring=dict(enabled=False),
    )


def test_schemas_forbid_extra_and_have_no_credential_fields() -> None:
    models = [obj for obj in vars(jdbc).values()
              if isinstance(obj, type) and issubclass(obj, jdbc.JdbcResponse)]
    denied = ("password", "username", "token", "secret", "connection_string", "key_file")
    for model in models:
        assert model.model_config["extra"] == "forbid"
        assert not any(word in name for name in model.model_fields for word in denied)
        with pytest.raises(ValidationError):
            model.model_validate({"password": "sentinel"})
    assert set(jdbc.JdbcSafeFields.model_fields) == {spec.member for spec in FIELDS}
    for model in (jdbc.JdbcSourceRow, jdbc.JdbcSourceDetail):
        parsed = model.model_validate(source_data())
        assert parsed.volume.event_count is None
        assert parsed.volume.target_end.tzinfo is UTC
        for block in (None, "qradar", "config", "volume", "flow", "monitoring"):
            data = source_data()
            (data if block is None else data[block])["password"] = "sentinel"
            with pytest.raises(ValidationError):
                model.model_validate(data)


@pytest.mark.parametrize("member,value", [
    ("database_host", "jdbc:postgres://user:password@db/secret"),
    ("table_name", "events; SELECT password"), ("select_list", "SELECT * FROM events"),
    ("source_label", "file:id.pem"), ("database_port", 65536),
    ("polling_interval_seconds", 0), ("database_name", "a" * 256),
    ("use_ssl", "true"), ("database_port", True),
])
def test_schemas_revalidate_safe_values(member: str, value: object) -> None:
    with pytest.raises(ValidationError):
        jdbc.JdbcSafeFields.model_validate({member: value})


def test_safe_schema_accepts_normalized_fields() -> None:
    values = {"database_type": "Postgres", "database_host": "db.example", "database_port": 5432,
              "tls_hostname": "db.example", "database_name": "audit", "schema_name": "public",
              "table_name": "events", "select_list": "id,event", "compare_field": "id",
              "polling_interval_seconds": 300, "start_time_local": "2026-11-20 12:00",
              "prepared_statements": False, "predefined_query": "none", "eps_throttle": 0,
              "source_label": "reader", "use_ssl": True, "allow_untrusted": False,
              "advanced_options": True}
    assert jdbc.JdbcSafeFields.model_validate(values).model_dump() == values


@pytest.mark.parametrize("changes", [
    {"field_states": {"password": {"code": "SET"}}},
    {"field_states": {"database_host": {"code": "UNKNOWN"}}},
    {"field_states": {"database_host": {"code": "SET", "reason": "raw warning"}}},
    {"failure_code": "password=sentinel"},
    {"support": "UNVERIFIED_PROTOCOL", "safe_fields": {"table_name": "events"}},
    {"support": "UNVERIFIED_PROTOCOL", "field_states": {"table_name": {"code": "SET"}}},
])
def test_schema_rejects_unknown_state_fields_and_unverified_parameters(changes: dict) -> None:
    with pytest.raises(ValidationError):
        jdbc.JdbcConfigOut.model_validate(config_data(**changes))


def test_schema_removed_state_and_unverified_null() -> None:
    config = jdbc.JdbcConfigOut.model_validate(config_data(
        field_states={"table_name": {"code": "REMOVED", "reason": None}}))
    assert config.field_states["table_name"].code == "REMOVED"
    assert jdbc.JdbcConfigOut.model_validate(config_data(
        support="UNVERIFIED_PROTOCOL", state="UNVERIFIED_PROTOCOL")).safe_fields is None


def test_schema_utc_and_nullable_counts() -> None:
    data = source_data()["volume"]
    data["target_start"] = TARGET[0].astimezone(timezone(timedelta(hours=3)))
    assert jdbc.JdbcVolumeOut.model_validate(data).target_start.tzinfo is UTC
    for invalid in (TARGET[0].replace(tzinfo=None),):
        with pytest.raises(ValidationError):
            jdbc.JdbcVolumeOut.model_validate(data | {"target_start": invalid})
    for invalid_count in (-1, True, 1.5):
        with pytest.raises(ValidationError):
            jdbc.JdbcVolumeOut.model_validate(data | {"event_count": invalid_count})
    for measurement, count in (("EXACT", 0), ("PARTIAL", None), ("TRUNCATED", None)):
        parsed = jdbc.JdbcVolumeOut.model_validate(data | {
            "measurement": measurement, "event_count": count})
        assert parsed.event_count == count
