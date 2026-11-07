"""SOC-17d's pure suppression resolver and break-through table."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.alerts.suppression import (
    BREAKTHROUGH_RULES,
    active_suppression,
    breaks_through,
)
from app.core.config import Settings
from app.models.enums import (
    AnomalyFamily,
    AnomalyType,
    Criticality,
    IpsAnomalyType,
    PwAnomalyType,
    WafAnomalyType,
)

AT = datetime(2026, 11, 7, 12, tzinfo=UTC)
SUBJECT = uuid.UUID("11111111-1111-1111-1111-111111111111")
FAMILY_ENUMS = {
    AnomalyFamily.LOG_SOURCE: AnomalyType,
    AnomalyFamily.WAF: WafAnomalyType,
    AnomalyFamily.IPS: IpsAnomalyType,
    AnomalyFamily.PROPERTY_WATCH: PwAnomalyType,
}


def _row(
    *,
    detector: str | None = AnomalyType.VOLUME_SPIKE.value,
    family: AnomalyFamily = AnomalyFamily.LOG_SOURCE,
    subject: uuid.UUID = SUBJECT,
    starts_at: datetime = AT - timedelta(hours=1),
    ends_at: datetime = AT + timedelta(hours=1),
    ended_at: datetime | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        family=family,
        subject_id=subject,
        detector_type=detector,
        starts_at=starts_at,
        ends_at=ends_at,
        ended_at=ended_at,
    )


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "encryption_key": "x" * 44,
        "anomaly_min_bucket_events": 20,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def _breaks(
    family: AnomalyFamily,
    detector: str,
    extra: dict | None = None,
    *,
    criticality: Criticality | None = None,
) -> bool:
    return breaks_through(
        family,
        detector,
        SimpleNamespace(extra=extra or {}),
        criticality=criticality,
        settings=_settings(),
    )


def test_active_suppression_prefers_the_detector_row_and_rejects_inactive_rows() -> None:
    wide = _row(detector=None)
    narrow = _row()
    other = _row(detector=AnomalyType.VOLUME_DROP.value)
    expired = _row(ends_at=AT)
    ended = _row(ended_at=AT - timedelta(minutes=1))
    future = _row(starts_at=AT + timedelta(microseconds=1))

    rows = [wide, other, expired, ended, future]
    assert active_suppression(
        rows,
        family=AnomalyFamily.LOG_SOURCE,
        subject_id=SUBJECT,
        detector=AnomalyType.VOLUME_SPIKE.value,
        at=AT,
    ) is wide
    assert active_suppression(
        [*rows, narrow],
        family=AnomalyFamily.LOG_SOURCE,
        subject_id=SUBJECT,
        detector=AnomalyType.VOLUME_SPIKE.value,
        at=AT,
    ) is narrow
    assert active_suppression(
        [narrow],
        family=AnomalyFamily.WAF,
        subject_id=SUBJECT,
        detector=AnomalyType.VOLUME_SPIKE.value,
        at=AT,
    ) is None
    assert active_suppression(
        [narrow],
        family=AnomalyFamily.LOG_SOURCE,
        subject_id=uuid.uuid4(),
        detector=AnomalyType.VOLUME_SPIKE.value,
        at=AT,
    ) is None


def test_every_detector_of_every_family_has_an_explicit_breakthrough_rule() -> None:
    assert set(BREAKTHROUGH_RULES) == set(FAMILY_ENUMS)
    for family, enum in FAMILY_ENUMS.items():
        assert set(BREAKTHROUGH_RULES[family]) == {item.value for item in enum}


def test_unknown_detectors_and_missing_or_non_numeric_magnitudes_never_break_through() -> None:
    assert not _breaks(AnomalyFamily.LOG_SOURCE, "MADE_UP", {"ratio": 100})
    for value in (None, "100", True, {}, []):
        extra = {"ratio": value} if value is not None else {}
        assert not _breaks(AnomalyFamily.WAF, WafAnomalyType.WAF_REQUEST_RATE_SPIKE, extra)


@pytest.mark.parametrize(
    ("family", "detector"),
    [
        (AnomalyFamily.WAF, WafAnomalyType.WAF_REQUEST_RATE_SPIKE),
        (AnomalyFamily.IPS, IpsAnomalyType.IPS_PORT_RATE_SPIKE),
        (AnomalyFamily.IPS, IpsAnomalyType.IPS_DESTINATION_RATE_SPIKE),
        (AnomalyFamily.IPS, IpsAnomalyType.IPS_SIGNATURE_BURST),
        (AnomalyFamily.IPS, IpsAnomalyType.IPS_PORT_FANOUT_SPIKE),
        (AnomalyFamily.PROPERTY_WATCH, PwAnomalyType.PW_VALUE_SPIKE),
    ],
)
def test_count_spikes_break_at_exactly_ten_times_but_not_just_below(
    family: AnomalyFamily, detector: str
) -> None:
    assert not _breaks(family, detector, {"ratio": 9.9999})
    assert _breaks(family, detector, {"ratio": 10})


@pytest.mark.parametrize(
    ("family", "detector"),
    [
        (AnomalyFamily.LOG_SOURCE, AnomalyType.VOLUME_SPIKE),
        (AnomalyFamily.WAF, WafAnomalyType.WAF_REQUEST_RATE_SPIKE),
        (AnomalyFamily.IPS, IpsAnomalyType.IPS_PORT_RATE_SPIKE),
        (AnomalyFamily.PROPERTY_WATCH, PwAnomalyType.PW_VALUE_SPIKE),
    ],
)
def test_an_undefined_expected_zero_ratio_is_not_invented_as_ten_times(
    family: AnomalyFamily, detector: str
) -> None:
    assert not _breaks(family, detector, {"ratio": None, "ratio_basis": "expected_zero"})


def test_phase_a_expected_zero_spike_uses_the_absolute_event_fallback() -> None:
    detector = AnomalyType.VOLUME_SPIKE
    assert not _breaks(
        AnomalyFamily.LOG_SOURCE,
        detector,
        {"ratio": None, "observed_events": 200},
    )
    assert not _breaks(
        AnomalyFamily.LOG_SOURCE,
        detector,
        {"ratio": None, "ratio_basis": "expected_zero", "observed_events": 199.9},
    )
    assert _breaks(
        AnomalyFamily.LOG_SOURCE,
        detector,
        {"ratio": None, "ratio_basis": "expected_zero", "observed_events": 200},
    )


@pytest.mark.parametrize(
    ("family", "detector"),
    [
        (AnomalyFamily.LOG_SOURCE, AnomalyType.VOLUME_DROP),
        (AnomalyFamily.PROPERTY_WATCH, PwAnomalyType.PW_VALUE_DROP),
    ],
)
def test_drops_break_at_exactly_one_tenth_but_not_above(
    family: AnomalyFamily, detector: str
) -> None:
    assert not _breaks(family, detector, {"ratio": 0.1001})
    assert _breaks(family, detector, {"ratio": 0.1})
    assert not _breaks(family, detector, {})


def test_only_a_critical_sources_no_events_silence_always_breaks_through() -> None:
    assert _breaks(
        AnomalyFamily.LOG_SOURCE,
        AnomalyType.NO_EVENTS,
        criticality=Criticality.CRITICAL,
    )
    for criticality in (None, Criticality.HIGH, Criticality.MEDIUM, Criticality.LOW):
        assert not _breaks(
            AnomalyFamily.LOG_SOURCE,
            AnomalyType.NO_EVENTS,
            criticality=criticality,
        )


@pytest.mark.parametrize(
    "detector",
    [
        WafAnomalyType.WAF_BLOCK_RATIO_SPIKE,
        WafAnomalyType.WAF_ERROR_RATIO_SPIKE,
        WafAnomalyType.WAF_CLIENT_ERROR_RATIO_SPIKE,
    ],
)
def test_waf_share_breakthrough_needs_both_the_half_share_and_three_times(
    detector: WafAnomalyType,
) -> None:
    def evidence(share: float, ratio: float) -> dict:
        return {"observed": {"proportion": share}, "ratio": ratio}

    assert not _breaks(AnomalyFamily.WAF, detector, evidence(0.4999, 3))
    assert not _breaks(AnomalyFamily.WAF, detector, evidence(0.5, 2.9999))
    assert _breaks(AnomalyFamily.WAF, detector, evidence(0.5, 3))


@pytest.mark.parametrize(
    ("family", "detector"),
    [
        (AnomalyFamily.WAF, WafAnomalyType.WAF_CLIENT_CONCENTRATION_SPIKE),
        (AnomalyFamily.IPS, IpsAnomalyType.IPS_SOURCE_CONCENTRATION_SPIKE),
    ],
)
def test_concentration_needs_eighty_percent_and_three_times(
    family: AnomalyFamily, detector: str
) -> None:
    def evidence(share: float, ratio: float) -> dict:
        return {"observed": {"proportion": share}, "ratio": ratio}

    assert not _breaks(family, detector, evidence(0.7999, 3))
    assert not _breaks(family, detector, evidence(0.8, 2.9999))
    assert _breaks(family, detector, evidence(0.8, 3))


def test_ips_block_shift_uses_absolute_rise_not_the_observed_share() -> None:
    detector = IpsAnomalyType.IPS_BLOCK_RATIO_SHIFT
    assert not _breaks(
        AnomalyFamily.IPS,
        detector,
        {
            "observed": {"proportion": 0.9},
            "absolute_deviation": 0.4999,
            "ratio": 3,
        },
    )
    assert not _breaks(
        AnomalyFamily.IPS,
        detector,
        {"absolute_deviation": 0.5, "ratio": 2.9999},
    )
    assert _breaks(
        AnomalyFamily.IPS,
        detector,
        {"absolute_deviation": 0.5, "ratio": 3},
    )


@pytest.mark.parametrize(
    ("family", "detector"),
    [
        *[
            (AnomalyFamily.LOG_SOURCE, detector)
            for detector in (
                AnomalyType.PARSING_DEGRADATION,
                AnomalyType.UNKNOWN_EVENT_SPIKE,
                AnomalyType.TIMESTAMP_DELAY,
                AnomalyType.CARDINALITY_DROP,
                AnomalyType.COLLECTION_ERROR,
                AnomalyType.REPEATED_PAYLOAD,
            )
        ],
        (AnomalyFamily.WAF, WafAnomalyType.WAF_NEW_ENDPOINT_ACCESS),
        (AnomalyFamily.WAF, WafAnomalyType.WAF_CLIENT_ENDPOINT_FANOUT_SPIKE),
        (AnomalyFamily.IPS, IpsAnomalyType.IPS_NEW_DESTINATION_PORT),
        (AnomalyFamily.PROPERTY_WATCH, PwAnomalyType.PW_NEW_VALUE),
        (AnomalyFamily.PROPERTY_WATCH, PwAnomalyType.PW_NEW_COMBINATION),
    ],
)
def test_every_never_rule_stays_closed_even_with_extreme_looking_fields(
    family: AnomalyFamily, detector: str
) -> None:
    assert not _breaks(
        family,
        detector,
        {
            "ratio": 1000,
            "observed": {"proportion": 1},
            "absolute_deviation": 1,
        },
        criticality=Criticality.CRITICAL,
    )


def test_a_disappeared_property_watch_value_is_the_named_always_rule() -> None:
    assert _breaks(AnomalyFamily.PROPERTY_WATCH, PwAnomalyType.PW_VALUE_DISAPPEARED)
