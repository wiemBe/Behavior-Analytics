"""Pure two-pass inventory evidence and value-free REST completeness evidence."""

import uuid

import httpx
import pytest

from app.providers.qradar_rest import QRadarRestProvider
from app.services.inventory_sync import absence_candidates, evaluate_absence


def ids(n):
    return {uuid.UUID(int=i) for i in range(1, n + 1)}


def evaluate(stored=None, present=None, previous=None, **kw):
    return evaluate_absence(
        ids(3) if stored is None else stored,
        ids(2) if present is None else present,
        set() if previous is None else previous,
        complete=kw.get("complete", True), row_count=kw.get("row_count", 2),
        previous_row_count=kw.get("previous_row_count", 3),
        validated=kw.get("validated", True), rows_dropped=kw.get("rows_dropped", 0),
        duplicate_ids=kw.get("duplicate_ids", 0),
    )


def test_two_pass_and_candidate_rotation():
    first = evaluate()
    assert first.candidates == ids(3) - ids(2) and not first.confirmed
    second = evaluate(previous=set(first.candidates))
    assert second.confirmed == first.candidates and not second.candidates
    assert second.state == "evaluated"


@pytest.mark.parametrize("complete,row_count", [(False, 2), (True, 0), (False, 0)])
def test_bad_pass_preserves_candidates_except_present(complete, row_count):
    result = evaluate(previous=ids(3), complete=complete, row_count=row_count)
    assert result.candidates == ids(3) - ids(2)
    assert not result.confirmed and result.state == "skipped"
    assert result.reason == ("incomplete_snapshot" if not complete else "empty_snapshot")


@pytest.mark.parametrize("complete", [True, False])
def test_reappearance_removes_candidate_and_restarts_two_passes(complete):
    appeared = evaluate(present=ids(3), previous=ids(3), complete=complete)
    assert not appeared.candidates and not appeared.confirmed
    missing_again = evaluate(previous=set(appeared.candidates))
    assert missing_again.candidates and not missing_again.confirmed


@pytest.mark.parametrize("previous_rows,threshold", [(0, 20), (1000, 20), (1001, 21), (8000, 160)])
@pytest.mark.parametrize("confirm", [False, True])
@pytest.mark.parametrize("extra", [0, 1])
def test_breaker_both_stages_edges(previous_rows, threshold, confirm, extra):
    missing = ids(threshold + extra)
    present = {uuid.UUID(int=10000)}
    result = evaluate(
        stored=missing | present, present=present, previous=missing if confirm else set(),
        previous_row_count=previous_rows,
    )
    if extra:
        assert result.state == "held" and result.reason == "mass_absence_held"
        assert not result.confirmed and result.held_count == len(missing)
        assert result.candidates == (missing if confirm else set())
    else:
        assert result.state == "evaluated"
        assert result.confirmed == (missing if confirm else set())
        assert result.candidates == (set() if confirm else missing)


def test_candidate_overflow_also_holds_confirmations():
    result = evaluate(stored=ids(23), present=ids(1), previous=ids(2))
    assert result.state == "held" and not result.confirmed and not result.candidates


def test_decode_internal_candidates():
    raw = [str(uuid.UUID(int=1)), "bad", None, 1]
    assert absence_candidates({"absence_candidates": raw}) == ids(1)
    assert absence_candidates({"absence_candidates": "bad"}) == set()
    assert absence_candidates({}) == set()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [None, [], {}, {"id": 2}, {"id": True, "name": "a"},
                                {"id": "2", "name": "a"}, {"id": 2, "name": 123},
                                {"id": 2, "name": " "},
                                {"id": 2, "name": "a", "description": [1]}])
async def test_dropped_or_wrong_type_row_preserves_completeness_with_invalid_evidence(bad):
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(
            200, json=[{"id": 1, "name": "valid"}, bad]
        )), base_url="https://example.invalid/api",
    )
    provider = QRadarRestProvider(base_url="https://example.invalid", sec_token="t", client=client,
                                 strict_source_enabled=True)
    try:
        walk = await provider.walk_log_sources()
    finally:
        await provider.aclose()
    assert walk.complete and walk.validated and walk.rows_dropped == 1 and walk.duplicate_ids == 0
    expected = [1]
    if isinstance(bad, dict) and type(bad.get("id")) is str:
        expected.append(int(bad["id"]))
    assert [source.qradar_id for source in walk.sources] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("across_pages", [True, False])
async def test_duplicate_id_within_or_across_pages_preserves_completeness(across_pages):
    def handler(request):
        page = int(request.headers["Range"].split("=")[1].split("-")[0])
        rows = [{"id": 1, "name": "first"}, {"id": 1, "name": "second"}]
        return httpx.Response(200, json=rows[page:page + 1] if across_pages else rows)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler),
                              base_url="https://example.invalid/api")
    provider = QRadarRestProvider(base_url="https://example.invalid", sec_token="t", client=client,
                                 page_size=1 if across_pages else 10)
    try:
        walk = await provider.walk_log_sources()
    finally:
        await provider.aclose()
    assert walk.complete and walk.validated and walk.duplicate_ids == 1 and walk.rows_dropped == 0
    assert len(walk.sources) == 2


@pytest.mark.asyncio
async def test_valid_duplicate_after_dropped_row_is_still_present_evidence():
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(
        200, json=[{"id": 1}, {"id": 1, "name": "present"}],
    )), base_url="https://example.invalid/api")
    provider = QRadarRestProvider(base_url="https://example.invalid", sec_token="t", client=client,
                                 strict_source_enabled=True)
    try:
        walk = await provider.walk_log_sources()
    finally:
        await provider.aclose()
    assert walk.complete and walk.validated and walk.rows_dropped == 1 and walk.duplicate_ids == 1
    assert [source.qradar_id for source in walk.sources] == [1]


@pytest.mark.parametrize("facts,reason", [
    ({"validated": False}, "walk_not_validated"),
    ({"rows_dropped": 1}, "rows_dropped"),
    ({"duplicate_ids": 1}, "duplicate_ids"),
])
def test_validation_guards_preserve_candidates_except_present(facts, reason):
    result = evaluate(previous=ids(3), **facts)
    assert result.state == "skipped" and result.reason == reason
    assert not result.confirmed and result.candidates == ids(3) - ids(2)
