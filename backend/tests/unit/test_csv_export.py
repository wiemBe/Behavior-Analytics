"""SOC-8a: the shared CSV rules and the two export column contracts.

The HTTP surfaces are exercised against Postgres in
`tests/integration/test_csv_export_api.py`; here the byte-level rules (RFC 4180,
the formula guard, `None` as an empty cell, the filename stamp) and the rows the
two exports build are pinned without a database.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import UTC, datetime

import pytest

from app.api.csv_export import (
    CSV_MEDIA_TYPE,
    _csv_field,
    _csv_time,
    csv_document,
    csv_enum,
    csv_export_response,
    csv_range_filename,
    csv_slug,
    csv_stamp,
)
from app.api.routes.alerts import ALERT_EXPORT_COLUMNS, _alert_export_row
from app.api.routes.anomalies import ANOMALY_EXPORT_COLUMNS, _anomaly_export_row
from app.models.alert import Alert
from app.models.enums import AlertStatus, AnomalyState, Severity

T0 = datetime(2026, 11, 5, 18, 0, tzinfo=UTC)


def test_a_field_is_empty_for_none_and_plain_for_a_number() -> None:
    assert _csv_field(None) == ""
    assert _csv_field(5) == "5"
    assert _csv_field(0.516666) == "0.517"
    assert _csv_field(True) == "true"
    assert _csv_field(False) == "false"


@pytest.mark.parametrize("prefix", ["=", "+", "-", "@", "\t", "\r"])
def test_every_formula_character_is_guarded(prefix: str) -> None:
    # A CR in the cell also makes RFC 4180 quote the field, so the guard is
    # checked on what a reader parses back, not on the raw bytes.
    cell = _csv_field(f"{prefix}cmd|calc")
    assert next(csv.reader(io.StringIO(cell, newline=""))) == ["'" + f"{prefix}cmd|calc"]


def test_a_comma_a_quote_or_a_break_is_quoted_rfc4180() -> None:
    assert _csv_field('a"b,c') == '"a""b,c"'
    assert _csv_field("a,b") == '"a,b"'
    assert _csv_field("line\rbreak") == '"line\rbreak"'
    assert _csv_field("plain/path") == "plain/path"


def test_times_are_iso_utc_with_z_and_none_stays_empty() -> None:
    assert _csv_time(T0) == "2026-11-05T18:00:00Z"
    assert _csv_time(None) is None


def test_an_enum_column_exports_the_token_it_is_stored_as() -> None:
    # A `String` column hands back the plain token; a member handed back by a
    # future column must still not serialize as `Severity.HIGH`.
    assert csv_enum("HIGH") == "HIGH"
    assert csv_enum(Severity.HIGH) == "HIGH"
    assert csv_enum(None) is None


def test_a_document_is_a_header_and_crlf_records() -> None:
    body = csv_document(("a", "b"), [(1, None), ("x,y", "z")])
    assert body == 'a,b\r\n1,\r\n"x,y",z\r\n'
    # An export that matched nothing is still a file with its columns.
    assert csv_document(("a", "b"), []) == "a,b\r\n"


def test_a_stamp_is_filename_safe_and_minute_resolution() -> None:
    assert csv_stamp(T0) == "20261105T1800Z"


def test_a_range_filename_names_both_ends_or_says_no_range() -> None:
    end = datetime(2026, 11, 6, 9, 30, tzinfo=UTC)
    assert csv_range_filename("alerts", T0, end) == "alerts-20261105T1800Z-20261106T0930Z.csv"
    # A half-open range is not a range: the file is not named after a window it
    # does not cover.
    assert csv_range_filename("alerts", None, None) == "alerts-no-range.csv"
    assert csv_range_filename("anomalies", None, end) == "anomalies-no-range.csv"


def test_a_slug_is_lowered_and_filename_safe() -> None:
    assert csv_slug("WAF-1 example.com") == "waf-1-example-com"
    assert csv_slug("---") == ""


def test_a_response_marks_truncation_only_when_the_export_was_cut() -> None:
    cut = csv_export_response("a\r\n", filename="alerts-no-range.csv", truncated=True)
    assert cut.status_code == 200
    assert cut.media_type == CSV_MEDIA_TYPE
    assert cut.headers["content-disposition"] == 'attachment; filename="alerts-no-range.csv"'
    assert cut.headers["x-export-truncated"] == "true"
    whole = csv_export_response("a\r\n", filename="alerts-no-range.csv", truncated=False)
    assert "x-export-truncated" not in whole.headers


def test_the_alert_columns_are_the_contract() -> None:
    assert ALERT_EXPORT_COLUMNS == (
        "id", "family", "source_type", "source_id", "title", "severity", "status",
        "opened_at", "acknowledged_at", "acknowledged_by", "resolved_at", "resolved_by",
        "occurrence_count", "fingerprint",
    )
    # The snapshot is raw evidence and the description is prose: neither is a list column.
    assert "evidence_snapshot" not in ALERT_EXPORT_COLUMNS
    assert "description" not in ALERT_EXPORT_COLUMNS


def _alert(source_type: str = "log_source", **kw: object) -> Alert:
    values: dict[str, object] = dict(
        dedup_key="dedup:1",
        fingerprint="fingerprint-1",
        title="spike on edge",
        severity=Severity.HIGH,
        status=AlertStatus.OPEN,
        source_type=source_type,
        source_id=None,
        context={},
        opened_at=T0,
        first_seen_at=T0,
        occurrence_count=3,
        evidence_snapshot={"raw": "never exported"},
        source_anomaly_ids=[],
    )
    values.update(kw)
    return Alert(**values)


def test_an_alert_row_maps_the_family_and_leaves_an_unknown_one_empty() -> None:
    known = _alert("waf_endpoint")
    family_at = ALERT_EXPORT_COLUMNS.index("family")
    assert _alert_export_row(known)[family_at] == "waf"
    # An unknown producer belongs to no family; the cell is empty, never forced.
    assert _alert_export_row(_alert("mystery_producer"))[family_at] is None


def test_an_alert_row_carries_the_clocks_and_an_empty_cell_for_a_missing_one() -> None:
    alert = _alert(id=uuid.UUID(int=5), acknowledged_by="alice")
    row = dict(zip(ALERT_EXPORT_COLUMNS, _alert_export_row(alert), strict=True))
    assert row["id"] == uuid.UUID(int=5)
    assert row["opened_at"] == "2026-11-05T18:00:00Z"
    assert row["acknowledged_at"] is None
    assert row["acknowledged_by"] == "alice"
    assert row["resolved_at"] is None
    assert row["occurrence_count"] == 3
    assert row["severity"] == "HIGH"
    assert row["status"] == "OPEN"


def test_the_anomaly_columns_are_the_contract() -> None:
    assert ANOMALY_EXPORT_COLUMNS == (
        "id", "family", "anomaly_type", "log_source_id", "log_source_name", "severity",
        "state", "detected_at", "opened_at", "resolved_at", "triage_status",
        "triage_owner", "triage_disposition",
    )
    # Reasoning and contributor evidence are not list columns.
    assert "explanation" not in ANOMALY_EXPORT_COLUMNS
    assert "contributors" not in ANOMALY_EXPORT_COLUMNS


def _anomaly_row(**kw: object) -> tuple[object, ...]:
    values: dict[str, object] = dict(
        id=uuid.UUID(int=7),
        subject_kind="LOG_SOURCE",
        anomaly_type="VOLUME_SPIKE",
        log_source_id=uuid.UUID(int=9),
        log_source_name="edge-1",
        severity="HIGH",
        state="OPEN",
        detected_at=T0,
        opened_at=T0,
        resolved_at=None,
        triage_status=None,
        triage_owner=None,
        triage_disposition=None,
    )
    values.update(kw)
    return _anomaly_export_row(values)


@pytest.mark.parametrize(
    ("subject_kind", "family"),
    [
        ("LOG_SOURCE", "log_source"),
        ("WAF_ENDPOINT", "waf"),
        ("IPS_ENTITY", "ips"),
        ("IPS_SOURCE", "ips"),
        ("PW_VALUE", "property_watch"),
        ("RULE", "rule"),
    ],
)
def test_an_anomaly_row_names_the_family_of_every_subject_kind(
    subject_kind: str, family: str
) -> None:
    row = dict(zip(ANOMALY_EXPORT_COLUMNS, _anomaly_row(subject_kind=subject_kind), strict=True))
    assert row["family"] == family


def test_an_anomaly_row_leaves_the_family_empty_for_an_unknown_subject_kind() -> None:
    # A subject kind the map does not carry belongs to no family. The cell is
    # empty, never forced and never raised: one unknown row must not cost the
    # whole export (the same rule the alert export applies to `source_type`).
    row = dict(
        zip(ANOMALY_EXPORT_COLUMNS, _anomaly_row(subject_kind="MYSTERY_SUBJECT"), strict=True)
    )
    assert row["family"] is None


def test_an_anomaly_row_reads_a_missing_triage_row_as_new_and_unowned() -> None:
    row = dict(zip(ANOMALY_EXPORT_COLUMNS, _anomaly_row(), strict=True))
    assert row["triage_status"] == "NEW"
    assert row["triage_owner"] is None
    assert row["triage_disposition"] is None
    assert row["state"] == AnomalyState.OPEN.value
    assert row["resolved_at"] is None
