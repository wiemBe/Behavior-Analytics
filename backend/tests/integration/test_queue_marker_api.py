"""SOC-4a: the per-user queue marker, `POST /queue/seen`, and `GET /queue`'s use of it.

Everything goes over HTTP through a committed session, except the deterministic row-lock race,
which drives the service with two sessions.
"""

from __future__ import annotations

import asyncio
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.models.queue_marker import UserQueueMarker
from app.security.rbac import Principal
from app.services.queue_marker import advance
from tests.integration.test_fleet_api import Client, _maker, run

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        "TEST_DATABASE_URL" not in os.environ,
        reason="set TEST_DATABASE_URL to run the queue marker tests",
    ),
]

SEEN = "/api/v1/queue/seen"
QUEUE = "/api/v1/queue"

ALICE = Principal(subject="alice", permissions=frozenset({"read:*"}))
BOB = Principal(subject="bob", permissions=frozenset({"read:*"}))
LOCAL_DEV = Principal(subject="local-dev", permissions=frozenset({"admin:*"}))
NOBODY = Principal(subject="nobody", permissions=frozenset())

NOW = datetime.now(UTC)
T1 = NOW - timedelta(hours=3)
T2 = NOW - timedelta(hours=2)
T3 = NOW - timedelta(hours=1)


def iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def post(client: Client, as_of, expected, ok: bool = True):  # type: ignore[no-untyped-def]
    return client.request(
        "POST",
        SEEN,
        json={"as_of": iso(as_of), "expected": iso(expected), "all_sources_ok": ok},
    )


def stored(subject: str) -> UserQueueMarker | None:
    async def read(session):  # type: ignore[no-untyped-def]
        return await session.scalar(
            select(UserQueueMarker).where(UserQueueMarker.owner_subject == subject)
        )

    return run(read)


def rows() -> int:
    async def count(session):  # type: ignore[no-untyped-def]
        return await session.scalar(select(func.count()).select_from(UserQueueMarker))

    return run(count)


def at(value: str) -> datetime:
    return datetime.fromisoformat(value)


@pytest.fixture(autouse=True)
def _schema(db_schema):  # type: ignore[no-untyped-def]
    return db_schema


# ------------------------------------------------------------------ compare-and-set
class TestCompareAndSet:
    def test_first_advance_with_null_expected(self) -> None:
        response = post(Client(ALICE), T1, None)
        assert response.status_code == 200, response.text
        body = response.json()
        assert at(body["marked_at"]) == T1
        assert body["previous_marked_at"] is None
        assert body["advanced"] is True
        assert at(body["marked_at"]) == stored("alice").marked_at

    def test_matching_expected_advances_and_keeps_the_previous(self) -> None:
        client = Client(ALICE)
        post(client, T1, None)
        body = post(client, T2, T1).json()
        assert at(body["marked_at"]) == T2
        assert at(body["previous_marked_at"]) == T1
        assert body["advanced"] is True

    def test_the_marker_is_the_as_of_sent_never_the_server_clock(self) -> None:
        post(Client(ALICE), T1, None)
        assert stored("alice").marked_at == T1

    def test_a_stale_expected_is_409_with_the_current_marker_and_no_write(self) -> None:
        client = Client(ALICE)
        post(client, T1, None)
        post(client, T2, T1)
        response = post(client, T3, T1)  # another tab already moved it to T2
        assert response.status_code == 409
        detail = response.json()["detail"]
        assert detail["code"] == "queue.marker.changed"
        assert at(detail["marker"]["marked_at"]) == T2
        assert at(detail["marker"]["previous_marked_at"]) == T1
        assert stored("alice").marked_at == T2

    def test_a_null_expected_against_a_stored_marker_is_409(self) -> None:
        client = Client(ALICE)
        post(client, T1, None)
        response = post(client, T2, None)
        assert response.status_code == 409
        assert at(response.json()["detail"]["marker"]["marked_at"]) == T1
        assert stored("alice").marked_at == T1

    def test_a_non_null_expected_with_no_row_is_409_and_creates_nothing(self) -> None:
        response = post(Client(ALICE), T2, T1)
        assert response.status_code == 409
        assert response.json()["detail"]["marker"]["marked_at"] is None
        assert rows() == 0


