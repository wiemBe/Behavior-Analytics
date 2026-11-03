"""The stored AQL of the QRadar health family (HLT-2): fixed list, no payload, bounded."""

from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest

from app.health_detail import queries, spec
from app.health_detail.queries import StoredQueryError, assert_stored_query

START, END = 1_762_160_000_000, 1_762_160_900_000


class TestMetricsQuery:
    def test_it_names_exactly_the_fixed_metric_list(self) -> None:
        aql = queries.metrics_query(start_ms=START, end_ms=END, limit=5001)
        listed = re.search(r'"Metric ID" IN \(([^)]*)\)', aql)
        assert listed is not None
        assert [x.strip().strip("'") for x in listed.group(1).split(",")] == list(spec.METRIC_IDS)

    def test_it_aggregates_and_never_selects_a_payload_or_every_column(self) -> None:
        aql = queries.metrics_query(start_ms=START, end_ms=END, limit=10)
        assert "GROUP BY" in aql
        assert "MAX(" in aql and "AVG(" in aql and "COUNT(*)" in aql
        assert not re.search(r"select\s+\*", aql, re.IGNORECASE)
        assert "payload" not in aql.lower()

    def test_the_window_is_half_open_and_the_limit_is_present(self) -> None:
        aql = queries.metrics_query(start_ms=START, end_ms=END, limit=77)
        assert f"starttime >= {START}" in aql
        assert f"starttime < {END}" in aql
        assert aql.endswith("LIMIT 77")

    def test_it_reads_only_the_health_metrics_log_sources_and_the_shipped_properties(self) -> None:
        aql = queries.metrics_query(start_ms=START, end_ms=END, limit=10)
        assert "LOGSOURCETYPENAME(devicetype) = 'Health Metrics'" in aql
        for name in ('"Metric ID"', '"Value"', '"Element"'):
            assert name in aql

    @pytest.mark.parametrize(
        ("start", "end"), [(5, 5), (9, 5), (True, 6), ("1", 5), (1.5, 9)]
    )
    def test_a_bad_window_is_refused(self, start: object, end: object) -> None:
        with pytest.raises(StoredQueryError):
            queries.metrics_query(start_ms=start, end_ms=end, limit=10)  # type: ignore[arg-type]

    @pytest.mark.parametrize("limit", [0, -1, True, "5"])
    def test_a_bad_limit_is_refused(self, limit: object) -> None:
        with pytest.raises(StoredQueryError):
            queries.metrics_query(start_ms=START, end_ms=END, limit=limit)  # type: ignore[arg-type]


class TestNotificationsQuery:
    def test_information_message_is_excluded_in_the_query_itself(self) -> None:
        aql = queries.notifications_query(start_ms=START, end_ms=END, limit=10)
        assert "QIDNAME(qid) <> 'Information Message'" in aql

    def test_it_reads_the_system_notification_sources_and_selects_no_payload(self) -> None:
        aql = queries.notifications_query(start_ms=START, end_ms=END, limit=1001)
        assert "LOGSOURCETYPENAME(devicetype) = 'System Notification'" in aql
        assert "GROUP BY LOGSOURCENAME(logsourceid), QIDNAME(qid)" in aql
        assert "payload" not in aql.lower() and "SELECT *" not in aql
        assert "MAX(severity)" in aql and "SUM(eventcount)" in aql and "MAX(starttime)" in aql
        assert aql.endswith("LIMIT 1001")


class TestValidation:
    @pytest.mark.parametrize(
        "aql",
        [
            "SELECT * FROM events GROUP BY x LIMIT 1",
            "SELECT UTF8(payload) FROM events GROUP BY x LIMIT 1",
            "SELECT payload FROM events GROUP BY x LIMIT 1",
            "SELECT x FROM events",
            "SELECT x FROM events LIMIT 5",
            "SELECT x FROM events WHERE \"Metric ID\" IN ('Rogue') GROUP BY x LIMIT 5",
            "SELECT x FROM events WHERE \"Metric ID\" IN ('DiskUsage', 'x'') OR 1=1') "
            "GROUP BY x LIMIT 5",
        ],
    )
    def test_an_unsafe_search_is_refused(self, aql: str) -> None:
        with pytest.raises(StoredQueryError):
            assert_stored_query(aql)

    def test_the_two_stored_queries_pass_their_own_validation(self) -> None:
        assert_stored_query(queries.metrics_query(start_ms=START, end_ms=END, limit=5))
        assert_stored_query(queries.notifications_query(start_ms=START, end_ms=END, limit=5))


class TestSpec:
    def test_the_list_is_fixed_unique_and_plain_identifiers(self) -> None:
        assert len(set(spec.METRIC_IDS)) == len(spec.METRIC_IDS)
        assert all(spec.METRIC_ID_PATTERN.fullmatch(m) for m in spec.METRIC_IDS)
        assert {"DiskUsage", "CREQueueSize", "QueuedEventThrottleFilterDiskSize"} <= set(
            spec.METRIC_IDS
        )

    def test_the_default_mount_allowlist_is_the_designs(self) -> None:
        from app.core.config import Settings

        assert Settings().qradar_health_disk_mounts == [
            "/", "/store", "/storetmp", "/transient", "/var/log", "/var/log/audit",
            "/opt", "/home", "/tmp", "/var", "/boot", "/recovery",  # noqa: S108
        ]


def test_the_provider_builds_the_window_in_epoch_milliseconds() -> None:
    from app.providers.qradar_rest import QRadarRestProvider

    start = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
    end = datetime(2026, 11, 3, 12, 15, tzinfo=UTC)
    _s, _e, start_ms, end_ms = QRadarRestProvider._health_window(start, end)
    assert (start_ms, end_ms) == (int(start.timestamp() * 1000), int(end.timestamp() * 1000))
    with pytest.raises(ValueError):
        QRadarRestProvider._health_window(start.replace(tzinfo=None), end)
    with pytest.raises(ValueError):
        QRadarRestProvider._health_window(end, start)
