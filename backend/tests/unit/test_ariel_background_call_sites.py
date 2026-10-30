"""AP-5 guard: retryable/background Ariel callers opt into the reserved-slot class."""

from __future__ import annotations

import ast
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.cli import waf as waf_cli
from app.history.lifecycle import deferred_scope_row
from app.history.state import HistoryFamily, HistoryState
from app.i18n.messages import render
from app.providers.dto import WafRejectionObservation
from app.services.ariel_permit import (
    _ARIEL_PRIORITY,
    ARIEL_HISTORY_DEFERRED_REASON,
    ArielPermitUnavailable,
    ariel_history_deferred_message,
    ariel_history_deferred_reason,
)

APP = Path(__file__).resolve().parents[2] / "app"


def _name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _function(tree: ast.Module, qualified: str) -> ast.AsyncFunctionDef:
    parts = qualified.split(".")
    nodes: list[ast.stmt] = tree.body
    found: ast.AsyncFunctionDef | None = None
    for part in parts:
        candidate = next(
            (
                node
                for node in nodes
                if isinstance(node, (ast.ClassDef, ast.AsyncFunctionDef))
                and node.name == part
            ),
            None,
        )
        assert candidate is not None, f"missing {qualified}"
        if isinstance(candidate, ast.AsyncFunctionDef):
            found = candidate
            break
        nodes = candidate.body
    assert found is not None, f"{qualified} is not async"
    return found


def _background_calls(path: str, function: str) -> list[str]:
    tree = ast.parse((APP / path).read_text())
    node = _function(tree, function)
    calls: list[str] = []
    for item in ast.walk(node):
        if not isinstance(item, ast.With):
            continue
        if not any(
            isinstance(context.context_expr, ast.Call)
            and _name(context.context_expr.func) == "ariel_background_priority"
            for context in item.items
        ):
            continue
        calls.extend(
            name
            for nested in item.body
            for call in ast.walk(nested)
            if isinstance(call, ast.Call)
            if (name := _name(call.func)) is not None
        )
    return calls


@pytest.mark.parametrize(
    ("path", "function", "expected"),
    [
        ("collectors/metric_collector.py", "MetricCollector._history_step", {"_search_window"}),
        (
            "collectors/waf_endpoint_collector.py",
            "WafEndpointCollector.history_volume_step",
            {"run"},
        ),
        (
            "collectors/waf_endpoint_collector.py",
            "WafEndpointCollector.history_breakdown_step",
            {"run"},
        ),
        ("collectors/ips_collector.py", "IpsCollector._history_scan", {"run_dimension"}),
        ("collectors/ips_collector.py", "IpsCollector._history_search_span", {"_search"}),
        (
            "collectors/catchall_collector.py",
            "CatchallCollector._read_history",
            {"run_catchall_search"},
        ),
        (
            "collectors/rule_match_collector.py",
            "RuleMatchCollector._read_history",
            {"get_console_event_totals", "get_rule_matches_windowed"},
        ),
        ("collectors/pw_collector.py", "PwCollector._run_kind", {"run_search"}),
        (
            "services/search_executor.py",
            "SearchExecutor._run_once",
            {"_hold_permit", "create_ariel_search", "get_ariel_search_results"},
        ),
        ("services/property_watches.py", "run_preview", {"run_search"}),
        (
            "services/waf_profiles.py",
            "WafProfileService.validate",
            {"get_log_source", "probe_waf_properties"},
        ),
        (
            "services/ips_profiles.py",
            "IpsProfileService.validate",
            {"get_log_source", "probe_ips_fields"},
        ),
        ("cli/waf.py", "probe_rejection", {"observe_property_rejection"}),
    ],
)
def test_each_background_call_site_sets_priority_only_for_its_call(
    path: str, function: str, expected: set[str]
) -> None:
    assert expected <= set(_background_calls(path, function))
    assert _ARIEL_PRIORITY.get() == "live"


@pytest.mark.parametrize("waited_seconds", [30, 30.0, 0.5, 12.25, 1e-05, 1_000_000.0])
def test_history_deferral_reason_keeps_the_old_general_number_format(
    waited_seconds: float,
) -> None:
    refusal = ArielPermitUnavailable(waited_seconds, 3, 6)
    message = ariel_history_deferred_message(refusal)
    legacy = ARIEL_HISTORY_DEFERRED_REASON.format(waited_seconds=waited_seconds)

    assert message.code == "history.deferred.no_permit"
    assert message.params == {"waited_seconds": waited_seconds}
    assert render(message) == legacy
    assert ariel_history_deferred_reason(refusal) == legacy


def test_deferred_scope_stores_the_ref_and_free_text_clears_it() -> None:
    instance_id = uuid.uuid4()
    through = datetime(2026, 10, 20, 10, 0, tzinfo=UTC)
    errored_at = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
    stored = SimpleNamespace(
        state=HistoryState.READING.value,
        days_requested=28,
        days_target=20,
        target_start=through,
        floor=through,
        through=through,
        anchor=through,
        started_at=through,
        finished_at=None,
        reason="reading",
        reason_i18n={"code": "history.step.read", "params": {}},
        last_error_class="ProviderError",
        last_error_at=errored_at,
        progress={"through": through.isoformat()},
    )

    row = deferred_scope_row(
        stored,
        instance_id=instance_id,
        family=HistoryFamily.PHASE_A,
        scope_key="",
        reason="deferred: no Ariel permit within 30 s",
        reason_message=ariel_history_deferred_message(ArielPermitUnavailable(30, 3, 6)),
    )

    assert row["state"] == HistoryState.READING.value
    assert row["through"] == through
    assert row["progress"] == stored.progress
    assert row["last_error_class"] == "ProviderError"
    assert row["last_error_at"] == errored_at
    assert row["reason"] == "deferred: no Ariel permit within 30 s"
    assert row["reason_i18n"] == {
        "code": "history.deferred.no_permit",
        "params": {"waited_seconds": 30},
    }

    free_text = deferred_scope_row(
        SimpleNamespace(**row),
        instance_id=instance_id,
        family=HistoryFamily.PHASE_A,
        scope_key="",
        reason="provider supplied free text",
    )
    assert free_text["reason"] == "provider supplied free text"
    assert free_text["reason_i18n"] is None