# ------------------------------------------------------------------- forward only
class TestForwardOnly:
    def test_an_older_as_of_is_a_no_op_that_returns_the_stored_value(self) -> None:
        client = Client(ALICE)
        post(client, T2, None)
        response = post(client, T1, T2)
        assert response.status_code == 200
        body = response.json()
        assert at(body["marked_at"]) == T2
        assert body["advanced"] is False
        assert stored("alice").marked_at == T2
        assert stored("alice").previous_marked_at is None

    def test_an_equal_as_of_is_a_no_op(self) -> None:
        client = Client(ALICE)
        post(client, T2, None)
        body = post(client, T2, T2).json()
        assert body["advanced"] is False
        assert stored("alice").previous_marked_at is None


# ----------------------------------------------------------------------- the future
class TestFuture:
    def test_beyond_the_skew_is_422_and_writes_nothing(self) -> None:
        response = post(Client(ALICE), datetime.now(UTC) + timedelta(minutes=10), None)
        assert response.status_code == 422
        assert response.json()["detail_code"] == "queue.marker.future"
        assert rows() == 0

    def test_within_the_skew_is_accepted(self) -> None:
        response = post(Client(ALICE), datetime.now(UTC) + timedelta(seconds=30), None)
        assert response.status_code == 200, response.text

    def test_a_naive_timestamp_is_read_as_utc(self) -> None:
        naive = (NOW - timedelta(hours=1)).replace(tzinfo=None)
        response = Client(ALICE).request(
            "POST",
            SEEN,
            json={"as_of": naive.isoformat(), "expected": None, "all_sources_ok": True},
        )
        assert response.status_code == 200, response.text
        assert stored("alice").marked_at == NOW - timedelta(hours=1)


# ------------------------------------------------------------------- failed source
class TestSourceFailed:
    def test_false_is_409_with_its_code_and_no_write(self) -> None:
        response = post(Client(ALICE), T1, None, ok=False)
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "queue.marker.source_failed"
        assert rows() == 0

    def test_false_leaves_an_existing_marker_where_it_was(self) -> None:
        client = Client(ALICE)
        post(client, T1, None)
        response = post(client, T2, T1, ok=False)
        assert response.status_code == 409
        assert at(response.json()["detail"]["marker"]["marked_at"]) == T1
        assert stored("alice").marked_at == T1

    def test_the_flag_is_required(self) -> None:
        response = Client(ALICE).request(
            "POST", SEEN, json={"as_of": iso(T1), "expected": None}
        )
        assert response.status_code == 422


# ------------------------------------------------------------------------ the race
class TestRace:
    def test_two_concurrent_posts_with_the_same_expected_one_wins(self) -> None:
        post(Client(ALICE), T1, None)
        barrier = threading.Barrier(2)

        def attempt(as_of: datetime) -> int:
            client = Client(ALICE)
            barrier.wait()
            return post(client, as_of, T1).status_code

        with ThreadPoolExecutor(2) as pool:
            codes = sorted(pool.map(attempt, [T2, T3]))
        assert codes == [200, 409]

    def test_two_concurrent_first_writes_one_wins(self) -> None:
        barrier = threading.Barrier(2)

        def attempt(as_of: datetime) -> int:
            client = Client(ALICE)
            barrier.wait()
            return post(client, as_of, None).status_code

        with ThreadPoolExecutor(2) as pool:
            codes = sorted(pool.map(attempt, [T1, T2]))
        assert codes == [200, 409]
        assert rows() == 1

    def test_a_second_writer_waits_on_the_row_and_then_loses(self) -> None:
        """Deterministic: B holds no answer while A has the row, and B reads A's result."""
        post(Client(ALICE), T1, None)

        async def scenario() -> None:
            engine_a, maker_a = _maker()
            engine_b, maker_b = _maker()
            try:
                async with maker_a() as a, maker_b() as b:
                    await advance(a, "alice", as_of=T2, expected=T1, all_sources_ok=True)
                    second = asyncio.create_task(
                        advance(b, "alice", as_of=T3, expected=T1, all_sources_ok=True)
                    )
                    await asyncio.sleep(0.5)
                    assert not second.done(), "the second writer did not wait for the row lock"
                    await a.commit()
                    with pytest.raises(Exception) as caught:
                        await second
                    assert getattr(caught.value, "status_code", None) == 409
            finally:
                await engine_a.dispose()
                await engine_b.dispose()

        asyncio.run(scenario())
        assert stored("alice").marked_at == T2


