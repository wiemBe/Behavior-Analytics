"""The public whole-app health answer.

The endpoint is exercised through the real ``create_app()`` so its open surface
and rate-limit exemption are covered too. Its seven checks are patched at the
module boundary: Redis, Celery and a live migration head are not part of a unit
test, and the status mapping is what these tests pin. The small check functions
get their own guard tests with replaceable edges.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.routes import healthz
from app.core.config import get_settings
from app.core.database import get_session
from app.core.rate_limit import limiter
from app.main import create_app

_CHECK_NAMES = ("database", "migrations", "redis", "workers", "walker", "beat")


@pytest.fixture(autouse=True)
def _reset_cache_and_limiter() -> Iterator[None]:
    healthz.invalidate_healthz_cache()
    limiter.reset()
    yield
    healthz.invalidate_healthz_cache()
    limiter.reset()


async def _no_session() -> AsyncIterator[object]:
    yield object()


def _client() -> TestClient:
    app = create_app()
    app.dependency_overrides[get_session] = _no_session
    return TestClient(app)


def _patch_checks(
    monkeypatch: pytest.MonkeyPatch,
    *,
    failing: str | None = None,
    hang: str | None = None,
    release: asyncio.Event | None = None,
) -> list[str]:
    """Replace every check with a bounded fake and record which ones ran."""
    calls: list[str] = []

    for name in _CHECK_NAMES:
        if name == hang:
            assert release is not None

            async def stuck(session: object = None, *, name: str = name) -> bool:
                del session
                calls.append(name)
                await release.wait()  # type: ignore[union-attr]
                return True

            check = stuck
        else:

            async def answer(session: object = None, *, name: str = name) -> bool:
                del session
                calls.append(name)
                return name != failing

            check = answer
        monkeypatch.setattr(healthz, f"_check_{name}", check)
    return calls


def test_all_ok_answers_200_with_the_exact_public_body(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_checks(monkeypatch)
    client = _client()
    response = client.get("/api/v1/healthz")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "components": dict.fromkeys(healthz.COMPONENTS, "ok"),
    }


@pytest.mark.parametrize("failing", _CHECK_NAMES)
def test_one_failing_component_degrades_only_that_one(
    monkeypatch: pytest.MonkeyPatch, failing: str
) -> None:
    _patch_checks(monkeypatch, failing=failing)
    client = _client()
    response = client.get("/api/v1/healthz")

    assert response.status_code == 503
    assert response.json() == {
        "status": "degraded",
        "components": {
            name: "fail" if name == failing else "ok" for name in healthz.COMPONENTS
        },
    }


async def test_a_hanging_check_becomes_unknown_inside_the_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = asyncio.Event()
    calls = _patch_checks(monkeypatch, hang="workers", release=release)
    monkeypatch.setattr(healthz, "CHECK_TIMEOUT_SECONDS", 0.05)
    app = create_app()
    app.dependency_overrides[get_session] = _no_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://backend") as client:
        response = await asyncio.wait_for(client.get("/api/v1/healthz"), timeout=1.0)
    # Let the blocked check finish so no task is left pending after the test.
    release.set()

    assert response.status_code == 503
    assert response.json()["components"]["workers"] == "unknown"
    assert calls.count("workers") == 1


def test_two_calls_within_ten_seconds_run_the_checks_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _patch_checks(monkeypatch)
    client = _client()
    first = client.get("/api/v1/healthz")
    second = client.get("/api/v1/healthz")
    healthz.invalidate_healthz_cache()
    third = client.get("/api/v1/healthz")

    assert [first.status_code, second.status_code, third.status_code] == [200, 200, 200]
    assert calls == list(_CHECK_NAMES) * 2


def test_body_carries_no_host_version_count_or_error_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "SuperSecretToken0123456789"
    _patch_checks(monkeypatch)

    async def leaky() -> bool:
        raise RuntimeError(f"redis at db.internal.example with {secret}; worker-9 refused 3")

    monkeypatch.setattr(healthz, "_check_redis", leaky)
    client = _client()
    response = client.get("/api/v1/healthz")

    body = response.json()
    assert response.status_code == 503
    assert set(body) == {"status", "components"}
    assert set(body["components"]) == set(healthz.COMPONENTS)
    assert set(body["components"].values()) <= {"ok", "fail", "unknown"}
    assert body["components"]["redis"] == "fail"
    for needle in (secret, "db.internal.example", "worker-9", "version", "revision", "host"):
        assert needle not in response.text.lower()


def test_public_and_rate_limit_exempt(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_checks(monkeypatch)
    monkeypatch.setattr(get_settings(), "rate_limit_default", "1/minute")
    client = _client()
    statuses = [client.get("/api/v1/healthz").status_code for _ in range(4)]

    assert statuses == [200, 200, 200, 200]


def test_proxy_config_routes_the_public_path_to_the_backend() -> None:
    conf = Path(__file__).resolve().parents[3] / "deploy" / "proxy" / "default.conf"
    text = conf.read_text(encoding="utf-8")
    public = text[text.index("listen 443 ssl") :]
    block = public[public.index("location = /healthz") :]
    block = block[: block.index("\n    }")]

    assert "access_log off;" in block
    assert "proxy_pass $backend_upstream/api/v1/healthz;" in block
    # The loopback container healthcheck is untouched.
    assert 'return 200 "ok\\n";' in text[: text.index("listen 443 ssl")]


class _Result:
    def __init__(self, rows: list[tuple[str, ...]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[str, ...]]:
        return self._rows


class _FakeSession:
    def __init__(self, rows: list[tuple[str, ...]]) -> None:
        self.rows = rows

    async def execute(self, statement: object) -> _Result:
        del statement
        return _Result(self.rows)


@pytest.mark.parametrize(
    ("head", "rows", "expected"),
    [
        ("abc123", [("abc123",)], True),
        ("abc123", [("old",)], False),
        ("abc123", [], False),
        (None, [("abc123",)], False),
    ],
)
async def test_migration_check_matches_exactly_one_head_row(
    monkeypatch: pytest.MonkeyPatch,
    head: str | None,
    rows: list[tuple[str, ...]],
    expected: bool,
) -> None:
    monkeypatch.setattr(healthz, "migration_head", lambda: head)
    assert await healthz._check_migrations(_FakeSession(rows)) is expected  # type: ignore[arg-type]


class _FakeRedis:
    def __init__(self, value: str | bytes | None) -> None:
        self.value = value
        self.closed = False

    async def get(self, key: str) -> str | bytes | None:
        assert key == healthz.BEAT_HEARTBEAT_KEY
        return self.value

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (datetime.now(UTC).isoformat(), "ok"),
        ((datetime.now(UTC) - timedelta(seconds=10_000)).isoformat(), "fail"),
        (datetime.now(UTC).replace(tzinfo=None).isoformat(), "fail"),
        (None, "fail"),
    ],
    ids=["fresh", "stale", "naive", "missing"],
)
async def test_beat_check_uses_the_doctors_freshness_rule(
    monkeypatch: pytest.MonkeyPatch, value: str | None, expected: str
) -> None:
    fake = _FakeRedis(value)
    monkeypatch.setattr(healthz.redis, "from_url", lambda *a, **k: fake)
    assert await healthz._bounded("beat", healthz._check_beat) == expected
    assert fake.closed


class _FakeInspect:
    def __init__(self, pings: dict[str, Any] | None, queues: dict[str, Any] | None) -> None:
        self.pings = pings
        self.queues = queues

    def ping(self) -> dict[str, Any] | None:
        return self.pings

    def active_queues(self) -> dict[str, Any] | None:
        return self.queues


class _FakeControl:
    def __init__(self, pings: dict[str, Any] | None, queues: dict[str, Any] | None) -> None:
        self.pings = pings
        self.queues = queues

    def inspect(self, timeout: float) -> _FakeInspect:
        del timeout
        return _FakeInspect(self.pings, self.queues)


class _FakeCelery:
    def __init__(self, pings: dict[str, Any] | None, queues: dict[str, Any] | None) -> None:
        self.control = _FakeControl(pings, queues)


async def test_workers_need_one_reply_and_walker_needs_the_walk_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pongs = {"worker-1": "pong"}
    cases = [
        (pongs, {}, True, False),
        ({}, {}, False, False),
        (None, None, False, False),
        (pongs, {"worker-1": [{"name": healthz.QRADAR_WALKS_QUEUE}]}, True, True),
        (pongs, {"worker-1": [{"name": "other"}]}, True, False),
    ]
    for pings, queues, workers, walker in cases:
        monkeypatch.setattr(healthz, "celery_app", _FakeCelery(pings, queues))
        assert await healthz._check_workers() is workers
        assert await healthz._check_walker() is walker


# A Celery inspect with no ``limit`` drains events until its own timeout, even
# after every worker has replied. The fake models that full wait plus the small
# overhead of opening the broker connection; if the inspect budget ever reaches
# the check budget, the outer asyncio timeout fires first and the check reads
# ``unknown`` on a perfectly healthy system (the F1 defect).
_BROKER_SETUP_SECONDS = 0.1


class _FullWaitInspect:
    def __init__(self, timeout: float) -> None:
        self.timeout = timeout

    def _wait(self) -> None:
        time.sleep(self.timeout + _BROKER_SETUP_SECONDS)

    def ping(self) -> dict[str, str]:
        self._wait()
        return {"worker-1": "pong"}

    def active_queues(self) -> dict[str, list[dict[str, str]]]:
        self._wait()
        return {"worker-1": [{"name": healthz.QRADAR_WALKS_QUEUE}]}


class _FullWaitControl:
    def __init__(self) -> None:
        self.timeouts: list[float] = []

    def inspect(self, timeout: float) -> _FullWaitInspect:
        self.timeouts.append(timeout)
        return _FullWaitInspect(timeout)


class _FullWaitCelery:
    def __init__(self) -> None:
        self.control = _FullWaitControl()


def test_inspect_timeout_leaves_margin_under_the_check_budget() -> None:
    # The worker thread starts after the asyncio clock and still has to open
    # the broker connection, so the full-wait inspect needs real headroom.
    margin = 0.25
    assert healthz.INSPECT_TIMEOUT_SECONDS + margin < healthz.CHECK_TIMEOUT_SECONDS
    assert healthz.CHECK_TIMEOUT_SECONDS <= healthz.TOTAL_TIMEOUT_SECONDS


async def test_full_wait_inspect_answers_inside_the_check_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    celery = _FullWaitCelery()
    monkeypatch.setattr(healthz, "celery_app", celery)

    assert await healthz._bounded("workers", healthz._check_workers) == "ok"
    assert await healthz._bounded("walker", healthz._check_walker) == "ok"
    assert celery.control.timeouts == [
        healthz.INSPECT_TIMEOUT_SECONDS,
        healthz.INSPECT_TIMEOUT_SECONDS,
    ]


async def test_concurrent_calls_on_a_cold_cache_run_the_checks_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    release = asyncio.Event()

    def make_check(name: str) -> Callable[[object], Awaitable[bool]]:
        async def check(session: object = None) -> bool:
            del session
            calls.append(name)
            await release.wait()
            return True

        return check

    for name in _CHECK_NAMES:
        monkeypatch.setattr(healthz, f"_check_{name}", make_check(name))
    # Keep the blocked checks from hitting the real budget while the requests
    # are being set up; the cache and single-flight behaviour is what is under
    # test here, not the timeout values.
    monkeypatch.setattr(healthz, "CHECK_TIMEOUT_SECONDS", 10.0)
    monkeypatch.setattr(healthz, "TOTAL_TIMEOUT_SECONDS", 12.0)

    app = create_app()
    app.dependency_overrides[get_session] = _no_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://backend") as client:
        tasks = [asyncio.create_task(client.get("/api/v1/healthz")) for _ in range(50)]
        # Let all 50 requests reach the cache check while the first compute is
        # still blocked on ``release``. The database pair is serialized by the
        # compute's own lock, so not all six checks can have started yet; a
        # generous, fixed number of loop turns is the reliable barrier here.
        for _ in range(200):
            await asyncio.sleep(0)
        release.set()
        responses = await asyncio.gather(*tasks)

    assert [response.status_code for response in responses] == [200] * 50
    assert sorted(calls) == sorted(_CHECK_NAMES)


def test_requests_leave_process_logging_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """F3: no lifespan means configure_logging() never touches root logging."""
    _patch_checks(monkeypatch)
    root = logging.getLogger()
    httpx_logger = logging.getLogger("httpx")
    httpcore_logger = logging.getLogger("httpcore")
    before = (
        list(root.handlers),
        list(root.filters),
        root.level,
        httpx_logger.level,
        httpcore_logger.level,
    )
    client = _client()
    assert client.get("/api/v1/healthz").status_code == 200
    after = (
        list(root.handlers),
        list(root.filters),
        root.level,
        httpx_logger.level,
        httpcore_logger.level,
    )
    assert after == before
