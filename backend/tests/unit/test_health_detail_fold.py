"""The pure fold of the QRadar health searches (HLT-2): allowlists, drops, remainder."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.health_detail.fold import fold_metrics, fold_notifications
from app.health_detail.rows import (
    HealthMetricRow,
    HealthNotificationRow,
    parse_health_metric_rows,
    parse_health_notification_rows,
)
from app.health_detail.spec import NOTIFICATION_TOP, parse_host
from app.providers.base import ProviderError

HOST = "Health Metrics-2 :: qradarce2"
MOUNTS = ("/", "/store", "/tmp")  # noqa: S108 - mount names to compare


def metric(
    metric_id: str | None,
    element: str | None = None,
    value: float | None = 1.0,
    *,
    source: str | None = HOST,
    avg: float | None = None,
    samples: int = 10,
) -> HealthMetricRow:
    return HealthMetricRow(
        source=source,
        metric=metric_id,
        element=element,
        max_value=value,
        avg_value=value if avg is None else avg,
        samples=samples,
    )


class TestHost:
    @pytest.mark.parametrize(
        ("name", "host"),
        [
            ("Health Metrics-2 :: qradarce2", "qradarce2"),
            ("Health Metrics-12 :: qr-managed.example.org", "qr-managed.example.org"),
            ("System Notification-3 :: console01", "console01"),
        ],
    )
    def test_a_name_with_a_hostname_parses(self, name: str, host: str) -> None:
        assert parse_host(name) == host

    @pytest.mark.parametrize(
        "name", [None, "", "Health Metrics-2", " :: qradarce2", "Health Metrics-2 :: ", 7,
                 "Health Metrics-2 :: bad host", "Health Metrics-2 :: ../etc"],
    )
    def test_anything_else_is_not_guessed(self, name: object) -> None:
        assert parse_host(name) is None


class TestMetrics:
    def test_only_allowlisted_mounts_are_kept_and_overlays_are_counted(self) -> None:
        rows = [
            metric("DiskUsage", "/store", 0.63),
            metric("DiskUsage", "/", 0.42),
            metric("DiskUsage", "overlay", 0.42),
            metric("DiskUsage", "/store/docker-data/containers/abc/mounts/shm", 0.0),
            metric("DiskUsage", None, 0.9),
        ]
        folded = fold_metrics(rows, mounts=MOUNTS)
        assert [(b.metric, b.element) for b in folded.buckets] == [
            ("DiskUsage", "/"),
            ("DiskUsage", "/store"),
        ]
        assert folded.dropped_mounts == 3

    def test_a_metric_outside_the_fixed_list_is_dropped_and_counted(self) -> None:
        rows = [
            metric("ProcessCPU", None, 5.0),
            metric(None, None, 1.0),
            metric("SystemCPU", None, 31),
        ]
        folded = fold_metrics(rows, mounts=MOUNTS)
        assert [b.metric for b in folded.buckets] == ["SystemCPU"]
        assert folded.dropped_metrics == 2

    def test_a_name_without_a_hostname_is_counted_once_per_name_and_never_guessed(self) -> None:
        rows = [
            metric("SystemCPU", None, 1, source="Health Metrics-9"),
            metric("LoadAvg5", None, 1, source="Health Metrics-9"),
            metric("SystemCPU", None, 1, source=None),
            metric("SystemCPU", None, 2),
        ]
        folded = fold_metrics(rows, mounts=MOUNTS)
        assert folded.unparseable_hosts == 2
        assert {b.host for b in folded.buckets} == {"qradarce2"}

    def test_a_null_value_is_not_a_zero(self) -> None:
        folded = fold_metrics([metric("CREQueueSize", None, None, avg=None)], mounts=MOUNTS)
        assert folded.buckets == ()
        assert folded.null_values == 1

    def test_a_real_zero_is_kept_as_a_measurement(self) -> None:
        folded = fold_metrics(
            [metric("QueuedEventThrottleFilterDiskSize", None, 0.0)], mounts=MOUNTS
        )
        assert [(b.metric, b.max_value) for b in folded.buckets] == [
            ("QueuedEventThrottleFilterDiskSize", 0.0)
        ]
        assert folded.null_values == 0

    @pytest.mark.parametrize(
        ("metric_id", "element", "value"),
        [
            ("SystemCPU", None, -1.0),
            ("DiskUsage", "/", 1.7),
            ("SystemCPU", None, 140.0),
            ("EventParserThreadInfo", "RunnableRatio", 250.0),
        ],
    )
    def test_a_negative_or_out_of_range_value_is_invalid(
        self, metric_id: str, element: str | None, value: float
    ) -> None:
        folded = fold_metrics([metric(metric_id, element, value)], mounts=MOUNTS)
        assert folded.buckets == ()
        assert folded.invalid_values == 1

    def test_float_noise_on_a_bound_is_tolerated(self) -> None:
        folded = fold_metrics([metric("DiskUsage", "/", 1.0000001)], mounts=MOUNTS)
        assert len(folded.buckets) == 1

    def test_two_log_sources_of_one_host_merge_with_a_weighted_average(self) -> None:
        rows = [
            metric("SystemCPU", None, 10.0, avg=10.0, samples=30),
            metric("SystemCPU", None, 50.0, avg=20.0, samples=10),
        ]
        [bucket] = fold_metrics(rows, mounts=MOUNTS).buckets
        assert bucket.max_value == 50.0
        assert bucket.samples == 40
        assert bucket.avg_value == pytest.approx(12.5)

    def test_a_merge_with_an_unknown_average_has_no_average(self) -> None:
        rows = [
            metric("SystemCPU", None, 10.0, avg=10.0),
            HealthMetricRow(source=HOST, metric="SystemCPU", max_value=5.0, samples=2),
        ]
        [bucket] = fold_metrics(rows, mounts=MOUNTS).buckets
        assert bucket.avg_value is None

    def test_a_non_disk_metric_ignores_its_element(self) -> None:
        folded = fold_metrics([metric("SystemCPU", "cpu0", 12.0)], mounts=MOUNTS)
        assert [(b.metric, b.element) for b in folded.buckets] == [("SystemCPU", "")]

    def test_a_stage_keeps_only_the_runnable_share_and_skips_the_other_states(self) -> None:
        states = (
            "BlockedRatio", "CpuUtil", "NewRatio", "RunnableRatio", "TerminatingRatio",
            "TimedWaitRatio", "WaitingRatio",
        )
        idle = {"WaitingRatio": 100.0, "RunnableRatio": 0.0}
        busy = {"RunnableRatio": 80.0, "WaitingRatio": 20.0}
        rows = [
            metric(stage, state, shares.get(state, 0.0))
            for stage, shares in (("AggOutThreadInfo", idle), ("WorkerThreadInfo", busy))
            for state in states
        ]
        folded = fold_metrics(rows, mounts=MOUNTS)
        assert [(b.metric, b.element, b.max_value) for b in folded.buckets] == [
            ("AggOutThreadInfo", "RunnableRatio", 0.0),
            ("WorkerThreadInfo", "RunnableRatio", 80.0),
        ]
        assert folded.dropped_metrics == 0 and folded.invalid_values == 0
        assert folded.dropped_stage_states == 0 and folded.null_values == 0

    @pytest.mark.parametrize("element", [None, "", "SpinningRatio"])
    def test_a_stage_row_with_no_known_state_is_counted_and_not_stored(
        self, element: str | None
    ) -> None:
        folded = fold_metrics([metric("AggOutThreadInfo", element, 100.0)], mounts=MOUNTS)
        assert folded.buckets == ()
        assert folded.dropped_stage_states == 1

    def test_a_stage_runnable_share_is_still_bounded_and_never_null(self) -> None:
        folded = fold_metrics(
            [
                metric("AggOutThreadInfo", "RunnableRatio", None),
                metric("WorkerThreadInfo", "RunnableRatio", -1.0),
            ],
            mounts=MOUNTS,
        )
        assert folded.buckets == ()
        assert (folded.null_values, folded.invalid_values) == (1, 1)

    def test_an_empty_mount_list_keeps_no_disk_row(self) -> None:
        folded = fold_metrics([metric("DiskUsage", "/", 0.4)], mounts=())
        assert folded.buckets == ()


class TestNotifications:
    def test_the_top_is_fifty(self) -> None:
        assert NOTIFICATION_TOP == 50

    def row(
        self, qid: str | None, events: int, *, source: str | None = None,
        severity: int | None = 4, seen: datetime | None = None,
    ) -> HealthNotificationRow:
        return HealthNotificationRow(
            source=source or "System Notification-2 :: qradarce2",
            qid_name=qid,
            events=events,
            max_severity=severity,
            last_seen=seen,
        )

    def test_the_top_fifty_are_kept_and_the_rest_is_a_counted_remainder(self) -> None:
        rows = [self.row(f"Message {n:03d}", 1000 - n) for n in range(NOTIFICATION_TOP + 7)]
        folded = fold_notifications(rows)
        assert len(folded.entries) == NOTIFICATION_TOP
        assert folded.entries[0].qid_name == "Message 000"
        assert folded.remainder_groups == 7
        assert folded.remainder_events == sum(1000 - n for n in range(NOTIFICATION_TOP, 57))
        total = sum(row.events for row in rows)
        assert sum(e.events for e in folded.entries) + folded.remainder_events == total

    def test_fifty_groups_or_fewer_have_no_remainder(self) -> None:
        folded = fold_notifications([self.row(f"M{n}", 1) for n in range(NOTIFICATION_TOP)])
        assert folded.remainder_groups == 0 and folded.remainder_events == 0

    def test_information_message_is_never_kept(self) -> None:
        folded = fold_notifications([self.row("Information Message", 99), self.row("Warning", 1)])
        assert [e.qid_name for e in folded.entries] == ["Warning"]
        assert folded.remainder_events == 0

    def test_the_same_group_from_two_sources_sums_and_keeps_the_worst(self) -> None:
        early = datetime(2026, 11, 3, 10, 0, tzinfo=UTC)
        late = datetime(2026, 11, 3, 10, 5, tzinfo=UTC)
        folded = fold_notifications(
            [
                self.row("Warning", 2, source="System Notification-2 :: a", severity=3, seen=early),
                self.row("Warning", 5, source="System Notification-3 :: a", severity=6, seen=late),
            ]
        )
        [entry] = folded.entries
        assert (entry.events, entry.max_severity, entry.last_seen) == (7, 6, late)

    def test_an_unparseable_host_or_no_name_is_dropped_and_counted(self) -> None:
        folded = fold_notifications(
            [
                self.row("Warning", 4, source="System Notification-2"),
                self.row(None, 3),
                self.row("Error", 1),
            ]
        )
        assert [e.qid_name for e in folded.entries] == ["Error"]
        assert folded.unparseable_hosts == 1
        assert folded.dropped_events == 7

    def test_ties_break_by_host_then_name_so_the_top_is_stable(self) -> None:
        rows = [self.row("B", 5), self.row("A", 5)]
        assert [e.qid_name for e in fold_notifications(rows).entries] == ["A", "B"]


class TestParsers:
    def test_a_row_that_is_not_an_object_fails_the_search(self) -> None:
        with pytest.raises(ProviderError):
            parse_health_metric_rows(["x"], max_rows=10)
        with pytest.raises(ProviderError):
            parse_health_notification_rows([None], max_rows=10)

    @pytest.mark.parametrize("samples", [None, -1, "many"])
    def test_a_malformed_count_fails_the_search(self, samples: object) -> None:
        with pytest.raises(ProviderError):
            parse_health_metric_rows([{"samples": samples}], max_rows=10)

    def test_a_value_that_is_not_a_finite_number_is_missing_not_zero(self) -> None:
        rows, truncated = parse_health_metric_rows(
            [
                {"metric": "A", "max_value": "n/a", "avg_value": float("nan"), "samples": 1},
                {"metric": "B", "max_value": None, "avg_value": None, "samples": 1},
                {"metric": "C", "max_value": "0.5", "avg_value": True, "samples": 1},
            ],
            max_rows=10,
        )
        assert not truncated
        assert [(r.max_value, r.avg_value) for r in rows] == [
            (None, None),
            (None, None),
            (0.5, None),
        ]

    def test_more_rows_than_the_cap_is_truncation(self) -> None:
        raw = [{"metric": "A", "samples": 1}] * 4
        rows, truncated = parse_health_metric_rows(raw, max_rows=3)
        assert len(rows) == 3 and truncated

    def test_a_severity_outside_zero_to_ten_is_unknown(self) -> None:
        rows, _ = parse_health_notification_rows(
            [
                {"qid_name": "A", "severity": 11, "events": 1},
                {"qid_name": "B", "severity": 5, "events": 1},
            ],
            max_rows=10,
        )
        assert [r.max_severity for r in rows] == [None, 5]
