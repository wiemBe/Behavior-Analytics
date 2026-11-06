"""Validation of the six tuning knobs (SOC-17b, design §3.1 and §3.6).

These pin the new refusal on new writes: each bound just inside and just
outside, wrong types, an unknown key, ``by_type`` against the same keys, and the
legacy ``Thresholds`` keys that stay legal. They also pin the PATCH's audit-only
``preview`` block, which must validate its shape and never enter the saved row.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.schemas.log_source import LogSourceUpdate, PreviewBlock, ThresholdConfig

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)


def _valid(value: dict) -> None:
    ThresholdConfig.model_validate(value)


def _invalid(value: dict) -> None:
    with pytest.raises(ValidationError):
        ThresholdConfig.model_validate(value)


class TestSixKnobs:
    @pytest.mark.parametrize(
        ("key", "inside", "outside_low", "outside_high"),
        [
            ("deviation_z", (0.5, 20.0), 0.499, 20.001),
            ("spike_ratio", (1.01, 100.0), 1.009, 100.1),
            ("drop_ratio", (0.01, 0.99), 0.009, 0.991),
            ("min_absolute_delta_events", (0, 1_000_000), -1, 1_000_001),
            ("open_after", (1, 10), 0, 11),
        ],
    )
    def test_each_bound_is_inclusive_and_refuses_just_outside(
        self, key: str, inside: tuple, outside_low: float, outside_high: float
    ) -> None:
        for value in inside:
            _valid({key: value})
        _invalid({key: outside_low})
        _invalid({key: outside_high})

    @pytest.mark.parametrize("direction", ["both", "spike", "drop"])
    def test_every_direction_is_legal(self, direction: str) -> None:
        _valid({"direction": direction})

    def test_an_unknown_direction_is_refused(self) -> None:
        _invalid({"direction": "up"})

    @pytest.mark.parametrize(
        "value",
        [
            {"deviation_z": "3.5"},
            {"open_after": 1.5},
            {"open_after": True},
            {"direction": 1},
        ],
    )
    def test_wrong_types_are_refused(self, value: dict) -> None:
        _invalid(value)

    def test_an_unknown_key_is_refused(self) -> None:
        _invalid({"deviationz": 3.5})

    def test_the_model_is_frozen(self) -> None:
        config = ThresholdConfig.model_validate({"deviation_z": 4.0})
        with pytest.raises(ValidationError):
            config.deviation_z = 5.0  # type: ignore[misc]


class TestByType:
    def test_a_nested_knob_is_validated_against_the_same_bounds(self) -> None:
        _valid({"by_type": {"VOLUME_SPIKE": {"deviation_z": 5.0}}})
        _invalid({"by_type": {"VOLUME_SPIKE": {"deviation_z": 21.0}}})

    def test_a_nested_unknown_key_is_refused(self) -> None:
        _invalid({"by_type": {"VOLUME_SPIKE": {"deviationz": 5.0}}})

    def test_an_unknown_anomaly_type_is_refused(self) -> None:
        _invalid({"by_type": {"NOT_A_DETECTOR": {"deviation_z": 5.0}}})

    def test_legacy_keys_inside_by_type_stay_legal(self) -> None:
        _valid({"by_type": {"NO_EVENTS": {"resolve_after": 5, "silence_grace_buckets": 2}}})


class TestLegacyKeys:
    def test_every_other_thresholds_field_is_still_accepted(self) -> None:
        _valid(
            {
                "min_bucket_events": 10.0,
                "silence_grace_buckets": 0,
                "min_parsed_ratio": 0.7,
                "max_unknown_ratio": 0.1,
                "delay_interval_multiple": 4.0,
                "cardinality_drop_fraction": 0.3,
                "collection_error_threshold": 2,
                "repeated_payload_intervals": 4,
                "resolve_after": 5,
            }
        )

    def test_a_legacy_key_with_the_wrong_type_is_refused(self) -> None:
        _invalid({"resolve_after": "3"})
        _invalid({"min_bucket_events": "10"})

    def test_an_empty_map_is_valid(self) -> None:
        assert ThresholdConfig.model_validate({}).model_dump(exclude_none=True) == {}


class TestUpdateSchema:
    def test_a_subset_is_validated_and_kept_unchanged(self) -> None:
        update = LogSourceUpdate.model_validate(
            {"custom_thresholds": {"deviation_z": 5, "direction": "drop"}}
        )
        assert update.custom_thresholds == {"deviation_z": 5, "direction": "drop"}

    def test_an_unknown_custom_threshold_key_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            LogSourceUpdate.model_validate({"custom_thresholds": {"made_up": 1}})

    def test_an_out_of_bounds_custom_threshold_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            LogSourceUpdate.model_validate({"custom_thresholds": {"spike_ratio": 0.4}})

    def test_no_custom_thresholds_is_still_valid(self) -> None:
        assert LogSourceUpdate.model_validate({"owner": "ada"}).custom_thresholds is None


class TestPreviewBlock:
    def test_a_valid_block_parses(self) -> None:
        block = PreviewBlock.model_validate(
            {
                "at": NOW.isoformat(),
                "window": 28,
                "current": 39,
                "proposed": 7,
                "state": "COMPLETE",
            }
        )
        assert block.current == 39 and block.state == "COMPLETE"

    @pytest.mark.parametrize(
        "value",
        [
            {"at": NOW.isoformat(), "window": 0, "state": "COMPLETE"},
            {"at": NOW.isoformat(), "window": 28, "state": "NORMAL"},
            {"at": NOW.isoformat(), "window": 28, "state": "COMPLETE", "extra": 1},
            {"window": 28, "state": "COMPLETE"},
            {
                "at": NOW.isoformat(),
                "window": 28,
                "current": -1,
                "state": "COMPLETE",
            },
        ],
    )
    def test_a_malformed_block_is_refused(self, value: dict) -> None:
        with pytest.raises(ValidationError):
            PreviewBlock.model_validate(value)
