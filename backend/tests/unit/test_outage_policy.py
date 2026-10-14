"""Pure resolution of the outage alert policy: precedence, scoping, fall-through.

`resolve` is the single answer to "does this source's NO_EVENTS page a human, and
after how many empty buckets?". These tests pin every branch: SOURCE beats CLASS
beats default, an instance row beats a global one, a row for another instance
never applies, and a NULL grace means "no override".
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import pytest

from app.fleet.outage_policy import resolve
from app.models.enums import OutagePolicyMode, OutagePolicyScope

DEFAULT = OutagePolicyMode.ALERT
INSTANCE = uuid.UUID("11111111-1111-1111-1111-111111111111")
OTHER_INSTANCE = uuid.UUID("22222222-2222-2222-2222-222222222222")
SOURCE = uuid.UUID("33333333-3333-3333-3333-333333333333")
OTHER_SOURCE = uuid.UUID("44444444-4444-4444-4444-444444444444")


@dataclass
class Row:
    """A policy row stand-in: any object with these attributes qualifies."""

    scope_kind: OutagePolicyScope
    scope_value: str
    mode: OutagePolicyMode
    instance_id: uuid.UUID | None = None
    silence_grace_buckets: int | None = None


def _resolve(
    rows: list[Row],
    *,
    log_source_id: uuid.UUID | None = SOURCE,
    source_class: str | None = "FIREWALL",
    instance_id: uuid.UUID | None = INSTANCE,
    default: OutagePolicyMode = DEFAULT,
):  # type: ignore[no-untyped-def]
    return resolve(
        rows,
        log_source_id=log_source_id,
        source_class=source_class,
        instance_id=instance_id,
        default=default,
    )


def test_no_rows_yields_the_default() -> None:
    resolved = _resolve([])
    assert resolved.mode is DEFAULT
    assert resolved.silence_grace_buckets is None
    assert resolved.basis == "DEFAULT"


def test_class_row_applies_when_no_source_row_exists() -> None:
    rows = [
        Row(OutagePolicyScope.CLASS, "FIREWALL", OutagePolicyMode.RECORD_ONLY,
            silence_grace_buckets=3)
    ]
    resolved = _resolve(rows)
    assert resolved.mode is OutagePolicyMode.RECORD_ONLY
    assert resolved.silence_grace_buckets == 3
    assert resolved.basis == "CLASS"


def test_class_row_for_another_class_does_not_apply() -> None:
    rows = [Row(OutagePolicyScope.CLASS, "DNS", OutagePolicyMode.RECORD_ONLY)]
    assert _resolve(rows).basis == "DEFAULT"


def test_source_row_beats_a_class_row() -> None:
    rows = [
        Row(OutagePolicyScope.CLASS, "FIREWALL", OutagePolicyMode.RECORD_ONLY,
            silence_grace_buckets=48),
        Row(OutagePolicyScope.SOURCE, str(SOURCE), OutagePolicyMode.ALERT,
            silence_grace_buckets=0),
    ]
    resolved = _resolve(rows)
    assert resolved.mode is OutagePolicyMode.ALERT
    assert resolved.silence_grace_buckets == 0
    assert resolved.basis == "SOURCE"


def test_source_row_for_another_source_is_ignored() -> None:
    rows = [
        Row(OutagePolicyScope.SOURCE, str(OTHER_SOURCE), OutagePolicyMode.RECORD_ONLY),
        Row(OutagePolicyScope.CLASS, "FIREWALL", OutagePolicyMode.ALERT),
    ]
    resolved = _resolve(rows)
    assert resolved.basis == "CLASS" and resolved.mode is OutagePolicyMode.ALERT


def test_instance_row_beats_a_global_row_at_class_scope() -> None:
    rows = [
        Row(OutagePolicyScope.CLASS, "FIREWALL", OutagePolicyMode.ALERT, instance_id=None),
        Row(
            OutagePolicyScope.CLASS,
            "FIREWALL",
            OutagePolicyMode.RECORD_ONLY,
            instance_id=INSTANCE,
        ),
    ]
    assert _resolve(rows).mode is OutagePolicyMode.RECORD_ONLY


def test_instance_row_beats_a_global_row_at_source_scope() -> None:
    rows = [
        Row(OutagePolicyScope.SOURCE, str(SOURCE), OutagePolicyMode.ALERT, instance_id=None),
        Row(
            OutagePolicyScope.SOURCE,
            str(SOURCE),
            OutagePolicyMode.RECORD_ONLY,
            instance_id=INSTANCE,
        ),
    ]
    assert _resolve(rows).mode is OutagePolicyMode.RECORD_ONLY


def test_a_row_for_another_instance_never_applies() -> None:
    rows = [
        Row(
            OutagePolicyScope.CLASS,
            "FIREWALL",
            OutagePolicyMode.RECORD_ONLY,
            instance_id=OTHER_INSTANCE,
        )
    ]
    assert _resolve(rows).basis == "DEFAULT"


def test_global_row_applies_to_every_instance() -> None:
    rows = [Row(OutagePolicyScope.CLASS, "FIREWALL", OutagePolicyMode.RECORD_ONLY)]
    assert _resolve(rows, instance_id=OTHER_INSTANCE).basis == "CLASS"


def test_instance_row_beats_a_global_source_row() -> None:
    # The instance source row beats the global source row, and both beat the class.
    rows = [
        Row(OutagePolicyScope.CLASS, "FIREWALL", OutagePolicyMode.ALERT),
        Row(OutagePolicyScope.SOURCE, str(SOURCE), OutagePolicyMode.ALERT, instance_id=None),
        Row(
            OutagePolicyScope.SOURCE,
            str(SOURCE),
            OutagePolicyMode.RECORD_ONLY,
            instance_id=INSTANCE,
            silence_grace_buckets=5,
        ),
    ]
    resolved = _resolve(rows)
    assert resolved.basis == "SOURCE" and resolved.mode is OutagePolicyMode.RECORD_ONLY
    assert resolved.silence_grace_buckets == 5


def test_summary_resolution_ignores_instance_and_source_rows() -> None:
    # The fleet summary asks "what does this class do?", so it passes no source
    # id and no instance: only a global CLASS row can apply.
    rows = [
        Row(OutagePolicyScope.SOURCE, str(SOURCE), OutagePolicyMode.RECORD_ONLY),
        Row(
            OutagePolicyScope.CLASS,
            "FIREWALL",
            OutagePolicyMode.ALERT,
            instance_id=INSTANCE,
        ),
        Row(OutagePolicyScope.CLASS, "FIREWALL", OutagePolicyMode.RECORD_ONLY, instance_id=None),
    ]
    resolved = _resolve(rows, log_source_id=None, instance_id=None, default=DEFAULT)
    assert resolved.basis == "CLASS" and resolved.mode is OutagePolicyMode.RECORD_ONLY


def test_summary_resolution_falls_through_to_the_default() -> None:
    rows = [
        Row(OutagePolicyScope.CLASS, "FIREWALL", OutagePolicyMode.RECORD_ONLY,
            instance_id=INSTANCE)
    ]
    resolved = _resolve(rows, log_source_id=None, instance_id=None)
    assert resolved.basis == "DEFAULT"


def test_null_grace_falls_through_while_a_mode_still_applies() -> None:
    rows = [
        Row(OutagePolicyScope.CLASS, "FIREWALL", OutagePolicyMode.RECORD_ONLY,
            silence_grace_buckets=None)
    ]
    resolved = _resolve(rows)
    assert resolved.mode is OutagePolicyMode.RECORD_ONLY
    assert resolved.silence_grace_buckets is None


def test_grace_zero_is_a_value_not_an_absence() -> None:
    # The engine checks `is not None`; a 0 must survive as an override.
    rows = [
        Row(OutagePolicyScope.SOURCE, str(SOURCE), OutagePolicyMode.ALERT,
            silence_grace_buckets=0)
    ]
    assert _resolve(rows).silence_grace_buckets == 0


def test_no_source_and_no_class_falls_to_the_default() -> None:
    rows = [
        Row(OutagePolicyScope.SOURCE, str(SOURCE), OutagePolicyMode.RECORD_ONLY),
        Row(OutagePolicyScope.CLASS, "FIREWALL", OutagePolicyMode.RECORD_ONLY),
    ]
    resolved = _resolve(rows, log_source_id=None, source_class=None)
    assert resolved.basis == "DEFAULT"


def test_default_is_respected_when_supplied_as_record_only() -> None:
    resolved = _resolve([], default=OutagePolicyMode.RECORD_ONLY)
    assert resolved.mode is OutagePolicyMode.RECORD_ONLY


@pytest.mark.parametrize("grace", [0, 1, 48])
def test_resolved_grace_is_the_row_value(grace: int) -> None:
    rows = [
        Row(OutagePolicyScope.CLASS, "FIREWALL", OutagePolicyMode.ALERT,
            silence_grace_buckets=grace)
    ]
    assert _resolve(rows).silence_grace_buckets == grace
