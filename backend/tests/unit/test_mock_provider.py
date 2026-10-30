"""MockQRadarProvider behaviour and determinism."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.providers.base import ProviderCapability
from app.providers.dto import OffenseDTO
from app.providers.mock import MockQRadarProvider

pytestmark = pytest.mark.asyncio


async def test_all_capabilities_supported(mock_provider: MockQRadarProvider) -> None:
    for cap in ProviderCapability:
        assert mock_provider.supports(cap)


async def test_log_sources_are_deterministic() -> None:
    a = await MockQRadarProvider(seed=1337).list_log_sources()
    b = await MockQRadarProvider(seed=1337).list_log_sources()
    assert [s.average_eps for s in a] == [s.average_eps for s in b]
    assert [s.qradar_id for s in a] == [s.qradar_id for s in b]


async def test_different_seed_changes_jitter() -> None:
    a = await MockQRadarProvider(seed=1).list_log_sources()
    b = await MockQRadarProvider(seed=2).list_log_sources()
    assert [s.average_eps for s in a] != [s.average_eps for s in b]


async def test_includes_an_unhealthy_silent_source(mock_provider: MockQRadarProvider) -> None:
    sources = await mock_provider.list_log_sources()
    silent = [s for s in sources if s.enabled and s.average_eps == 0.0]
    assert silent, "mock must include a silent-but-enabled source for the detector"


async def test_offenses_include_unassigned(mock_provider: MockQRadarProvider) -> None:
    result = await mock_provider.list_offenses(open_only=True)
    offenses = result.rows
    assert result.truncated is False
    assert all(o.status == "OPEN" for o in offenses)
    assert any(o.assigned_to is None for o in offenses)


async def test_ariel_lifecycle_completes(mock_provider: MockQRadarProvider) -> None:
    aql = "SELECT sourceip, COUNT(*) FROM events GROUP BY sourceip LAST 1 HOURS"
    handle = await mock_provider.create_ariel_search(aql)
    status = await mock_provider.get_ariel_search_status(handle.search_id)
    assert status.is_terminal and status.is_success

    results = await mock_provider.get_ariel_search_results(handle.search_id, max_rows=100)
    assert results.total_count == status.record_count
    assert not results.truncated


async def test_ariel_results_respect_max_rows() -> None:
    # A query that produces several rows, truncated to 1.
    provider = MockQRadarProvider(seed=1337)
    # Find an AQL that yields >1 row deterministically.
    aql = "SELECT a FROM events GROUP BY a"  # digest-driven row count
    handle = await provider.create_ariel_search(aql)
    full = await provider.get_ariel_search_results(handle.search_id, max_rows=100)
    if full.total_count > 1:
        truncated = await provider.get_ariel_search_results(handle.search_id, max_rows=1)
        assert len(truncated.rows) == 1
        assert truncated.truncated is True


async def test_offenses_come_back_oldest_first_and_a_ceiling_is_visible() -> None:
    """The mock knows its catalog, so a full page is truncated only when rows remain."""
    base = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
    provider = MockQRadarProvider(now=base)
    provider.offense_page_size = 2
    provider.offense_script = [
        OffenseDTO(qradar_id=3, status="OPEN", last_updated_time=base + timedelta(seconds=3)),
        OffenseDTO(qradar_id=1, status="OPEN", last_updated_time=base + timedelta(seconds=1)),
        OffenseDTO(qradar_id=2, status="OPEN", last_updated_time=base + timedelta(seconds=2)),
        OffenseDTO(qradar_id=4, status="CLOSED", last_updated_time=base + timedelta(seconds=4)),
    ]

    first = await provider.list_offenses(open_only=False, max_pages=1)
    assert first.truncated is True
    assert [row.qradar_id for row in first.rows] == [1, 2]

    # Three rows are strictly newer than id 1, and the page holds two, so the
    # boundary row (id 2) is still in the next read.
    second = await provider.list_offenses(
        open_only=False, updated_since=base + timedelta(seconds=1), max_pages=1
    )
    assert second.truncated is True
    assert [row.qradar_id for row in second.rows] == [2, 3]

    # Two rows fit the ceiling exactly and nothing remains. The mock can see
    # that, so a full page is not reported as a cut.
    provider.offense_script = provider.offense_script[:2]
    exact = await provider.list_offenses(open_only=False, max_pages=1)
    assert exact.truncated is False
    assert [row.qradar_id for row in exact.rows] == [1, 3]


async def test_unknown_search_id_returns_error(mock_provider: MockQRadarProvider) -> None:
    status = await mock_provider.get_ariel_search_status("does-not-exist")
    assert status.status == "ERROR"
    assert not status.is_success