# --------------------------------------------------------------------- isolation
class TestSubjectIsolation:
    def test_one_users_marker_never_moves_or_shows_in_anothers(self) -> None:
        post(Client(ALICE), T1, None)
        # Bob has no marker: null expected is right for him, whatever Alice holds.
        assert post(Client(BOB), T3, None).status_code == 200
        assert stored("alice").marked_at == T1
        assert stored("bob").marked_at == T3
        assert Client(ALICE).get(QUEUE).json()["marker"]["marked_at"] == iso_z(T1)
        assert Client(BOB).get(QUEUE).json()["marker"]["marked_at"] == iso_z(T3)

    def test_bobs_stale_expected_does_not_see_alices_value(self) -> None:
        post(Client(ALICE), T1, None)
        response = post(Client(BOB), T2, T1)
        assert response.status_code == 409
        assert response.json()["detail"]["marker"]["marked_at"] is None

    def test_the_local_dev_principal_works(self) -> None:
        client = Client(LOCAL_DEV)
        assert post(client, T1, None).status_code == 200
        assert client.get(QUEUE).json()["marker"]["kind"] == "STORED"
        assert stored("local-dev").marked_at == T1

    def test_a_principal_with_no_permission_may_move_its_own_marker(self) -> None:
        assert post(Client(NOBODY), T1, None).status_code == 200
        assert stored("nobody").marked_at == T1


def iso_z(value: datetime) -> str:
    """The wire form the marker comes back in, parsed back for equality."""
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


# ------------------------------------------------------------------ GET /queue
class TestQueueUsesTheMarker:
    def test_stored_marker_is_the_comparison_instant(self) -> None:
        post(Client(ALICE), T1, None)
        body = Client(ALICE).get(QUEUE).json()
        assert body["marker"]["kind"] == "STORED"
        assert at(body["marker"]["compare_from"]) == T1
        assert at(body["marker"]["marked_at"]) == T1

    def test_since_overrides_the_stored_marker_but_still_reports_it(self) -> None:
        post(Client(ALICE), T1, None)
        since = (NOW - timedelta(hours=8)).strftime("%Y-%m-%dT%H:%M:%SZ")
        body = Client(ALICE).get(QUEUE, params={"since": since}).json()
        assert body["marker"]["kind"] == "OVERRIDE"
        assert at(body["marker"]["compare_from"]) == at(since.replace("Z", "+00:00"))
        assert at(body["marker"]["marked_at"]) == T1

    def test_without_a_stored_marker_it_is_the_default_window(self) -> None:
        body = Client(ALICE).get(QUEUE).json()
        assert body["marker"]["kind"] == "DEFAULT_WINDOW"
        assert body["marker"]["marked_at"] is None
        as_of = at(body["as_of"])
        assert as_of - at(body["marker"]["compare_from"]) == timedelta(hours=24)

    def test_another_users_marker_does_not_leak_into_the_window(self) -> None:
        post(Client(BOB), T1, None)
        assert Client(ALICE).get(QUEUE).json()["marker"]["kind"] == "DEFAULT_WINDOW"

    def test_get_queue_writes_nothing(self) -> None:
        client = Client(ALICE)
        client.get(QUEUE)
        assert rows() == 0
        post(client, T1, None)
        before = stored("alice")
        client.get(QUEUE)
        client.get(QUEUE, params={"since": iso_z(T2)})
        after = stored("alice")
        assert rows() == 1
        assert (after.marked_at, after.previous_marked_at, after.updated_at) == (
            before.marked_at,
            before.previous_marked_at,
            before.updated_at,
        )

    def test_with_every_source_forbidden_the_page_still_runs_no_statement(self) -> None:
        post(Client(NOBODY), T1, None)
        client = Client(NOBODY)
        body = client.get(QUEUE).json()
        assert client.statements == 0
        assert body["marker"]["kind"] == "DEFAULT_WINDOW"
