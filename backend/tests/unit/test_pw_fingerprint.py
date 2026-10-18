"""The property-watch alert fingerprint (design §G.10.1)."""

from __future__ import annotations

import uuid

import pytest

from app.alerts.fingerprint import (
    anomaly_fingerprint,
    custom_rule_breach_fingerprint,
    inventory_fingerprint,
    ips_anomaly_fingerprint,
    pw_anomaly_fingerprint,
    search_failure_fingerprint,
    search_threshold_fingerprint,
    waf_anomaly_fingerprint,
)

INSTANCE = uuid.UUID("11111111-1111-1111-1111-111111111111")
WATCH = uuid.UUID("22222222-2222-2222-2222-222222222222")
SPIKE = "PW_VALUE_SPIKE"
DROP = "PW_VALUE_DROP"


def test_the_fingerprint_is_the_natural_key_and_nothing_else() -> None:
    first = pw_anomaly_fingerprint(INSTANCE, WATCH, SPIKE, "alice")
    assert first == pw_anomaly_fingerprint(str(INSTANCE), str(WATCH), SPIKE, "alice")
    assert first.startswith("pw_anomaly:")


def test_the_value_text_enters_only_as_a_hash() -> None:
    text = "Zq-Secret-User\x1flogin"
    fingerprint = pw_anomaly_fingerprint(INSTANCE, WATCH, SPIKE, text)
    assert "secret" not in fingerprint.lower() and "zq" not in fingerprint.lower()
    # a 32-hex digest after the condition: no part of the key is readable in it
    assert len(fingerprint.split(":", 1)[1]) == 32


@pytest.mark.parametrize(
    "other",
    [
        (uuid.uuid4(), WATCH, SPIKE, "alice"),
        (INSTANCE, uuid.uuid4(), SPIKE, "alice"),
        (INSTANCE, WATCH, DROP, "alice"),
        (INSTANCE, WATCH, SPIKE, "bob"),
    ],
)
def test_every_part_of_the_key_changes_the_fingerprint(other: tuple) -> None:
    assert pw_anomaly_fingerprint(*other) != pw_anomaly_fingerprint(INSTANCE, WATCH, SPIKE, "alice")


def test_two_values_that_differ_only_by_case_are_two_values() -> None:
    assert pw_anomaly_fingerprint(INSTANCE, WATCH, SPIKE, "Alice") != pw_anomaly_fingerprint(
        INSTANCE, WATCH, SPIKE, "alice"
    )


def test_the_tuple_separator_cannot_blur_two_values() -> None:
    assert pw_anomaly_fingerprint(INSTANCE, WATCH, SPIKE, "a\x1fb") != pw_anomaly_fingerprint(
        INSTANCE, WATCH, SPIKE, "ab"
    )


def test_the_fingerprint_does_not_depend_on_a_row_or_a_version() -> None:
    """Retirement and reactivation make a new value row, never a new identity: the key has
    no id and no `watch_version` slot, and repeating the call is the same string."""
    one = pw_anomaly_fingerprint(INSTANCE, WATCH, SPIKE, "alice")
    assert one == pw_anomaly_fingerprint(INSTANCE, WATCH, SPIKE, "alice")
    with pytest.raises(TypeError):
        pw_anomaly_fingerprint(INSTANCE, WATCH, SPIKE, "alice", 2)  # type: ignore[call-arg]


def test_an_empty_value_key_is_refused() -> None:
    with pytest.raises(ValueError):
        pw_anomaly_fingerprint(INSTANCE, WATCH, SPIKE, "")


def test_it_never_equals_another_family_for_the_same_ids() -> None:
    mine = pw_anomaly_fingerprint(INSTANCE, WATCH, SPIKE, "alice")
    others = {
        anomaly_fingerprint(WATCH, SPIKE),
        inventory_fingerprint(INSTANCE, 1),
        waf_anomaly_fingerprint(
            instance_id=INSTANCE,
            log_source_id=WATCH,
            virtual_host=None,
            normalized_endpoint="alice",
            http_method="GET",
            normalizer_version=1,
            detector_type=SPIKE,
        ),
        ips_anomaly_fingerprint(
            instance_id=INSTANCE,
            log_source_id=WATCH,
            detector_type=SPIKE,
            dimension="SIGNATURE",
            value_key="alice",
        ),
        ips_anomaly_fingerprint(instance_id=INSTANCE, log_source_id=WATCH, detector_type=SPIKE),
        custom_rule_breach_fingerprint(WATCH),
        search_threshold_fingerprint(WATCH),
        search_failure_fingerprint(WATCH),
    }
    assert mine not in others
    assert all(not item.startswith("pw_anomaly:") for item in others)
