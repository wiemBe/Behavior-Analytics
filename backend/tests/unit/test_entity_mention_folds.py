"""Pure stored-parent folds for every entity mention source."""

from __future__ import annotations

import ast
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.services.entity_mention_folds import (
    catchall_mentions,
    explanation_mentions,
    ips_entity_mentions,
    ips_top_mentions,
    log_source_mentions,
    offense_mentions,
    pw_value_mentions,
    waf_top_mentions,
)
from app.services.entity_mentions import EntityMentionRow

NOW = datetime(2026, 11, 11, 8, 0, tzinfo=UTC)
END = NOW + timedelta(minutes=5)
INSTANCE = uuid.UUID("11111111-1111-4111-8111-111111111111")
PARENT = uuid.UUID("22222222-2222-4222-8222-222222222222")
OTHER_PARENT = uuid.UUID("33333333-3333-4333-8333-333333333333")

type RowView = tuple[str, str, str, str, str, str, datetime, datetime | None]


def _view(row: EntityMentionRow) -> RowView:
    return (
        row.kind,
        row.value_norm,
        row.role,
        row.source,
        row.ref_table,
        row.ref_id,
        row.occurred_at,
        row.occurred_end,
    )


def _expected(
    kind: str,
    value: str,
    role: str,
    source: str,
    ref_table: str,
    ref_id: object,
    *,
    occurred_at: datetime = NOW,
    occurred_end: datetime | None = None,
) -> RowView:
    return kind, value, role, source, ref_table, str(ref_id), occurred_at, occurred_end


@pytest.mark.parametrize(
    ("contributors", "expected"),
    [
        (
            [
                {"id": PARENT, "dimension": "source_ip", "value": "2001:0db8::1"},
                {"id": OTHER_PARENT, "dimension": "username", "value": "CORP\\Alice"},
                {"id": uuid.UUID(int=4), "dimension": "destination_ip", "value": "192.0.2.4"},
            ],
            [
                _expected(
                    "IP",
                    "2001:db8::1",
                    "SOURCE_IP",
                    "EXPLANATION",
                    "anomaly_explanation_contributor",
                    PARENT,
                    occurred_end=END,
                ),
                _expected(
                    "USER",
                    "alice",
                    "USER",
                    "EXPLANATION",
                    "anomaly_explanation_contributor",
                    OTHER_PARENT,
                    occurred_end=END,
                ),
                _expected(
                    "IP",
                    "192.0.2.4",
                    "DESTINATION_IP",
                    "EXPLANATION",
                    "anomaly_explanation_contributor",
                    uuid.UUID(int=4),
                    occurred_end=END,
                ),
            ],
        ),
        (
            [
                {"id": PARENT, "dimension": "source_ip", "value": "not-an-ip"},
                {"id": PARENT, "dimension": "qid", "value": "192.0.2.1"},
                {"id": PARENT, "dimension": "username", "value": "x" * 256},
            ],
            [],
        ),
        ([], []),
    ],
)
def test_explanation_mentions_are_exact_stored_contributor_rows(
    contributors: list[dict[str, object]], expected: list[RowView]
) -> None:
    rows = explanation_mentions(
        INSTANCE,
        contributors,
        anomaly_window_start=NOW,
        anomaly_window_end=END,
    )

    assert [_view(row) for row in rows] == expected


def test_explanation_mentions_preserve_the_stored_cap_and_duplicates() -> None:
    contributors = [
        {"id": uuid.UUID(int=index + 1), "dimension": "username", "value": "same"}
        for index in range(20)
    ]

    rows = explanation_mentions(
        INSTANCE,
        contributors,
        anomaly_window_start=NOW,
        anomaly_window_end=END,
    )

    assert [_view(row) for row in rows] == [
        _expected(
            "USER",
            "same",
            "USER",
            "EXPLANATION",
            "anomaly_explanation_contributor",
            contributor["id"],
            occurred_end=END,
        )
        for contributor in contributors
    ]


@pytest.mark.parametrize(
    ("top_clients", "expected"),
    [
        (None, []),
        ([], []),
        (
            [
                {"source_ip": "192.0.2.1", "requests": 9},
                {"source_ip": "2001:0db8::2", "requests": 8},
                {"source_ip": "192.0.2.1", "requests": 7},
                {"source_ip": "host.example", "requests": 6},
                {"source_ip": "", "requests": 5},
            ],
            [
                _expected(
                    "IP", "192.0.2.1", "SOURCE_IP", "WAF_TOP", "waf_endpoint_metric", PARENT
                ),
                _expected(
                    "IP", "2001:db8::2", "SOURCE_IP", "WAF_TOP", "waf_endpoint_metric", PARENT
                ),
                _expected(
                    "IP", "192.0.2.1", "SOURCE_IP", "WAF_TOP", "waf_endpoint_metric", PARENT
                ),
            ],
        ),
    ],
)
def test_waf_top_mentions_fold_the_stored_five_client_list(
    top_clients: list[dict[str, object]] | None, expected: list[RowView]
) -> None:
    rows = waf_top_mentions(
        INSTANCE,
        waf_endpoint_id=PARENT,
        bucket_start=NOW,
        top_clients=top_clients,
    )

    assert [_view(row) for row in rows] == expected


