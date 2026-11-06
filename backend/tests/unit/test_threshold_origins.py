"""The resolver reports where each tuning knob's value came from (SOC-17b r1).

The tuning form pre-fills from the resolved values, so it needs the layer that
supplied each one. These tests pin the resolver's own precedence -- default,
criticality, LAB_MODE, custom -- and that ``resolve`` still returns exactly the
same values now that ``resolve_traced`` records the origin.
"""

from __future__ import annotations

import pytest

from app.anomaly.thresholds import (
    TUNING_KNOBS,
    ThresholdOrigin,
    ThresholdResolver,
)
from app.core.config import Settings
from app.models.enums import AnomalyType, Criticality


def settings(**overrides) -> Settings:
    return Settings(_env_file=None, debug=False, **overrides)


def test_the_six_knobs_start_from_the_settings_as_default() -> None:
    resolver = ThresholdResolver(settings())
    resolved = resolver.resolve_traced(criticality=Criticality.MEDIUM)

    assert list(TUNING_KNOBS) == [
        "deviation_z",
        "spike_ratio",
        "drop_ratio",
        "min_absolute_delta_events",
        "open_after",
        "direction",
    ]
    by_knob = {knob: getattr(resolved.values, knob) for knob in TUNING_KNOBS}
    assert by_knob == {
        "deviation_z": 3.5,
        "spike_ratio": 2.0,
        "drop_ratio": 0.5,
        "min_absolute_delta_events": 100.0,
        "open_after": 2,
        "direction": "both",
    }
    assert resolved.origin("deviation_z") is ThresholdOrigin.DEFAULT
    assert resolved.origin("spike_ratio") is ThresholdOrigin.DEFAULT
    assert resolved.origin("drop_ratio") is ThresholdOrigin.DEFAULT
    assert resolved.origin("min_absolute_delta_events") is ThresholdOrigin.DEFAULT
    assert resolved.origin("direction") is ThresholdOrigin.DEFAULT


@pytest.mark.parametrize(
    ("criticality", "open_after"),
    [
        (Criticality.CRITICAL, 1),
        (Criticality.HIGH, 2),
        (Criticality.MEDIUM, 2),
        (Criticality.LOW, 3),
    ],
)
def test_open_after_says_it_came_from_criticality(
    criticality: Criticality, open_after: int
) -> None:
    resolver = ThresholdResolver(settings())

    resolved = resolver.resolve_traced(criticality=criticality)

    assert resolved.values.open_after == open_after
    assert resolved.origin("open_after") is ThresholdOrigin.CRITICALITY
    # The detector thresholds are not part of the criticality matrix.
    assert resolved.origin("deviation_z") is ThresholdOrigin.DEFAULT


def test_lab_mode_supplies_open_after_and_never_a_detection_knob() -> None:
    resolver = ThresholdResolver(settings(lab_mode=True))

    resolved = resolver.resolve_traced(criticality=Criticality.LOW)

    assert resolved.values.open_after == 1
    assert resolved.values.resolve_after == 2
    assert resolved.origin("open_after") is ThresholdOrigin.LAB_MODE
    assert resolved.origin("resolve_after") is ThresholdOrigin.LAB_MODE
    # LAB_MODE may shorten timing only: the four detection guards keep the
    # production values and their default origin (design §1.3, §3.2).
    assert resolved.values.deviation_z == 3.5
    assert resolved.origin("deviation_z") is ThresholdOrigin.DEFAULT
    assert resolved.origin("spike_ratio") is ThresholdOrigin.DEFAULT
    assert resolved.origin("drop_ratio") is ThresholdOrigin.DEFAULT
    assert resolved.origin("min_absolute_delta_events") is ThresholdOrigin.DEFAULT


def test_a_custom_value_names_custom_for_every_knob() -> None:
    resolver = ThresholdResolver(settings())
    custom = {
        "deviation_z": 4.5,
        "spike_ratio": 3.0,
        "drop_ratio": 0.4,
        "min_absolute_delta_events": 250.0,
        "open_after": 5,
        "direction": "spike",
    }

    resolved = resolver.resolve_traced(
        criticality=Criticality.CRITICAL, custom=custom
    )

    for knob, value in custom.items():
        assert getattr(resolved.values, knob) == value, knob
        assert resolved.origin(knob) is ThresholdOrigin.CUSTOM, knob


def test_by_type_does_not_change_the_top_level_origin() -> None:
    resolver = ThresholdResolver(settings())
    custom = {"by_type": {"VOLUME_SPIKE": {"deviation_z": 9.0}}}

    top = resolver.resolve_traced(criticality=Criticality.MEDIUM, custom=custom)
    typed = resolver.resolve_traced(
        criticality=Criticality.MEDIUM,
        custom=custom,
        anomaly_type=AnomalyType.VOLUME_SPIKE,
    )

    # The detectors resolve without a type, so the top-level value is unchanged
    # and must not claim to be custom (design §3.2).
    assert top.values.deviation_z == 3.5
    assert top.origin("deviation_z") is ThresholdOrigin.DEFAULT
    # The per-type resolution does move it; only the hysteresis reads that.
    assert typed.values.deviation_z == 9.0
    assert typed.origin("deviation_z") is ThresholdOrigin.CUSTOM


def test_a_malformed_stored_value_is_reported_not_rejected() -> None:
    """Reads are never rejected: a row that never passed validation still shows."""
    resolver = ThresholdResolver(settings())
    custom = {"direction": "sideways", "deviation_z": None}

    resolved = resolver.resolve_traced(
        criticality=Criticality.MEDIUM, custom=custom
    )

    assert resolved.values.direction == "sideways"
    assert resolved.origin("direction") is ThresholdOrigin.CUSTOM
    assert resolved.values.deviation_z is None
    assert resolved.origin("deviation_z") is ThresholdOrigin.CUSTOM


def test_resolve_still_returns_the_traced_values() -> None:
    resolver = ThresholdResolver(settings())
    custom = {"open_after": 4, "direction": "drop"}

    assert resolver.resolve(
        criticality=Criticality.LOW, custom=custom
    ) == resolver.resolve_traced(
        criticality=Criticality.LOW, custom=custom
    ).values
