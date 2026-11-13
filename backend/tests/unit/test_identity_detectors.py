"""Exit paths for the identity detectors. Pure: no database and no clock."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.anomaly.seasonal import CellSummary
from app.identity.detectors import (
    NOVELTY_HISTORY_DAYS,
    SPIKE_HIGH_RATIO,
    NameEvidence,
    NoveltyHistoryBucket,
    SpikeThresholds,
    detect_new_privileged_account,
    detect_rate_spike,
    detect_threshold,
    verify_novelty_history,
)
from app.models.enums import (
    DetectorSignal,
    IdentityAnomalyType,
    IdentityMeasurement,
    Severity,
)

EXACT = IdentityMeasurement.EXACT
TRUNCATED = IdentityMeasurement.TRUNCATED
MISSING = IdentityMeasurement.NOT_COLLECTED
CLEARED = IdentityAnomalyType.IDENTITY_AUDIT_LOG_CLEARED
CREATED = IdentityAnomalyType.IDENTITY_ACCOUNT_CREATED
DELETED = IdentityAnomalyType.IDENTITY_ACCOUNT_DELETED
GROUP = IdentityAnomalyType.IDENTITY_PRIVILEGED_GROUP_ADD
SPIKE = IdentityAnomalyType.IDENTITY_FAILED_LOGON_SPIKE
LOCKOUT = IdentityAnomalyType.IDENTITY_LOCKOUT_SPIKE
NOVEL = IdentityAnomalyType.IDENTITY_NEW_PRIVILEGED_ACCOUNT
ALICE = NameEvidence(rank=1, username="alice", count=4)
BOB = NameEvidence(rank=2, username="bob", count=2)
START = datetime(2026, 11, 1, tzinfo=UTC)
WIDTH = 300


def _cell(median: float = 1.0, *, mad: float = 0.2, p95: float = 2.0) -> CellSummary:
    return CellSummary(
        median=median,
        mad=mad,
        p05=median,
        p95=p95,
        sample_count=8,
        is_reliable=True,
        observations=[median] * 8,
    )


def _history(
    *names: str,
    start: datetime = START,
    measurement: IdentityMeasurement = EXACT,
    truncated: bool = False,
    catalog: int = 1,
    width: int = WIDTH,
) -> NoveltyHistoryBucket:
    return NoveltyHistoryBucket(
        bucket_start=start,
        names_measurement=measurement,
        admission_truncated=truncated,
        catalog_version=catalog,
        bucket_seconds=width,
        usernames=frozenset(names),
    )


def test_threshold_fires_at_one_for_exact_and_truncated_counts() -> None:
    exact = detect_threshold(DELETED, event_id=4726, count=1, measurement=EXACT)
    cut = detect_threshold(
        CLEARED, event_id=1102, count=4, measurement=TRUNCATED, usernames=(ALICE,)
    )
    created = detect_threshold(CREATED, event_id=4720, count=2, measurement=EXACT)
    group = detect_threshold(GROUP, event_id=4728, count=1, measurement=EXACT)
    assert exact.signal is DetectorSignal.ANOMALOUS
    assert exact.insufficient is False
    assert exact.severity is Severity.HIGH
    assert cut.signal is DetectorSignal.ANOMALOUS
    assert cut.usernames == (ALICE,)
    assert created.severity is Severity.MEDIUM
    assert group.severity is Severity.HIGH
    assert "alice" not in exact.reason


def test_threshold_exact_zero_is_healthy_and_a_cut_below_one_is_not() -> None:
    healthy = detect_threshold(DELETED, event_id=4726, count=0, measurement=EXACT)
    floor = detect_threshold(DELETED, event_id=4726, count=0, measurement=TRUNCATED)
    assert healthy.signal is DetectorSignal.HEALTHY
    assert healthy.insufficient is False
    assert floor.signal is DetectorSignal.UNKNOWN
    assert floor.insufficient is True
    assert floor.signal is not DetectorSignal.HEALTHY


def test_threshold_not_collected_and_a_missing_count_are_insufficient() -> None:
    missing = detect_threshold(CLEARED, event_id=1102, count=None, measurement=MISSING)
    empty = detect_threshold(GROUP, event_id=4732, count=None, measurement=EXACT)
    assert missing.insufficient is True
    assert missing.signal is DetectorSignal.UNKNOWN
    assert empty.insufficient is True
    assert "not collected" in missing.reason


def test_novelty_fires_only_for_a_name_absent_from_a_whole_history() -> None:
    fresh = detect_new_privileged_account(
        event_id=4728,
        measurement=EXACT,
        admission_truncated=False,
        history_verified=True,
        known_names=frozenset({"alice"}),
        usernames=(ALICE, BOB),
    )
    known = detect_new_privileged_account(
        event_id=4728,
        measurement=EXACT,
        admission_truncated=False,
        history_verified=True,
        known_names=frozenset({"alice", "bob"}),
        usernames=(ALICE, BOB),
    )
    empty = detect_new_privileged_account(
        event_id=4756,
        measurement=EXACT,
        admission_truncated=False,
        history_verified=True,
        known_names=frozenset({"alice"}),
        usernames=(),
    )
    assert fresh.signal is DetectorSignal.ANOMALOUS
    assert fresh.severity is Severity.HIGH
    assert fresh.usernames == (BOB,)
    assert "bob" not in fresh.reason
    assert known.signal is DetectorSignal.HEALTHY
    assert empty.signal is DetectorSignal.HEALTHY
    assert empty.insufficient is False


def test_novelty_refuses_a_cut_list_and_an_incomplete_history() -> None:
    cut = detect_new_privileged_account(
        event_id=4728,
        measurement=EXACT,
        admission_truncated=True,
        history_verified=True,
        known_names=frozenset(),
        usernames=(BOB,),
    )
    gap = detect_new_privileged_account(
        event_id=4732,
        measurement=EXACT,
        admission_truncated=False,
        history_verified=False,
        known_names=frozenset(),
        usernames=(BOB,),
    )
    uncollected = detect_new_privileged_account(
        event_id=4756,
        measurement=MISSING,
        admission_truncated=False,
        history_verified=True,
        known_names=frozenset(),
        usernames=(BOB,),
    )
    truncated_names = detect_new_privileged_account(
        event_id=4728,
        measurement=TRUNCATED,
        admission_truncated=False,
        history_verified=True,
        known_names=frozenset(),
        usernames=(BOB,),
    )
    for verdict in (cut, gap, uncollected, truncated_names):
        assert verdict.signal is DetectorSignal.UNKNOWN
        assert verdict.insufficient is True
        assert verdict.usernames == ()


def test_novelty_history_requires_every_bucket_whole() -> None:
    end = START + timedelta(seconds=WIDTH * 2)
    whole = (
        _history("alice", start=START),
        _history("bob", start=START + timedelta(seconds=WIDTH)),
    )
    verified, names = verify_novelty_history(
        whole, window_start=START, window_end=end, bucket_seconds=WIDTH, catalog_version=1
    )
    assert verified is True
    assert names == frozenset({"alice", "bob"})

    gapped, _ = verify_novelty_history(
        whole[:1], window_start=START, window_end=end, bucket_seconds=WIDTH, catalog_version=1
    )
    cut_bucket = (
        whole[0],
        _history("bob", start=START + timedelta(seconds=WIDTH), truncated=True),
    )
    cut, _ = verify_novelty_history(
        cut_bucket, window_start=START, window_end=end, bucket_seconds=WIDTH, catalog_version=1
    )
    assert gapped is False
    assert cut is False
    assert NOVELTY_HISTORY_DAYS == 7


def test_spike_exact_count_inside_the_band_is_healthy() -> None:
    verdict = detect_rate_spike(
        SPIKE,
        event_id=4625,
        count=1.2,
        measurement=EXACT,
        cell=_cell(),
        cold=False,
        thresholds=SpikeThresholds(),
        usernames=(ALICE,),
    )
    assert verdict.signal is DetectorSignal.HEALTHY
    assert verdict.insufficient is False


def test_spike_cold_band_is_never_healthy_including_an_exact_zero() -> None:
    cold = detect_rate_spike(
        SPIKE,
        event_id=4625,
        count=0,
        measurement=EXACT,
        cell=None,
        cold=True,
        thresholds=SpikeThresholds(),
    )
    unready = detect_rate_spike(
        LOCKOUT,
        event_id=4740,
        count=9,
        measurement=EXACT,
        cell=None,
        cold=False,
        thresholds=SpikeThresholds(),
    )
    assert cold.signal is DetectorSignal.UNKNOWN
    assert cold.insufficient is True
    assert cold.signal is not DetectorSignal.HEALTHY
    assert unready.insufficient is True


def test_spike_not_collected_is_insufficient() -> None:
    verdict = detect_rate_spike(
        SPIKE,
        event_id=4625,
        count=None,
        measurement=MISSING,
        cell=_cell(),
        cold=False,
        thresholds=SpikeThresholds(),
    )
    assert verdict.insufficient is True
    assert verdict.signal is DetectorSignal.UNKNOWN


def test_spike_lower_bound_inside_the_band_does_not_count_as_healthy() -> None:
    verdict = detect_rate_spike(
        SPIKE,
        event_id=4625,
        count=1.0,
        measurement=TRUNCATED,
        cell=_cell(),
        cold=False,
        thresholds=SpikeThresholds(),
    )
    assert verdict.insufficient is True
    assert verdict.signal is not DetectorSignal.HEALTHY


def test_spike_lower_bound_above_the_band_fires() -> None:
    verdict = detect_rate_spike(
        LOCKOUT,
        event_id=4740,
        count=10,
        measurement=TRUNCATED,
        cell=_cell(),
        cold=False,
        thresholds=SpikeThresholds(),
        usernames=(ALICE,),
    )
    assert verdict.signal is DetectorSignal.ANOMALOUS
    assert verdict.insufficient is False
    assert verdict.ratio is not None
    assert verdict.ratio >= SPIKE_HIGH_RATIO
    assert verdict.severity is Severity.HIGH
    assert verdict.usernames == (ALICE,)
    assert "alice" not in verdict.reason


def test_spike_exact_rise_below_four_is_medium() -> None:
    verdict = detect_rate_spike(
        SPIKE,
        event_id=4625,
        count=3,
        measurement=EXACT,
        cell=_cell(median=1.0, mad=0.05, p95=1.2),
        cold=False,
        thresholds=SpikeThresholds(),
    )
    assert verdict.signal is DetectorSignal.ANOMALOUS
    assert verdict.severity is Severity.MEDIUM