def test_ips_top_mentions_fold_both_stored_ten_item_lists_in_order() -> None:
    source_rows = [{"source_ip": f"192.0.2.{index}"} for index in range(1, 11)]
    fanout_rows = [{"source_ip": f"198.51.100.{index}"} for index in range(1, 10)]
    fanout_rows.append({"source_ip": "192.0.2.1"})

    rows = ips_top_mentions(
        INSTANCE,
        log_source_id=PARENT,
        bucket_start=NOW,
        contributors={"SOURCE_IP": source_rows, "PORT_FANOUT": fanout_rows},
    )

    assert [_view(row) for row in rows] == [
        _expected("IP", item["source_ip"], "SOURCE_IP", "IPS_TOP", "ips_source_bucket", PARENT)
        for item in [*source_rows, *fanout_rows]
    ]


@pytest.mark.parametrize(
    ("contributors", "expected"),
    [
        ({}, []),
        ({"SOURCE_IP": [], "PORT_FANOUT": []}, []),
        (
            {"SOURCE_IP": [{"source_ip": "host"}], "PORT_FANOUT": [{"wrong": "value"}]},
            [],
        ),
    ],
)
def test_ips_top_mentions_skip_empty_and_rejected_stored_lists(
    contributors: dict[str, list[dict[str, object]]], expected: list[RowView]
) -> None:
    rows = ips_top_mentions(
        INSTANCE,
        log_source_id=PARENT,
        bucket_start=NOW,
        contributors=contributors,
    )

    assert [_view(row) for row in rows] == expected


@pytest.mark.parametrize(
    ("value_key", "expected"),
    [
        (
            "2001:0db8::9",
            [
                _expected(
                    "IP",
                    "2001:db8::9",
                    "DESTINATION_IP",
                    "IPS_ENTITY",
                    "ips_entity",
                    PARENT,
                )
            ],
        ),
        ("destination.example", []),
        ("", []),
    ],
)
def test_ips_entity_mentions_use_the_entity_parent_not_the_metric_parent(
    value_key: str, expected: list[RowView]
) -> None:
    rows = ips_entity_mentions(
        INSTANCE,
        ips_entity_id=PARENT,
        bucket_start=NOW,
        value_key=value_key,
    )

    assert [_view(row) for row in rows] == expected


def test_offense_mentions_fold_stored_users_source_and_times_exactly() -> None:
    rows = offense_mentions(
        INSTANCE,
        qradar_offense_id=42,
        captured_at=END,
        usernames=["CORP\\Alice", "alice@other", "CORP\\Alice", "x" * 256, None],
        offense_source="2001:0db8::42",
        offense_type=11,
        start_time=NOW,
        last_updated_time=NOW + timedelta(minutes=2),
        close_time=END,
    )

    assert [_view(row) for row in rows] == [
        _expected("USER", "alice", "USER", "OFFENSE", "offense_snapshot", 42, occurred_end=END),
        _expected("USER", "alice", "USER", "OFFENSE", "offense_snapshot", 42, occurred_end=END),
        _expected("USER", "alice", "USER", "OFFENSE", "offense_snapshot", 42, occurred_end=END),
        _expected(
            "IP",
            "2001:db8::42",
            "DESTINATION_IP",
            "OFFENSE",
            "offense_snapshot",
            42,
            occurred_end=END,
        ),
    ]


def test_offense_mentions_use_fallback_time_skip_hosts_and_keep_the_32_user_cap() -> None:
    captured_at = NOW + timedelta(hours=1)
    usernames = [f"DOMAIN\\user{index}" for index in range(32)]
    rows = offense_mentions(
        INSTANCE,
        qradar_offense_id=43,
        captured_at=captured_at,
        usernames=usernames,
        offense_source="gateway.example",
        offense_type=7,
        start_time=None,
        last_updated_time=None,
        close_time=NOW,
    )

    assert [_view(row) for row in rows] == [
        _expected(
            "USER",
            f"user{index}",
            "USER",
            "OFFENSE",
            "offense_snapshot",
            43,
            occurred_at=captured_at,
        )
        for index in range(32)
    ]


