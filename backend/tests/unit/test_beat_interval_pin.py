"""The Phase A collection beat interval is defined in exactly one place."""

from app.workers.celery_app import (
    ANOMALY_EVALUATION_CADENCE_SECONDS,
    BEAT_INTERVAL_SECONDS,
    celery_app,
)


def test_beat_interval_constant_matches_heartbeat_schedule() -> None:
    assert BEAT_INTERVAL_SECONDS == 60
    assert (
        celery_app.conf.beat_schedule["beat-heartbeat"]["schedule"]
        == float(BEAT_INTERVAL_SECONDS)
    )


def test_beat_interval_constant_matches_metrics_schedule() -> None:
    assert (
        celery_app.conf.beat_schedule["collect-metrics"]["schedule"]
        == float(BEAT_INTERVAL_SECONDS)
    )


def test_evaluation_cadence_is_separate_from_beat_interval() -> None:
    # The doctor uses both terms in its allowance; they must stay separate.
    assert ANOMALY_EVALUATION_CADENCE_SECONDS == 60
    assert BEAT_INTERVAL_SECONDS == ANOMALY_EVALUATION_CADENCE_SECONDS
