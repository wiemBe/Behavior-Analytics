"""Pure mention rows for SOC-16d1's two bounded collectors."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.collectors.catchall_collector import _catchall_mention_rows
from app.collectors.pw_collector import _pw_mention_row
from app.property_watch.admission import TrackedValue

NOW = datetime(2026, 11, 11, 8, 0, tzinfo=UTC)
INSTANCE = uuid.UUID("11111111-1111-4111-8111-111111111111")
PARENT = uuid.UUID("22222222-2222-4222-8222-222222222222")


def _value(raw: str, *, truncated: bool = False) -> TrackedValue:
    return TrackedValue(
        id=uuid.uuid4(),
        value_key=raw,
        components=(raw,),
        pinned=False,
        active=True,
        first_seen_at=NOW,
        last_seen_at=NOW,
        tracked_since=NOW,
        truncated=truncated,
    )


def test_catchall_stored_sender_rows_classify_canonical_ips_and_hosts() -> None:
    rows = _catchall_mention_rows(
        INSTANCE,
        [
            {
                "catchall_log_source_id": PARENT,
                "bucket_start": NOW,
                "bucket_seconds": 300,
                "top_senders": [
                    {"identifier": "192.0.2.9", "events": 8},
                    {"identifier": "Edge-Relay.", "events": 7},
                    {"identifier": "x" * 256, "events": 6},
                ],
            }
        ],
    )

    assert [(row.kind, row.value_norm, row.role) for row in rows] == [
        ("IP", "192.0.2.9", "SOURCE_IP"),
        ("HOST", "edge-relay", "HOST"),
    ]
    assert {row.ref_table for row in rows} == {"catchall_bucket"}
    assert {row.ref_id for row in rows} == {str(PARENT)}
    assert {row.occurred_end for row in rows} == {
        datetime(2026, 11, 11, 8, 5, tzinfo=UTC)
    }


@pytest.mark.parametrize(
    ("name", "raw", "expected"),
    [
        (" username ", "CORP\\Alice", ("USER", "alice", "USER")),
        ("SOURCEIP", "192.0.2.10", ("IP", "192.0.2.10", "SOURCE_IP")),
        (
            " DestinationIp ",
            "2001:0db8::10",
            ("IP", "2001:db8::10", "DESTINATION_IP"),
        ),
    ],
)
def test_property_watch_exact_names_map_stored_values_to_their_entity_role(
    name: str, raw: str, expected: tuple[str, str, str]
) -> None:
    row = _pw_mention_row(
        INSTANCE,
        [{"kind": "CUSTOM_PROPERTY", "name": name}],
        _value(raw),
        bucket_start=NOW,
        bucket_seconds=300,
    )

    assert row is not None
    assert (row.kind, row.value_norm, row.role) == expected
    assert row.ref_table == "pw_value_metric"


@pytest.mark.parametrize(
    ("properties", "value"),
    [
        ([{"kind": "CUSTOM_PROPERTY", "name": "source_ip"}], _value("192.0.2.1")),
        ([{"kind": "CUSTOM_PROPERTY", "name": "user"}], _value("alice")),
        ([{"kind": "CUSTOM_PROPERTY", "name": "sourceip"}], _value("not-an-ip")),
        ([{"kind": "NORMALIZED", "name": "username"}], _value("alice", truncated=True)),
        (
            [
                {"kind": "NORMALIZED", "name": "username"},
                {"kind": "CUSTOM_PROPERTY", "name": "Action"},
            ],
            _value("alice\x1fallow"),
        ),
    ],
)
def test_property_watch_rejects_fuzzy_names_invalid_ips_truncation_and_combinations(
    properties: list[dict[str, str]], value: TrackedValue
) -> None:
    assert (
        _pw_mention_row(
            INSTANCE,
            properties,
            value,
            bucket_start=NOW,
            bucket_seconds=300,
        )
        is None
    )