def test_log_source_mentions_fold_host_ip_rejections_and_duplicates() -> None:
    rows = log_source_mentions(
        INSTANCE,
        [
            {"id": PARENT, "name": "Edge-Gateway.", "identifier": "192.0.2.20"},
            {"id": OTHER_PARENT, "name": "dup", "identifier": "dup"},
            {"id": uuid.UUID(int=5), "name": "x" * 256, "identifier": None},
        ],
        observed_at=NOW,
    )

    assert [_view(row) for row in rows] == [
        _expected("HOST", "edge-gateway", "HOST", "LOG_SOURCE", "log_source", PARENT),
        _expected("HOST", "192.0.2.20", "HOST", "LOG_SOURCE", "log_source", PARENT),
        _expected("IP", "192.0.2.20", "HOST", "LOG_SOURCE", "log_source", PARENT),
        _expected("HOST", "dup", "HOST", "LOG_SOURCE", "log_source", OTHER_PARENT),
        _expected("HOST", "dup", "HOST", "LOG_SOURCE", "log_source", OTHER_PARENT),
    ]
    assert log_source_mentions(INSTANCE, [], observed_at=NOW) == []


def test_catchall_mentions_fold_the_stored_twenty_sender_cap_and_empty_list() -> None:
    senders = [{"identifier": f"Host-{index}."} for index in range(18)]
    senders += [{"identifier": "192.0.2.30"}, {"identifier": "Host-0."}]
    rows = catchall_mentions(
        INSTANCE,
        [
            {
                "catchall_log_source_id": PARENT,
                "bucket_start": NOW,
                "bucket_seconds": 300,
                "top_senders": senders,
            },
            {
                "catchall_log_source_id": OTHER_PARENT,
                "bucket_start": END,
                "bucket_seconds": 300,
                "top_senders": None,
            },
        ],
    )

    assert [_view(row) for row in rows] == [
        *[
            _expected(
                "HOST",
                f"host-{index}",
                "HOST",
                "CATCHALL",
                "catchall_bucket",
                PARENT,
                occurred_end=END,
            )
            for index in range(18)
        ],
        _expected(
            "IP",
            "192.0.2.30",
            "SOURCE_IP",
            "CATCHALL",
            "catchall_bucket",
            PARENT,
            occurred_end=END,
        ),
        _expected(
            "HOST",
            "host-0",
            "HOST",
            "CATCHALL",
            "catchall_bucket",
            PARENT,
            occurred_end=END,
        ),
    ]


@pytest.mark.parametrize(
    ("properties", "value_key", "truncated", "expected"),
    [
        (
            [{"kind": "CUSTOM_PROPERTY", "name": " username "}],
            "CORP\\Alice",
            False,
            [
                _expected(
                    "USER",
                    "alice",
                    "USER",
                    "PW_VALUE",
                    "pw_value_metric",
                    PARENT,
                    occurred_end=END,
                )
            ],
        ),
        (
            [{"kind": "CUSTOM_PROPERTY", "name": "SOURCEIP"}],
            "2001:0db8::10",
            False,
            [
                _expected(
                    "IP",
                    "2001:db8::10",
                    "SOURCE_IP",
                    "PW_VALUE",
                    "pw_value_metric",
                    PARENT,
                    occurred_end=END,
                )
            ],
        ),
        (
            [{"kind": "CUSTOM_PROPERTY", "name": "DestinationIp"}],
            "192.0.2.11",
            False,
            [
                _expected(
                    "IP",
                    "192.0.2.11",
                    "DESTINATION_IP",
                    "PW_VALUE",
                    "pw_value_metric",
                    PARENT,
                    occurred_end=END,
                )
            ],
        ),
        ([{"kind": "CUSTOM_PROPERTY", "name": "source_ip"}], "192.0.2.1", False, []),
        ([{"kind": "CUSTOM_PROPERTY", "name": "sourceip"}], "host", False, []),
        ([{"kind": "CUSTOM_PROPERTY", "name": "username"}], "alice", True, []),
        (
            [
                {"kind": "CUSTOM_PROPERTY", "name": "username"},
                {"kind": "CUSTOM_PROPERTY", "name": "action"},
            ],
            "alice\x1fallow",
            False,
            [],
        ),
        ([], "alice", False, []),
    ],
)
def test_pw_value_mentions_fold_only_exact_single_property_stored_values(
    properties: list[dict[str, str]],
    value_key: str,
    truncated: bool,
    expected: list[RowView],
) -> None:
    rows = pw_value_mentions(
        INSTANCE,
        properties,
        pw_value_id=PARENT,
        value_key=value_key,
        truncated=truncated,
        bucket_start=NOW,
        bucket_seconds=300,
    )

    assert [_view(row) for row in rows] == expected


def test_only_the_shared_fold_module_constructs_entity_mention_rows() -> None:
    backend = Path(__file__).resolve().parents[2]
    allowed = backend / "app" / "services" / "entity_mention_folds.py"
    offenders: list[str] = []
    for root in (backend / "app" / "collectors", backend / "app" / "services"):
        for path in sorted(root.rglob("*.py")):
            if path == allowed:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = node.func.id if isinstance(node.func, ast.Name) else None
                if isinstance(node.func, ast.Attribute):
                    name = node.func.attr
                if name == "EntityMentionRow":
                    offenders.append(str(path.relative_to(backend)))

    assert offenders == []
