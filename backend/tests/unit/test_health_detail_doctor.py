"""The `qradar_disk` and `qradar_pipeline` doctor lines and the disk thresholds (HLT-2)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest

from app.cli import doctor as doctor_module
from app.cli.doctor import Doctor, Status
from app.cli.doctor_qradar_health import disk_line, pipeline_line
from app.core.config import Settings
from app.schemas.qradar_health_detail import (
    ConsoleLatest,
    HostDisk,
    HostHealth,
    HostPipeline,
    HostSystem,
    LatestOut,
    MetricCell,
    MountValue,
    PartState,
    PartStatus,
)
from app.services.qradar_health_detail import disk_level, host_health, is_stale

KEY = "sxvVvbfjEG8mA0m2m6b1cQ2E0N4l7rXqO4uJ6c8zY5A="
NOW = datetime(2026, 11, 3, 12, 20, tzinfo=UTC)
SETTINGS = Settings(encryption_key=KEY)


def ok(value: float) -> MetricCell:
    return MetricCell(state=PartState.OK, value=value, average=value, samples=5)


def missing() -> MetricCell:
    return MetricCell(state=PartState.NOT_COLLECTED, reason_code="x", reason="not reported")


def host(
    name: str = "qr1",
    *,
    fraction: float | None = 0.5,
    mount: str = "/store",
    queue: MetricCell | None = None,
    spill: MetricCell | None = None,
) -> HostHealth:
    value = (
        MountValue(
            mount=mount,
            used_fraction=fraction,
            used_percent=fraction * 100,
            free_bytes=2.0e9,
            level=disk_level(fraction, SETTINGS),
        )
        if fraction is not None
        else None
    )
    disk = (
        HostDisk(state=PartState.OK, fullest=value, mounts=[value])
        if value is not None
        else HostDisk(state=PartState.NOT_COLLECTED, reason_code="x", reason="not reported")
    )
    return HostHealth(
        host=name,
        disk=disk,
        system=HostSystem(
            cpu_percent=ok(1), memory_percent=ok(1), load_avg_5=ok(1), swap_percent=ok(0),
            io_wait=ok(0), running_queries=ok(1), host_running_queries=ok(1),
        ),
        pipeline=HostPipeline(
            cre_queue=queue or ok(100),
            throttle_spill_bytes=spill or ok(0),
            throttle_spilling=None,
            ect_tcp_to_ep_disk_bytes=ok(0),
            flow_governor_queue_disk_bytes=ok(0),
        ),
    )


def console(
    hosts: list[HostHealth],
    *,
    state: PartState = PartState.OK,
    last: datetime | None = NOW - timedelta(minutes=20),
    name: str = "lab",
) -> ConsoleLatest:
    status = (
        PartStatus(state=state)
        if state is PartState.OK
        else PartStatus(state=state, reason_code="r", reason="the reason")
    )
    return ConsoleLatest(
        instance_id=uuid.uuid4(),
        instance_name=name,
        metrics=status,
        notifications=PartStatus(state=PartState.OK),
        hosts=hosts if state in (PartState.OK, PartState.PARTIAL, PartState.STALE) else [],
        last_metrics_window_end=last,
    )


def latest(*consoles: ConsoleLatest) -> LatestOut:
    return LatestOut(generated_at=NOW, consoles=list(consoles))


class TestThresholds:
    @pytest.mark.parametrize(
        ("fraction", "level"),
        [(None, None), (0.0, "OK"), (0.8499, "OK"), (0.85, "WARN"), (0.9499, "WARN"),
         (0.95, "FAIL"), (1.0, "FAIL")],
    )
    def test_disk_levels_follow_the_settings(
        self, fraction: float | None, level: str | None
    ) -> None:
        assert disk_level(fraction, SETTINGS) == level

    def test_the_levels_move_with_the_settings(self) -> None:
        tight = Settings(
            encryption_key=KEY,
            qradar_health_disk_warn_percent=50,
            qradar_health_disk_fail_percent=60,
        )
        assert disk_level(0.55, tight) == "WARN" and disk_level(0.6, tight) == "FAIL"

    def test_a_window_is_stale_after_the_configured_cadences_plus_the_settle_time(self) -> None:
        # 3 cadences of 900 s and the 120 s settle time: 2820 s.
        end = NOW - timedelta(seconds=2820)
        assert is_stale(end, 900, NOW, SETTINGS) is False
        assert is_stale(end - timedelta(seconds=1), 900, NOW, SETTINGS) is True


class TestDiskLine:
    def test_below_the_warning_share_passes_and_names_the_fullest_mount(self) -> None:
        word, text = disk_line(latest(console([host(fraction=0.5)])), SETTINGS)
        assert word == "PASS" and "qr1 /store 50.0 %" in text

    def test_at_the_warning_share_warns_naming_host_and_mount(self) -> None:
        word, text = disk_line(
            latest(console([host("qr2", fraction=0.86, mount="/var/log")])), SETTINGS
        )
        assert word == "WARN"
        assert "qr2 /var/log 86.0 %" in text and "85" in text

    def test_at_the_failing_share_fails(self) -> None:
        word, text = disk_line(latest(console([host("qr2", fraction=0.95)])), SETTINGS)
        assert word == "FAIL" and "qr2 /store 95.0 %" in text

    def test_the_worst_host_decides_and_all_are_named(self) -> None:
        word, text = disk_line(
            latest(
                console(
                    [host("a", fraction=0.2), host("b", fraction=0.9), host("c", fraction=0.97)]
                )
            ),
            SETTINGS,
        )
        assert word == "FAIL" and "c /store" in text and "b /store" in text

    @pytest.mark.parametrize(
        "state", [PartState.NOT_COLLECTED, PartState.FAILED, PartState.STALE, PartState.PARTIAL]
    )
    def test_a_window_that_is_not_whole_is_a_warn_that_says_since_when(
        self, state: PartState
    ) -> None:
        word, text = disk_line(latest(console([host(fraction=0.1)], state=state)), SETTINGS)
        assert word == "WARN"
        assert "not collected since 2026-11-03 12:00 UTC" in text

    def test_a_console_that_never_had_a_window_is_a_warn_never_a_pass(self) -> None:
        never = console([], state=PartState.NOT_COLLECTED, last=None)
        word, text = disk_line(latest(never), SETTINGS)
        assert word == "WARN" and "not collected yet" in text

    def test_a_host_with_no_allowlisted_mount_is_unread_not_fine(self) -> None:
        word, text = disk_line(latest(console([host(fraction=None)])), SETTINGS)
        assert word == "WARN" and "reported no allowlisted mount" in text

    def test_a_failing_disk_beside_a_stale_console_still_fails(self) -> None:
        word, _ = disk_line(
            latest(console([host(fraction=0.99)]), console([], state=PartState.FAILED, name="b")),
            SETTINGS,
        )
        assert word == "FAIL"

    def test_no_console_is_skipped(self) -> None:
        assert disk_line(latest(), SETTINGS)[0] == "SKIP"


class TestPipelineLine:
    def test_no_spill_and_a_short_queue_pass(self) -> None:
        word, text = pipeline_line(latest(console([host(queue=ok(120))])), SETTINGS)
        assert word == "PASS" and "CRE queue 120" in text

    def test_a_throttle_spill_above_zero_warns(self) -> None:
        word, text = pipeline_line(latest(console([host(spill=ok(5_000_000))])), SETTINGS)
        assert word == "WARN" and "qr1 spilled" in text

    def test_a_queue_above_the_setting_warns_and_at_the_setting_does_not(self) -> None:
        over = pipeline_line(latest(console([host(queue=ok(10_001))])), SETTINGS)
        at = pipeline_line(latest(console([host(queue=ok(10_000))])), SETTINGS)
        assert over[0] == "WARN" and "CRE queue 10001" in over[1]
        assert at[0] == "PASS"

    def test_the_queue_setting_is_honoured(self) -> None:
        small = Settings(encryption_key=KEY, qradar_health_cre_queue_warn=50)
        assert pipeline_line(latest(console([host(queue=ok(51))])), small)[0] == "WARN"

    def test_a_figure_that_was_not_read_is_a_warn_not_a_pass(self) -> None:
        word, text = pipeline_line(latest(console([host(queue=missing())])), SETTINGS)
        assert word == "WARN" and "CRE queue not read" in text
        word, text = pipeline_line(latest(console([host(spill=missing())])), SETTINGS)
        assert word == "WARN" and "throttle spill not read" in text

    @pytest.mark.parametrize(
        "state", [PartState.NOT_COLLECTED, PartState.FAILED, PartState.STALE, PartState.PARTIAL]
    )
    def test_a_window_that_is_not_whole_is_a_warn_that_says_since_when(
        self, state: PartState
    ) -> None:
        word, text = pipeline_line(latest(console([host()], state=state)), SETTINGS)
        assert word == "WARN" and "not collected since 2026-11-03 12:00 UTC" in text


class TestHostCells:
    def test_the_fullest_mount_has_the_free_bytes_only_when_both_figures_were_read(self) -> None:
        from app.models.qradar_health import QRadarHealthMetricBucket as Row

        def row(metric: str, element: str, value: float) -> Row:
            return Row(
                instance_id=uuid.uuid4(), bucket_start=NOW, bucket_end=NOW, host="h",
                metric=metric, element=element, max_value=value, avg_value=value, samples=3,
            )

        rows = {
            ("DiskUsage", "/store"): row("DiskUsage", "/store", 0.9),
            ("DiskSpaceUsed", "/store"): row("DiskSpaceUsed", "/store", 90.0),
            ("DiskSpaceTotal", "/store"): row("DiskSpaceTotal", "/store", 100.0),
            ("DiskUsage", "/"): row("DiskUsage", "/", 0.2),
        }
        health = host_health(rows, "h", PartState.OK, SETTINGS)
        assert health.disk.fullest is not None
        assert (health.disk.fullest.mount, health.disk.fullest.free_bytes) == ("/store", 10.0)
        root = next(m for m in health.disk.mounts if m.mount == "/")
        assert root.free_bytes is None and root.total_bytes is None
        assert health.system.cpu_percent.state is PartState.NOT_COLLECTED
        assert health.system.cpu_percent.value is None
        assert health.pipeline.throttle_spilling is None
        assert health.pipeline.busiest_stage is None


class TestBusiestStage:
    @staticmethod
    def _rows(*items: tuple[str, str, float]):  # type: ignore[no-untyped-def]
        from app.models.qradar_health import QRadarHealthMetricBucket as Row

        return {
            (metric, element): Row(
                instance_id=uuid.uuid4(), bucket_start=NOW, bucket_end=NOW, host="h",
                metric=metric, element=element, max_value=value, avg_value=value, samples=3,
            )
            for metric, element, value in items
        }

    def test_the_busiest_stage_is_the_highest_runnable_share(self) -> None:
        rows = self._rows(
            ("AggOutThreadInfo", "RunnableRatio", 0.0),
            ("WorkerThreadInfo", "RunnableRatio", 80.0),
            ("EventParserThreadInfo", "RunnableRatio", 12.5),
        )
        stage = host_health(rows, "h", PartState.OK, SETTINGS).pipeline.busiest_stage
        assert stage is not None and (stage.stage, stage.busy_percent) == ("Worker", 80.0)

    def test_an_idle_host_shows_a_measured_zero_not_a_hundred(self) -> None:
        rows = self._rows(("AggOutThreadInfo", "RunnableRatio", 0.0))
        stage = host_health(rows, "h", PartState.OK, SETTINGS).pipeline.busiest_stage
        assert stage is not None and stage.busy_percent == 0.0

    def test_a_window_with_only_old_stage_rows_has_no_busiest_stage(self) -> None:
        rows = self._rows(("AggOutThreadInfo", "", 100.0), ("WorkerThreadInfo", "", 66.0))
        assert host_health(rows, "h", PartState.OK, SETTINGS).pipeline.busiest_stage is None

    def test_a_host_with_no_stage_rows_has_no_busiest_stage(self) -> None:
        rows = self._rows(("SystemCPU", "", 30.0))
        assert host_health(rows, "h", PartState.OK, SETTINGS).pipeline.busiest_stage is None


class TestDoctorWiring:
    def test_the_checks_are_registered_before_qradar_health_with_their_runbooks(self) -> None:
        ids = list(doctor_module._CHECK_RUNBOOKS)
        assert ("qradar_disk", "OPS-R69") in ids and ("qradar_pipeline", "OPS-R70") in ids
        assert ids[-1] == ("qradar_health", "OPS-R67")
        for check_id, _ in ids:
            assert hasattr(Doctor, f"check_{check_id}")

    @pytest.mark.asyncio
    async def test_the_collection_off_is_a_skip(self) -> None:
        d = Doctor(
            settings=Settings(encryption_key=KEY, qradar_health_collection_enabled=False),
            sessionmaker=cast(Any, lambda: (_ for _ in ()).throw(AssertionError("no session"))),
        )
        assert (await d.check_qradar_disk()).status is Status.SKIP
        assert (await d.check_qradar_pipeline()).status is Status.SKIP
