"""The catch-all fold: sanitization, admission, order, the top bound, LOWER_BOUND."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.catchall.fold import (
    IDENTIFIER_MAX,
    CatchallFoldError,
    SenderLedger,
    fold_source_bucket,
    sanitize_identifier,
)
from app.models.enums import CatchallMeasurement as M
from app.providers.dto import CatchallSenderRow
from app.waf.admission import AdmissionPolicy

T = datetime(2026, 10, 13, 9, 0, tzinfo=UTC)
POLICY = AdmissionPolicy(max_new_per_bucket=20, max_active=2000, min_requests=3)


def r(sender: str | None, events: int = 10, records: int = 1) -> CatchallSenderRow:
    return CatchallSenderRow(
        qradar_log_source_id=1, sender=sender, bucket_start=T, events=events, records=records
    )


def fold(rows, ledger=None, *, policy=POLICY, top_n=20, measurement=M.EXACT):
    return fold_source_bucket(
        rows, ledger or SenderLedger(), measurement=measurement, policy=policy, top_n=top_n
    )


class TestSanitize:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("fw-01", "fw-01"),
            ("  fw-01 \t", "fw-01"),
            ("host name", "host name"),
            ("ünï-host", "ünï-host"),
            ("a" * IDENTIFIER_MAX, "a" * IDENTIFIER_MAX),
        ],
    )
    def test_a_printable_value_is_kept_stripped(self, raw: str, expected: str) -> None:
        assert sanitize_identifier(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        [None, "", "   ", "a\nb", "a\x00b", "a\tb", "\x1b[31mred", "a" * (IDENTIFIER_MAX + 1)],
    )
    def test_anything_else_is_not_a_sender(self, raw: str | None) -> None:
        assert sanitize_identifier(raw) is None


class TestFold:
    def test_invalid_senders_are_counted_untracked_and_never_named(self) -> None:
        fact = fold([r("ok", 10), r("bad\nname", 7), r(None, 4), r("   ", 2)])
        assert fact.events == 23 and fact.records == 4
        assert fact.untracked_senders == 3 and fact.untracked_events == 13
        assert fact.distinct_senders == 4
        assert [name for name, _ in fact.top_senders] == ["ok"]
        assert set(fact.tracked_events) == {"ok"}

    def test_the_same_sender_in_many_rows_is_merged(self) -> None:
        fact = fold([r("a", 2), r("a ", 2), r("a", 1)])
        assert fact.tracked_events == {"a": 5}
        assert fact.distinct_senders == 1

    def test_a_sender_below_the_threshold_is_refused_and_counted(self) -> None:
        fact = fold([r("big", 3), r("small", 2)])
        assert set(fact.tracked_events) == {"big"}
        assert fact.below_admission_threshold == 1
        assert (fact.untracked_senders, fact.untracked_events) == (1, 2)

    def test_the_threshold_is_inclusive(self) -> None:
        assert fold([r("edge", 3)]).admitted == 1
        assert fold([r("edge", 2)]).admitted == 0

    def test_the_per_bucket_quota_keeps_the_busiest_and_counts_the_rest(self) -> None:
        policy = AdmissionPolicy(max_new_per_bucket=2, max_active=2000, min_requests=3)
        fact = fold([r("c", 5), r("a", 9), r("b", 7), r("d", 6)], policy=policy)
        assert fact.admitted_identifiers == ("a", "b")
        assert fact.rejected_over_quota == 2
        assert (fact.untracked_senders, fact.untracked_events) == (2, 11)
        assert "c" not in fact.tracked_events and "d" not in fact.tracked_events

    def test_the_active_ceiling_bounds_what_a_source_tracks(self) -> None:
        policy = AdmissionPolicy(max_new_per_bucket=20, max_active=3, min_requests=3)
        ledger = SenderLedger(active={"old1", "old2"})
        fact = fold([r("n1", 9), r("n2", 8)], ledger, policy=policy)
        assert fact.admitted_identifiers == ("n1",)
        assert fact.rejected_over_quota == 1
        assert ledger.active == {"old1", "old2", "n1"}

    def test_a_full_ceiling_admits_nobody_but_still_records_the_tracked(self) -> None:
        policy = AdmissionPolicy(max_new_per_bucket=20, max_active=1, min_requests=3)
        fact = fold([r("known", 1), r("new", 50)], SenderLedger(active={"known"}), policy=policy)
        assert fact.admitted == 0 and fact.tracked_events == {"known": 1}
        assert fact.rejected_over_quota == 1

    def test_a_tracked_sender_is_kept_whatever_the_quota_or_threshold(self) -> None:
        policy = AdmissionPolicy(max_new_per_bucket=0, max_active=1, min_requests=100)
        fact = fold([r("known", 1)], SenderLedger(active={"known"}), policy=policy)
        assert fact.tracked_events == {"known": 1}
        assert fact.admitted == 0 and fact.untracked_senders == 0

    def test_a_flood_of_new_names_cannot_displace_a_tracked_sender(self) -> None:
        policy = AdmissionPolicy(max_new_per_bucket=3, max_active=2000, min_requests=3)
        flood = [r(f"x{i:03d}", 1000 + i) for i in range(200)]
        fact = fold([*flood, r("known", 3)], SenderLedger(active={"known"}), policy=policy)
        assert "known" in fact.tracked_events
        assert fact.admitted == 3 and fact.rejected_over_quota == 197

    def test_the_order_is_deterministic_whatever_the_input_order(self) -> None:
        policy = AdmissionPolicy(max_new_per_bucket=2, max_active=2000, min_requests=3)
        rows = [r("d", 5), r("b", 5), r("a", 5), r("c", 5), r("z", 9)]
        forward = fold(rows, policy=policy)
        backward = fold(list(reversed(rows)), policy=policy)
        assert forward.admitted_identifiers == backward.admitted_identifiers == ("a", "z")
        assert forward == backward

    def test_a_refused_sender_is_never_stored_by_name(self) -> None:
        policy = AdmissionPolicy(max_new_per_bucket=1, max_active=2000, min_requests=3)
        ledger = SenderLedger()
        fact = fold([r("kept", 9), r("quota-refused", 8), r("small", 1), r("bad\x00", 5)],
                    ledger, policy=policy)
        refused = {"quota-refused", "small", "bad\x00", "bad"}
        assert not refused & ledger.active
        assert not refused & set(fact.tracked_events)
        assert not refused & {name for name, _ in fact.top_senders}
        assert not refused & set(fact.admitted_identifiers)

    def test_the_top_list_is_bounded_busiest_first_with_a_name_tie_break(self) -> None:
        rows = [r(f"s{i:02d}", 10 + (i % 3)) for i in range(30)]
        fact = fold(rows, top_n=5)
        assert len(fact.top_senders) == 5
        assert [e for _, e in fact.top_senders] == [12] * 5
        assert [n for n, _ in fact.top_senders] == ["s02", "s05", "s08", "s11", "s14"]

    def test_the_top_list_holds_tracked_senders_only(self) -> None:
        policy = AdmissionPolicy(max_new_per_bucket=1, max_active=2000, min_requests=3)
        fact = fold([r("a", 9), r("b", 8)], policy=policy, top_n=20)
        assert fact.top_senders == (("a", 9),)

    def test_a_measured_zero_is_an_exact_empty_fact(self) -> None:
        fact = fold([])
        assert (fact.events, fact.records, fact.distinct_senders) == (0, 0, 0)
        assert fact.top_senders == () and fact.measurement is M.EXACT
        assert fact.distinct_senders_lower_bound is False

    def test_lower_bound_propagates(self) -> None:
        fact = fold([r("a", 9)], measurement=M.LOWER_BOUND)
        assert fact.measurement is M.LOWER_BOUND
        assert fact.distinct_senders_lower_bound is True
        exact = fold([r("a", 9)])
        assert exact.distinct_senders_lower_bound is False

    def test_the_ledger_gains_only_the_admitted_senders(self) -> None:
        ledger = SenderLedger(active={"old"})
        fold([r("new", 5), r("tiny", 1)], ledger)
        assert ledger.active == {"old", "new"}
        again = fold([r("new", 5)], ledger)
        assert again.admitted == 0 and again.tracked_events == {"new": 5}

    def test_top_n_must_be_positive(self) -> None:
        with pytest.raises(CatchallFoldError):
            fold([r("a")], top_n=0)
