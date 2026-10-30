"""MSG-8a: the history backfill sentences render as code plus raw values, English unchanged.

Every golden string below is the sentence the pre-MSG-8a f-string built, copied from that code.
The scenario tests then drive the real functions and check that the sentence a function returns
is exactly what its reference renders (a reference must never describe other words than the
ones stored beside it).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.history.estimate import estimate_source_family, not_estimable
from app.history.families import fit_family_window
from app.history.lifecycle import (
    ActionKind,
    ConsoleMode,
    DecisionView,
    ScopeRowView,
    coded_reason,
    scope_action,
    step_row,
)
from app.history.planner import (
    ChunkResult,
    Gap,
    PlannerSettings,
    ScanOutcome,
    ScanSpanResult,
    SearchStatus,
    Span,
    StepAction,
    StepBudget,
    fit_window,
    interpret_scan,
    new_progress,
    plan_scan,
    plan_step,
    settle_step,
)
from app.history.reasons import message_wire, semicolon_reason, semicolon_ref
from app.history.source_floors import SourceFloors, finish_with_sources_coded
from app.history.state import (
    HistoryFamily,
    HistoryState,
    HistoryTrigger,
    legal_triggers,
    next_state,
)
from app.i18n.messages import REGISTRY, MessageRef, render
from app.schemas.messages import stored_message

SET = PlannerSettings()
B = SET.bucket
END = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
T0 = END - timedelta(days=28)
STARTED = END - timedelta(hours=1)
BUDGET = StepBudget(searches=10, seconds_left=120)
ISO_FIRST = datetime(2026, 10, 1, 8, 30, tzinfo=UTC)
ISO_FLOOR = datetime(2026, 10, 2, 8, 30, tzinfo=UTC)


def prog(**kw: Any):  # type: ignore[no-untyped-def]
    from dataclasses import replace

    return replace(new_progress(T0, STARTED, SET), **kw)


def ok(start: datetime, n: int, *, rows: int = 0, ns: int | None = None, lb: int = 0):  # type: ignore[no-untyped-def]
    return ChunkResult(
        Span(start, start + n * B), SearchStatus.COMPLETE, rows, n if ns is None else ns, lb
    )


def bad(start: datetime, n: int, status: SearchStatus):  # type: ignore[no-untyped-def]
    return ChunkResult(Span(start, start + n * B), status)


def scan_ok(span: Span, event_at: datetime | None = None) -> ScanSpanResult:
    totals = ((event_at, 7),) if event_at is not None else ()
    return ScanSpanResult(span, SearchStatus.COMPLETE, totals)


def ref(code: str, **params: Any) -> MessageRef:
    return MessageRef(code, params)


# ----------------------------------------------------------------------------- golden English

SETTING_21 = ref("history.window.limit_setting", setting="BASELINE_LOOKBACK_DAYS", days=21)
RETENTION_22 = ref(
    "history.window.limit_retention",
    setting="RETENTION_LOG_SOURCE_METRIC_DAYS",
    days=22,
    hours_setting="HISTORY_BACKFILL_MAX_HOURS",
    hours=72,
)
GAP = ref("history.gap.search_failed", failures=3)

GOLDEN: list[tuple[MessageRef, str]] = [
    (
        ref("history.deferred.no_permit", waited_seconds=30.0),
        "deferred: no Ariel permit within 30 s",
    ),
    (ref("history.scan.end"), "the scan reached the end of the window"),
    (ref("history.scan.search"), "scan for the first event of the scope"),
    (ref("history.scan.no_result"), "no scan result was provided"),
    (
        ref("history.scan.failed", failures=3),
        "the floor scan failed 3 times; history is unavailable",
    ),
    (
        ref("history.scan.retry", status="not_run", failures=2, max_failures=3),
        "a scan search was not_run; it proves nothing, so it is retried (2 of 3)",
    ),
    (
        ref("history.scan.too_recent", margin=24),
        "the first event is too recent to prove anything older: the margin of 24 h reaches "
        "the end of the window",
    ),
    (
        ref("history.scan.floor_found", first=ISO_FIRST, floor=ISO_FLOOR),
        "QRadar holds events of the scope from 2026-10-01T08:30:00+00:00; history starts at "
        "2026-10-02T08:30:00+00:00",
    ),
    (
        ref("history.scan.floor_found_late", first=ISO_FIRST, floor=ISO_FLOOR),
        "QRadar holds events of the scope from 2026-10-01T08:30:00+00:00; history starts at "
        "2026-10-02T08:30:00+00:00 (later than the asked start)",
    ),
    (
        ref("history.scan.no_event_window"),
        "no event of the scope in the whole window; nothing is written",
    ),
    (ref("history.scan.no_event_span"), "no event in the scanned span; scan the next one"),
    (ref("history.step.stopped"), "history reading was stopped by an operator"),
    (ref("history.step.time_limit", hours=72), "the reading did not finish in 72 h"),
    (ref("history.step.wait_budget"), "waiting for search budget"),
    (
        ref("history.step.read", buckets=8640, searches=3, width=2880),
        "read 8640 bucket(s) in 3 search(es) of up to 2880",
    ),
    (
        ref(
            "history.finish.partial",
            problems=[ref("history.finish.floor_cut"), ref("history.finish.gaps", count=2)],
        ),
        "reached the end; QRadar holds less than asked (the floor is later than the asked "
        "start); 2 span(s) could not be read",
    ),
    (
        ref("history.finish.partial", problems=[ref("history.finish.gaps", count=1)]),
        "reached the end; 1 span(s) could not be read",
    ),
    (
        ref("history.finish.complete"),
        "reached the end with the whole asked window read",
    ),
    (
        ref("history.width.released", steps=3, fit=320),
        "3 clean steps brought the failure ceiling to the row fit of 320; the ceiling is released",
    ),
    (
        ref("history.width.ceiling_doubled", steps=3, ceiling=160),
        "3 clean steps doubled the failure ceiling to 160",
    ),
    (
        ref(
            "history.settle.ticked",
            clauses=[
                ref("history.settle.read", count=100),
                ref("history.settle.width", count=50),
                ref("history.settle.gap_recorded", gap=GAP),
            ],
        ),
        "read 100 bucket(s); the width is 50 bucket(s); recorded a gap: "
        "search failed 3 times at the minimum width",
    ),
    (ref("history.settle.too_many_gaps", count=20), "more than 20 unreadable spans"),
    (
        ref("history.settle.failed_minimum", failures=1, max_failures=3),
        "a search failed at the minimum width (1 of 3); the same span is tried again",
    ),
    (
        ref("history.settle.failed_halve", buckets=64, width=32),
        "a search of 64 buckets failed; the width halves to 32",
    ),
    (
        ref("history.settle.truncated_halve", buckets=64, width=32),
        "a search of 64 buckets was truncated and keeps nothing; the width halves to 32",
    ),
    (
        ref("history.settle.budget_ended"),
        "the budget ended before this span was searched",
    ),
    (
        ref("history.settle.density", share=0.1, limit=0.05),
        "10\u00a0% of the buckets read are lower bounds (limit 5\u00a0%): too many distinct "
        "values per bucket, so history would be lower bounds",
    ),
    (
        ref("history.fit.reduced", asked=28, days=21, limit=72),
        "window reduced from 28 to 21 days: the estimate exceeded 72 hours",
    ),
    (
        ref("history.fit.fits", days=28, hours=23.4),
        "the 28-day window fits in an estimated 23.4 hours",
    ),
    (
        ref("history.fit.fits", days=7, hours=0.0),
        "the 7-day window fits in an estimated 0.0 hours",
    ),
    (
        ref("history.fit.fits", days=28, hours=1260.0),
        "the 28-day window fits in an estimated 1260.0 hours",
    ),
    (
        ref("history.fit.refused", days=7, hours=90.04, searches=700, ticks=70, limit=72),
        "even 7 days need about 90.0 hours (700 searches, 70 ticks), over the 72-hour limit; "
        "history is unavailable",
    ),
    (
        ref("history.window.reduced", asked=28, days=21, limits=[SETTING_21]),
        "window reduced from 28 to 21 days: BASELINE_LOOKBACK_DAYS is 21 days",
    ),
    (
        ref("history.window.reduced", asked=28, days=21, limits=[SETTING_21, RETENTION_22]),
        "window reduced from 28 to 21 days: BASELINE_LOOKBACK_DAYS is 21 days and "
        "RETENTION_LOG_SOURCE_METRIC_DAYS is 22 days and HISTORY_BACKFILL_MAX_HOURS is "
        "72 hours, so the reading would outlive the retention",
    ),
    (
        ref("history.window.unavailable", days=5, minimum=7, limits=[SETTING_21]),
        "history unavailable: the window would be 5 days, below the 7-day minimum; "
        "BASELINE_LOOKBACK_DAYS is 21 days",
    ),
    (ref("history.later_yes.possible"), "possible"),
    (
        ref("history.later_yes.not_possible"),
        "not possible: this source already collected",
    ),
    (
        ref("history.later_yes.has_scope"),
        "not needed: this source has a history scope",
    ),
    (
        ref("history.family.always_on"),
        "source-volume collection is the platform's base collection",
    ),
    (
        ref("history.family.switch_on", setting="WAF_COLLECTION_ENABLED"),
        "WAF_COLLECTION_ENABLED is on",
    ),
    (
        ref("history.family.switch_off", setting="IPS_COLLECTION_ENABLED"),
        "IPS_COLLECTION_ENABLED is off",
    ),
    (
        ref("history.family.zero_endpoint", limit=72),
        "fitted with no tracked endpoint yet; the 72-hour limit still bounds the reading",
    ),
    (
        ref("history.family.zero_entity", limit=72),
        "fitted with no admitted entity yet; the 72-hour limit still bounds the reading",
    ),
    (
        ref("history.family.fair_share", share=3, total=10),
        "fitted with a fair share of 3 of the 10 shared history searches per tick",
    ),
    (
        ref("history.estimate.all_refused", sources=4, days=7, limit=72),
        "all 4 sources are unavailable: even 7 days exceed the 72-hour per-source limit",
    ),
    (
        ref("history.estimate.some_refused", refused=1, sources=4, limit=72),
        "1 of 4 sources are unavailable because even 7 days exceed the 72-hour per-source "
        "limit; the remaining source windows fit",
    ),
    (
        ref(
            "history.estimate.some_refused_reduced",
            refused=1,
            sources=4,
            limit=72,
            asked=28,
            days=21,
        ),
        "1 of 4 sources are unavailable because even 7 days exceed the 72-hour per-source "
        "limit; the remaining source windows fit, with the smallest reduced from 28 to 21 days",
    ),
    (
        ref("history.estimate.reduced", asked=28, days=21, limit=72),
        "window reduced from 28 to 21 days for the smallest per-source fit: the estimate "
        "exceeded 72 hours",
    ),
    (
        ref("history.estimate.all_fit", sources=4, days=28, hours=61.25),
        "all 4 per-source 28-day windows fit; the console total is an estimated 61.2 hours",
    ),
    (
        ref(
            "history.estimate.no_collection",
            reason=ref("history.family.switch_off", setting="IPS_COLLECTION_ENABLED"),
        ),
        "no estimate: collection is switched off (IPS_COLLECTION_ENABLED is off)",
    ),
    (
        ref("history.estimate.planner_refused", error="max_hours must be >= 1"),
        "no estimate: the planner refused the settings (max_hours must be >= 1)",
    ),
    (
        ref("history.estimate.no_monitored"),
        "no estimate: the console has no monitored log source yet",
    ),
    (ref("history.estimate.no_waf"), "no estimate: no WAF source is enabled on this console"),
    (ref("history.estimate.no_ips"), "no estimate: no IPS source is enabled on this console"),
    (
        ref("history.estimate.no_catchall", days=7),
        "no estimate: no catch-all bucket has been collected in the last 7 days",
    ),
    (ref("history.estimate.no_rules"), "no estimate: the console has no enabled rule"),
    (
        ref(
            "history.estimate.empty_sources",
            reason=ref("history.estimate.all_fit", sources=4, days=28, hours=61.2),
            count=2,
            noun="tracked endpoint",
            limit=72,
        ),
        "all 4 per-source 28-day windows fit; the console total is an estimated 61.2 hours; "
        "2 source(s) fitted with no tracked endpoint yet; the 72-hour limit still bounds "
        "the reading",
    ),
    (
        ref(
            "history.estimate.window_limited",
            window=ref("history.window.reduced", asked=28, days=21, limits=[SETTING_21]),
            fit=ref("history.fit.fits", days=21, hours=45.0),
        ),
        "window reduced from 28 to 21 days: BASELINE_LOOKBACK_DAYS is 21 days; the 21-day "
        "window fits in an estimated 45.0 hours",
    ),
    (
        ref("history.request.start"),
        "Read history now is recorded. Each family applies it at its next collection; "
        "no scope has changed yet.",
    ),
    (
        ref("history.request.stop"),
        "Stop reading is recorded. Each family ends its reading scopes at its next "
        "collection; no scope has changed yet.",
    ),
    (
        ref("history.request.refused"),
        "record Yes first: the console has no Yes answer to start or stop",
    ),
    (ref("history.decide.never_reversed"), "a Yes is never reversed; stop the reading instead"),
    (
        ref("history.decide.meantime"),
        "a decision was recorded in the meantime; it is shown here",
    ),
    (
        ref("history.lifecycle.not_eligible"),
        "this source already collected live data; history cannot be read before it "
        "(disable the profile and create it again to read its history)",
    ),
    (ref("history.lifecycle.restart"), "read history now: topping up"),
    (ref("history.lifecycle.stopped_by", by="ayse"), "stopped by ayse"),
    (ref("history.words.source_one"), "1 source"),
    (
        ref(
            "history.finish.cut_sources",
            cut=2,
            total=ref("history.words.source_many", count=5),
        ),
        "QRadar holds less than asked for 2 of 5 sources",
    ),
    (
        ref("history.finish.silent_sources", sources=ref("history.words.source_one")),
        "1 source had no event in the window",
    ),
    (
        ref("history.phase_a.no_event"),
        "QRadar holds no event of a monitored source in the window; nothing is written",
    ),
    (
        ref("history.phase_a.no_monitored"),
        "no log source is monitored, so there is nothing to read history for",
    ),
    (
        ref("history.catchall.no_event"),
        "no catch-all source had an event in the window; nothing is written",
    ),
    (
        ref("history.catchall.anchor_gone"),
        "the live anchor is gone: the history has no end to read up to",
    ),
    (
        ref("history.catchall.unreadable"),
        "the stored history progress could not be read back",
    ),
    (ref("history.waf.volume_pass_finished"), "history volume pass finished"),
    (ref("history.waf.wait_time_budget"), "waiting for the history time budget"),
    (ref("history.assumption.shape_windows"), "hourly windows"),
    (
        ref(
            "history.assumption.opening",
            shape=ref("history.assumption.shape_buckets", minutes=5),
            budget=ref("history.assumption.budget_plain", per_tick=10),
        ),
        "5-minute buckets, 10 history searches per tick",
    ),
    (
        ref(
            "history.assumption.opening",
            shape=ref("history.assumption.shape_windows"),
            budget=ref("history.assumption.budget_waf", per_source=10, sources=4),
        ),
        "hourly windows, 10 history searches per source tick times 4 sources at once",
    ),
    (
        ref(
            "history.assumption.opening",
            shape=ref("history.assumption.shape_buckets", minutes=5),
            budget=ref("history.assumption.budget_ips", per_tick=10),
        ),
        "5-minute buckets, 10 history searches shared per tick",
    ),
    (
        ref("history.assumption.cadence", interval=ref("history.interval.minute_one")),
        "one tick every 1 minute (the family's Search cadence)",
    ),
    (
        ref("history.assumption.cadence", interval=ref("history.interval.minute_many", count=15)),
        "one tick every 15 minutes (the family's Search cadence)",
    ),
    (
        ref("history.assumption.cadence", interval=ref("history.interval.hour_many", count=2)),
        "one tick every 2 hours (the family's Search cadence)",
    ),
    (
        ref("history.assumption.cadence", interval=ref("history.interval.day_one")),
        "one tick every 1 day (the family's Search cadence)",
    ),
    (
        ref("history.assumption.qradar_time"),
        "QRadar time per search not measured on this console",
    ),
    (
        ref("history.assumption.phase_a_rows", sources=120),
        "120 monitored sources, one row each per bucket",
    ),
    (
        ref("history.assumption.waf_sources", count=4, endpoints=18),
        "4 WAF sources with 18 tracked endpoints",
    ),
    (
        ref("history.assumption.waf_breakdown", days=7),
        "action, response code and source IP read over 7 days only",
    ),
    (
        ref(
            "history.assumption.ips_sources",
            count=6,
            plans=ref("history.words.plan_one"),
        ),
        "6 IPS sources in 1 per-source plan",
    ),
    (
        ref(
            "history.assumption.ips_sources",
            count=6,
            plans=ref("history.words.plan_many", count=6),
        ),
        "6 IPS sources in 6 per-source plans",
    ),
    (
        ref("history.assumption.ips_values", source_ips=50, action_values=3, fanout=10),
        "50 source IPs, 3 action values and 10 port fan-out rows per source and bucket assumed",
    ),
    (
        ref("history.assumption.ips_fair_share", share=3, per_tick=10),
        "each per-source plan is fitted with a fair share of 3 of the 10 shared searches "
        "per tick",
    ),
    (
        ref(
            "history.assumption.catchall_senders",
            senders=12,
            buckets=ref("history.words.bucket_one"),
            days=7,
        ),
        "12 senders per bucket (the mean of 1 live bucket of the last 7 days)",
    ),
    (
        ref(
            "history.assumption.catchall_senders",
            senders=12,
            buckets=ref("history.words.bucket_many", count=2016),
            days=7,
        ),
        "12 senders per bucket (the mean of 2016 live buckets of the last 7 days)",
    ),
    (ref("history.assumption.catchall_rows", rows=5000), "5000 rows per search"),
    (
        ref("history.assumption.rules", rules=40),
        "40 enabled rules, every one assumed to match in each window",
    ),
    (
        ref("history.assumption.rule_rows", rows=1000),
        "1000 rows per search, plus one console total",
    ),
]

#: The state table as it read before MSG-8a, sentence by sentence.
OLD_TRANSITIONS = {
    (HistoryState.NOT_REQUESTED, HistoryTrigger.REQUEST_AWAITING_ANCHOR): (
        "history was requested, and the scope waits for its first live bucket (the anchor)"
    ),
    (HistoryState.NOT_REQUESTED, HistoryTrigger.REQUEST_READY): (
        "history was requested, and the scope can be planned at its next collection"
    ),
    (HistoryState.NOT_REQUESTED, HistoryTrigger.NOT_ELIGIBLE): (
        "the scope already has live buckets, and a prepend would move first_seen_at (§B.5)"
    ),
    (HistoryState.WAITING, HistoryTrigger.ANCHOR_FOUND): (
        "the first live bucket exists; reading can start"
    ),
    (HistoryState.WAITING, HistoryTrigger.NOT_ELIGIBLE): (
        "the scope turned out to have live buckets that history cannot precede"
    ),
    (HistoryState.READING, HistoryTrigger.NOTHING_TO_READ): (
        "QRadar holds no event of the scope in the window, or the floor scan could not be done"
    ),
    (HistoryState.WAITING, HistoryTrigger.NOTHING_TO_READ): (
        "QRadar holds no event of the scope in the window"
    ),
    (HistoryState.READING, HistoryTrigger.WINDOW_TOO_SMALL): (
        "the estimate exceeds the time limit even at the smallest window"
    ),
    (HistoryState.READING, HistoryTrigger.FINISHED_COMPLETE): (
        "the reading reached the end; the floor is within one bucket of the asked start "
        "and no gap exists"
    ),
    (HistoryState.READING, HistoryTrigger.FINISHED_PARTIAL): (
        "the reading reached the end, but QRadar holds less than asked or a gap exists"
    ),
    (HistoryState.READING, HistoryTrigger.DENSITY_GATE): (
        "too many buckets would be lower bounds; reading further would store only those"
    ),
    (HistoryState.READING, HistoryTrigger.TOO_MANY_GAPS): (
        "the gap list is full; the scope has too many unreadable spans"
    ),
    (HistoryState.READING, HistoryTrigger.TIME_LIMIT): (
        "the reading did not finish inside its time limit"
    ),
    (HistoryState.NOT_REQUESTED, HistoryTrigger.SWITCHED_OFF): (
        "history reading is switched off; the scope starts live"
    ),
    (HistoryState.WAITING, HistoryTrigger.SWITCHED_OFF): (
        "history reading was switched off while waiting"
    ),
    (HistoryState.READING, HistoryTrigger.SWITCHED_OFF): "history reading was switched off",
    (HistoryState.WAITING, HistoryTrigger.STOPPED_BY_OPERATOR): (
        "stopped by an operator while waiting"
    ),
    (HistoryState.READING, HistoryTrigger.STOPPED_BY_OPERATOR): "stopped by an operator",
}
RESTART_TEXT = "the reading restarts: a new answer, a topped-up source or a new watch version"


@pytest.mark.parametrize(("message", "english"), GOLDEN, ids=[g[0].code for g in GOLDEN])
def test_each_history_code_renders_the_sentence_the_old_code_built(
    message: MessageRef, english: str
) -> None:
    assert render(message) == english


def test_the_golden_table_covers_every_history_code_but_the_pre_existing_ones() -> None:
    covered = {message.code for message, _ in GOLDEN}
    uncovered = {
        spec.code
        for spec in REGISTRY
        if spec.code.startswith("history.")
        and spec.code not in covered
        # the state table, the density/gap words and the joiners have their own tests below
        and not spec.code.startswith("history.state.")
        and spec.code
        not in {
            "history.reason.joined",
            "history.gap.search_failed",
            "history.window.limit_setting",
            "history.window.limit_retention",
        }
        and not spec.code.startswith("history.interval.")
        and spec.code
        not in {
            "history.assumption.shape_buckets",
            "history.assumption.budget_waf",
            "history.assumption.budget_ips",
            "history.assumption.budget_plain",
            "history.words.source_many",
            "history.words.plan_one",
            "history.words.plan_many",
            "history.words.bucket_one",
            "history.words.bucket_many",
            "history.settle.read",
            "history.settle.width",
            "history.settle.gap_recorded",
            "history.settle.ticked",
            "history.finish.floor_cut",
            "history.finish.gaps",
        }
    }
    assert uncovered == set()


# ----------------------------------------------------------------------------- the state table


@pytest.mark.parametrize(("key", "english"), list(OLD_TRANSITIONS.items()))
def test_every_transition_keeps_its_sentence_and_names_its_code(
    key: tuple[HistoryState, HistoryTrigger], english: str
) -> None:
    change = next_state(*key)
    assert change.reason == english
    assert render(change.reason_message) == english
    assert change.reason_message.code.startswith("history.state.")


def test_the_restart_transition_is_one_code_from_every_state() -> None:
    for state in (
        HistoryState.READING,
        HistoryState.COMPLETE,
        HistoryState.PARTIAL,
        HistoryState.UNAVAILABLE,
    ):
        change = next_state(state, HistoryTrigger.RESTART)
        assert change.reason == RESTART_TEXT
        assert change.reason_message.code == "history.state.restart"


def test_every_legal_transition_is_coded_and_consistent() -> None:
    seen = 0
    for state in HistoryState:
        for trigger in legal_triggers(state):
            change = next_state(state, trigger)
            assert render(change.reason_message) == change.reason
            seen += 1
    assert seen == len(OLD_TRANSITIONS) + 4


# ----------------------------------------------------------------------------- the planner


def _consistent(*decisions: Any) -> None:
    for decision in decisions:
        assert decision.reason_message is not None, decision.reason
        assert render(decision.reason_message) == decision.reason


def test_scan_sentences_carry_the_reference_that_renders_them() -> None:
    p = prog()
    plan = plan_scan(p, END, SET)
    assert plan.reason == "scan for the first event of the scope"
    span = plan.span
    assert span is not None
    b0 = T0 + timedelta(days=3, minutes=5)
    late = interpret_scan(p, [scan_ok(span, b0)], END, SET)
    assert late.reason.endswith("(later than the asked start)")
    assert late.reason_message is not None
    assert late.reason_message.code == "history.scan.floor_found_late"
    exact = interpret_scan(p, [scan_ok(span, T0 - timedelta(hours=24))], END, SET)
    assert exact.reason_message is not None
    assert exact.reason_message.code == "history.scan.floor_found"
    empty = interpret_scan(p, [scan_ok(span)], END, SET)
    assert empty.reason_message is not None
    assert empty.reason_message.code == "history.scan.no_event_span"
    retry = interpret_scan(p, [ScanSpanResult(span, SearchStatus.NOT_RUN)], END, SET)
    assert retry.reason == (
        "a scan search was not_run; it proves nothing, so it is retried (1 of 3)"
    )
    too_recent_end = T0 + timedelta(days=2)
    recent_span = plan_scan(p, too_recent_end, SET).span
    assert recent_span is not None
    recent = interpret_scan(
        p, [scan_ok(recent_span, too_recent_end - timedelta(hours=2))], too_recent_end, SET
    )
    assert recent.outcome is ScanOutcome.UNAVAILABLE
    nothing_end = T0 + timedelta(days=10)
    cursor = p
    last = None
    while True:
        step = plan_scan(cursor, nothing_end, SET)
        if step.span is None:
            _consistent(step)
            break
        last = interpret_scan(cursor, [scan_ok(step.span)], nothing_end, SET)
        _consistent(last)
        cursor = last.progress
        if last.outcome is ScanOutcome.UNAVAILABLE:
            break
    failing = p
    for _ in range(SET.max_failures):
        failed = interpret_scan(failing, [ScanSpanResult(span, SearchStatus.FAILED)], END, SET)
        _consistent(failed)
        failing = failed.progress
    assert failed.reason == "the floor scan failed 3 times; history is unavailable"
    _consistent(late, exact, empty, retry, recent)
    assert interpret_scan(p, [], END, SET).reason == "no scan result was provided"
    _consistent(interpret_scan(p, [], END, SET))


def test_step_plans_carry_the_reference_that_renders_them() -> None:
    base: dict[str, Any] = {"now": STARTED, "end": END, "budget": BUDGET, "settings": SET}
    plans = [
        plan_step(prog(), **base),
        plan_step(prog(floor=T0), **base),
        plan_step(prog(floor=T0), **base, enabled=False),
        plan_step(prog(floor=T0), **base, stop_requested=True),
        plan_step(
            prog(floor=T0),
            now=STARTED + timedelta(hours=SET.max_hours, minutes=1),
            end=END,
            budget=BUDGET,
            settings=SET,
        ),
        plan_step(prog(floor=T0), now=STARTED, end=END, budget=StepBudget(0, 100), settings=SET),
        plan_step(prog(scan_cursor=END), **base),
        plan_step(prog(floor=T0, through=END - B), **base),
        plan_step(
            prog(floor=T0 + timedelta(days=5), through=END - B), **base
        ),
        plan_step(
            prog(floor=T0, through=END - B, gaps=(Gap(T0, T0 + B, "x"),)), **base
        ),
    ]
    _consistent(*plans)
    codes = [plan.reason_message.code for plan in plans if plan.reason_message]
    assert "history.step.read" in codes
    assert "history.step.wait_budget" in codes
    assert "history.step.stopped" in codes
    assert "history.state.switched_off" in codes
    assert "history.step.time_limit" in codes
    assert "history.finish.complete" in codes
    assert "history.finish.partial" in codes
    assert all(plan.action is not StepAction.READ or plan.chunks for plan in plans)
    partial = next(
        plan
        for plan in plans
        if plan.reason_message and plan.reason_message.code == "history.finish.partial"
    )
    assert partial.reason.startswith("reached the end; ")
    both = plans[-1]
    assert both.reason == "reached the end; 1 span(s) could not be read"


def test_the_finish_verdict_joins_its_clauses_exactly_as_before() -> None:
    both = plan_step(
        prog(floor=T0 + timedelta(days=5), through=END - B, gaps=(Gap(T0, T0 + B, "x"),)),
        now=STARTED,
        end=END,
        budget=BUDGET,
        settings=SET,
    )
    assert both.reason == (
        "reached the end; QRadar holds less than asked (the floor is later than the asked "
        "start); 1 span(s) could not be read"
    )
    assert both.reason_message is not None
    assert render(both.reason_message) == both.reason
    problems = both.reason_message.to_wire()["params"]["problems"]  # type: ignore[index]
    assert problems["items"][0]["code"] == "history.finish.floor_cut"


def test_settled_steps_carry_the_reference_that_renders_them() -> None:
    settled = [
        settle_step(prog(floor=T0), [ok(T0, 100, rows=500, ns=50)], end=END, settings=SET),
        settle_step(prog(floor=T0), [bad(T0, 64, SearchStatus.TRUNCATED)], end=END, settings=SET),
        settle_step(prog(floor=T0), [bad(T0, 64, SearchStatus.FAILED)], end=END, settings=SET),
        settle_step(prog(floor=T0), [bad(T0, 10, SearchStatus.NOT_RUN)], end=END, settings=SET),
        settle_step(prog(floor=T0), [ok(T0, 100, lb=10)], end=END, settings=SET),
        settle_step(prog(floor=T0), [ok(T0, 100, lb=5)], end=END, settings=SET),
        settle_step(
            prog(floor=T0, through=END - 3 * B), [ok(END - 2 * B, 2)], end=END, settings=SET
        ),
    ]
    p = prog(floor=T0, chunk_buckets=1, chunk_ceiling=1)
    for _ in range(SET.max_failures):
        step = settle_step(p, [bad(T0, 1, SearchStatus.FAILED)], end=END, settings=SET)
        settled.append(step)
        p = step.progress
    assert step.new_gap is not None
    gaps = tuple(Gap(T0 + i * B, T0 + (i + 1) * B, "x") for i in range(SET.max_gaps))
    full = prog(
        floor=T0,
        through=T0 + (SET.max_gaps - 1) * B,
        gaps=gaps,
        failures=SET.max_failures - 1,
    )
    settled.append(
        settle_step(
            full, [bad(T0 + SET.max_gaps * B, 1, SearchStatus.FAILED)], end=END, settings=SET
        )
    )
    _consistent(*settled)
    rendered = [step.reason for step in settled]
    assert any(r.startswith("read 100 bucket(s); the width is 320 bucket(s)") for r in rendered)
    assert any("the width halves to 32" in r for r in rendered)
    assert any("(limit 5\u00a0%)" in r for r in rendered)
    assert any(r.startswith("a search failed at the minimum width (1 of 3)") for r in rendered)
    assert any("more than 20 unreadable spans" in r for r in rendered)
    gap_step = settled[-2]
    assert "recorded a gap: search failed 3 times at the minimum width" in gap_step.reason


def test_the_width_notes_are_coded() -> None:
    p = prog(floor=T0, rows_seen=200, non_silent_buckets=20)
    p = settle_step(p, [bad(T0, 40, SearchStatus.FAILED)], end=END, settings=SET).progress
    start = T0
    notes = []
    for _ in range(15):
        width = p.chunk_buckets or 1
        landed = settle_step(
            p, [ok(start, width, rows=10 * width, ns=width)], end=END, settings=SET
        )
        p = landed.progress
        assert p.through is not None
        start = p.through + B
        notes.append(landed)
    _consistent(*notes)
    joined = " | ".join(n.reason for n in notes)
    assert "3 clean steps doubled the failure ceiling to 40" in joined
    assert "3 clean steps brought the failure ceiling to the row fit of 320" in joined


def test_fit_window_sentences_carry_their_reference() -> None:
    reduced = fit_window(
        28, lambda days: days * 100, end=END, settings=SET, searches_per_tick=10
    )
    refused = fit_window(
        28, lambda days: days * 100_000, end=END, settings=SET, searches_per_tick=10
    )
    fits = fit_window(28, lambda days: 100, end=END, settings=SET, searches_per_tick=10)
    _consistent(reduced, refused, fits)
    assert fits.reason == "the 28-day window fits in an estimated 0.8 hours"
    assert refused.refused and refused.reason.startswith("even 7 days need about ")
    assert reduced.reason_message is not None


# ----------------------------------------------------------------------------- the family window


def test_the_family_window_reasons_join_the_limit_words_with_and() -> None:
    from app.core.config import Settings

    reduced = fit_family_window(
        HistoryFamily.PHASE_A,
        Settings(baseline_lookback_days=21, history_backfill_days=28, _env_file=None),
    )
    assert reduced.reason is not None and reduced.reason_message is not None
    assert render(reduced.reason_message) == reduced.reason
    assert reduced.reason.startswith("window reduced from 28 to 21 days: ")
    assert reduced.reason_message.code == "history.window.reduced"
    gone = fit_family_window(
        HistoryFamily.PHASE_A,
        Settings(baseline_lookback_days=3, history_backfill_days=28, _env_file=None),
    )
    assert gone.reason is not None and gone.reason_message is not None
    assert render(gone.reason_message) == gone.reason
    assert gone.reason.startswith("history unavailable: the window would be 3 days")
    whole = fit_family_window(
        HistoryFamily.PHASE_A, Settings(history_backfill_days=7, _env_file=None)
    )
    assert whole.reason is None and whole.reason_message is None


# ----------------------------------------------------------------------------- estimate


def test_the_estimate_summary_sentences_carry_their_reference() -> None:
    searches = [lambda days: days * 100] * 4
    all_fit = estimate_source_family(
        "WAF",
        searches,
        days_requested=28,
        end=END,
        ps=SET,
        family_searches_per_tick=10,
        source_searches_per_tick=10,
        assumptions=[MessageRef("history.assumption.qradar_time")],
    )
    assert all_fit.reason_message is not None
    assert render(all_fit.reason_message) == all_fit.reason
    assert [render(m) for m in all_fit.assumption_codes] == list(all_fit.assumptions)
    assert all_fit.assumptions == ("QRadar time per search not measured on this console",)
    none = not_estimable(
        "WAF",
        days_requested=28,
        reason="no estimate: no WAF source is enabled on this console",
        assumptions=[MessageRef("history.assumption.qradar_time")],
        message=MessageRef("history.estimate.no_waf"),
    )
    assert none.reason_message is not None and render(none.reason_message) == none.reason


# ----------------------------------------------------------------------------- the lifecycle


def _decision(**kw: Any) -> DecisionView:
    base: dict[str, Any] = {
        "answer": "YES",
        "days": 28,
        "start_requested_at": None,
        "stop_requested_at": None,
        "stop_requested_by": None,
    }
    base.update(kw)
    if base["stop_requested_by"] is not None:
        base["stop_requested_at"] = END
    return DecisionView(**base)


def test_a_stop_names_who_stopped_and_codes_it() -> None:
    row = ScopeRowView(
        state=HistoryState.READING,
        days_requested=28,
        days_target=28,
        target_start=END,
        floor=END,
        through=END,
        anchor=END,
        started_at=END,
        finished_at=None,
        reason="reading",
        progress={},
    )
    action = scope_action(
        family=HistoryFamily.PHASE_A,
        mode=ConsoleMode.STOPPED,
        decision=_decision(stop_requested_by="ayse"),
        row=row,
        has_live_data=True,
        live_anchor=END,
        switch_on=True,
    )
    assert action.kind is ActionKind.TRANSITION
    assert action.reason == "stopped by ayse"
    assert action.reason_message == {
        "code": "history.lifecycle.stopped_by",
        "params": {"by": "ayse"},
    }


def test_a_move_with_its_own_words_never_borrows_the_tables_code() -> None:
    from app.history.lifecycle import _moving

    own = _moving(
        ActionKind.TRANSITION,
        HistoryState.READING,
        HistoryTrigger.SWITCHED_OFF,
        "some words of the caller",
    )
    assert own.reason == "some words of the caller"
    assert own.reason_message is None
    table = _moving(ActionKind.TRANSITION, HistoryState.READING, HistoryTrigger.SWITCHED_OFF)
    assert table.reason == "history reading was switched off"
    assert table.reason_message == MessageRef("history.state.switched_off").to_wire()


def test_a_reason_given_without_a_code_is_never_paired_with_the_tables_code() -> None:
    text, wire = coded_reason("some words", None)
    assert (text, wire) == ("some words", None)
    long = "x" * 300
    text, wire = coded_reason(long, MessageRef("history.step.wait_budget"))
    assert len(text) == 255 and wire is None


def test_step_row_clears_the_code_of_the_sentence_it_replaces() -> None:
    base = dict.fromkeys(__import__("app.history.lifecycle", fromlist=["x"]).SCOPE_COLUMNS)
    base.update(reason="old", reason_i18n={"code": "history.step.wait_budget", "params": {}})
    assert step_row(base, state=HistoryState.READING, reason="new")["reason_i18n"] is None
    wire = MessageRef("history.step.wait_budget").to_wire()
    coded = step_row(
        base, state=HistoryState.READING, reason="waiting for search budget", reason_i18n=wire
    )
    assert coded["reason_i18n"] == wire
    untouched = step_row(base, state=HistoryState.READING)
    assert untouched["reason_i18n"] == base["reason_i18n"]


# ----------------------------------------------------------------------------- the joiners


def test_semicolon_reason_joins_text_and_references_the_same_way() -> None:
    a, b = MessageRef("history.step.wait_budget"), MessageRef("history.scan.end")
    text, wire = semicolon_reason(
        [("waiting for search budget", a), ("the scan reached the end of the window", b)]
    )
    assert text == "waiting for search budget; the scan reached the end of the window"
    assert wire == {
        "code": "history.reason.joined",
        "params": {"clauses": {"kind": "list", "items": [a.to_wire(), b.to_wire()]}},
    }
    # The same shape `MessageRef.to_wire` builds, and a valid stored value.
    assert wire == MessageRef("history.reason.joined", {"clauses": [a, b]}).to_wire()
    assert stored_message(wire) is not None
    assert render(MessageRef("history.reason.joined", {"clauses": [a, b]})) == text


def test_one_clause_keeps_its_own_code_and_an_empty_clause_is_dropped() -> None:
    a = MessageRef("history.step.wait_budget")
    assert semicolon_reason([("", None), ("waiting for search budget", a)]) == (
        "waiting for search budget",
        a.to_wire(),
    )
    assert semicolon_reason([("", None)]) == ("", None)


def test_an_uncoded_clause_leaves_the_whole_joined_sentence_uncoded() -> None:
    a = MessageRef("history.step.wait_budget")
    text, wire = semicolon_reason([("waiting for search budget", a), ("free text", None)])
    assert text == "waiting for search budget; free text"
    assert wire is None
    text, ref = semicolon_ref([("waiting for search budget", a), ("free text", None)])
    assert ref is None
    assert message_wire(None) is None


# ----------------------------------------------------------------------------- multi-source finish


def test_the_multi_source_finish_counts_sources_and_codes_the_sentence() -> None:
    floors = SourceFloors()
    trigger, reason, wire = finish_with_sources_coded(
        HistoryTrigger.FINISHED_COMPLETE,
        "reached the end with the whole asked window read",
        floors,
        known=["a", "b", "c"],
        target_start=T0,
        margin=timedelta(hours=24),
        bucket=B,
    )
    assert trigger is HistoryTrigger.FINISHED_PARTIAL
    assert reason == "3 sources had no event in the window"
    assert wire == {
        "code": "history.finish.silent_sources",
        "params": {"sources": {"code": "history.words.source_many", "params": {"count": 3}}},
    }
    one = finish_with_sources_coded(
        HistoryTrigger.FINISHED_COMPLETE,
        "x",
        floors,
        known=["a"],
        target_start=T0,
        margin=timedelta(hours=24),
        bucket=B,
    )
    assert one[1] == "1 source had no event in the window"
    assert one[2]["params"]["sources"] == {"code": "history.words.source_one", "params": {}}  # type: ignore[index]


def test_each_later_yes_sentence_names_the_code_that_renders_it() -> None:
    from app.services.history_backfill import (
        LATER_YES_HAS_SCOPE,
        LATER_YES_MESSAGES,
        LATER_YES_NOT_POSSIBLE,
        LATER_YES_POSSIBLE,
    )

    assert set(LATER_YES_MESSAGES) == {
        LATER_YES_POSSIBLE,
        LATER_YES_NOT_POSSIBLE,
        LATER_YES_HAS_SCOPE,
    }
    for english, message in LATER_YES_MESSAGES.items():
        assert render(message) == english
