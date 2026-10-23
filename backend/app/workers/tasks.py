"""Celery task entrypoints for background collection, detection and delivery.

Each task is a thin synchronous wrapper that runs an async orchestration on a
fresh event loop with its own AsyncSession. Tests import and await those
orchestration functions directly.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Coroutine
from datetime import UTC, datetime
from typing import Any

from redis import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.dispatcher import NotificationDispatcher
from app.alerts.fingerprint import compute_fingerprint
from app.alerts.notifiers.registry import build_notifiers
from app.alerts.routing import default_policy
from app.alerts.search_alerts import SearchAlertEvaluator
from app.alerts.service import AlertInput, AlertService
from app.anomaly.baseline import BaselineBuilder
from app.anomaly.engine import EXPLAINABLE_TYPES, AnomalyEngine
from app.collectors.catchall_collector import CatchallCollector
from app.collectors.explanation_collector import (
    EXPLANATION_COLLECTOR,
    ExplanationCollector,
)
from app.collectors.geo_collector import GeoCollector
from app.collectors.ips_collector import IpsCollector
from app.collectors.log_source_collector import LogSourceCollector
from app.collectors.metric_collector import MetricCollector
from app.collectors.offense_collector import OffenseCollector
from app.collectors.rule_collector import RuleCollector
from app.collectors.rule_match_collector import RuleMatchCollector
from app.collectors.rule_metric_collector import RuleMetricCollector
from app.core.config import Settings, get_settings
from app.core.database import dispose_engine, get_sessionmaker
from app.custom_rules.engine import CustomRuleEngine
from app.custom_rules.retention import CustomRuleRetention
from app.models.enums import EvidenceStatus, Severity
from app.models.instance import QRadarInstance
from app.models.ips import IpsSourceProfile
from app.models.log_source import LogSource, LogSourceAnomaly
from app.models.monitoring import CollectionWatermark
from app.models.waf import WafSourceProfile
from app.providers.base import QRadarProvider
from app.providers.factory import build_provider, build_provider_for_instance
from app.repositories.offense import OffenseRepository
from app.services.anomaly_fault_retention import AnomalyFaultRetention
from app.services.ariel_permit import ArielPermitBudget
from app.services.catchall_retention import CatchallRetention
from app.services.detection_coverage import DetectionCoverageEvaluator
from app.services.geo_retention import GeoRetention
from app.services.ips_anomaly_retention import IpsAnomalyRetention
from app.services.ips_pipeline import IpsInstancePipeline, ips_pipeline_lock_key
from app.services.ips_profiles import IpsProfileService
from app.services.ips_retention import IpsRetention
from app.services.locks import CollectorAdvisoryLock
from app.services.log_source_anomaly_retention import LogSourceAnomalyRetention
from app.services.pw_pipeline import PwInstancePipeline
from app.services.pw_retention import PwRetention
from app.services.rule_health import RuleHealthEvaluator
from app.services.search_executor import SearchExecutor
from app.services.search_result_retention import SearchResultRetention
from app.services.search_scheduler import SearchScheduler
from app.services.snapshot_retention import SnapshotRetention
from app.services.timescale import apply_policies
from app.services.waf_pipeline import PIPELINE_LOCK, eligible_profile_ids, run_waf_source
from app.services.waf_profiles import WafProfileService
from app.services.waf_retention import WafRetention
from app.workers.celery_app import (
    BEAT_HEARTBEAT_KEY,
    BEAT_HEARTBEAT_TTL_SECONDS,
    celery_app,
)

logger = logging.getLogger("app.tasks")


def _run[T](coro: Coroutine[Any, Any, T]) -> T:
    async def run_and_dispose() -> T:
        try:
            return await coro
        finally:
            # Every Celery shim owns a short-lived event loop. Close asyncpg
            # connections on that same loop so none leak into the next task.
            await dispose_engine()

    return asyncio.run(run_and_dispose())


# ---------------------------------------------------------------- collection
async def collect_metrics(instance_name: str | None = None) -> dict:
    """Collect log-source metrics independently for every enabled instance."""
    from app.models.enums import SearchFamily
    from app.services.search_cadence import SearchCadenceService

    settings = get_settings()
    family = SearchFamily.PHASE_A_VOLUME
    maker = get_sessionmaker()
    async with maker() as cadence_session:
        cadence = SearchCadenceService(cadence_session, settings)
        claimed_slot = await cadence.claim_slot(family)
        await cadence_session.commit()
    if claimed_slot is None:
        return {"status": "not_due", "family": family.value}

    async def run(
        session: AsyncSession, instance: QRadarInstance, provider: QRadarProvider | None
    ) -> dict:
        assert provider is not None
        report = await MetricCollector(session, provider, settings=settings).collect(instance)
        history = report.history
        return {
            "status": (
                "skipped_locked"
                if report.skipped_locked
                else "partial" if report.partial else "ok"
            ),
            "intervals": report.intervals_collected,
            "buckets_collected": report.buckets_collected,
            "searches_sent": report.searches_sent,
            "splits": report.splits,
            "samples": report.samples_written,
            "watermark_at": report.watermark_at,
            "lag_seconds": report.lag_seconds,
            # The Phase A history step (HB-3): its searches are Phase A volume searches
            # against Ariel, so the cadence record below counts them with the live ones.
            "history_searches": history.searches if history else 0,
            "history_state": history.state if history else None,
        }

    try:
        result = await _for_each_instance(run, instance_name)
    except Exception:
        # The slot stays claimed (no retry inside a slot); the counts are unknown.
        async with maker() as cadence_session:
            await SearchCadenceService(cadence_session, settings).record_run(
                family, outcome="failed", searches=None, buckets=None, splits=None
            )
            await cadence_session.commit()
        raise
    statuses = [item.get("status") for item in result.get("results", [])]
    if "failed" in statuses:
        outcome = "failed"
        searches = buckets = splits = None
    else:
        outcome = (
            "partial"
            if any(status in {"partial", "skipped_locked"} for status in statuses)
            else "ok"
        )
        searches = sum(
            item.get("searches_sent", 0) + item.get("history_searches", 0)
            for item in result.get("results", [])
        )
        buckets = sum(
            item.get("buckets_collected", 0) for item in result.get("results", [])
        )
        splits = sum(item.get("splits", 0) for item in result.get("results", []))
    async with maker() as cadence_session:
        cadence = SearchCadenceService(cadence_session, settings)
        await cadence.record_run(
            family,
            outcome=outcome,
            searches=searches,
            buckets=buckets,
            splits=splits,
        )
        await cadence_session.commit()
    return result


# ------------------------------------------------------- phase 3 collection
async def _for_each_instance(
    run: Callable[[AsyncSession, QRadarInstance, QRadarProvider | None], Awaitable[dict]],
    instance_name: str | None = None,
    *,
    needs_provider: bool = True,
) -> dict:
    """Run `run` against every enabled instance, or one named instance.

    Each instance gets a provider built from its own stored credentials and its
    own transaction, so one unreachable console neither fails nor rolls back
    the others. Provider clients are always closed, including on failure — a
    leaked httpx client holds a TLS connection to the SIEM open.

    `needs_provider=False` skips construction entirely for evaluators that only
    read what collection already stored; there is no reason to open a
    connection to QRadar to classify rows in our own database.
    """
    maker = get_sessionmaker()
    results: list[dict] = []

    async with maker() as session:
        query = select(QRadarInstance).where(QRadarInstance.enabled.is_(True))
        if instance_name:
            query = query.where(QRadarInstance.name == instance_name)
        instances = list((await session.scalars(query)).all())

        if not instances:
            return {"status": "no-instance", "instances": 0, "results": []}

        # A rollback expires every loaded object, and reading an expired attribute on
        # the async session raises MissingGreenlet. So the names are read once, here,
        # and the instances a failed run left expired are reloaded before their turn.
        names = [instance.name for instance in instances]
        rolled_back = False
        for instance, name in zip(instances, names, strict=True):
            if rolled_back:
                await session.refresh(instance)
                rolled_back = False
            provider = (
                # audit_session wires the MCP audit sink to AuditLog; it is
                # inert for the REST provider, which audits via telemetry.
                build_provider_for_instance(instance, audit_session=session)
                if needs_provider
                else None
            )
            try:
                outcome = await run(session, instance, provider)
                await session.commit()
            except Exception as exc:
                await session.rollback()
                rolled_back = True
                # The exception type only: an upstream message may carry
                # request detail we must not persist or log.
                outcome = {"status": "failed", "error_class": type(exc).__name__}
                logger.warning(
                    "collection run failed",
                    extra={
                        "instance": name,
                        "error_class": type(exc).__name__,
                    },
                )
            finally:
                if provider is not None:
                    await provider.aclose()
            results.append({"instance": name, **outcome})

    return {"instances": len(results), "results": results}


async def sync_log_sources(instance_name: str | None = None) -> dict:
    async def run(
        session: AsyncSession, instance: QRadarInstance, provider: QRadarProvider | None
    ) -> dict:
        assert provider is not None
        report = await LogSourceCollector(session, provider).sync(instance)
        return {
            "status": (
                "skipped_locked"
                if report.skipped_locked
                else "failed" if report.partial_failure else "ok"
            ),
            "seen": report.log_sources_seen,
            "created": report.created,
            "updated": report.updated,
            "duration_ms": report.duration_ms,
            "error": report.error,
        }

    return await _for_each_instance(run, instance_name)


async def collect_offenses(instance_name: str | None = None) -> dict:
    async def run(
        session: AsyncSession, instance: QRadarInstance, provider: QRadarProvider | None
    ) -> dict:
        assert provider is not None
        report = await OffenseCollector(session, provider).collect(instance)
        return {
            "status": (
                "skipped_locked"
                if report.skipped_locked
                else "failed" if report.partial_failure else "ok"
            ),
            "seen": report.offenses_seen,
            "written": report.snapshots_written,
            "unchanged": report.unchanged,
            "duration_ms": report.duration_ms,
            "error": report.error,
        }

    return await _for_each_instance(run, instance_name)


async def sync_rule_inventory(instance_name: str | None = None) -> dict:
    """Synchronize analytics rules *and* building blocks.

    One task covers both because `RuleCollector.sync` merges the two endpoints
    into a single locked pass over one table. Splitting them would double the
    upstream fetch and make the two halves contend for the same advisory lock.
    """

    async def run(
        session: AsyncSession, instance: QRadarInstance, provider: QRadarProvider | None
    ) -> dict:
        assert provider is not None
        report = await RuleCollector(session, provider).sync(instance)
        return {
            "status": (
                "skipped_locked"
                if report.skipped_locked
                else "failed" if report.partial_failure else "ok"
            ),
            "rules_seen": report.rules_seen,
            "building_blocks": report.building_blocks,
            "created": report.created,
            "updated": report.updated,
            "unchanged": report.unchanged,
            "dependencies": report.dependencies_written,
            "transitions": report.transitions_recorded,
            "duration_ms": report.duration_ms,
            "error": report.error,
        }

    return await _for_each_instance(run, instance_name)


async def collect_rule_metrics(instance_name: str | None = None) -> dict:
    """Collect honest lower-bound firing evidence from stored offenses."""

    async def run(
        session: AsyncSession, instance: QRadarInstance, _: QRadarProvider | None
    ) -> dict:
        report = await RuleMetricCollector(session).collect(instance)
        return {
            "status": (
                "skipped_locked"
                if report.skipped_locked
                else "failed" if report.partial_failure else "ok"
            ),
            "offenses_seen": report.offenses_seen,
            "rules_matched": report.rules_matched,
            "metrics_written": report.metrics_written,
            "contributions": report.contributions,
            "unmatched_rule_ids": report.unmatched_rule_ids,
            "completeness": "incomplete",
            "provenance": "offense_contribution",
            "duration_ms": report.duration_ms,
            "error": report.error,
        }

    return await _for_each_instance(run, instance_name, needs_provider=False)


async def collect_rule_matches(instance_name: str | None = None) -> dict:
    """Collect measured rule matches, one keyed Ariel search per cadence tick.

    A no-op while RULE_MATCH_COLLECTION_ENABLED is false: the cost on a busy
    console is unmeasured, so the beat entry exists but does nothing by default.
    The RULE_MATCH cadence slot is claimed first, in its own committed session,
    so a tick that is not due builds no provider and sends nothing to QRadar.
    """
    settings = get_settings()
    if not settings.rule_match_collection_enabled:
        return {"status": "disabled"}

    from app.models.enums import SearchFamily
    from app.services.search_cadence import SearchCadenceService

    family = SearchFamily.RULE_MATCH
    maker = get_sessionmaker()
    async with maker() as cadence_session:
        cadence = SearchCadenceService(cadence_session, settings)
        claimed_slot = await cadence.claim_slot(family)
        await cadence_session.commit()
    if claimed_slot is None:
        return {"status": "not_due", "family": family.value}

    async def run(
        session: AsyncSession, instance: QRadarInstance, provider: QRadarProvider | None
    ) -> dict:
        assert provider is not None
        report = await RuleMatchCollector(session, provider).collect(instance)
        return {
            "status": (
                "skipped_locked"
                if report.skipped_locked
                else "unsupported"
                if report.unsupported
                else "failed" if report.partial_failure else "ok"
            ),
            "windows_attempted": report.windows_attempted,
            "windows_complete": report.windows_complete,
            "windows_truncated": report.windows_truncated,
            "windows_failed": report.windows_failed,
            "searches_sent": report.searches_sent,
            "splits": report.splits,
            "rows_written": report.rows_written,
            "unknown_ids": report.unknown_ids,
            "duration_ms": report.duration_ms,
            "error": report.error,
        }

    result = await _for_each_instance(run, instance_name)
    results = result.get("results", [])
    outcome = result.get("status")
    if outcome is None:
        outcome = (
            "failed" if any(item.get("status") == "failed" for item in results) else "ok"
        )
    # An instance that raised reports no counts, so the totals are unknown, not
    # a smaller number: record None rather than a count that under-reports.
    measured = bool(results) and all("searches_sent" in item for item in results)
    async with maker() as cadence_session:
        cadence = SearchCadenceService(cadence_session, settings)
        await cadence.record_run(
            family,
            outcome=outcome,
            searches=sum(i["searches_sent"] for i in results) if measured else None,
            buckets=(
                sum(i["windows_complete"] + i["windows_truncated"] for i in results)
                if measured
                else None
            ),
            splits=sum(i["splits"] for i in results) if measured else None,
        )
        await cadence_session.commit()
    return result


async def collect_catchall_senders(instance_name: str | None = None) -> dict:
    """Collect who feeds the catch-all sources, one keyed Ariel search per cadence tick.

    A no-op while CATCHALL_COLLECTION_ENABLED is false. The CATCHALL_SENDERS
    cadence slot is claimed first, in its own committed session, so a tick that
    is not due builds no provider and sends nothing to QRadar.
    """
    settings = get_settings()
    if not settings.catchall_collection_enabled:
        return {"status": "disabled"}

    from app.models.enums import SearchFamily
    from app.services.search_cadence import SearchCadenceService

    family = SearchFamily.CATCHALL_SENDERS
    maker = get_sessionmaker()
    async with maker() as cadence_session:
        cadence = SearchCadenceService(cadence_session, settings)
        claimed_slot = await cadence.claim_slot(family)
        await cadence_session.commit()
    if claimed_slot is None:
        return {"status": "not_due", "family": family.value}

    async def run(
        session: AsyncSession, instance: QRadarInstance, provider: QRadarProvider | None
    ) -> dict:
        assert provider is not None
        report = await CatchallCollector(session, provider, settings=settings).collect(instance)
        return {
            "status": (
                "skipped_locked"
                if report.skipped_locked
                else "unsupported"
                if report.unsupported
                else "failed" if report.partial_failure else "ok"
            ),
            "buckets_attempted": report.buckets_attempted,
            "buckets_complete": report.buckets_complete,
            "buckets_lower_bound": report.buckets_lower_bound,
            "buckets_failed": report.buckets_failed,
            "buckets_not_collected": report.buckets_not_collected,
            "searches_sent": report.searches_sent,
            "splits": report.splits,
            "senders_admitted": report.senders_admitted,
            "alerts_raised": report.alerts_raised,
            "duration_ms": report.duration_ms,
            "error": report.error,
        }

    result = await _for_each_instance(run, instance_name)
    results = result.get("results", [])
    outcome = result.get("status")
    if outcome is None:
        outcome = (
            "failed" if any(item.get("status") == "failed" for item in results) else "ok"
        )
    # An instance that raised reports no counts, so the totals are unknown, not
    # a smaller number: record None rather than a count that under-reports.
    measured = bool(results) and all("searches_sent" in item for item in results)
    async with maker() as cadence_session:
        await SearchCadenceService(cadence_session, settings).record_run(
            family,
            outcome=outcome,
            searches=sum(i["searches_sent"] for i in results) if measured else None,
            buckets=(
                sum(i["buckets_complete"] + i["buckets_lower_bound"] for i in results)
                if measured
                else None
            ),
            splits=sum(i["splits"] for i in results) if measured else None,
        )
        await cadence_session.commit()
    return result


async def apply_catchall_retention() -> dict:
    maker = get_sessionmaker()
    async with maker() as session:
        report = await CatchallRetention(session).run()
        return report.as_dict()


async def collect_geo_countries(instance_name: str | None = None) -> dict:
    """Collect events per country, two keyed Ariel searches per cadence tick (D-10).

    A no-op while GEO_COLLECTION_ENABLED is false. The GEO_COLLECTION cadence slot is
    claimed first, in its own committed session, so a tick that is not due builds no
    provider and sends nothing to QRadar.
    """
    settings = get_settings()
    if not settings.geo_collection_enabled:
        return {"status": "disabled"}

    from app.models.enums import SearchFamily
    from app.services.search_cadence import SearchCadenceService

    family = SearchFamily.GEO_COLLECTION
    maker = get_sessionmaker()
    async with maker() as cadence_session:
        cadence = SearchCadenceService(cadence_session, settings)
        claimed_slot = await cadence.claim_slot(family)
        await cadence_session.commit()
    if claimed_slot is None:
        return {"status": "not_due", "family": family.value}

    async def run(
        session: AsyncSession, instance: QRadarInstance, provider: QRadarProvider | None
    ) -> dict:
        assert provider is not None
        report = await GeoCollector(session, provider, settings=settings).collect(instance)
        return report.as_dict()

    result = await _for_each_instance(run, instance_name)
    results = result.get("results", [])
    outcome = result.get("status")
    if outcome is None:
        outcome = (
            "failed" if any(item.get("status") == "failed" for item in results) else "ok"
        )
    # An instance that raised reports no counts, so the totals are unknown, not
    # a smaller number: record None rather than a count that under-reports.
    measured = bool(results) and all("searches_sent" in item for item in results)
    async with maker() as cadence_session:
        await SearchCadenceService(cadence_session, settings).record_run(
            family,
            outcome=outcome,
            searches=sum(i["searches_sent"] for i in results) if measured else None,
            buckets=(
                sum(i["buckets_complete"] + i["buckets_lower_bound"] for i in results)
                if measured
                else None
            ),
            splits=sum(i["splits"] for i in results) if measured else None,
        )
        await cadence_session.commit()
    return result


async def apply_geo_retention() -> dict:
    maker = get_sessionmaker()
    async with maker() as session:
        report = await GeoRetention(session).run()
        return report.as_dict()


async def detect_stale_offense_collection(instance_name: str | None = None) -> dict:
    """Open/resolve one deduplicated alert per stale offense collector."""

    async def run(
        session: AsyncSession, instance: QRadarInstance, _: QRadarProvider | None
    ) -> dict:
        settings = get_settings()
        started = time.perf_counter()
        async with CollectorAdvisoryLock(
            session, settings, instance.id, "offense_stale_detection"
        ) as acquired:
            if not acquired:
                return {"status": "skipped_locked", "duration_ms": 0}

            now = datetime.now(UTC)
            watermark = await session.scalar(
                select(CollectionWatermark).where(
                    CollectionWatermark.instance_id == instance.id,
                    CollectionWatermark.collector == "offense_snapshot",
                )
            )
            last_run = watermark.last_run_at if watermark is not None else None
            if last_run is not None and last_run.tzinfo is None:
                last_run = last_run.replace(tzinfo=UTC)
            age_seconds = (
                int((now - last_run).total_seconds()) if last_run is not None else None
            )
            failures = watermark.consecutive_failures if watermark is not None else 0
            stale = (
                last_run is None
                or age_seconds is None
                or age_seconds > settings.offense_stale_after_seconds
                or failures > 0
            )

            fingerprint = compute_fingerprint(
                source_type="qradar_instance",
                source_id=instance.id,
                condition="offense_collection_stale",
            )
            alerts = AlertService(session)
            dispatcher = NotificationDispatcher(
                session, notifiers={}, policy=default_policy()
            )
            transition = None
            if stale:
                result = await alerts.open_or_update(
                    AlertInput(
                        fingerprint=fingerprint,
                        title=f"Offense collection is stale for {instance.name}",
                        description=(
                            "The read-only offense collector has not completed within "
                            "its configured freshness threshold."
                        ),
                        severity=Severity.HIGH,
                        source_type="qradar_instance",
                        source_id=instance.id,
                        evidence={
                            "last_run_at": last_run.isoformat() if last_run else None,
                            "age_seconds": age_seconds,
                            "threshold_seconds": settings.offense_stale_after_seconds,
                            "consecutive_failures": failures,
                        },
                    )
                )
                transition = result.transition
                if transition is not None:
                    await dispatcher.enqueue(result.alert, transition)
            else:
                resolved = await alerts.resolve_by_fingerprint(
                    fingerprint,
                    actor="system:offense-collection-monitor",
                    reason="Offense collection freshness recovered.",
                )
                if resolved is not None and resolved.transition is not None:
                    transition = resolved.transition
                    await dispatcher.enqueue(resolved.alert, resolved.transition)

            return {
                "status": "ok",
                "stale": stale,
                "age_seconds": age_seconds,
                "consecutive_failures": failures,
                "transition": str(transition) if transition is not None else None,
                "duration_ms": int((time.perf_counter() - started) * 1000),
            }

    return await _for_each_instance(run, instance_name, needs_provider=False)


async def calculate_offense_aggregates(instance_name: str | None = None) -> dict:
    """Calculate bounded current-offense aggregates for cache warming/monitoring."""

    async def run(
        session: AsyncSession, instance: QRadarInstance, _: QRadarProvider | None
    ) -> dict:
        settings = get_settings()
        started = time.perf_counter()
        async with CollectorAdvisoryLock(
            session, settings, instance.id, "offense_aggregate"
        ) as acquired:
            if not acquired:
                return {"status": "skipped_locked", "duration_ms": 0}
            aggregates = await OffenseRepository(session).aggregate_metrics(
                instance.id,
                sla_hours=settings.offense_sla_hours,
                critical_magnitude=settings.offense_critical_magnitude,
            )
            elapsed = int((time.perf_counter() - started) * 1000)
            # No snapshot: the counts are null. `processed` stays an integer,
            # and the skip says why, the same way a locked run reports a skip
            # with a zero count rather than an empty success.
            if aggregates["active"] is None:
                return {
                    "status": "skipped",
                    "duration_ms": elapsed,
                    **aggregates,
                    "processed": 0,
                    "skipped_reason": "no offense snapshot",
                }
            return {
                "status": "ok",
                "processed": aggregates["active"],
                "duration_ms": elapsed,
                **aggregates,
            }

    return await _for_each_instance(run, instance_name, needs_provider=False)


async def evaluate_rule_health(instance_name: str | None = None) -> dict:
    """Classify rule health. Needs no provider — it reads what we already stored."""

    async def run(
        session: AsyncSession, instance: QRadarInstance, _: QRadarProvider | None
    ) -> dict:
        settings = get_settings()
        async with CollectorAdvisoryLock(
            session, settings, instance.id, "rule_health"
        ) as acquired:
            if not acquired:
                return {"status": "skipped_locked", "evaluated": 0}
            report = await RuleHealthEvaluator(session).evaluate_instance(instance)
            return {
                "status": "ok",
                "evaluated": report.evaluated,
                "by_status": {str(k): v for k, v in report.by_status.items()},
            }

    return await _for_each_instance(run, instance_name, needs_provider=False)


async def evaluate_detection_coverage(instance_name: str | None = None) -> dict:
    async def run(
        session: AsyncSession, instance: QRadarInstance, _: QRadarProvider | None
    ) -> dict:
        settings = get_settings()
        async with CollectorAdvisoryLock(
            session, settings, instance.id, "detection_coverage"
        ) as acquired:
            if not acquired:
                return {"status": "skipped_locked", "techniques": 0}
            report = await DetectionCoverageEvaluator(session).evaluate_instance(instance)
            return {
                "status": "ok",
                "techniques": report.techniques_evaluated,
                "by_status": {str(k): v for k, v in report.by_status.items()},
            }

    return await _for_each_instance(run, instance_name, needs_provider=False)


# ------------------------------------------------------------------ anomalies
async def evaluate_anomalies() -> dict:
    maker = get_sessionmaker()
    async with maker() as session:
        policy = default_policy()
        # Enqueue-only dispatcher: notifiers not needed to create rows.
        enqueuer = NotificationDispatcher(session, notifiers={}, policy=policy)
        engine = AnomalyEngine(session, enqueuer=enqueuer)
        sources = list(
            (
                await session.scalars(
                    select(LogSource).where(LogSource.monitoring_enabled.is_(True))
                )
            ).all()
        )
        opened = resolved = failed = given_up = 0
        for source in sources:
            report = await engine.evaluate_pending(source)
            opened += len(report.opened)
            resolved += len(report.resolved)
            failed += report.failed
            given_up += report.given_up
        await session.commit()
        return {
            "sources": len(sources),
            "opened": opened,
            "resolved": resolved,
            "failed": failed,
            "given_up": given_up,
        }


# -------------------------------------------------------- anomaly explanation
async def collect_anomaly_explanations(instance_name: str | None = None) -> dict:
    """Build bounded evidence packages for anomalies awaiting explanation.

    Runs separately from detection so an unresponsive appliance delays evidence
    rather than delaying alerts, and holds a per-instance advisory lock so two
    workers cannot both hammer Ariel with the same investigation queries.
    """
    settings = get_settings()
    if not settings.explanation_enabled:
        return {"status": "disabled", "instances": 0, "results": []}

    from app.models.enums import SearchFamily
    from app.services.search_cadence import SearchCadenceService

    family = SearchFamily.ANOMALY_EXPLANATION
    maker = get_sessionmaker()
    async with maker() as cadence_session:
        cadence = SearchCadenceService(cadence_session, settings)
        claimed_slot = await cadence.claim_slot(family)
        await cadence_session.commit()
    if claimed_slot is None:
        return {"status": "not_due", "family": family.value}

    async def run(
        session: AsyncSession, instance: QRadarInstance, provider: QRadarProvider | None
    ) -> dict:
        assert provider is not None
        async with CollectorAdvisoryLock(
            session, settings, instance.id, EXPLANATION_COLLECTOR
        ) as acquired:
            if not acquired:
                return {"status": "locked", "explained": 0}

            pending = list(
                (
                    await session.scalars(
                        select(LogSourceAnomaly)
                        .join(LogSource, LogSource.id == LogSourceAnomaly.log_source_id)
                        .where(
                            LogSource.instance_id == instance.id,
                            LogSourceAnomaly.evidence_status == EvidenceStatus.PENDING,
                            LogSourceAnomaly.anomaly_type.in_(
                                [t.value for t in EXPLAINABLE_TYPES]
                            ),
                        )
                        # Oldest first: an anomaly whose window is about to age
                        # out of Ariel retention is the one that cannot wait.
                        .order_by(LogSourceAnomaly.detected_at)
                        .limit(settings.explanation_max_per_run)
                    )
                ).all()
            )

            collector = ExplanationCollector(session, provider, settings=settings)
            statuses: dict[str, int] = {}
            for anomaly in pending:
                report = await collector.collect(anomaly)
                key = report.status.value
                statuses[key] = statuses.get(key, 0) + 1
            return {
                "status": "ok",
                "explained": len(pending),
                "by_status": statuses,
            }

    result = await _for_each_instance(run, instance_name)
    outcome = result.get("status")
    if outcome is None:
        outcome = (
            "failed"
            if any(item.get("status") == "failed" for item in result["results"])
            else "ok"
        )
    async with maker() as cadence_session:
        cadence = SearchCadenceService(cadence_session, settings)
        await cadence.record_run(
            family,
            outcome=outcome,
            searches=None,
            buckets=None,
            splits=None,
        )
        await cadence_session.commit()
    return result


# ------------------------------------------------------------------ baselines
async def rebuild_baselines() -> dict:
    maker = get_sessionmaker()
    async with maker() as session:
        builder = BaselineBuilder(session)
        sources = list(
            (
                await session.scalars(
                    select(LogSource).where(LogSource.monitoring_enabled.is_(True))
                )
            ).all()
        )
        cells = 0
        for source in sources:
            cells += await builder.rebuild_for_source(source)
        await session.commit()
        return {"sources": len(sources), "cells": cells}


# ------------------------------------------------------------ scheduled search
# The scheduler has no QRadar instance; a fixed id gives `collector_lock_key` a stable key.
SEARCH_SCHEDULER_LOCK_ID = uuid.UUID(int=0)


async def run_due_searches() -> dict:
    """Execute every stored search whose cron schedule has come due.

    Threshold and failure alerts raised by a run are enqueued here (delivery is
    the dispatch_notifications task's job), so a breach detected by a scheduled
    search reaches the same notification pipeline as an anomaly.
    """
    settings = get_settings()
    maker = get_sessionmaker()
    # One cycle at a time. `CollectorAdvisoryLock` holds the lock on a
    # connection of its own, outside this session: the scheduler's session
    # commits after every search and returns its connection to the pool, which
    # would not keep a lock taken on it. If this process dies the lock's
    # connection closes and PostgreSQL frees the lock.
    async with maker() as session, CollectorAdvisoryLock(
        session, settings, SEARCH_SCHEDULER_LOCK_ID, "search_scheduler"
    ) as acquired:
        if not acquired:
            return {"status": "skipped_locked"}
        return await _run_due_searches_cycle(settings, maker)


async def _run_due_searches_cycle(settings: Settings, maker: Any) -> dict:
    provider = build_provider()
    try:
        async with maker() as session:
            # Searches are not instance-scoped yet (execution_instance_key is always
            # "default"), so the permit is global-only; a per-console budget binds
            # without further change once they carry real instance ids.
            permit = ArielPermitBudget.for_session(session, settings, instance_id=None)
            enqueuer = NotificationDispatcher(session, notifiers={}, policy=default_policy())
            scheduler = SearchScheduler(
                session,
                SearchExecutor(
                    session, provider, permit, settings=settings, commit_progress=True
                ),
                settings=settings,
                alert_evaluator=SearchAlertEvaluator(
                    session, settings=settings, enqueuer=enqueuer
                ),
            )
            report = await scheduler.run_due()
            await session.commit()
            return {
                "considered": report.considered,
                "dispatched": len(report.dispatched),
                "seeded": len(report.seeded),
                "skipped_running": len(report.skipped_running),
                "invalid_cron": len(report.invalid_cron),
            }
    finally:
        await provider.aclose()


# ------------------------------------------------------------------ WAF (B.1)
async def dispatch_waf_collection(enqueue: Callable[[str], object] | None = None) -> dict:
    """Enqueue one independent run per eligible WAF source, once per cadence slot.

    Fan-out rather than a loop, so one appliance search hanging for its full
    timeout on one source cannot delay any other source. Duplicate delivery is
    harmless: each run takes its source's advisory lock, a second concurrent run
    skips, and every write is an idempotent upsert behind a watermark.

    Ticks every minute; the WAF_COLLECTION cadence gate decides whether a tick is
    due, and a tick that is not sends nothing. Only the dispatch is gated:
    `collect_waf_source` runs whenever it is enqueued. The per-source tasks run
    asynchronously and are not aggregated, so the run is recorded without counts.
    """
    settings = get_settings()
    if not settings.waf_collection_enabled:
        return {"status": "disabled", "dispatched": 0}

    from app.models.enums import SearchFamily
    from app.services.search_cadence import SearchCadenceService

    family = SearchFamily.WAF_COLLECTION
    maker = get_sessionmaker()
    async with maker() as cadence_session:
        claimed_slot = await SearchCadenceService(cadence_session, settings).claim_slot(family)
        await cadence_session.commit()
    if claimed_slot is None:
        return {"status": "not_due", "family": family.value}

    async with maker() as session:
        profile_ids, skipped = await eligible_profile_ids(session, settings)
    send = enqueue or collect_waf_source_task.delay
    for profile_id in profile_ids:
        send(str(profile_id))
    async with maker() as cadence_session:
        await SearchCadenceService(cadence_session, settings).record_run(
            family,
            outcome="dispatched" if profile_ids else "no-sources",
            searches=None,
            buckets=None,
            splits=None,
        )
        await cadence_session.commit()
    return {"status": "ok", "dispatched": len(profile_ids), "skipped": skipped}


async def collect_waf_source(profile_id: str) -> dict:
    """Collect, evaluate and train one WAF source in its own session."""
    maker = get_sessionmaker()
    async with maker() as session:
        enqueuer = NotificationDispatcher(session, notifiers={}, policy=default_policy())
        return await run_waf_source(
            session,
            uuid.UUID(profile_id),
            provider_factory=lambda instance: build_provider_for_instance(
                instance, audit_session=session
            ),
            enqueuer=enqueuer,
        )


async def validate_waf_profile(
    profile_id: str,
    lock_retry_count: int = 0,
    enqueue: Callable[[str, int, int], object] | None = None,
) -> dict:
    """Validate one requested profile without stamping a result onto a newer version."""
    maker = get_sessionmaker()
    async with maker() as session:
        profile_uuid = uuid.UUID(profile_id)
        profile = await session.get(WafSourceProfile, profile_uuid)
        if profile is None:
            return {"status": "not_found", "profile_id": profile_id}
        if profile.validation_requested_at is None:
            return {"status": "not_pending", "profile_id": profile_id}

        source = await session.get(LogSource, profile.log_source_id)
        instance = await session.get(QRadarInstance, profile.instance_id)
        actor = profile.validation_requested_by or "system"
        started_version = profile.profile_version
        settings = get_settings()

        def send(retry_count: int, countdown: int) -> object:
            if enqueue is not None:
                return enqueue(profile_id, retry_count, countdown)
            return validate_waf_profile_task.apply_async(
                args=[profile_id, retry_count], countdown=countdown
            )

        async def fail(error_class: str) -> dict:
            await session.rollback()
            current = await session.scalar(
                select(WafSourceProfile)
                .where(WafSourceProfile.id == profile_uuid)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if current is None:
                await session.commit()
                return {"status": "not_found", "profile_id": profile_id}
            if current.profile_version != started_version:
                await session.commit()
                if current.validation_requested_at is not None:
                    send(0, 0)
                return {
                    "status": "stale_requeued",
                    "profile_id": profile_id,
                    "validated_version": started_version,
                    "current_version": current.profile_version,
                }
            await WafProfileService(session, settings=settings).record_validation_exception(
                current,
                actor=actor,
                error_class=error_class,
            )
            await session.commit()
            return {
                "status": "failed",
                "profile_id": profile_id,
                "error_class": error_class,
            }

        if source is None or instance is None:
            return await fail("WafProfileError")

        async with CollectorAdvisoryLock(
            session,
            settings,
            instance.id,
            f"{PIPELINE_LOCK}:{source.qradar_id}",
        ) as acquired:
            if not acquired:
                await session.rollback()
                if lock_retry_count < settings.waf_validation_lock_max_retries:
                    next_retry_count = lock_retry_count + 1
                    send(
                        next_retry_count,
                        settings.waf_validation_lock_retry_delay_seconds,
                    )
                    return {
                        "status": "locked_requeued",
                        "profile_id": profile_id,
                        "lock_retry_count": next_retry_count,
                    }

                current = await session.scalar(
                    select(WafSourceProfile)
                    .where(WafSourceProfile.id == profile_uuid)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if current is None:
                    await session.commit()
                    return {"status": "not_found", "profile_id": profile_id}
                if current.profile_version != started_version:
                    await session.commit()
                    if current.validation_requested_at is not None:
                        send(0, 0)
                    return {
                        "status": "stale_requeued",
                        "profile_id": profile_id,
                        "validated_version": started_version,
                        "current_version": current.profile_version,
                    }
                if current.validation_requested_at is None:
                    await session.commit()
                    return {"status": "not_pending", "profile_id": profile_id}
                await WafProfileService(
                    session, settings=settings
                ).record_validation_source_busy(
                    current,
                    actor=actor,
                    retry_count=lock_retry_count,
                )
                await session.commit()
                logger.warning(
                    "WAF profile validation exhausted source-lock retries",
                    extra={
                        "profile_id": profile_id,
                        "profile_version": started_version,
                        "lock_retry_count": lock_retry_count,
                    },
                )
                return {
                    "status": "source_busy",
                    "profile_id": profile_id,
                    "lock_retry_count": lock_retry_count,
                }

            provider: QRadarProvider | None = None
            result = None
            error_class: str | None = None
            try:
                provider = build_provider_for_instance(instance, audit_session=session)
                service = WafProfileService(session, settings=settings)
                result = await service.validate(
                    profile,
                    provider,
                    actor=actor,
                    persist=False,
                )
            except Exception as exc:
                error_class = type(exc).__name__
            finally:
                if provider is not None:
                    try:
                        await provider.aclose()
                    except Exception as exc:
                        error_class = error_class or type(exc).__name__
            if error_class is not None:
                return await fail(error_class)
            assert result is not None

            current = await session.scalar(
                select(WafSourceProfile)
                .where(WafSourceProfile.id == profile_uuid)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if current is None:
                await session.commit()
                return {"status": "not_found", "profile_id": profile_id}
            if current.profile_version != started_version:
                await session.commit()
                if current.validation_requested_at is not None:
                    send(0, 0)
                return {
                    "status": "stale_requeued",
                    "profile_id": profile_id,
                    "validated_version": started_version,
                    "current_version": current.profile_version,
                }

            await service.store_validation(current, result, actor=actor)
            await session.commit()
            return {
                "status": "ok",
                "profile_id": profile_id,
                "profile_version": started_version,
                "validation_status": result.status.value,
            }


async def validate_ips_profile(
    profile_id: str,
    lock_retry_count: int = 0,
    enqueue: Callable[[str, int, int], object] | None = None,
) -> dict:
    """Validate one IPS profile without stamping a stale mapping version."""
    maker = get_sessionmaker()
    async with maker() as session:
        profile_uuid = uuid.UUID(profile_id)
        profile = await session.get(IpsSourceProfile, profile_uuid)
        if profile is None:
            return {"status": "not_found", "profile_id": profile_id}
        if profile.validation_requested_at is None:
            return {"status": "not_pending", "profile_id": profile_id}

        source = await session.get(LogSource, profile.log_source_id)
        instance = await session.get(QRadarInstance, profile.instance_id)
        actor = profile.validation_requested_by or "system"
        started_version = profile.profile_version
        settings = get_settings()

        def send(retry_count: int, countdown: int) -> object:
            if enqueue is not None:
                return enqueue(profile_id, retry_count, countdown)
            return validate_ips_profile_task.apply_async(
                args=[profile_id, retry_count], countdown=countdown
            )

        async def fail(error_class: str) -> dict:
            await session.rollback()
            current = await session.scalar(
                select(IpsSourceProfile)
                .where(IpsSourceProfile.id == profile_uuid)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if current is None:
                await session.commit()
                return {"status": "not_found", "profile_id": profile_id}
            if current.profile_version != started_version:
                await session.commit()
                if current.validation_requested_at is not None:
                    send(0, 0)
                return {
                    "status": "stale_requeued",
                    "profile_id": profile_id,
                    "validated_version": started_version,
                    "current_version": current.profile_version,
                }
            await IpsProfileService(session, settings=settings).record_validation_exception(
                current, actor=actor, error_class=error_class
            )
            await session.commit()
            return {
                "status": "failed",
                "profile_id": profile_id,
                "error_class": error_class,
            }

        if source is None or instance is None:
            return await fail("IpsProfileError")

        async with CollectorAdvisoryLock(
            session,
            settings,
            instance.id,
            ips_pipeline_lock_key(source.qradar_id),
        ) as acquired:
            if not acquired:
                await session.rollback()
                if lock_retry_count < settings.ips_validation_lock_max_retries:
                    next_retry_count = lock_retry_count + 1
                    send(next_retry_count, settings.ips_validation_lock_retry_delay_seconds)
                    return {
                        "status": "locked_requeued",
                        "profile_id": profile_id,
                        "lock_retry_count": next_retry_count,
                    }

                current = await session.scalar(
                    select(IpsSourceProfile)
                    .where(IpsSourceProfile.id == profile_uuid)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if current is None:
                    await session.commit()
                    return {"status": "not_found", "profile_id": profile_id}
                if current.profile_version != started_version:
                    await session.commit()
                    if current.validation_requested_at is not None:
                        send(0, 0)
                    return {
                        "status": "stale_requeued",
                        "profile_id": profile_id,
                        "validated_version": started_version,
                        "current_version": current.profile_version,
                    }
                if current.validation_requested_at is None:
                    await session.commit()
                    return {"status": "not_pending", "profile_id": profile_id}
                await IpsProfileService(
                    session, settings=settings
                ).record_validation_source_busy(
                    current, actor=actor, retry_count=lock_retry_count
                )
                await session.commit()
                logger.warning(
                    "IPS profile validation exhausted source-lock retries",
                    extra={
                        "profile_id": profile_id,
                        "profile_version": started_version,
                        "lock_retry_count": lock_retry_count,
                    },
                )
                return {
                    "status": "source_busy",
                    "profile_id": profile_id,
                    "lock_retry_count": lock_retry_count,
                }

            provider: QRadarProvider | None = None
            result = None
            error_class: str | None = None
            try:
                provider = build_provider_for_instance(instance, audit_session=session)
                service = IpsProfileService(session, settings=settings)
                result = await service.validate(
                    profile, provider, actor=actor, persist=False
                )
            except Exception as exc:
                error_class = type(exc).__name__
            finally:
                if provider is not None:
                    try:
                        await provider.aclose()
                    except Exception as exc:
                        error_class = error_class or type(exc).__name__
            if error_class is not None:
                return await fail(error_class)
            assert result is not None

            current = await session.scalar(
                select(IpsSourceProfile)
                .where(IpsSourceProfile.id == profile_uuid)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if current is None:
                await session.commit()
                return {"status": "not_found", "profile_id": profile_id}
            if current.profile_version != started_version:
                await session.commit()
                if current.validation_requested_at is not None:
                    send(0, 0)
                return {
                    "status": "stale_requeued",
                    "profile_id": profile_id,
                    "validated_version": started_version,
                    "current_version": current.profile_version,
                }
            await service.store_validation(current, result, actor=actor)
            await session.commit()
            return {
                "status": "ok",
                "profile_id": profile_id,
                "profile_version": started_version,
                "validation_status": result.status.value,
            }


async def apply_waf_retention() -> dict:
    maker = get_sessionmaker()
    async with maker() as session:
        report = await WafRetention(session).run()
        return report.as_dict()


async def apply_log_source_anomaly_retention() -> dict:
    """Delete closed Phase A incidents past the retention window.

    Not gated by RETENTION_ENABLED. Live incidents, detector pointers and any
    alert that lists the incident are kept for the life of the alert.
    Exclusive children leave by cascade.
    """
    maker = get_sessionmaker()
    async with maker() as session:
        return (await LogSourceAnomalyRetention(session).run()).as_dict()


async def apply_anomaly_fault_retention() -> dict:
    """Delete closed anomaly evaluation faults older than the retention window.

    An open fault is the source's current state and is never touched; a
    given-up one is kept for the full window, because it is the only record
    that its bucket has no verdict.
    """
    maker = get_sessionmaker()
    async with maker() as session:
        return (await AnomalyFaultRetention(session).run()).as_dict()


async def evaluate_custom_rules() -> dict:
    """Judge custom threshold rules from stored buckets. Sends no Ariel search.

    A no-op, and no session, while CUSTOM_RULES_ENABLED is false. The first
    enabled run seeds each kind's watermark and judges nothing.
    """
    settings = get_settings()
    if not settings.custom_rules_enabled:
        return {"status": "disabled"}
    maker = get_sessionmaker()
    async with maker() as session:
        # Enqueue only. Delivery stays on the notification beat, as for IPS.
        # The engine drops this when shadow mode is on and writes no alert row.
        enqueuer = NotificationDispatcher(session, notifiers={}, policy=default_policy())
        report = await CustomRuleEngine(session, settings, enqueuer=enqueuer).run()
        await session.commit()
        return report.as_dict()


async def apply_custom_rule_retention() -> dict:
    """Delete resolved breaches older than the retention window, in bounded batches.

    The service commits each batch. Live breaches, and rules, are kept.
    """
    maker = get_sessionmaker()
    async with maker() as session:
        report = await CustomRuleRetention(session).run()
        return report.as_dict()


# ------------------------------------------------------------------ IPS (D.5)
async def dispatch_ips_collection(instance_name: str | None = None) -> dict:
    """Run one fleet-wide IPS plan per instance after claiming its cadence slot."""
    settings = get_settings()
    if not settings.ips_collection_enabled:
        return {"status": "disabled", "instances": 0, "results": []}

    from app.models.enums import SearchFamily
    from app.services.search_cadence import SearchCadenceService

    family = SearchFamily.IPS_COLLECTION
    maker = get_sessionmaker()
    async with maker() as cadence_session:
        cadence = SearchCadenceService(cadence_session, settings)
        claimed = await cadence.claim_slot(family)
        await cadence_session.commit()
    if claimed is None:
        return {"status": "not_due", "family": family.value}

    async def run(
        session: AsyncSession, instance: QRadarInstance, provider: QRadarProvider | None
    ) -> dict:
        assert provider is not None
        collector = IpsCollector(session, provider, settings=settings)
        collection = (await collector.collect(instance)).as_dict()
        enqueuer = NotificationDispatcher(session, notifiers={}, policy=default_policy())
        pipeline = await IpsInstancePipeline(
            session, settings=settings, enqueuer=enqueuer
        ).run(instance)
        return {**collection, "pipeline": pipeline.as_dict()}

    result = await _for_each_instance(run, instance_name)
    failed = any(item.get("status") == "failed" for item in result.get("results", []))
    searches = None if failed else sum(
        int(item.get("searches_sent", 0)) for item in result.get("results", [])
    )
    buckets = None if failed else sum(
        int(item.get("buckets_written", 0)) for item in result.get("results", [])
    )
    async with maker() as cadence_session:
        await SearchCadenceService(cadence_session, settings).record_run(
            family,
            outcome="failed" if failed else "ok",
            searches=searches,
            buckets=buckets,
            splits=None,
        )
        await cadence_session.commit()
    return result


async def apply_ips_retention() -> dict:
    maker = get_sessionmaker()
    async with maker() as session:
        return (await IpsRetention(session).run()).as_dict()


async def apply_ips_anomaly_retention() -> dict:
    """Delete closed IPS incidents past the retention window.

    Not the metric task: a lookback skip there must not freeze incident aging.
    """
    maker = get_sessionmaker()
    async with maker() as session:
        return (await IpsAnomalyRetention(session).run()).as_dict()


# ------------------------------------------------------- property watches (G.6)
async def dispatch_property_watch_collection(instance_name: str | None = None) -> dict:
    """Run one property-watch plan per instance after claiming its cadence slot.

    The plan is the whole pipeline: collect, then evaluate, then train (design §G.6), all in
    one call to `PwInstancePipeline`. A no-op while `PW_COLLECTION_ENABLED` is false, and the
    slot is claimed before any provider is built, so a losing or early minute sends nothing
    to QRadar.
    """
    settings = get_settings()
    if not settings.pw_collection_enabled:
        return {"status": "disabled", "instances": 0, "results": []}

    from app.models.enums import SearchFamily
    from app.services.search_cadence import SearchCadenceService

    family = SearchFamily.PROPERTY_WATCH
    maker = get_sessionmaker()
    async with maker() as cadence_session:
        cadence = SearchCadenceService(cadence_session, settings)
        claimed = await cadence.claim_slot(family)
        await cadence_session.commit()
    if claimed is None:
        return {"status": "not_due", "family": family.value}

    async def run(
        session: AsyncSession, instance: QRadarInstance, provider: QRadarProvider | None
    ) -> dict:
        assert provider is not None
        pipeline = await PwInstancePipeline(session, provider, settings=settings).run(instance)
        return pipeline.as_dict()

    result = await _for_each_instance(run, instance_name)
    statuses = [item.get("status") for item in result.get("results", [])]
    failed = any(status == "failed" for status in statuses)
    searches = None if failed else sum(
        int(item.get("searches_sent", 0)) for item in result.get("results", [])
    )
    buckets = None if failed else sum(
        int(item.get("buckets_written", 0)) for item in result.get("results", [])
    )
    async with maker() as cadence_session:
        await SearchCadenceService(cadence_session, settings).record_run(
            family,
            # A lock skip is not a failure (the geo and metric dispatches record it
            # `partial`): another worker holds the run, so the tick simply moves on.
            outcome=(
                "failed"
                if failed
                else "partial" if "skipped_locked" in statuses else "ok"
            ),
            searches=searches,
            buckets=buckets,
            splits=None,
        )
        await cadence_session.commit()
    return result


async def apply_property_watch_retention() -> dict:
    maker = get_sessionmaker()
    async with maker() as session:
        return (await PwRetention(session).run()).as_dict()


async def apply_search_result_retention() -> dict:
    maker = get_sessionmaker()
    async with maker() as session:
        return (await SearchResultRetention(session).run()).as_dict()


async def apply_snapshot_retention() -> dict:
    """Delete aged rule-health and coverage snapshots. Not RETENTION_ENABLED."""
    maker = get_sessionmaker()
    async with maker() as session:
        return (await SnapshotRetention(session).run()).as_dict()


async def apply_timescale_policies() -> dict:
    """Reconcile the Timescale retention and compression policies with the settings.

    Runs even while RETENTION_ENABLED is false: reconciling then removes any
    retention policy, so switching the flag off stops a policy that would still
    drop chunks. With the defaults no policy exists and nothing changes.
    """
    settings = get_settings()
    if not settings.retention_enabled:
        logger.info("RETENTION_ENABLED is false; removing any Timescale retention policy")
    maker = get_sessionmaker()
    async with maker() as session:
        outcome = await apply_policies(session, settings)
    failed = sorted(k for k, v in outcome.items() if v.startswith("error:"))
    if failed:
        logger.error("timescale policy reconcile failed", extra={"failed": failed})
    return {"outcome": outcome, "failed": failed}


# --------------------------------------------------------------- notifications
async def dispatch_notifications() -> dict:
    maker = get_sessionmaker()
    notifiers = build_notifiers()
    try:
        async with maker() as session:
            dispatcher = NotificationDispatcher(session, notifiers, default_policy())
            stats = await dispatcher.dispatch_due()
            await session.commit()
            return stats
    finally:
        for n in notifiers.values():
            client = getattr(n, "_client", None)
            if client is not None:
                await client.aclose()


# --------------------------------------------------------------- celery shims
@celery_app.task(name="beat_heartbeat")
def beat_heartbeat_task() -> dict[str, str]:
    """Prove beat is dispatching by writing only a short-lived UTC timestamp."""
    timestamp = datetime.now(UTC).isoformat()
    with Redis.from_url(get_settings().redis_url, decode_responses=True) as client:
        client.set(BEAT_HEARTBEAT_KEY, timestamp, ex=BEAT_HEARTBEAT_TTL_SECONDS)
    return {"timestamp": timestamp}


@celery_app.task(name="collect_metrics")
def collect_metrics_task(instance_name: str | None = None) -> dict:
    return _run(collect_metrics(instance_name))


@celery_app.task(name="sync_log_sources")
def sync_log_sources_task(instance_name: str | None = None) -> dict:
    return _run(sync_log_sources(instance_name))


@celery_app.task(name="collect_offenses")
def collect_offenses_task(instance_name: str | None = None) -> dict:
    return _run(collect_offenses(instance_name))


@celery_app.task(name="sync_rule_inventory")
def sync_rule_inventory_task(instance_name: str | None = None) -> dict:
    return _run(sync_rule_inventory(instance_name))


@celery_app.task(name="collect_rule_metrics")
def collect_rule_metrics_task(instance_name: str | None = None) -> dict:
    return _run(collect_rule_metrics(instance_name))


@celery_app.task(name="detect_stale_offense_collection")
def detect_stale_offense_collection_task(instance_name: str | None = None) -> dict:
    return _run(detect_stale_offense_collection(instance_name))


@celery_app.task(name="calculate_offense_aggregates")
def calculate_offense_aggregates_task(instance_name: str | None = None) -> dict:
    return _run(calculate_offense_aggregates(instance_name))


@celery_app.task(name="collect_rule_matches")
def collect_rule_matches_task(instance_name: str | None = None) -> dict:
    return _run(collect_rule_matches(instance_name))


@celery_app.task(name="evaluate_rule_health")
def evaluate_rule_health_task(instance_name: str | None = None) -> dict:
    return _run(evaluate_rule_health(instance_name))


@celery_app.task(name="evaluate_detection_coverage")
def evaluate_detection_coverage_task(instance_name: str | None = None) -> dict:
    return _run(evaluate_detection_coverage(instance_name))


@celery_app.task(name="evaluate_anomalies")
def evaluate_anomalies_task() -> dict:
    return _run(evaluate_anomalies())


@celery_app.task(name="collect_anomaly_explanations")
def collect_anomaly_explanations_task(instance_name: str | None = None) -> dict:
    return _run(collect_anomaly_explanations(instance_name))


@celery_app.task(name="rebuild_baselines")
def rebuild_baselines_task() -> dict:
    return _run(rebuild_baselines())


@celery_app.task(name="run_due_searches")
def run_due_searches_task() -> dict:
    return _run(run_due_searches())


@celery_app.task(name="dispatch_notifications")
def dispatch_notifications_task() -> dict:
    return _run(dispatch_notifications())


@celery_app.task(name="dispatch_waf_collection")
def dispatch_waf_collection_task() -> dict:
    return _run(dispatch_waf_collection())


@celery_app.task(name="collect_waf_source")
def collect_waf_source_task(profile_id: str) -> dict:
    return _run(collect_waf_source(profile_id))


@celery_app.task(name="validate_waf_profile")
def validate_waf_profile_task(profile_id: str, lock_retry_count: int = 0) -> dict:
    return _run(validate_waf_profile(profile_id, lock_retry_count))


@celery_app.task(name="validate_ips_profile")
def validate_ips_profile_task(profile_id: str, lock_retry_count: int = 0) -> dict:
    return _run(validate_ips_profile(profile_id, lock_retry_count))


@celery_app.task(name="collect_catchall_senders")
def collect_catchall_senders_task(instance_name: str | None = None) -> dict:
    return _run(collect_catchall_senders(instance_name))


@celery_app.task(name="collect_geo_countries")
def collect_geo_countries_task(instance_name: str | None = None) -> dict:
    return _run(collect_geo_countries(instance_name))


@celery_app.task(name="apply_geo_retention")
def apply_geo_retention_task() -> dict:
    return _run(apply_geo_retention())


@celery_app.task(name="apply_catchall_retention")
def apply_catchall_retention_task() -> dict:
    return _run(apply_catchall_retention())


@celery_app.task(name="apply_waf_retention")
def apply_waf_retention_task() -> dict:
    return _run(apply_waf_retention())


@celery_app.task(name="apply_anomaly_fault_retention")
def apply_anomaly_fault_retention_task() -> dict:
    return _run(apply_anomaly_fault_retention())


@celery_app.task(name="apply_log_source_anomaly_retention")
def apply_log_source_anomaly_retention_task() -> dict:
    return _run(apply_log_source_anomaly_retention())


@celery_app.task(name="dispatch_ips_collection")
def dispatch_ips_collection_task(instance_name: str | None = None) -> dict:
    return _run(dispatch_ips_collection(instance_name))


@celery_app.task(name="apply_ips_retention")
def apply_ips_retention_task() -> dict:
    return _run(apply_ips_retention())


@celery_app.task(name="apply_ips_anomaly_retention")
def apply_ips_anomaly_retention_task() -> dict:
    return _run(apply_ips_anomaly_retention())


@celery_app.task(name="dispatch_property_watch_collection")
def dispatch_property_watch_collection_task(instance_name: str | None = None) -> dict:
    return _run(dispatch_property_watch_collection(instance_name))


@celery_app.task(name="apply_property_watch_retention")
def apply_property_watch_retention_task() -> dict:
    return _run(apply_property_watch_retention())


@celery_app.task(name="apply_search_result_retention")
def apply_search_result_retention_task() -> dict:
    return _run(apply_search_result_retention())


@celery_app.task(name="apply_snapshot_retention")
def apply_snapshot_retention_task() -> dict:
    return _run(apply_snapshot_retention())


@celery_app.task(name="apply_timescale_policies")
def apply_timescale_policies_task() -> dict:
    return _run(apply_timescale_policies())


@celery_app.task(name="evaluate_custom_rules")
def evaluate_custom_rules_task() -> dict:
    return _run(evaluate_custom_rules())


@celery_app.task(name="apply_custom_rule_retention")
def apply_custom_rule_retention_task() -> dict:
    return _run(apply_custom_rule_retention())
