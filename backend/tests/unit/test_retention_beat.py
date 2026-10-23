"""Daily beat entries for the D-36 deleters, and the doctor catalog matches the beat."""

from __future__ import annotations

import inspect

from app.cli.doctor import BATCH_DELETERS
from app.core.config import get_settings
from app.models.enums import AnomalyState
from app.services.ips_anomaly_retention import DELETE_SQL as IPS_DELETE_SQL
from app.services.ips_anomaly_retention import TERMINAL_STATES as IPS_TERMINAL
from app.services.log_source_anomaly_retention import DELETE_SQL as PHASE_A_DELETE_SQL
from app.services.log_source_anomaly_retention import TERMINAL_STATES as PHASE_A_TERMINAL
from app.workers import tasks
from app.workers.celery_app import _send_startup_tasks_at_beat_start, celery_app


def test_new_retention_tasks_are_daily_beats() -> None:
    settings = get_settings()
    expected = {
        "apply-log-source-anomaly-retention": (
            "apply_log_source_anomaly_retention",
            settings.log_source_anomaly_retention_interval_seconds,
            tasks.apply_log_source_anomaly_retention_task,
        ),
        "apply-ips-anomaly-retention": (
            "apply_ips_anomaly_retention",
            settings.ips_anomaly_retention_interval_seconds,
            tasks.apply_ips_anomaly_retention_task,
        ),
        "apply-snapshot-retention": (
            "apply_snapshot_retention",
            settings.snapshot_retention_interval_seconds,
            tasks.apply_snapshot_retention_task,
        ),
    }
    for key, (name, interval, shim) in expected.items():
        entry = celery_app.conf.beat_schedule[key]
        assert entry["task"] == name
        assert entry["schedule"] == float(interval)
        assert interval == 86400
        assert shim.name == name
        assert name in celery_app.tasks


def test_beat_start_does_not_send_the_new_deleters() -> None:
    source = inspect.getsource(_send_startup_tasks_at_beat_start)
    assert "apply_timescale_policies" in source
    assert "beat_heartbeat" in source
    for name in (
        "apply_log_source_anomaly_retention",
        "apply_ips_anomaly_retention",
        "apply_snapshot_retention",
    ):
        assert name not in source


def test_incident_sql_names_terminal_states_and_both_reference_guards() -> None:
    live = (
        AnomalyState.SUPPRESSED.value,
        AnomalyState.CANDIDATE.value,
        AnomalyState.OPEN.value,
        AnomalyState.RECOVERING.value,
    )
    for sql, states in (
        (PHASE_A_DELETE_SQL, PHASE_A_TERMINAL),
        (IPS_DELETE_SQL, IPS_TERMINAL),
    ):
        for state in states:
            assert f"'{state}'" in sql
        for state in live:
            assert state not in sql
        assert sql.count("NOT EXISTS") == 2
        assert "jsonb_exists(al.source_anomaly_ids, a.id::text)" in sql
        assert "status <> 'RESOLVED'" not in sql
        assert "al.status" not in sql
    assert "log_source_detector_state" in PHASE_A_DELETE_SQL
    assert "ips_detector_state" in IPS_DELETE_SQL
    assert "subject_kind" not in IPS_DELETE_SQL


def test_catalog_names_every_beat_task_whose_name_contains_retention() -> None:
    scheduled = {
        entry["task"]
        for entry in celery_app.conf.beat_schedule.values()
        if "retention" in str(entry["task"])
    }
    catalog = {deleter.task for deleter in BATCH_DELETERS}
    assert scheduled == catalog
    assert "apply_timescale_policies" not in catalog
