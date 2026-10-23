"""Read-only in-container diagnostics for a QRadar Observability deployment.

The host-only qradar_clock and disk free-space checks are printed by
``./qradar-dash doctor``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import ssl
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import redis.asyncio as redis
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Environment, Settings, get_settings, unknown_setting_names
from app.core.database import dispose_engine, get_sessionmaker
from app.history.origin import is_live_clause
from app.models.enums import SearchFamily, default_search_cadence
from app.models.instance import QRadarInstance
from app.models.log_source import LogSource, LogSourceMetric
from app.models.monitoring import (
    AnomalyEvaluationFault,
    AnomalyEvaluationWatermark,
    CollectionWatermark,
)
from app.models.search_cadence import SearchCadenceSetting
from app.providers.base import QRadarProvider
from app.providers.factory import build_provider_for_instance
from app.providers.tls import build_ssl_context
from app.security.redaction import redact
from app.services.ariel_permit import ArielPermitBudget
from app.workers.celery_app import (
    ANOMALY_EVALUATION_CADENCE_SECONDS,
    BEAT_HEARTBEAT_KEY,
    BEAT_HEARTBEAT_TTL_SECONDS,
    BEAT_INTERVAL_SECONDS,
    celery_app,
)

FRONTEND_HEALTH_URL = "http://frontend:3000/api/health"
BACKEND_VERSION_URL = "http://backend:8000/api/v1/version"
CHECK_TIMEOUT_SECONDS = 10.0
_CHECK_RUNBOOKS = (
    ("db_connect", "OPS-R36"),
    ("migration_head", "OPS-R11"),
    ("timescale", "OPS-R14"),
    ("retention_policy", "OPS-R14"),
    ("batch_retention", "OPS-R14"),
    ("redis", "OPS-R9"),
    ("celery_workers", "OPS-R41"),
    ("celery_beat", "OPS-R9"),
    ("qradar_tls", "OPS-R4"),
    ("qradar_auth", "OPS-R1"),
    ("watermarks", "OPS-R30"),
    ("cadence", "OPS-R30"),
    ("families_off", "OPS-R7"),
    ("errors_recent", "OPS-R30"),
    ("anomaly_evaluation", "OPS-R61"),
    ("pg_database_size", "OPS-R14"),
    ("encryption", "OPS-R16"),
    ("secrets_files", "OPS-R1"),
    ("public_origin", "OPS-R12"),
    ("version_match", "OPS-R11"),
    ("unknown_settings", "OPS-R62"),
    ("ariel_permits", "OPS-R63"),
)
_TOKENISH_RE = re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{24,}(?![A-Za-z0-9_-])")
_CREDENTIAL_URL_RE = re.compile(r"([a-z][a-z0-9+.-]*://)[^/@\s]+@", re.IGNORECASE)
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(token|secret|password|passwd|encryption_key|authorization|sec)\b"
    r"\s*[:=]\s*\S+"
)


class Status(StrEnum):
    PASS = "PASS"  # noqa: S105 - diagnostic status, not a credential
    WARN = "WARN"
    FAIL = "FAIL"


@dataclass(frozen=True)
class CheckResult:
    id: str
    status: Status
    message: str
    runbook: str | None = None


@dataclass(frozen=True)
class TlsProbe:
    expires_at: datetime


#: Every collector name the code defines: the values `CollectionWatermark.collector` can hold
#: (`geo_countries_destination`) and the advisory-lock names the same code uses (`geo_countries`,
#: `property_watch`, `anomaly_explanation`). They are a fixed, known set of code constants, never
#: operator or QRadar text, so they print as they are. The token heuristic in `_safe_text` would
#: mask a long one (`geo_countries_destination` is 25 characters).
_WATERMARK_NAMES = frozenset(
    {
        "offense_snapshot",
        "ips_collection",
        "ips_evaluation",
        "rule_inventory",
        "rule_metric",
        "rule_match",
        "log_source_metric",
        "log_source_inventory",
        "geo_countries",
        "geo_countries_source",
        "geo_countries_destination",
        "waf_endpoint_metric",
        "waf_endpoint_evaluation",
        "anomaly_explanation",
        "catchall_senders",
        "property_watch",
    }
)


@dataclass(frozen=True)
class BatchTable:
    """One table a daily deleter ages, and the settings attribute holding its days."""

    table: str
    days_attr: str


@dataclass(frozen=True)
class BatchDeleter:
    """One scheduled row deleter. ``gate`` is its own switch, not RETENTION_ENABLED.

    ``apply_timescale_policies`` is not here: it reconciles chunk policies and
    its task name does not contain ``retention``. ``check_retention_policy``
    owns that concern.
    """

    task: str
    tables: tuple[BatchTable, ...]
    gate: str | None = None


#: Every daily ``DELETE`` task on the beat. Days are read from settings at check
#: time. A unit test pins this set to every beat task whose name contains
#: ``retention``.
BATCH_DELETERS: tuple[BatchDeleter, ...] = (
    BatchDeleter(
        "apply_search_result_retention",
        (BatchTable("search_result_metric", "search_result_metric_retention_days"),),
    ),
    BatchDeleter(
        "apply_catchall_retention",
        (
            BatchTable("catchall_bucket", "catchall_retention_days"),
            BatchTable("catchall_sender", "catchall_retention_days"),
        ),
    ),
    BatchDeleter(
        "apply_geo_retention",
        (BatchTable("geo_country_bucket", "geo_retention_days"),),
    ),
    BatchDeleter(
        "apply_waf_retention",
        (
            BatchTable("waf_endpoint_metric", "waf_metric_retention_days"),
            BatchTable("waf_source_bucket", "waf_metric_retention_days"),
            BatchTable("waf_endpoint_baseline", "waf_metric_retention_days"),
            BatchTable("waf_endpoint", "waf_metric_retention_days"),
            BatchTable("waf_anomaly", "waf_anomaly_retention_days"),
        ),
        "WAF_RETENTION_ENABLED",
    ),
    BatchDeleter(
        "apply_ips_retention",
        (
            BatchTable("ips_entity_metric", "ips_metric_retention_days"),
            BatchTable("ips_source_bucket", "ips_metric_retention_days"),
            BatchTable("ips_entity", "ips_metric_retention_days"),
        ),
        "IPS_RETENTION_ENABLED",
    ),
    BatchDeleter(
        "apply_ips_anomaly_retention",
        (BatchTable("ips_anomaly", "ips_anomaly_retention_days"),),
    ),
    BatchDeleter(
        "apply_property_watch_retention",
        (
            BatchTable("pw_value_metric", "pw_metric_retention_days"),
            BatchTable("pw_bucket", "pw_metric_retention_days"),
            BatchTable("pw_value", "pw_metric_retention_days"),
            BatchTable("pw_anomaly", "pw_anomaly_retention_days"),
        ),
    ),
    BatchDeleter(
        "apply_anomaly_fault_retention",
        (BatchTable("anomaly_evaluation_fault", "anomaly_fault_retention_days"),),
    ),
    BatchDeleter(
        "apply_custom_rule_retention",
        (BatchTable("custom_rule_breach", "custom_rule_breach_retention_days"),),
    ),
    BatchDeleter(
        "apply_log_source_anomaly_retention",
        (BatchTable("log_source_anomaly", "log_source_anomaly_retention_days"),),
    ),
    BatchDeleter(
        "apply_snapshot_retention",
        (
            BatchTable("rule_health_snapshot", "rule_health_snapshot_retention_days"),
            BatchTable(
                "detection_coverage_snapshot",
                "detection_coverage_snapshot_retention_days",
            ),
        ),
    ),
)

#: Table names from the deleter catalog. Derived, not copied, so a new table
#: cannot be redacted by ``_safe_text`` while the catalog still names it.
#: Task names are not in this set; OPS-R14 names the tasks.
_BATCH_TABLE_NAMES = frozenset(
    item.table for deleter in BATCH_DELETERS for item in deleter.tables
)

#: Shared render cap for ``_safe_text`` and ``_watermark_list``. 1000 keeps
#: ``batch_retention`` whole when every deleter has its own day count. The
#: gate names are at the end of that line, so a short cap hides them.
_MESSAGE_CAP = 1000


def _batch_retention_message(settings: Settings) -> str:
    """Group catalog tables by their day count. Each own gate is named once.

    Task names are left to OPS-R14 and the configuration reference. No last-run
    row exists; this line does not invent one, and the runbook says so.
    """
    grouped: dict[int, list[str]] = {}
    gates: list[str] = []
    for deleter in BATCH_DELETERS:
        if deleter.gate is not None and deleter.gate not in gates:
            gates.append(deleter.gate)
        for item in deleter.tables:
            days = int(getattr(settings, item.days_attr))
            grouped.setdefault(days, []).append(item.table)
    parts = [
        f"{days}d: {', '.join(sorted(names))}"
        for days, names in sorted(grouped.items(), reverse=True)
    ]
    tail = "not gated by RETENTION_ENABLED"
    if gates:
        tail = f"{tail}; {', '.join(gates)}"
    return "; ".join(parts) + "; " + tail


def _safe_text(value: object) -> str:
    """Redact untrusted text before it can reach terminal or JSON output."""
    cleaned = str(redact(str(value))).replace("\r", " ").replace("\n", " ")
    cleaned = _CREDENTIAL_URL_RE.sub(r"\1<redacted>@", cleaned)
    cleaned = _SECRET_ASSIGNMENT_RE.sub(r"\1=<redacted>", cleaned)

    def replace_tokenish(match: re.Match[str]) -> str:
        candidate = match.group(0)
        # A known collector name is a code constant, not a secret. The output layer redacts
        # every message a second time, so without this the name would be masked here and the
        # operator would never learn which collector failed. Catalog table names are the
        # same kind of constant: derived from ``BATCH_DELETERS``, not operator text.
        if candidate in _WATERMARK_NAMES or candidate in _BATCH_TABLE_NAMES:
            return candidate
        if "_" in candidate and candidate.upper() == candidate:
            return candidate
        return "***redacted***"

    return _TOKENISH_RE.sub(replace_tokenish, cleaned)[:_MESSAGE_CAP]


def _watermark_list(names: Sequence[str]) -> str:
    """Collector names for output: a known name as is, anything else redacted."""
    return ", ".join(n if n in _WATERMARK_NAMES else _safe_text(n) for n in names)[:_MESSAGE_CAP]


def _host_from_url(value: str, fallback: str) -> str:
    return urlsplit(value).hostname or fallback


def _human_bytes(size: int) -> str:
    amount = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if amount < 1024 or unit == "TiB":
            return f"{amount:.1f} {unit}" if unit != "B" else f"{int(amount)} B"
        amount /= 1024
    return f"{amount:.1f} TiB"


def _image_migration_head() -> str:
    backend_root = Path(__file__).resolve().parents[2]
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "alembic"))
    head = ScriptDirectory.from_config(config).get_current_head()
    if head is None:
        raise RuntimeError("image has no Alembic head")
    return head


async def _tls_handshake(host: str, port: int, context: ssl.SSLContext) -> TlsProbe:
    _reader, writer = await asyncio.open_connection(
        host=host,
        port=port,
        ssl=context,
        server_hostname=host,
    )
    try:
        ssl_object = writer.get_extra_info("ssl_object")
        if ssl_object is None:
            raise ssl.SSLError("TLS handshake did not produce a peer certificate")
        certificate = ssl_object.getpeercert()
        not_after = certificate.get("notAfter")
        if not isinstance(not_after, str):
            raise ssl.SSLError("peer certificate has no expiry")
        return TlsProbe(datetime.fromtimestamp(ssl.cert_time_to_seconds(not_after), tz=UTC))
    finally:
        writer.close()
        await writer.wait_closed()


def _worst(results: Sequence[CheckResult]) -> Status:
    if any(result.status is Status.FAIL for result in results):
        return Status.FAIL
    if any(result.status is Status.WARN for result in results):
        return Status.WARN
    return Status.PASS


def exit_code(results: Sequence[CheckResult]) -> int:
    return {Status.PASS: 0, Status.WARN: 1, Status.FAIL: 2}[_worst(results)]


#: Every search family that has an off switch, mapped to the setting that is it. A family
#: absent here (only `PHASE_A_VOLUME`) has no switch: the log-source volume collector is the
#: always-on core of the platform. `test_doctor` fails if a new `SearchFamily` member appears
#: in neither this map nor `_FAMILIES_WITHOUT_SWITCH`, so the two cannot drift.
_FAMILY_SWITCHES: dict[str, str] = {
    SearchFamily.WAF_COLLECTION.value: "waf_collection_enabled",
    SearchFamily.ANOMALY_EXPLANATION.value: "explanation_enabled",
    SearchFamily.RULE_MATCH.value: "rule_match_collection_enabled",
    SearchFamily.IPS_COLLECTION.value: "ips_collection_enabled",
    SearchFamily.PROPERTY_WATCH.value: "pw_collection_enabled",
    SearchFamily.CATCHALL_SENDERS.value: "catchall_collection_enabled",
    SearchFamily.GEO_COLLECTION.value: "geo_collection_enabled",
}

#: The families with no off switch, listed so the drift test can tell "deliberately switchless"
#: from "forgotten". `PHASE_A_VOLUME` is the only one.
_FAMILIES_WITHOUT_SWITCH = frozenset({SearchFamily.PHASE_A_VOLUME.value})


class Doctor:
    """Runs bounded, read-only checks with replaceable edges for unit tests."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        sessionmaker: async_sessionmaker[AsyncSession] | None = None,
        now: Callable[[], datetime] | None = None,
        migration_head: Callable[[], str] = _image_migration_head,
        tls_probe: Callable[[str, int, ssl.SSLContext], Awaitable[TlsProbe]] = _tls_handshake,
        provider_factory: Callable[[QRadarInstance], QRadarProvider] | None = None,
        frontend_health_url: str = FRONTEND_HEALTH_URL,
        backend_version_url: str = BACKEND_VERSION_URL,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.sessionmaker = sessionmaker or get_sessionmaker()
        self.now = now or (lambda: datetime.now(UTC))
        self.migration_head = migration_head
        self.tls_probe = tls_probe
        self.provider_factory = provider_factory or (
            lambda instance: build_provider_for_instance(instance, self.settings)
        )
        self.frontend_health_url = frontend_health_url
        self.backend_version_url = backend_version_url
        self.environ = os.environ if environ is None else environ

    async def _scalar(self, sql: str) -> Any:
        async with self.sessionmaker() as session:
            return await session.scalar(text(sql))

    async def check_db_connect(self) -> CheckResult:
        try:
            if await self._scalar("SELECT 1") == 1:
                return CheckResult("db_connect", Status.PASS, "database accepted SELECT 1")
            raise RuntimeError("SELECT 1 returned an unexpected value")
        except Exception as exc:
            return CheckResult(
                "db_connect",
                Status.FAIL,
                f"database host {self.settings.postgres_host} rejected SELECT 1 "
                f"({type(exc).__name__})",
                "OPS-R36",
            )

    async def check_migration_head(self) -> CheckResult:
        try:
            expected = self.migration_head()
            async with self.sessionmaker() as session:
                revisions = await session.scalars(text("SELECT version_num FROM alembic_version"))
                rows = list(revisions.all())
            if len(rows) == 1 and rows[0] == expected:
                return CheckResult(
                    "migration_head", Status.PASS, f"database and image are at {expected}"
                )
            found = ", ".join(str(row) for row in rows) if rows else "no revision"
            return CheckResult(
                "migration_head",
                Status.FAIL,
                f"database at {found}, image expects {expected}",
                "OPS-R11",
            )
        except Exception as exc:
            return CheckResult(
                "migration_head",
                Status.FAIL,
                f"could not compare migration heads ({type(exc).__name__})",
                "OPS-R11",
            )

    async def check_timescale(self) -> CheckResult:
        try:
            async with self.sessionmaker() as session:
                extension = await session.scalar(
                    text("SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'")
                )
                if extension is None:
                    return CheckResult(
                        "timescale",
                        (
                            Status.FAIL
                            if self.settings.environment is Environment.PRODUCTION
                            else Status.WARN
                        ),
                        "TimescaleDB extension is missing",
                        "OPS-R14",
                    )
                hypertable = await session.scalar(
                    text(
                        "SELECT 1 FROM timescaledb_information.hypertables "
                        "WHERE hypertable_name = 'log_source_metric'"
                    )
                )
            if hypertable is None:
                return CheckResult(
                    "timescale",
                    Status.WARN,
                    "TimescaleDB is installed but log_source_metric is not a hypertable",
                    "OPS-R14",
                )
            return CheckResult(
                "timescale", Status.PASS, "extension and log_source_metric hypertable are present"
            )
        except Exception as exc:
            return CheckResult(
                "timescale",
                Status.FAIL,
                f"TimescaleDB metadata could not be read ({type(exc).__name__})",
                "OPS-R14",
            )

    async def check_retention_policy(self) -> CheckResult:
        if not self.settings.retention_enabled:
            return CheckResult(
                "retention_policy", Status.PASS, "retention is disabled by configuration"
            )
        configured = [
            table
            for table, days in self.settings.retention_days().items()
            if days is not None
        ]
        if not configured:
            return CheckResult(
                "retention_policy", Status.PASS, "no retention tables are configured"
            )
        try:
            async with self.sessionmaker() as session:
                present = set(
                    (
                        await session.scalars(
                            text(
                                "SELECT hypertable_name "
                                "/* log_source_metric retention metadata, not metric rows */ "
                                "FROM timescaledb_information.jobs "
                                "WHERE proc_name = 'policy_retention'"
                            )
                        )
                    ).all()
                )
            missing = [table for table in configured if table not in present]
            if missing:
                return CheckResult(
                    "retention_policy",
                    Status.WARN,
                    "retention is enabled but these tables have no retention policy: "
                    + ", ".join(missing),
                    "OPS-R14",
                )
            return CheckResult(
                "retention_policy",
                Status.PASS,
                "retention policies are present: " + ", ".join(configured),
            )
        except Exception as exc:
            return CheckResult(
                "retention_policy",
                Status.WARN,
                f"retention policy metadata could not be read ({type(exc).__name__})",
                "OPS-R14",
            )

    async def check_batch_retention(self) -> CheckResult:
        """Name every daily row deleter. Issues no query.

        The tasks return a dict to Celery and write no watermark or last-run
        row. The result backend is not a last-run record, so this check does
        not invent one and does not read Redis. Days come from settings.
        ``check_retention_policy`` stays the Timescale concern and still
        returns immediately when ``RETENTION_ENABLED`` is false. The line
        groups tables by day count. Task names stay in OPS-R14.
        """
        return CheckResult(
            "batch_retention",
            Status.PASS,
            _batch_retention_message(self.settings),
            "OPS-R14",
        )

    async def check_redis(self) -> CheckResult:
        client = redis.from_url(self.settings.redis_url)
        try:
            if await client.ping():
                return CheckResult("redis", Status.PASS, "Redis returned PONG")
            raise RuntimeError("PING returned false")
        except Exception as exc:
            host = _host_from_url(self.settings.redis_url, "redis")
            return CheckResult(
                "redis",
                Status.FAIL,
                f"Redis host {host} did not answer PING ({type(exc).__name__})",
                "OPS-R9",
            )
        finally:
            await client.aclose()

    async def check_celery_workers(self) -> CheckResult:
        def ping() -> dict[str, Any] | None:
            return celery_app.control.inspect(timeout=2.0).ping()

        try:
            workers = await asyncio.to_thread(ping)
            if workers:
                node_names = _safe_text(", ".join(sorted(workers)))
                return CheckResult(
                    "celery_workers",
                    Status.PASS,
                    f"{len(workers)} worker(s) returned pong: {node_names}",
                )
            return CheckResult(
                "celery_workers", Status.FAIL, "no Celery worker returned pong", "OPS-R41"
            )
        except Exception as exc:
            return CheckResult(
                "celery_workers",
                Status.FAIL,
                f"Celery inspect failed ({type(exc).__name__})",
                "OPS-R41",
            )

    async def check_celery_beat(self) -> CheckResult:
        client = redis.from_url(self.settings.redis_url, decode_responses=True)
        try:
            value = await client.get(BEAT_HEARTBEAT_KEY)
            if not value:
                return CheckResult(
                    "celery_beat",
                    Status.FAIL,
                    "beat is not dispatching, or no worker is running its tasks; "
                    "heartbeat is missing",
                    "OPS-R9",
                )
            try:
                timestamp = value.decode() if isinstance(value, bytes) else value
                at = datetime.fromisoformat(timestamp)
                if at.tzinfo is None:
                    raise ValueError("timestamp has no timezone")
            except (TypeError, ValueError):
                return CheckResult(
                    "celery_beat", Status.FAIL, "beat heartbeat is not a UTC timestamp", "OPS-R9"
                )
            age = (self.now() - at.astimezone(UTC)).total_seconds()
            if age < 0 or age >= BEAT_HEARTBEAT_TTL_SECONDS:
                return CheckResult(
                    "celery_beat",
                    Status.FAIL,
                    "beat is not dispatching, or no worker is running its tasks; "
                    f"heartbeat is stale ({max(0, int(age))} s old)",
                    "OPS-R9",
                )
            return CheckResult(
                "celery_beat", Status.PASS, f"beat heartbeat is {int(age)} s old"
            )
        except Exception as exc:
            host = _host_from_url(self.settings.redis_url, "redis")
            return CheckResult(
                "celery_beat",
                Status.FAIL,
                f"heartbeat could not be read from Redis host {host} ({type(exc).__name__})",
                "OPS-R9",
            )
        finally:
            await client.aclose()

    async def _instance(self) -> QRadarInstance | None:
        async with self.sessionmaker() as session:
            return await session.scalar(
                select(QRadarInstance)
                .where(QRadarInstance.enabled.is_(True))
                .order_by(QRadarInstance.created_at)
                .limit(1)
            )

    async def check_qradar_tls(self) -> CheckResult:
        host = _host_from_url(self.settings.qradar_host, "qradar")
        try:
            instance = await self._instance()
            raw_url = instance.console_host if instance else self.settings.qradar_host
            ca_bundle = instance.ca_bundle_path if instance else self.settings.qradar_ca_bundle
            parsed = urlsplit(raw_url)
            host = parsed.hostname or host
            if parsed.scheme != "https" or not parsed.hostname:
                return CheckResult(
                    "qradar_tls", Status.FAIL, "configured QRadar URL is not HTTPS", "OPS-R18"
                )
            context = build_ssl_context(
                ca_bundle, allow_missing_aki=self.settings.qradar_tls_allow_missing_aki
            )
            probe = await self.tls_probe(parsed.hostname, parsed.port or 443, context)
            days = int((probe.expires_at - self.now()).total_seconds() // 86400)
            if days < 0:
                return CheckResult(
                    "qradar_tls", Status.FAIL, "QRadar certificate has expired", "OPS-R2"
                )
            if days <= 30 or self.settings.qradar_tls_allow_missing_aki:
                reasons = []
                if days <= 30:
                    reasons.append(f"certificate expires in {days} days")
                if self.settings.qradar_tls_allow_missing_aki:
                    reasons.append("QRADAR_TLS_ALLOW_MISSING_AKI is enabled")
                runbook = "OPS-R3" if self.settings.qradar_tls_allow_missing_aki else "OPS-R2"
                return CheckResult("qradar_tls", Status.WARN, "; ".join(reasons), runbook)
            return CheckResult(
                "qradar_tls", Status.PASS, f"verified certificate expires in {days} days"
            )
        except Exception as exc:
            detail = _safe_text(exc)
            if "Missing Authority Key Identifier" in detail:
                runbook = "OPS-R3"
            elif isinstance(exc, (ConnectionError, OSError, TimeoutError)):
                runbook = "OPS-R4"
            else:
                runbook = "OPS-R2"
            return CheckResult(
                "qradar_tls",
                Status.FAIL,
                f"TLS handshake to {host} failed ({type(exc).__name__}: {detail})",
                runbook,
            )

    async def check_qradar_auth(self) -> CheckResult:
        provider: QRadarProvider | None = None
        host = _host_from_url(self.settings.qradar_host, "qradar")
        try:
            instance = await self._instance()
            if instance is None:
                return CheckResult(
                    "qradar_auth", Status.FAIL, "no enabled QRadar instance is registered", "OPS-R1"
                )
            host = _host_from_url(instance.console_host, host)
            provider = self.provider_factory(instance)
            info = await provider.validate_connection()
            version = _safe_text(info.version or "version not reported")
            return CheckResult(
                "qradar_auth", Status.PASS, f"GET /system/about succeeded; QRadar {version}"
            )
        except Exception as exc:
            return CheckResult(
                "qradar_auth",
                Status.FAIL,
                f"GET /system/about on {host} failed "
                f"({type(exc).__name__}: {_safe_text(exc)})",
                "OPS-R1",
            )
        finally:
            if provider is not None:
                await provider.aclose()

    def _watermark_cadence(self, collector: str, cadences: dict[str, int]) -> int:
        def family_cadence(family: SearchFamily) -> int:
            return cadences.get(family.value, int(default_search_cadence(family)))

        if collector.startswith("waf_"):
            return family_cadence(SearchFamily.WAF_COLLECTION)
        if collector.startswith("ips_"):
            return family_cadence(SearchFamily.IPS_COLLECTION)
        if collector == "rule_match":
            return family_cadence(SearchFamily.RULE_MATCH)
        if collector == "catchall_senders":
            return family_cadence(SearchFamily.CATCHALL_SENDERS)
        if collector.startswith("geo_countries"):
            return family_cadence(SearchFamily.GEO_COLLECTION)
        return {
            "log_source_metric": self.settings.collection_interval_seconds,
            "log_source_inventory": self.settings.log_source_sync_interval_seconds,
            "offense_snapshot": self.settings.offense_collection_interval_seconds,
            "rule_inventory": self.settings.rule_collection_interval_seconds,
            "rule_metric": self.settings.rule_metric_collection_interval_seconds,
        }.get(collector, 3600)

    async def check_watermarks(self) -> CheckResult:
        try:
            async with self.sessionmaker() as session:
                rows = list((await session.scalars(select(CollectionWatermark))).all())
                cadence_rows = list((await session.scalars(select(SearchCadenceSetting))).all())
            if not rows:
                return CheckResult("watermarks", Status.PASS, "no collection watermarks exist")
            cadences = {row.family: row.cadence_seconds for row in cadence_rows}
            failed: list[str] = []
            late: list[str] = []
            for row in rows:
                if row.consecutive_failures >= 3 or row.last_error:
                    failed.append(row.collector)
                elif row.lag_seconds is not None and row.lag_seconds > 2 * self._watermark_cadence(
                    row.collector, cadences
                ):
                    late.append(row.collector)
            if failed:
                return CheckResult(
                    "watermarks",
                    Status.FAIL,
                    f"{len(failed)} watermark(s) failed: {_watermark_list(failed)}",
                    "OPS-R30",
                )
            if late:
                return CheckResult(
                    "watermarks",
                    Status.WARN,
                    f"{len(late)} watermark(s) exceed twice their cadence: "
                    f"{_watermark_list(late)}",
                    "OPS-R30",
                )
            return CheckResult(
                "watermarks", Status.PASS, f"{len(rows)} watermark(s) have no error or excess lag"
            )
        except Exception as exc:
            return CheckResult(
                "watermarks",
                Status.FAIL,
                f"watermarks could not be read ({type(exc).__name__})",
                "OPS-R30",
            )

    def _family_enabled(self, family: str) -> bool:
        attribute = _FAMILY_SWITCHES.get(family)
        if attribute is None:
            # `PHASE_A_VOLUME`, and anything unknown: no switch, so treat it as enabled.
            return True
        return bool(getattr(self.settings, attribute))

    async def check_cadence(self) -> CheckResult:
        try:
            async with self.sessionmaker() as session:
                rows = list((await session.scalars(select(SearchCadenceSetting))).all())
            late: list[str] = []
            for row in rows:
                if not self._family_enabled(row.family):
                    continue
                if row.last_run_at is None:
                    late.append(row.family)
                    continue
                age = (self.now() - row.last_run_at).total_seconds()
                limit = row.cadence_seconds + self.settings.search_cadence_settle_seconds + 60
                if age > limit:
                    late.append(row.family)
            if late:
                return CheckResult(
                    "cadence",
                    Status.WARN,
                    f"enabled families late or never run: {_watermark_list(late)}",
                    "OPS-R30",
                )
            return CheckResult(
                "cadence", Status.PASS, f"{len(rows)} cadence row(s) are on time or disabled"
            )
        except Exception as exc:
            return CheckResult(
                "cadence",
                Status.WARN,
                f"cadence rows could not be read ({type(exc).__name__})",
                "OPS-R30",
            )

    async def check_families_off(self) -> CheckResult:
        # Every family switch in `_FAMILY_SWITCHES` order, then the custom-rule engine's switch
        # (F.1), which is not a `SearchFamily`. The environment name is the field's upper case.
        flags = {
            attribute.upper(): bool(getattr(self.settings, attribute))
            for attribute in _FAMILY_SWITCHES.values()
        }
        flags["CUSTOM_RULES_ENABLED"] = self.settings.custom_rules_enabled
        summary = ", ".join(
            f"{name}={'on' if enabled else 'off'}" for name, enabled in flags.items()
        )
        return CheckResult("families_off", Status.PASS, summary)

    async def check_errors_recent(self) -> CheckResult:
        try:
            async with self.sessionmaker() as session:
                count = int(
                    await session.scalar(
                        select(text("count(*)")).select_from(CollectionWatermark).where(
                            CollectionWatermark.consecutive_failures > 0
                        )
                    )
                    or 0
                )
            if count == 0:
                return CheckResult("errors_recent", Status.PASS, "no watermark has recent failures")
            if count < 3:
                return CheckResult(
                    "errors_recent",
                    Status.WARN,
                    f"{count} watermark(s) have recent failures",
                    "OPS-R30",
                )
            return CheckResult(
                "errors_recent",
                Status.FAIL,
                f"{count} watermark(s) have recent failures",
                "OPS-R30",
            )
        except Exception as exc:
            return CheckResult(
                "errors_recent",
                Status.FAIL,
                f"recent errors could not be counted ({type(exc).__name__})",
                "OPS-R30",
            )

    async def check_anomaly_evaluation(self) -> CheckResult:
        """Whether the anomaly engine is judging every monitored source's buckets.

        `AnomalyEvaluationWatermark` is not a `CollectionWatermark`, so the
        `watermarks` check cannot see it. Every read here is set-based: given-up
        and open faults are counted on the fault table, and a trailing source is
        found in one correlated `EXISTS`, never a round trip per source.

        A pending bucket is not late the moment it is written: buckets are
        `bucket_seconds` wide, the collector writes after the bucket ends plus
        the settle, the beat can wait one more tick, and the engine judges it on
        the next evaluation tick. A source trails only once that allowance has
        elapsed, measured per row, so a healthy system does not WARN between a
        collection and the tick that judges it.
        """
        now = self.now()
        cutoff = now - timedelta(hours=24)
        allowance = timedelta(
            seconds=(
                self.settings.search_cadence_settle_seconds
                + BEAT_INTERVAL_SECONDS
                + 2 * ANOMALY_EVALUATION_CADENCE_SECONDS
            )
        )
        try:
            async with self.sessionmaker() as session:
                given_up, sources = (
                    await session.execute(
                        select(
                            func.count(),
                            func.count(AnomalyEvaluationFault.log_source_id.distinct()),
                        ).where(AnomalyEvaluationFault.given_up_at >= cutoff)
                    )
                ).one()
                last = (
                    await session.execute(
                        select(
                            AnomalyEvaluationFault.error_class,
                            AnomalyEvaluationFault.last_failed_at,
                        )
                        .where(AnomalyEvaluationFault.given_up_at >= cutoff)
                        .order_by(AnomalyEvaluationFault.last_failed_at.desc())
                        .limit(1)
                    )
                ).first()
                open_faults = int(
                    await session.scalar(
                        select(func.count())
                        .select_from(AnomalyEvaluationFault)
                        .where(
                            AnomalyEvaluationFault.resolved_at.is_(None),
                            AnomalyEvaluationFault.given_up_at.is_(None),
                        )
                    )
                    or 0
                )
                # `make_interval` keeps the per-row width in SQL; `allowance`
                # is the constant part, bound once.
                expected_judged_at = (
                    LogSourceMetric.bucket_start
                    + func.make_interval(0, 0, 0, 0, 0, 0, LogSourceMetric.bucket_seconds)
                    + allowance
                )
                pending = (
                    select(LogSourceMetric.bucket_start)
                    .where(
                        LogSourceMetric.log_source_id == LogSource.id,
                        is_live_clause(LogSourceMetric),
                        or_(
                            AnomalyEvaluationWatermark.evaluated_through.is_(None),
                            LogSourceMetric.bucket_start
                            > AnomalyEvaluationWatermark.evaluated_through,
                        ),
                        expected_judged_at < now,
                    )
                    .correlate(LogSource, AnomalyEvaluationWatermark)
                    .exists()
                )
                trailing = int(
                    await session.scalar(
                        select(func.count())
                        .select_from(LogSource)
                        .outerjoin(
                            AnomalyEvaluationWatermark,
                            AnomalyEvaluationWatermark.log_source_id == LogSource.id,
                        )
                        .where(LogSource.monitoring_enabled.is_(True), pending)
                    )
                    or 0
                )
        except Exception as exc:
            return CheckResult(
                "anomaly_evaluation",
                Status.FAIL,
                f"anomaly evaluation state could not be read ({type(exc).__name__})",
                "OPS-R61",
            )

        if given_up:
            # A given-up bucket is data the platform promised and did not
            # produce, so it outranks the retryable states below.
            if last is not None:
                when = last[1].astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
                detail = f"last failure {_safe_text(last[0])} at {when}"
            else:
                detail = "the failing class was not recorded"
            return CheckResult(
                "anomaly_evaluation",
                Status.FAIL,
                f"{int(given_up)} anomaly bucket(s) were not judged in the last 24 h "
                f"on {int(sources)} source(s); {detail}",
                "OPS-R61",
            )
        if open_faults:
            return CheckResult(
                "anomaly_evaluation",
                Status.WARN,
                f"{open_faults} anomaly bucket(s) are failing evaluation and will be retried",
                "OPS-R61",
            )
        if trailing:
            return CheckResult(
                "anomaly_evaluation",
                Status.WARN,
                f"{trailing} monitored source(s) have a live bucket that should already "
                "have been judged",
                "OPS-R61",
            )
        return CheckResult(
            "anomaly_evaluation",
            Status.PASS,
            "no bucket given up in 24 h, no open fault, and every monitored source is judged",
        )

    async def check_pg_database_size(self) -> CheckResult:
        try:
            size = int(await self._scalar("SELECT pg_database_size(current_database())"))
            return CheckResult(
                "pg_database_size",
                Status.PASS,
                f"database uses {_human_bytes(size)}; host free-space check is unavailable "
                "in-container",
            )
        except Exception as exc:
            return CheckResult(
                "pg_database_size",
                Status.FAIL,
                f"database size could not be read ({type(exc).__name__})",
                "OPS-R14",
            )

    async def check_encryption(self) -> CheckResult:
        try:
            async with self.sessionmaker() as session:
                token = await session.scalar(
                    select(QRadarInstance.sec_token)
                    .where(QRadarInstance.sec_token.is_not(None))
                    .limit(1)
                )
            if token is None:
                return CheckResult(
                    "encryption",
                    Status.WARN,
                    "no stored instance token is available to decrypt",
                    "OPS-R16",
                )
            return CheckResult("encryption", Status.PASS, "one stored instance token decrypted")
        except Exception as exc:
            return CheckResult(
                "encryption",
                Status.FAIL,
                f"stored instance token could not be decrypted ({type(exc).__name__})",
                "OPS-R16",
            )

    async def check_secrets_files(self) -> CheckResult:
        paths = {
            "QRadar token": self.settings.qradar_api_token_file,
            "QRadar CA": self.settings.qradar_ca_bundle,
        }
        failures: list[str] = []
        for label, raw_path in paths.items():
            if not raw_path:
                failures.append(f"{label} file is not configured")
                continue
            try:
                with Path(raw_path).open("rb") as handle:
                    if not handle.read(1):
                        failures.append(f"{label} file is empty")
            except OSError as exc:
                failures.append(f"{label} file is unreadable ({type(exc).__name__})")
        if failures:
            return CheckResult(
                "secrets_files", Status.FAIL, "; ".join(failures), "OPS-R1"
            )
        return CheckResult("secrets_files", Status.PASS, "QRadar token and CA files are readable")

    async def check_public_origin(self) -> CheckResult:
        origin = urlsplit(self.settings.public_origin)
        if (
            origin.scheme == "https"
            and origin.hostname
            and not origin.path.rstrip("/")
            and not origin.query
            and not origin.fragment
        ):
            return CheckResult("public_origin", Status.PASS, "public origin is HTTPS")
        return CheckResult(
            "public_origin",
            Status.FAIL,
            "public origin is not a valid HTTPS origin",
            "OPS-R12",
        )

    async def check_version_match(self) -> CheckResult:
        async with httpx.AsyncClient(timeout=5.0) as client:
            frontend_response, backend_response = await asyncio.gather(
                client.get(self.frontend_health_url),
                client.get(self.backend_version_url),
                return_exceptions=True,
            )
        if isinstance(frontend_response, BaseException):
            return CheckResult(
                "version_match",
                Status.FAIL,
                f"frontend probe is not reachable ({type(frontend_response).__name__})",
                "OPS-R11",
            )
        if isinstance(backend_response, BaseException):
            return CheckResult(
                "version_match",
                Status.WARN,
                f"backend version route is not reachable ({type(backend_response).__name__}); "
                "version comparison is unavailable",
                "OPS-R11",
            )
        try:
            if frontend_response.status_code != 200:
                return CheckResult(
                    "version_match",
                    Status.FAIL,
                    f"frontend probe returned HTTP {frontend_response.status_code}",
                    "OPS-R11",
                )
            if backend_response.status_code != 200:
                return CheckResult(
                    "version_match",
                    Status.WARN,
                    f"backend version route returned HTTP {backend_response.status_code}; "
                    "version comparison is unavailable",
                    "OPS-R11",
                )
            frontend = frontend_response.json().get("version")
            backend = backend_response.json().get("version")
            if not frontend or not backend:
                return CheckResult(
                    "version_match",
                    Status.WARN,
                    "a version field is not available yet; version comparison is unavailable",
                    "OPS-R11",
                )
            if frontend != backend:
                return CheckResult(
                    "version_match",
                    Status.WARN,
                    f"frontend {_safe_text(frontend)} differs from backend {_safe_text(backend)}",
                    "OPS-R11",
                )
            return CheckResult(
                "version_match", Status.PASS, f"frontend and backend report {_safe_text(frontend)}"
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            return CheckResult(
                "version_match",
                Status.WARN,
                f"version response could not be read ({type(exc).__name__})",
                "OPS-R11",
            )

    async def check_unknown_settings(self) -> CheckResult:
        """WARN when an environment name looks like a setting and is not one.

        The message lists names and never values. A deployment with no such
        names passes, including the ``NOTIFY_*`` and ``SMTP_*`` names the
        notifier reads outside ``Settings``.
        """
        names = unknown_setting_names(self.environ)
        if not names:
            return CheckResult("unknown_settings", Status.PASS, "no unknown settings")
        return CheckResult(
            "unknown_settings",
            Status.WARN,
            "ignored unknown settings: " + ", ".join(names),
            "OPS-R62",
        )

    async def check_ariel_permits(self) -> CheckResult:
        """Read the Ariel permit pools once.

        The doctor builds a global-only budget, so the console figure is every
        console-pool lock in this database, printed as
        ``console-pool locks N (cap P per console)``. N can be larger than P.
        A budget that has a console prints ``console held/cap`` for that one
        console instead.
        """
        try:
            async with self.sessionmaker() as session:
                budget = ArielPermitBudget.for_session(
                    session, self.settings, instance_id=None
                )
                counts = await budget.active_permits(session)
        except Exception as exc:
            return CheckResult(
                "ariel_permits",
                Status.FAIL,
                f"Ariel permit counts could not be read ({type(exc).__name__})",
                "OPS-R63",
            )
        if counts is None:
            return CheckResult("ariel_permits", Status.PASS, "not enforced")
        if budget.instance_id is not None:
            console_figure = f"console {counts.instance_held}/{counts.instance_cap}"
        else:
            console_figure = (
                f"console-pool locks {counts.instance_held} "
                f"(cap {counts.instance_cap} per console)"
            )
        message = (
            f"global {counts.global_held}/{counts.global_cap}, "
            f"reserved live slots {counts.reserved_live_slots}, "
            f"{console_figure}"
        )
        if counts.global_held >= counts.global_cap:
            return CheckResult(
                "ariel_permits",
                Status.WARN,
                f"{message}; live work is waiting and history yields",
                "OPS-R63",
            )
        return CheckResult("ariel_permits", Status.PASS, message)

    async def run(self) -> list[CheckResult]:
        results: list[CheckResult] = []
        for check_id, runbook in _CHECK_RUNBOOKS:
            check = getattr(self, f"check_{check_id}")
            try:
                result = await asyncio.wait_for(check(), timeout=CHECK_TIMEOUT_SECONDS)
            except TimeoutError:
                result = CheckResult(
                    check_id,
                    Status.FAIL,
                    f"did not answer within {CHECK_TIMEOUT_SECONDS:g} s",
                    runbook,
                )
            results.append(result)
        return results


def render_text(results: Sequence[CheckResult]) -> str:
    width = max(len(result.id) for result in results)
    lines = []
    for result in results:
        suffix = f"    runbook: {result.runbook}" if result.runbook else ""
        lines.append(
            f"{result.status.value:<5} {result.id:<{width}} {_safe_text(result.message)}{suffix}"
        )
    return "\n".join(lines)


def render_json(results: Sequence[CheckResult]) -> str:
    payload = [
        {**asdict(result), "message": _safe_text(result.message)} for result in results
    ]
    return json.dumps(payload, default=str, sort_keys=True)


async def _async_main(as_json: bool) -> int:
    try:
        results = await Doctor().run()
        print(render_json(results) if as_json else render_text(results))
        return exit_code(results)
    finally:
        await dispose_engine()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run read-only in-container diagnostics")
    parser.add_argument(
        "--json", action="store_true", help="emit the same redacted results as JSON"
    )
    args = parser.parse_args()
    return asyncio.run(_async_main(args.json))


if __name__ == "__main__":
    raise SystemExit(main())
