"""Guards on the WAF history bulk insert's interpolated identifiers.

`_history_insert_jsonb` builds one `INSERT ... SELECT ... FROM
jsonb_populate_recordset(...)` statement with `text()`. The table and the
conflict clause come from a closed set, and every identifier is a dict key, so
each refusal below must be raised before any statement reaches the session.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.waf_endpoint_collector import WafEndpointCollector
from app.core.config import Settings
from app.providers.base import QRadarProvider
from app.waf.profile import WafFieldMapping

BUCKET = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
SOURCE_ID = uuid.UUID("00000000-0000-0000-0000-000000000010")
ENDPOINT_ID = uuid.UUID("00000000-0000-0000-0000-000000000011")

BUCKET_CONFLICT = "ON CONFLICT (log_source_id, bucket_start) DO NOTHING"
METRIC_CONFLICT = "ON CONFLICT (waf_endpoint_id, bucket_start) DO NOTHING"


def settings() -> Settings:
    return Settings(_env_file=None)


class RecordingSession:
    """Session double: records `execute`, and refuses it when a test forbids it."""

    def __init__(self, *, forbid: bool = False) -> None:
        self.forbid = forbid
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def execute(self, statement: object, params: dict[str, object] | None = None) -> None:
        if self.forbid:
            raise AssertionError("a refused bulk insert must execute nothing")
        self.calls.append((str(statement), dict(params or {})))


def collector(session: RecordingSession) -> WafEndpointCollector:
    return WafEndpointCollector(
        cast(AsyncSession, session),
        cast(QRadarProvider, object()),
        mapping=WafFieldMapping(url="URL", method="METHOD"),
        profile_version=1,
        settings=settings(),
    )


def bucket_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "log_source_id": SOURCE_ID,
        "bucket_start": BUCKET,
        "total_events": 12,
    }
    row.update(overrides)
    return row


class TestRefusals:
    async def test_unknown_table_executes_nothing(self) -> None:
        session = RecordingSession(forbid=True)
        with pytest.raises(ValueError, match="unsupported WAF history bulk target"):
            await collector(session)._history_insert_jsonb(
                "waf_endpoint", [bucket_row()], conflict=BUCKET_CONFLICT
            )
        assert session.calls == []

    async def test_known_table_with_the_other_tables_conflict_executes_nothing(self) -> None:
        session = RecordingSession(forbid=True)
        with pytest.raises(ValueError, match="unsupported WAF history bulk target"):
            await collector(session)._history_insert_jsonb(
                "waf_source_bucket", [bucket_row()], conflict=METRIC_CONFLICT
            )
        assert session.calls == []

    async def test_rows_with_different_keys_execute_nothing(self) -> None:
        session = RecordingSession(forbid=True)
        rows = [bucket_row(), bucket_row(total_events=13, untracked_requests=1)]
        with pytest.raises(ValueError, match="identical columns"):
            await collector(session)._history_insert_jsonb(
                "waf_source_bucket", rows, conflict=BUCKET_CONFLICT
            )
        assert session.calls == []

    async def test_a_key_that_is_not_a_column_executes_nothing(self) -> None:
        session = RecordingSession(forbid=True)
        rows = [bucket_row(history_rows=None), bucket_row(history_rows=None)]
        with pytest.raises(ValueError, match="is not a column of waf_source_bucket"):
            await collector(session)._history_insert_jsonb(
                "waf_source_bucket", rows, conflict=BUCKET_CONFLICT
            )
        assert session.calls == []

    async def test_a_key_with_a_double_quote_executes_nothing(self) -> None:
        session = RecordingSession(forbid=True)
        with pytest.raises(ValueError, match="is not a column of waf_endpoint_metric"):
            await collector(session)._history_insert_jsonb(
                "waf_endpoint_metric",
                [{"waf_endpoint_id": ENDPOINT_ID, 'request_count") SELECT 1 --': 1}],
                conflict=METRIC_CONFLICT,
            )
        assert session.calls == []


class TestAcceptedCalls:
    async def test_an_empty_payload_executes_nothing_and_does_not_raise(self) -> None:
        session = RecordingSession(forbid=True)
        await collector(session)._history_insert_jsonb(
            "waf_source_bucket", [], conflict=BUCKET_CONFLICT
        )
        assert session.calls == []

    async def test_a_valid_payload_executes_one_statement_for_the_named_table(self) -> None:
        session = RecordingSession()
        rows = [bucket_row(), bucket_row(bucket_start=BUCKET.replace(minute=5))]
        await collector(session)._history_insert_jsonb(
            "waf_source_bucket", rows, conflict=BUCKET_CONFLICT
        )
        assert len(session.calls) == 1
        statement, params = session.calls[0]
        assert 'INSERT INTO "waf_source_bucket"' in statement
        assert 'jsonb_populate_recordset(NULL::waf_source_bucket' in statement
        assert '"log_source_id", "bucket_start", "total_events"' in statement
        assert BUCKET_CONFLICT in statement
        assert '"bucket_start"' in str(params["history_rows"])

    async def test_a_valid_metric_payload_names_the_metric_table(self) -> None:
        session = RecordingSession()
        await collector(session)._history_insert_jsonb(
            "waf_endpoint_metric",
            [{"waf_endpoint_id": ENDPOINT_ID, "bucket_start": BUCKET, "request_count": 3}],
            conflict=METRIC_CONFLICT,
        )
        assert len(session.calls) == 1
        statement, _ = session.calls[0]
        assert 'INSERT INTO "waf_endpoint_metric"' in statement
        assert 'NULL::waf_endpoint_metric' in statement
        assert METRIC_CONFLICT in statement
