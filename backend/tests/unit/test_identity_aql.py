"""Identity AQL, chunking, the 0069 caller order, and the collection beat."""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.identity.aql import build_query, validate_query_inputs
from app.identity.dto import IdentitySearchKind
from app.identity.plan import plan_chunks
from app.identity.reference import IdentityFieldMapping, IdentityReferenceError
from app.identity.rows import parse_identity_rows
from app.models.ariel_search import CALLERS
from app.models.enums import (
    IDENTITY_EVENT_CATALOG,
    SearchCadence,
    SearchFamily,
    default_search_cadence,
)
from app.providers.base import ProviderError

CATALOG = [event_id for event_id, _kind in IDENTITY_EVENT_CATALOG]
START = 1_700_000_000_000
END = START + 300_000
BUCKET_MS = 300_000


def _query(
    kind: IdentitySearchKind, sources: tuple[int, ...] = (1003, 1004), **over: object
) -> str:
    values: dict[str, object] = {
        "source_ids": sources,
        "event_id_property": "EventID",
        "username_property": "username",
        "event_ids": CATALOG,
        "max_sources": 50,
    }
    values.update(over)
    spec = validate_query_inputs(kind, **values)  # type: ignore[arg-type]
    return build_query(spec, start_ms=START, end_ms=END, bucket_ms=BUCKET_MS, max_rows=5000)


def test_three_searches_cover_a_chunk_and_limit_is_one_past_the_cap() -> None:
    sources = tuple(range(1003, 1011))
    queries = [_query(kind, sources) for kind in IdentitySearchKind]
    assert len(queries) == 3
    assert len({kind.value for kind in IdentitySearchKind}) == 3
    for query in queries:
        assert query.endswith("LIMIT 5001")
        assert "LIMIT 5000" not in query
        assert 'SUM(eventcount) AS "events"' in query
        for source_id in sources:
            assert str(source_id) in query
        assert "alice" not in query
        assert "mallory" not in query
    counts, rare, volume = queries
    assert "4625" in counts and "1102" in counts
    assert "4720" in rare and "4625" not in rare
    assert "4625" in volume and "4740" in volume and "4720" not in volume
    assert '"username"' in rare and '"username"' not in counts


def test_limit_is_n_plus_one() -> None:
    spec = validate_query_inputs(
        IdentitySearchKind.COUNTS,
        source_ids=(1003,),
        event_id_property="EventID",
        username_property="username",
        event_ids=(4625,),
        max_sources=50,
    )
    query = build_query(spec, start_ms=START, end_ms=END, bucket_ms=BUCKET_MS, max_rows=5)
    assert query.endswith("LIMIT 6")
    assert "LIMIT 5" not in query


def test_qid_mapping_uses_win_literals() -> None:
    query = _query(IdentitySearchKind.COUNTS, (1003,), event_id_property="qid")
    assert "QIDNAME(qid)" in query
    assert "'WIN-4625'" in query
    assert "EventID" not in query


def test_one_chunk_per_mapping_not_per_source_or_username() -> None:
    shared = IdentityFieldMapping.from_parts("EventID", "username", None, CATALOG)
    other = IdentityFieldMapping.from_parts("qid", "username", None, CATALOG)
    rows = [(1003 + index, uuid4(), shared) for index in range(5)]
    assert len(plan_chunks(rows, max_sources_per_search=50)) == 1
    split = plan_chunks([*rows, (2000, uuid4(), other)], max_sources_per_search=50)
    assert len(split) == 2
    assert {len(chunk.sources) for chunk in split} == {5, 1}
    assert "usernames" not in validate_query_inputs.__code__.co_varnames
    assert "usernames" not in build_query.__code__.co_varnames


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("event_id_property", 'bad"name'),
        ("event_id_property", "bad\\name"),
        ("username_property", "username OR 1=1"),
        ("event_id_property", "sourceip"),
        ("event_id_property", "EventID\n"),
        ("username_property", "user\x00name"),
    ],
)
def test_identifiers_are_rejected_and_not_echoed(field: str, value: str) -> None:
    kwargs: dict[str, object] = {
        "source_ids": (1003,),
        "event_id_property": "EventID",
        "username_property": "username",
        "event_ids": (4625,),
        "max_sources": 50,
    }
    kwargs[field] = value
    with pytest.raises(IdentityReferenceError) as caught:
        validate_query_inputs(IdentitySearchKind.COUNTS, **kwargs)  # type: ignore[arg-type]
    assert value not in str(caught.value)