@pytest.mark.asyncio
async def test_waf_cli_rejection_probe_is_background_only_while_calling_provider(
    monkeypatch,
) -> None:
    priorities: list[str] = []

    class Provider:
        closed = False

        async def observe_property_rejection(self, **kwargs):  # type: ignore[no-untyped-def]
            priorities.append(_ARIEL_PRIORITY.get())
            missing, control = kwargs["property_names"]
            return [
                WafRejectionObservation(
                    property_name=missing,
                    outcome="REJECTED_AT_CREATE",
                    create_http_status=422,
                    query="bounded missing-property probe",
                ),
                WafRejectionObservation(
                    property_name=control,
                    outcome="SEARCH_COMPLETED",
                    search_created=True,
                    search_status="COMPLETED",
                    record_count=1,
                    query="bounded control probe",
                ),
            ]

        async def aclose(self) -> None:
            self.closed = True

    async def record_audit(*args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        return None

    provider = Provider()
    monkeypatch.setattr(waf_cli, "build_provider_for_instance", lambda _instance: provider)
    monkeypatch.setattr(waf_cli, "record_audit", record_audit)

    result = await waf_cli.probe_rejection(
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(qradar_id=312),
        SimpleNamespace(id=uuid.uuid4(), url_property="URL"),
        "tester",
    )

    assert result["conclusion_code"] == "CONFIRMED_422"
    assert priorities == ["background"]
    assert provider.closed is True
    assert _ARIEL_PRIORITY.get() == "live"


class _BucketBatch:
    def __init__(self, truncated: bool) -> None:
        self.truncated = truncated
        self.buckets: dict[object, list[object]] = {}


@pytest.mark.asyncio
async def test_a_live_window_keeps_its_prefix_when_a_later_half_is_refused() -> None:
    """The shared search routine must not let a refusal escape and drop the prefix."""

    from app.collectors.metric_collector import MetricCollector

    start = datetime(2026, 10, 22, 10, 0, tzinfo=UTC)
    interval = 300
    priorities: list[str] = []

    class Provider:
        async def get_log_source_metrics_bucketed(self, first, end, **kwargs):  # type: ignore[no-untyped-def]
            priorities.append(_ARIEL_PRIORITY.get())
            assert (end - first).total_seconds() == interval * 2
            return _BucketBatch(truncated=True)

        async def get_log_source_metrics(self, first, end, **kwargs):  # type: ignore[no-untyped-def]
            priorities.append(_ARIEL_PRIORITY.get())
            if first == start:
                return []
            raise ArielPermitUnavailable(30, 3, 6)

    collector = MetricCollector(
        SimpleNamespace(),  # type: ignore[arg-type]
        Provider(),  # type: ignore[arg-type]
        settings=SimpleNamespace(ariel_multi_bucket_max_rows=1000),  # type: ignore[arg-type]
        clock=lambda: start,
    )

    found = await collector._search_window(start, 2, interval, max_searches=4)

    assert start in found.completed
    assert isinstance(found.failure, ArielPermitUnavailable)
    assert found.failure.waited_seconds == 30
    assert priorities == ["live", "live", "live"]
    assert _ARIEL_PRIORITY.get() == "live"


@pytest.mark.asyncio
async def test_a_live_breakdown_refusal_is_a_failed_result_and_history_reraises() -> None:
    from app.collectors.waf_endpoint_collector import WafEndpointCollector
    from app.models.enums import WafBreakdown, WafTelemetryStatus
    from app.waf.profile import WafFieldMapping

    start = datetime(2026, 10, 22, 10, 0, tzinfo=UTC)

    class Provider:
        async def get_waf_breakdown(self, **kwargs):  # type: ignore[no-untyped-def]
            assert _ARIEL_PRIORITY.get() == "live"
            raise ArielPermitUnavailable(30, 3, 6)

    collector = WafEndpointCollector(
        SimpleNamespace(),  # type: ignore[arg-type]
        Provider(),  # type: ignore[arg-type]
        mapping=WafFieldMapping(url="URL", method="HTTP_Method", action="Action"),
        profile_version=1,
        settings=SimpleNamespace(waf_max_breakdown_groups_per_bucket=10),  # type: ignore[arg-type]
    )
    source = SimpleNamespace(qradar_id=312)

    results, truncated, searches = await collector._breakdown_search(
        source,  # type: ignore[arg-type]
        start,
        start + timedelta(seconds=300),
        300,
        1,
        WafBreakdown.ACTION,
    )

    assert truncated is False and searches == 0
    assert results[start].status is WafTelemetryStatus.FAILED
    assert results[start].error_class == "ArielPermitUnavailable"
    assert _ARIEL_PRIORITY.get() == "live"

    with pytest.raises(ArielPermitUnavailable):
        await collector._breakdown_search(
            source,  # type: ignore[arg-type]
            start,
            start + timedelta(seconds=300),
            300,
            1,
            WafBreakdown.ACTION,
            yield_permit_refusal=True,
        )

