"""``_for_each_instance``: one console's failure is recorded and the next console runs.

DB-gated, on the real async session. A rollback expires every loaded object, and
reading an expired attribute on an async session raises ``MissingGreenlet`` -- the
failure branch used to read ``instance.name`` after rolling back.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.instance import QRadarInstance
from app.workers import tasks

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


class _Closable:
    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


async def test_a_failed_console_is_recorded_and_the_next_one_still_runs(
    db_schema: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = create_async_engine(db_schema, future=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as setup:
        for name in ("alpha", "bravo"):
            setup.add(QRadarInstance(name=name, console_host="mock", provider_kind="mock"))
        await setup.commit()

    providers: list[_Closable] = []

    def build(instance, *, audit_session=None):  # type: ignore[no-untyped-def]
        provider = _Closable()
        providers.append(provider)
        return provider

    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: maker)
    monkeypatch.setattr(tasks, "build_provider_for_instance", build)
    ran: list[str] = []

    async def run(session, instance, provider):  # type: ignore[no-untyped-def]
        ran.append(instance.name)
        if len(ran) == 1:  # whichever console the loop reaches first fails
            raise RuntimeError("console unreachable")
        return {"status": "ok"}

    try:
        result = await tasks._for_each_instance(run)
    finally:
        await engine.dispose()

    first, second = ran
    by_name = {item["instance"]: item for item in result["results"]}
    assert sorted(ran) == ["alpha", "bravo"], "the second console ran after the first failed"
    assert by_name[first] == {
        "instance": first, "status": "failed", "error_class": "RuntimeError"
    }
    assert by_name[second] == {"instance": second, "status": "ok"}
    assert result["instances"] == 2
    assert all(p.closed for p in providers) and len(providers) == 2