def test_empty_sources_and_non_integers_are_refused_without_the_value() -> None:
    with pytest.raises(IdentityReferenceError) as empty:
        validate_query_inputs(
            IdentitySearchKind.COUNTS,
            source_ids=[],
            event_id_property="EventID",
            username_property="username",
            event_ids=(4625,),
            max_sources=50,
        )
    assert "at least one" in str(empty.value)
    with pytest.raises(IdentityReferenceError) as text_id:
        validate_query_inputs(
            IdentitySearchKind.COUNTS,
            source_ids=["1003"],  # type: ignore[list-item]
            event_id_property="EventID",
            username_property="username",
            event_ids=(4625,),
            max_sources=50,
        )
    assert "1003" not in str(text_id.value)
    with pytest.raises(IdentityReferenceError) as flag:
        validate_query_inputs(
            IdentitySearchKind.COUNTS,
            source_ids=[True],  # type: ignore[list-item]
            event_id_property="EventID",
            username_property="username",
            event_ids=[True],  # type: ignore[list-item]
            max_sources=50,
        )
    assert "True" not in str(flag.value)


def test_a_row_outside_the_ask_fails_the_search_without_quoting_it() -> None:
    start = datetime(2026, 11, 13, 11, 55, tzinfo=UTC)
    with pytest.raises(ProviderError) as caught:
        parse_identity_rows(
            [{"log_source_id": 9999, "event_id": 4625, "k": 0, "events": 1}],
            source_ids=(1003,),
            event_ids=(4625,),
            window_start=start,
            window_end=start,
            bucket_seconds=300,
            max_rows=10,
            with_username=False,
        )
    assert "9999" not in str(caught.value)


def test_0069_caller_check_matches_the_model_and_follows_0068() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "0069_identity_collection.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0069_identity", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "0069"
    assert module.down_revision == "0068"
    assert ", ".join(f"'{item}'" for item in CALLERS) == module._NEW_CALLERS
    assert module._NEW_CALLERS.endswith("'ACCESS_CHECK', 'IDENTITY_COLLECTION'")
    assert "IDENTITY_COLLECTION" not in module._OLD_CALLERS


def test_identity_beat_entries_match_the_tasks() -> None:
    from app.workers import tasks
    from app.workers.celery_app import celery_app

    schedule = celery_app.conf.beat_schedule
    assert schedule["dispatch-identity-collection"] == {
        "task": "dispatch_identity_collection",
        "schedule": 60.0,
    }
    retention = schedule["apply-identity-retention"]
    assert retention["task"] == "apply_identity_retention"
    assert retention["schedule"] == float(Settings().identity_retention_interval_seconds)
    assert tasks.dispatch_identity_collection_task.name == "dispatch_identity_collection"
    assert tasks.apply_identity_retention_task.name == "apply_identity_retention"
    assert "dispatch_identity_collection" in celery_app.tasks
    assert "apply_identity_retention" in celery_app.tasks
    cadence = default_search_cadence(SearchFamily.IDENTITY_COLLECTION)
    assert cadence is SearchCadence.FIFTEEN_MINUTES


async def test_dispatch_disabled_opens_no_session(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.workers import tasks

    monkeypatch.setattr(
        tasks, "get_settings", lambda: Settings(identity_collection_enabled=False)
    )

    def boom() -> None:
        raise AssertionError("session opened")

    monkeypatch.setattr(tasks, "get_sessionmaker", boom)
    result = await tasks.dispatch_identity_collection()
    assert result == {"status": "disabled", "instances": 0, "results": []}
