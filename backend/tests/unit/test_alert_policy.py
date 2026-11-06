"""The pure SOC-17c alert-policy resolver (design §4.3)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.alerts.policy import resolve
from app.models.alert_policy import DIGEST_ERROR_CODES
from app.models.enums import AlertPolicyMode, AlertPolicyScope, AnomalyFamily
from app.services.email_delivery import ERROR_CODES


@dataclass(frozen=True)
class Row:
    instance_id: uuid.UUID | None
    scope_kind: AlertPolicyScope
    scope_value: str
    family: AnomalyFamily
    detector_type: str | None
    mode: AlertPolicyMode


INSTANCE = uuid.UUID("11111111-1111-1111-1111-111111111111")
OTHER_INSTANCE = uuid.UUID("22222222-2222-2222-2222-222222222222")
SOURCE = uuid.UUID("33333333-3333-3333-3333-333333333333")


def _row(
    *,
    scope: AlertPolicyScope,
    value: str,
    family: AnomalyFamily = AnomalyFamily.LOG_SOURCE,
    detector: str | None = None,
    mode: AlertPolicyMode = AlertPolicyMode.RECORD_ONLY,
    instance_id: uuid.UUID | None = None,
) -> Row:
    return Row(instance_id, scope, value, family, detector, mode)


def _resolve(rows: list[Row], **overrides: object):  # type: ignore[no-untyped-def]
    values: dict[str, object] = {
        "instance_id": INSTANCE,
        "family": AnomalyFamily.LOG_SOURCE,
        "source_id": SOURCE,
        "source_class": "FIREWALL",
        "detector": "VOLUME_SPIKE",
        "default": AlertPolicyMode.ALERT,
    }
    values.update(overrides)
    return resolve(rows, **values)  # type: ignore[arg-type]


def test_no_rows_is_the_configured_default() -> None:
    verdict = _resolve([])
    assert verdict.mode is AlertPolicyMode.ALERT
    assert verdict.basis == "DEFAULT"


def test_source_detector_beats_source_all_beats_class_beats_default() -> None:
    source_detector = _row(
        scope=AlertPolicyScope.SOURCE, value=str(SOURCE), detector="VOLUME_SPIKE",
        mode=AlertPolicyMode.DIGEST,
    )
    source_all = _row(
        scope=AlertPolicyScope.SOURCE, value=str(SOURCE), mode=AlertPolicyMode.RECORD_ONLY
    )
    class_detector = _row(
        scope=AlertPolicyScope.CLASS, value="FIREWALL", detector="VOLUME_SPIKE",
        mode=AlertPolicyMode.ALERT,
    )
    class_all = _row(
        scope=AlertPolicyScope.CLASS, value="FIREWALL", mode=AlertPolicyMode.DIGEST
    )
    # All four rows apply; the source's detector-specific row wins.
    verdict = _resolve([class_all, class_detector, source_all, source_detector])
    assert verdict.mode is AlertPolicyMode.DIGEST and verdict.basis == "SOURCE_DETECTOR"
    # Without it, the source-wide row is next, ahead of the class's detector row.
    verdict = _resolve([class_all, class_detector, source_all])
    assert verdict.mode is AlertPolicyMode.RECORD_ONLY and verdict.basis == "SOURCE"
    # A class-only resolution skips the source steps.
    verdict = _resolve([class_all, class_detector], source_id=None)
    assert verdict.mode is AlertPolicyMode.ALERT and verdict.basis == "CLASS_DETECTOR"
    verdict = _resolve(
        [class_all, class_detector], source_id=None, detector="VOLUME_DROP"
    )
    assert verdict.mode is AlertPolicyMode.DIGEST and verdict.basis == "CLASS"
    # An unclassified source has no class step at all.
    assert _resolve([class_all], source_class=None).mode is AlertPolicyMode.ALERT


def test_a_detector_specific_row_is_more_specific_than_the_all_row() -> None:
    rows = [
        _row(
            scope=AlertPolicyScope.SOURCE, value=str(SOURCE), mode=AlertPolicyMode.DIGEST
        ),
        _row(
            scope=AlertPolicyScope.SOURCE, value=str(SOURCE), detector="VOLUME_SPIKE",
            mode=AlertPolicyMode.ALERT,
        ),
    ]
    assert _resolve(rows).mode is AlertPolicyMode.ALERT
    assert _resolve(rows, detector="NO_EVENTS").mode is AlertPolicyMode.DIGEST


def test_an_instance_row_beats_a_global_row_at_the_same_specificity() -> None:
    global_row = _row(
        scope=AlertPolicyScope.SOURCE, value=str(SOURCE), detector="VOLUME_SPIKE",
        mode=AlertPolicyMode.RECORD_ONLY,
    )
    instance_row = _row(
        scope=AlertPolicyScope.SOURCE, value=str(SOURCE), detector="VOLUME_SPIKE",
        mode=AlertPolicyMode.DIGEST, instance_id=INSTANCE,
    )
    verdict = _resolve([global_row, instance_row])
    assert verdict.mode is AlertPolicyMode.DIGEST and verdict.basis == "SOURCE_DETECTOR"
    # Another console's row never applies; the global one answers.
    assert _resolve(
        [instance_row], instance_id=OTHER_INSTANCE
    ).mode is AlertPolicyMode.ALERT


def test_property_watch_skips_class_and_uses_the_watch_as_its_source() -> None:
    class_row = _row(
        scope=AlertPolicyScope.CLASS, value="FIREWALL", family=AnomalyFamily.PROPERTY_WATCH,
        mode=AlertPolicyMode.DIGEST,
    )
    assert _resolve(
        [class_row], family=AnomalyFamily.PROPERTY_WATCH, source_class="FIREWALL"
    ).mode is AlertPolicyMode.ALERT
    watch_row = _row(
        scope=AlertPolicyScope.SOURCE, value=str(SOURCE),
        family=AnomalyFamily.PROPERTY_WATCH, detector="PW_VALUE_SPIKE",
        mode=AlertPolicyMode.DIGEST,
    )
    assert _resolve(
        [watch_row], family=AnomalyFamily.PROPERTY_WATCH, source_class=None,
        detector="PW_VALUE_SPIKE",
    ).mode is AlertPolicyMode.DIGEST


def test_a_row_of_another_family_or_source_never_matches() -> None:
    rows = [
        _row(
            scope=AlertPolicyScope.SOURCE, value=str(SOURCE), family=AnomalyFamily.WAF,
            detector="VOLUME_SPIKE", mode=AlertPolicyMode.DIGEST,
        ),
        _row(
            scope=AlertPolicyScope.SOURCE, value=str(uuid.uuid4()),
            detector="VOLUME_SPIKE", mode=AlertPolicyMode.DIGEST,
        ),
    ]
    assert _resolve(rows).mode is AlertPolicyMode.ALERT


def test_every_mode_round_trips_through_the_resolver() -> None:
    for mode in AlertPolicyMode:
        row = _row(
            scope=AlertPolicyScope.SOURCE, value=str(SOURCE), detector="VOLUME_SPIKE",
            mode=mode,
        )
        assert _resolve([row]).mode is mode


def test_digest_error_codes_mirror_the_delivery_codes() -> None:
    """The model cannot import the service (circular), so the copy is pinned."""
    assert DIGEST_ERROR_CODES == ERROR_CODES
