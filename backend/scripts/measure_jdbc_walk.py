"""Synthetic JDBC walk measurement (J1q item 6). Not a test: no assertion on time.

Run from ``backend/`` with the backend virtualenv::

    PYTHONPATH=. ENCRYPTION_KEY=<test key> TEST_DATABASE_URL=<your test db> \
        .venv/bin/python scripts/measure_jdbc_walk.py --drop-schema-on-test-db

It builds the item-1 8,000-source fixture for J=200 and J=800 in process, injects
50 ms of latency per page, runs the real ``walk_jdbc_configs``, and prints fixture
bytes, elapsed time, decode+projection time and the ``tracemalloc`` peak.

**The apply step is destructive.** With a ``TEST_DATABASE_URL`` and the explicit
``--drop-schema-on-test-db`` flag, it seeds an 8,000-source inventory and times
``apply_jdbc_walk`` on the 800 configs. Before any DB work the script parses the
URL and runs the apply step only if the named database's **name contains
``test``** *and* that flag was passed. Otherwise the walk still runs and the
apply step is skipped (or refused, for a non-test name, with a non-zero exit).
The apply step **drops and recreates the whole schema of the named test
database**: never point ``TEST_DATABASE_URL`` at a real database. The script
reads only ``TEST_DATABASE_URL``; it never falls back to ``DATABASE_URL``.

Every number is synthetic: an in-process fixture on one machine, not production
capacity. No QRadar request and no retained history are involved.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import tracemalloc
from collections.abc import Callable
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("QRADAR_PROVIDER", "mock")
os.environ.setdefault("SECRET_KEY", "measurement-only-key-at-least-32-characters!!")

import httpx

from app.jdbc.dto import JdbcConfigWalk
from app.providers.qradar_rest import QRadarRestProvider

PATH = "/api/config/event_sources/log_source_management/log_sources"
FLEET = 8_000
PAGE_LATENCY = 0.05

# The 18 allow-listed names/ids and 17 denied names/ids of JDBC-0's 35-entry array.
ACCEPTED = {
    "identifier": (8000, "labjdbc-flowing"), "databaseType": (8005, "Postgres"),
    "databaseName": (8010, "app_audit"), "schema": (8017, "public"),
    "databaseServerHostname": (8020, "db.example.com"), "databasePort": (8030, "5432"),
    "queryList": (8080, "none"), "eventTable": (8090, "app_audit_flowing"),
    "eventQuery": (8100, "id,event_time"), "compareField": (8110, "id"),
    "usePreparedStatements": (8120, "true"), "startTime": (8130, "2026-11-20 10:30"),
    "pollingInterval": (8140, "30"), "epsThrottle": (8150, "20000"),
    "useSSL": (8210, "false"), "MSSQLServerHostname": (8280, "tls.example.com"),
    "enableAdvancedOptions": (8290, "false"), "allowUntrusted": (8320, "false"),
}
DENIED = {
    "warehouse": 8022, "role": 8026, "databaseUsername": 8040,
    "keyPairAuthentication": 8051, "databasePassword": 8050,
    "privateKeyFileName": 8052, "databaseDomain": 8060, "databaseInstance": 8070,
    "namedPipe": 8160, "clusterName": 8170, "useNTLMv2": 8200,
    "useMicrosoftJDBC": 8270, "useOracleEncryption": 8220,
    "informixDatabaseLocale": 8240, "informixCodeSet": 8250,
    "db2SecurityMechanism": 8260, "addWithNoLock": 8310,
}
CANARY = "MEASUREMENT-CANARY"
DROP_FLAG = "--drop-schema-on-test-db"


class GuardResult(Enum):
    """Outcome of the pre-DB safety check for the destructive apply step."""

    ALLOW = "allow"
    SKIP = "skip"
    REFUSE = "refuse"


def check_apply_guard(
    url: str | None = None, drop_flag: bool | None = None,
) -> tuple[GuardResult, str]:
    """Decide whether the destructive apply step may run; no DB work happens here.

    ``url`` defaults to ``TEST_DATABASE_URL`` and ``drop_flag`` to the presence of
    ``--drop-schema-on-test-db`` in ``sys.argv``, so the real wiring is testable
    through monkeypatch. Both must hold for ``ALLOW``: the parsed database name
    contains ``test``, and the flag was passed. A non-test name is ``REFUSE``
    (the caller exits non-zero); a test name without the flag is ``SKIP`` (the
    walk still runs, the apply step does not). ``DATABASE_URL`` is never read.
    """
    if url is None:
        url = os.environ.get("TEST_DATABASE_URL")
    if drop_flag is None:
        drop_flag = DROP_FLAG in sys.argv[1:]
    if not url:
        return GuardResult.SKIP, "TEST_DATABASE_URL is not set"
    try:
        database = make_url(url).database
    except ArgumentError as exc:
        return GuardResult.REFUSE, f"refusing: cannot parse TEST_DATABASE_URL ({exc})"
    if not database or "test" not in database.lower():
        return GuardResult.REFUSE, (
            f"refusing to apply: TEST_DATABASE_URL database {database!r} "
            "does not contain 'test' (the apply step drops and recreates the schema)"
        )
    if not drop_flag:
        return GuardResult.SKIP, (
            f"apply skipped: TEST_DATABASE_URL names test database {database!r} "
            f"but --{DROP_FLAG} was not passed (the apply step drops and recreates the schema)"
        )
    return GuardResult.ALLOW, ""


def jdbc_row(index: int) -> dict[str, Any]:
    """One source with the full 35-entry array, including Unicode and long denied values."""
    parameters = [
        {"id": pid, "name": name, "value": value}
        for name, (pid, value) in ACCEPTED.items()
    ]
    parameters += [
        {"id": pid, "name": name,
         "value": f"{CANARY}-{name}-ПАРОЛЬ" if name != "informixCodeSet" else CANARY + "D" * 5_000}
        for name, pid in DENIED.items()
    ]
    return {"id": index, "protocol_type_id": 8, "protocol_parameters": parameters}


def fixture(j: int, page_bytes: list[int]) -> Callable[[httpx.Request], Any]:
    """An 8k fleet whose MockTransport applies the filter and Range to JDBC ids only."""
    jdbc_ids = list(range(0, j * 10, 10))

    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(PAGE_LATENCY)  # in-process latency injection
        start, end = map(int, request.headers["Range"].removeprefix("items=").split("-"))
        encoded = json.dumps([jdbc_row(i) for i in jdbc_ids[start : end + 1]])
        page_bytes.append(len(encoded))  # len(json), as specified
        return httpx.Response(200, content=encoded.encode(), headers={
            "content-type": "application/json",
        })

    return handler


async def measure_walk(j: int) -> tuple[JdbcConfigWalk, list[int], float, float, int]:
    """Run the real provider walk; instrument decode/projection and peak memory."""
    import app.providers.qradar_rest as rest

    page_bytes: list[int] = []
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(fixture(j, page_bytes)),
        base_url="https://qradar.example/api",
    )
    token = os.environ.get("QRADAR_MEASUREMENT_TOKEN", "placeholder")
    provider = QRadarRestProvider(
        base_url="https://qradar.example", sec_token=token, client=client,
        max_retries=0,
    )
    decode_seconds = 0.0
    project_seconds = 0.0
    original_decode = provider._decode
    original_project = rest.project_jdbc_config

    def decode(response: httpx.Response, outcome: dict[str, Any]) -> Any:
        nonlocal decode_seconds
        started = time.perf_counter()
        try:
            return original_decode(response, outcome)
        finally:
            decode_seconds += time.perf_counter() - started

    def project(**kwargs: Any) -> Any:
        nonlocal project_seconds
        started = time.perf_counter()
        try:
            return original_project(**kwargs)
        finally:
            project_seconds += time.perf_counter() - started

    provider._decode = decode
    rest.project_jdbc_config = project
    tracemalloc.start()
    started = time.perf_counter()
    try:
        result = await provider.walk_jdbc_configs()
    finally:
        elapsed = time.perf_counter() - started
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        rest.project_jdbc_config = original_project
        await provider.aclose()
    return result, page_bytes, elapsed, decode_seconds + project_seconds, peak


async def measure_apply(result: JdbcConfigWalk, url: str) -> None:
    """Drop and recreate the named test database's schema, then time the apply.

    The caller must have passed ``check_apply_guard``; this function does the
    destructive DB work and never consults another URL.
    """
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models import Base
    from app.models.instance import QRadarInstance
    from app.models.jdbc import JdbcSourceConfig
    from app.models.log_source import LogSource
    from app.services.jdbc_sync import apply_jdbc_walk

    engine = create_async_engine(url, future=True)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
            await connection.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        now = datetime.now(UTC)
        jdbc_ids = {config.qradar_id for config in result.configs}
        async with maker() as session:
            instance = QRadarInstance(name="jdbc-measure", console_host="qradar.lab.local")
            session.add(instance)
            await session.flush()
            session.add_all([
                LogSource(
                    instance_id=instance.id, qradar_id=qid, name=f"src-{qid}",
                    protocol_type_id=8 if qid in jdbc_ids else 1,
                    enabled=True, monitoring_enabled=True,
                )
                for qid in range(FLEET)
            ])
            await session.flush()
            by_qid = dict((await session.execute(
                select(LogSource.qradar_id, LogSource.id)
            )).all())
            session.add_all([
                JdbcSourceConfig(
                    log_source_id=by_qid[qid], protocol_type_id=8, field_states={},
                    allowlist_version=1, config_state="COMPLETE", observed_at=now,
                )
                for qid in jdbc_ids
            ])
            await session.flush()
            started = time.perf_counter()
            summary = await apply_jdbc_walk(session, instance.id, result, now=now)
            elapsed = time.perf_counter() - started
            print(
                f"apply: configs={len(result.configs)} inventory={FLEET} "
                f"elapsed={elapsed:.3f}s written={summary.written} "
                f"chunks={summary.chunks} marked_absent={summary.marked_absent}"
            )
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
    finally:
        await engine.dispose()


async def main() -> None:
    """Print synthetic numbers for J=200 and J=800; no wall-time assertion."""
    url = os.environ.get("TEST_DATABASE_URL")
    verdict, message = check_apply_guard(url, DROP_FLAG in sys.argv[1:])

    print(f"synthetic fixture: {FLEET} sources, latency={PAGE_LATENCY * 1000:.0f}ms/page")
    j800: JdbcConfigWalk | None = None
    for j in (200, 800):
        result, page_bytes, elapsed, cpu, peak = await measure_walk(j)
        print(
            f"J={j}: pages={len(page_bytes)} bytes/page={page_bytes} "
            f"bytes_total={sum(page_bytes)} configs={len(result.configs)} "
            f"state={result.state.value} elapsed={elapsed:.3f}s "
            f"decode_project={cpu:.3f}s tracemalloc_peak={peak} bytes"
        )
        if j == 800:
            j800 = result
    assert j800 is not None

    # The guard ran before any DB work; only the apply step can touch a database.
    if verdict is GuardResult.REFUSE:
        print(message, file=sys.stderr)
        raise SystemExit(2)
    if verdict is GuardResult.SKIP:
        print(f"apply: {message}")
        return
    assert url is not None  # the guard only allows a parsed TEST_DATABASE_URL
    await measure_apply(j800, url)


if __name__ == "__main__":
    asyncio.run(main())
