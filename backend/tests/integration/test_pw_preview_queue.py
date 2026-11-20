"""The property-watch preview is a queued task, not a held request (SCALE-2h3, D-87, D-92).

DB-gated. `POST .../preview` keeps its guards, takes the watch's lock and answers 202 without
building a provider; one preview per watch at a time (409 naming since when); the task runs
what the route used to run, writes the same audit row, releases the lock whatever happens, and
leaves its result only in the store (a dict-backed stand-in for Redis: this suite has none, so
real expiry timing and a real server's `NX` are not exercised). `GET .../preview/{id}` refuses
another watch's or another user's id, calls an unknown id EXPIRED, and shows a failure as a
catalog code, never an exception text.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.api.routes import property_watches as routes
from app.api.routes.property_watches import get_preview_store_factory
from app.models.base import Base
from app.models.property_watch import PropertyWatch
from app.providers.mock import MockQRadarProvider
from app.services.property_watch_preview import KEY_PREFIX, LOCK_PREFIX
from app.workers import tasks
from tests.integration.test_property_watches_api import (  # noqa: F401
    PREFIX,
    _audits,
    _client,
    _create,
    _Previews,
    _principal,
    _reset_rate_limit,
    _scripted,
    _seed,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        "TEST_DATABASE_URL" not in os.environ,
        reason="set TEST_DATABASE_URL to run property-watch integration tests",
    ),
]

@pytest.fixture
def previews(monkeypatch: pytest.MonkeyPatch) -> _Previews:
    return _Previews(monkeypatch)


ENGINEER = "detection_engineer"
CONFLICT = "pw.preview.conflict.running"


def _forbid_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the request must not build a provider")

    monkeypatch.setattr(routes, "build_provider_for_instance", forbidden)
    monkeypatch.setattr("app.providers.factory.build_provider_for_instance", forbidden)
    monkeypatch.setattr(tasks, "build_provider_for_instance", forbidden)


async def _watch(db_session, db_schema: str, **client_args):  # type: ignore[no-untyped-def]
    await _seed(db_session)
    provider = _scripted(MockQRadarProvider(seed=1))
    client = _client(db_schema, _principal(ENGINEER), provider=provider, **client_args)
    return client, provider, _create(client, "queued")


def _status(client, watch_id: str, preview_id: str):  # type: ignore[no-untyped-def]
    return client.get(f"{PREFIX}/{watch_id}/preview/{preview_id}")


async def test_the_route_queues_and_builds_no_provider(
    db_session, db_schema: str, monkeypatch: pytest.MonkeyPatch, previews: _Previews
) -> None:
    client, provider, made = await _watch(db_session, db_schema)
    _forbid_provider(monkeypatch)
    queued = client.post(f"{PREFIX}/{made['id']}/preview")
    assert queued.status_code == 202, queued.text
    body = queued.json()
    assert set(body) == {"preview_id", "watch_id", "requested_at"}
    assert body["watch_id"] == made["id"]
    assert (datetime.now(UTC) - datetime.fromisoformat(body["requested_at"])) < timedelta(
        seconds=30
    )
    assert previews.queued == [([body["preview_id"], made["id"], ENGINEER], body["preview_id"])]
    assert provider.pw_searches == [], "nothing was sent to QRadar by the request"
    state = _status(client, made["id"], body["preview_id"]).json()
    assert state["status"] == "QUEUED" and state["result"] is None
    assert datetime.fromisoformat(state["requested_at"]) == datetime.fromisoformat(
        body["requested_at"]
    )


async def test_a_second_preview_of_the_watch_is_409_naming_since_and_frees_after_the_task(
    db_session, db_schema: str, previews: _Previews
) -> None:
    client, provider, made = await _watch(db_session, db_schema)
    first = client.post(f"{PREFIX}/{made['id']}/preview").json()
    second = client.post(f"{PREFIX}/{made['id']}/preview")
    assert second.status_code == 409, second.text
    detail = second.json()
    assert detail["detail_code"] == CONFLICT
    assert datetime.fromisoformat(detail["detail_params"]["since"]) == datetime.fromisoformat(
        first["requested_at"]
    )
    assert detail["detail"].endswith(detail["detail_params"]["since"])
    assert len(previews.queued) == 1, "the refused request queued nothing"
    other = _create(client, "other-watch")
    assert client.post(f"{PREFIX}/{other['id']}/preview").status_code == 202, "per watch"
    await previews.run(client)
    assert len(provider.pw_searches) == 4
    again = client.post(f"{PREFIX}/{made['id']}/preview")
    assert again.status_code == 202, "the task released the lock"


async def test_the_lock_is_released_after_a_failed_task_and_the_failure_has_no_text(
    db_session, db_schema: str, previews: _Previews
) -> None:
    client, provider, made = await _watch(db_session, db_schema)
    provider.pw_fault = lambda _s: RuntimeError("boom https://qradar.internal SEC=abc123")
    queued = client.post(f"{PREFIX}/{made['id']}/preview").json()
    ran = await previews.run(client)
    assert ran[0]["status"] == "failed" and ran[0]["reason_code"] == "pw.preview.worker_failed"
    read = _status(client, made["id"], queued["preview_id"])
    body = read.json()
    assert body["status"] == "FAILED" and body["result"] is None
    assert body["reason_code"] == "pw.preview.worker_failed"
    assert "qradar.internal" not in read.text and "SEC=" not in read.text
    assert LOCK_PREFIX + made["id"] not in client.store._client.values  # type: ignore[attr-defined]
    assert client.post(f"{PREFIX}/{made['id']}/preview").status_code == 202
    assert await _audits(db_session, "property_watch.preview") == [], "no result, no audit"


async def test_the_lock_is_released_after_a_successful_task(
    db_session, db_schema: str, previews: _Previews
) -> None:
    client, _, made = await _watch(db_session, db_schema)
    client.post(f"{PREFIX}/{made['id']}/preview")
    assert LOCK_PREFIX + made["id"] in client.store._client.values  # type: ignore[attr-defined]
    await previews.run(client)
    assert LOCK_PREFIX + made["id"] not in client.store._client.values  # type: ignore[attr-defined]


async def test_the_task_writes_the_audit_row_the_route_wrote(
    db_session, db_schema: str, previews: _Previews
) -> None:
    client, _, made = await _watch(db_session, db_schema)
    result = (await previews.preview(client, made["id"]))["result"]
    rows = await _audits(db_session, "property_watch.preview")
    assert len(rows) == 1
    audit = rows[0]
    assert audit.actor == ENGINEER and audit.object_type == "property_watch"
    assert audit.object_id == made["id"] and audit.outcome == "SUCCESS"
    assert audit.detail == {
        "watch_version": result["watch_version"],
        "searches": 2,
        "outcome": "MEASURED",
        "buckets_measured": result["buckets_measured"],
        "buckets_lower_bound": result["buckets_lower_bound"],
        "buckets_unmeasured": result["buckets_unmeasured"],
        "rows": 2,
        "total_events": result["total_events"],
        "parsed_events": result["parsed_events"],
        "property_parses": True,
    }


async def test_a_preview_adds_no_row_but_its_audit_and_its_search_log(
    db_session, db_schema: str, previews: _Previews
) -> None:
    client, _, made = await _watch(db_session, db_schema)

    async def counts() -> dict[str, int]:
        db_session.expire_all()
        out: dict[str, int] = {}
        for table in Base.metadata.sorted_tables:
            out[table.name] = int(
                await db_session.scalar(select(func.count()).select_from(table)) or 0
            )
        return out

    before = await counts()
    read = await previews.preview(client, made["id"])
    assert read["status"] == "DONE"
    after = await counts()
    changed = {name: after[name] - before[name] for name in after if after[name] != before[name]}
    assert changed == {"audit_log": 1, "ariel_search_run": 2}, changed


async def test_the_read_refuses_another_watch_and_another_user(
    db_session, db_schema: str, previews: _Previews
) -> None:
    client, _, made = await _watch(db_session, db_schema)
    other_watch = _create(client, "second")
    queued = client.post(f"{PREFIX}/{made['id']}/preview").json()["preview_id"]
    await previews.run(client)
    assert _status(client, made["id"], queued).json()["status"] == "DONE"
    wrong_watch = _status(client, other_watch["id"], queued)
    assert wrong_watch.status_code == 404, wrong_watch.text
    admin = _client(db_schema, _principal("admin"), provider=MockQRadarProvider())
    admin.app.dependency_overrides[get_preview_store_factory] = (  # type: ignore[attr-defined]
        lambda: lambda _settings: client.store  # type: ignore[attr-defined]
    )
    stranger = _status(admin, made["id"], queued)
    assert stranger.status_code == 404, stranger.text
    assert "SeededDave" not in stranger.text


async def test_an_unknown_or_expired_id_is_expired_not_an_error(
    db_session, db_schema: str, previews: _Previews
) -> None:
    client, _, made = await _watch(db_session, db_schema)
    unknown = _status(client, made["id"], str(uuid.uuid4()))
    assert unknown.status_code == 200
    assert unknown.json()["status"] == "EXPIRED" and unknown.json()["result"] is None
    queued = client.post(f"{PREFIX}/{made['id']}/preview").json()["preview_id"]
    await previews.run(client)
    assert _status(client, made["id"], queued).json()["status"] == "DONE"
    client.store._client.expire(KEY_PREFIX + queued)  # type: ignore[attr-defined]
    assert _status(client, made["id"], queued).json()["status"] == "EXPIRED"
    missing = _status(client, str(uuid.UUID(int=0)), queued)
    assert missing.status_code == 404, "an unknown watch is still 404"


async def test_the_result_lives_only_under_its_expiry(
    db_session, db_schema: str, previews: _Previews
) -> None:
    client, _, made = await _watch(db_session, db_schema, pw_preview_result_ttl_seconds=77)
    queued = client.post(f"{PREFIX}/{made['id']}/preview").json()["preview_id"]
    assert client.store._client.ttls[KEY_PREFIX + queued] >= client.store.lock_ttl  # type: ignore[attr-defined]
    await previews.run(client)
    assert client.store._client.ttls[KEY_PREFIX + queued] == 77  # type: ignore[attr-defined]


async def test_a_scope_that_stopped_resolving_fails_with_its_code_and_no_audit(
    db_session, db_schema: str, previews: _Previews
) -> None:
    world = await _seed(db_session)
    provider = _scripted(MockQRadarProvider(seed=1))
    client = _client(db_schema, _principal(ENGINEER), provider=provider)
    group = _create(client, "g", scope_kind="LOG_SOURCE_GROUP", scope_type_id=None,
                    scope_group_id=10)
    queued = client.post(f"{PREFIX}/{group['id']}/preview").json()["preview_id"]
    world.other.group_ids = []
    world.source.group_ids = []
    await db_session.commit()
    ran = await previews.run(client)
    assert ran[0]["status"] == "failed", ran
    body = _status(client, group["id"], queued).json()
    assert body["status"] == "FAILED" and body["reason_code"] == "pw.preview.scope_unresolved"
    assert "SCOPE_EMPTY" in body["reason"]
    assert provider.pw_searches == []
    assert await _audits(db_session, "property_watch.preview") == []
    assert client.post(f"{PREFIX}/{group['id']}/preview").status_code == 409, "scope refused"


async def test_a_watch_deleted_while_queued_is_failed_and_frees_the_lock(
    db_session, db_schema: str, previews: _Previews
) -> None:
    client, provider, made = await _watch(db_session, db_schema)
    queued = client.post(f"{PREFIX}/{made['id']}/preview").json()["preview_id"]
    row = await db_session.get(PropertyWatch, uuid.UUID(made["id"]))
    await db_session.delete(row)
    await db_session.commit()
    await previews.run(client)
    record = client.store.get(queued)  # type: ignore[attr-defined]
    assert record["status"] == "FAILED" and record["reason_code"] == "pw.preview.watch_gone"
    assert LOCK_PREFIX + made["id"] not in client.store._client.values  # type: ignore[attr-defined]
    assert provider.pw_searches == []


async def test_a_duplicate_delivery_does_nothing_and_leaves_the_lock_alone(
    db_session, db_schema: str, previews: _Previews
) -> None:
    client, provider, made = await _watch(db_session, db_schema)
    first = client.post(f"{PREFIX}/{made['id']}/preview").json()
    args = list(previews.queued[0][0])
    await previews.run(client)
    assert client.post(f"{PREFIX}/{made['id']}/preview").status_code == 202, "a new preview"
    previews.queued.clear()  # the new preview stays queued: it holds the lock under test
    searches = len(provider.pw_searches)
    previews.queued.append((args, first["preview_id"]))
    redelivered = (await previews.run(client))[0]
    assert redelivered["status"] == "not_queued"
    assert len(provider.pw_searches) == searches
    held = client.post(f"{PREFIX}/{made['id']}/preview")
    assert held.status_code == 409, "the redelivery did not release the new preview's lock"


async def test_a_running_preview_whose_worker_is_gone_reads_as_failed(
    db_session, db_schema: str, previews: _Previews
) -> None:
    client, _, made = await _watch(db_session, db_schema)
    queued = client.post(f"{PREFIX}/{made['id']}/preview").json()["preview_id"]
    store = client.store  # type: ignore[attr-defined]
    record = store.get(queued)
    store.update(
        record, "RUNNING",
        requested_at=(datetime.now(UTC) - timedelta(seconds=store.lock_ttl + 5)).isoformat(),
    )
    body = _status(client, made["id"], queued).json()
    assert body["status"] == "FAILED" and body["reason_code"] == "pw.preview.worker_lost"
    fresh = store.get(queued)
    store.update(fresh, "RUNNING", requested_at=datetime.now(UTC).isoformat())
    assert _status(client, made["id"], queued).json()["status"] == "RUNNING"


async def test_a_broker_failure_is_a_coded_503_and_frees_the_watch(
    db_session, db_schema: str, monkeypatch: pytest.MonkeyPatch, previews: _Previews
) -> None:
    client, _, made = await _watch(db_session, db_schema)

    def down(*_args: object, **_kwargs: object) -> None:
        raise ConnectionError("broker down")

    monkeypatch.setattr(tasks.run_property_watch_preview_task, "apply_async", down)
    refused = client.post(f"{PREFIX}/{made['id']}/preview")
    assert refused.status_code == 503, refused.text
    assert refused.json()["detail_code"] == "pw.preview.not_queued"
    assert "broker down" not in refused.text
    assert client.store._client.values == {}  # type: ignore[attr-defined]


async def test_a_redis_outage_fails_closed_on_both_routes(
    db_session, db_schema: str, previews: _Previews
) -> None:
    client, provider, made = await _watch(db_session, db_schema)
    queued = client.post(f"{PREFIX}/{made['id']}/preview").json()["preview_id"]
    client.store._client.fail = True  # type: ignore[attr-defined]
    post = client.post(f"{PREFIX}/{_create_name(client)}/preview")
    assert post.status_code == 503 and post.json()["detail_code"] == "pw.preview.not_queued"
    read = _status(client, made["id"], queued)
    assert read.status_code == 503 and read.json()["detail_code"] == "pw.preview.store_unreadable"
    assert provider.pw_searches == []


def _create_name(client) -> str:  # type: ignore[no-untyped-def]
    return _create(client, f"w-{uuid.uuid4().hex[:6]}")["id"]


async def test_the_read_needs_the_write_permission(
    db_session, db_schema: str, previews: _Previews
) -> None:
    client, _, made = await _watch(db_session, db_schema)
    queued = client.post(f"{PREFIX}/{made['id']}/preview").json()["preview_id"]
    viewer = _client(db_schema, _principal("viewer"), provider=MockQRadarProvider())
    viewer.app.dependency_overrides[get_preview_store_factory] = (  # type: ignore[attr-defined]
        lambda: lambda _settings: client.store  # type: ignore[attr-defined]
    )
    denied = _status(viewer, made["id"], queued)
    assert denied.status_code == 403, denied.text
    assert "property_watch:write" in denied.json()["detail"]
