"""Celery application.

Phase 1 defines the app and the beat schedule placeholder only; the collection,
anomaly and notification tasks are registered in Phase 2. APScheduler is the
documented MVP fallback for teams not running Celery — see README.
"""

from __future__ import annotations

import logging

from celery import Celery
from celery.signals import beat_init

from app.core.config import get_settings

settings = get_settings()

BEAT_HEARTBEAT_KEY = "qradar-observability:celery-beat-heartbeat"
BEAT_HEARTBEAT_TTL_SECONDS = 180

celery_app = Celery(
    "qradar_observability",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
)

# Ensure task modules are imported so the workers register them.
celery_app.autodiscover_tasks(["app.workers"])
celery_app.conf.imports = ("app.workers.tasks",)

# Periodic schedule. Intervals are conservative defaults; tune per deployment.
celery_app.conf.beat_schedule = {
    "beat-heartbeat": {
        "task": "beat_heartbeat",
        "schedule": 60.0,
    },
    "collect-metrics": {
        "task": "collect_metrics",
        "schedule": 60.0,
    },
    "evaluate-anomalies": {
        # Runs shortly after each collection cycle.
        "task": "evaluate_anomalies",
        "schedule": 60.0,
    },
    "dispatch-notifications": {
        "task": "dispatch_notifications",
        "schedule": 30.0,
    },
    "run-due-searches": {
        "task": "run_due_searches",
        "schedule": 60.0,
    },
    "rebuild-baselines": {
        # Daily; baselining is expensive and does not need to be frequent.
        "task": "rebuild_baselines",
        "schedule": 86400.0,
    },
    "collect-anomaly-explanations": {
        # Phase A investigation evidence. Deliberately decoupled from
        # detection: each anomaly costs two bounded Ariel queries per
        # dimension, so an unresponsive appliance must delay evidence rather
        # than delay the alert that something is wrong.
        "task": "collect_anomaly_explanations",
        "schedule": 60.0,
    },
    # --- Phase 3 -------------------------------------------------------------
    # Every interval below is configurable; none is a hardcoded production
    # cadence. Ordering matters: inventory feeds health, health feeds coverage,
    # so each runs no more often than what it depends on.
    "sync-log-sources": {
        "task": "sync_log_sources",
        "schedule": float(settings.log_source_sync_interval_seconds),
    },
    "collect-offenses": {
        "task": "collect_offenses",
        "schedule": float(settings.offense_collection_interval_seconds),
    },
    "detect-stale-offense-collection": {
        "task": "detect_stale_offense_collection",
        "schedule": float(settings.offense_stale_check_interval_seconds),
    },
    "calculate-offense-aggregates": {
        "task": "calculate_offense_aggregates",
        "schedule": float(settings.offense_aggregate_interval_seconds),
    },
    "sync-rule-inventory": {
        # Also covers building blocks: RuleCollector merges both endpoints.
        "task": "sync_rule_inventory",
        "schedule": float(settings.rule_collection_interval_seconds),
    },
    "collect-rule-metrics": {
        # Inferred lower bounds from stored offense contribution; no QRadar
        # write and no invented rule-statistics endpoint.
        "task": "collect_rule_metrics",
        "schedule": float(settings.rule_metric_collection_interval_seconds),
    },
    "collect-rule-matches": {
        # Measured rule matches (Phase E.1a): one read-only keyed Ariel search
        # per cadence tick. Ticks every minute; the RULE_MATCH cadence gate
        # decides whether a tick is due. A no-op while
        # RULE_MATCH_COLLECTION_ENABLED is false.
        "task": "collect_rule_matches",
        "schedule": 60.0,
    },
    "evaluate-rule-health": {
        "task": "evaluate_rule_health",
        "schedule": float(settings.rule_health_evaluation_interval_seconds),
    },
    "evaluate-detection-coverage": {
        "task": "evaluate_detection_coverage",
        "schedule": float(settings.coverage_evaluation_interval_seconds),
    },
    # --- Phase B.1 -----------------------------------------------------------
    "dispatch-waf-collection": {
        # Enqueues one `collect_waf_source` task per eligible WAF source, so a
        # slow or failing source never delays another. Each run collects, then
        # evaluates, then trains, under that source's own advisory lock. Ticks
        # every minute; the WAF_COLLECTION cadence gate decides whether a tick
        # is due. A no-op while WAF_COLLECTION_ENABLED is false.
        "task": "dispatch_waf_collection",
        "schedule": 60.0,
    },
    # --- Phase C.2b ----------------------------------------------------------
    "collect-catchall-senders": {
        # One read-only keyed Ariel search per cadence tick over the catch-all
        # device types. Ticks every minute; the CATCHALL_SENDERS cadence gate
        # decides whether a tick is due. A no-op while
        # CATCHALL_COLLECTION_ENABLED is false.
        "task": "collect_catchall_senders",
        "schedule": 60.0,
    },
    "apply-catchall-retention": {
        "task": "apply_catchall_retention",
        "schedule": float(settings.catchall_retention_interval_seconds),
    },
    # --- D-10 ----------------------------------------------------------------
    "collect-geo-countries": {
        # Two read-only keyed Ariel searches per cadence tick (one per direction).
        # Ticks every minute; the GEO_COLLECTION cadence gate decides whether a tick
        # is due. A no-op while GEO_COLLECTION_ENABLED is false.
        "task": "collect_geo_countries",
        "schedule": 60.0,
    },
    "apply-geo-retention": {
        "task": "apply_geo_retention",
        "schedule": float(settings.geo_retention_interval_seconds),
    },
    "apply-waf-retention": {
        "task": "apply_waf_retention",
        "schedule": float(settings.waf_retention_interval_seconds),
    },
    # --- Phase D -------------------------------------------------------------
    "dispatch-ips-collection": {
        # The task claims IPS_COLLECTION before it builds a provider or plans
        # the fleet, so a losing/early minute sends nothing to QRadar.
        "task": "dispatch_ips_collection",
        "schedule": 60.0,
    },
    "apply-ips-retention": {
        "task": "apply_ips_retention",
        "schedule": float(settings.ips_retention_interval_seconds),
    },
    # --- Phase F.2 -----------------------------------------------------------
    "apply-search-result-retention": {
        # Bounded delete of search_result_metric rows by bucket_start. Daily,
        # like the other retention tasks. Not the optional Timescale chunk
        # policy (that stays behind RETENTION_ENABLED).
        "task": "apply_search_result_retention",
        "schedule": float(settings.search_result_retention_interval_seconds),
    },
    # --- Phase F -------------------------------------------------------------
    "evaluate-custom-rules": {
        # DB-only. A no-op while CUSTOM_RULES_ENABLED is false, which is the
        # default, so the beat itself changes no behaviour on upgrade.
        "task": "evaluate_custom_rules",
        "schedule": 60.0,
    },
    "apply-custom-rule-retention": {
        "task": "apply_custom_rule_retention",
        "schedule": float(settings.custom_rules_retention_interval_seconds),
    },
    "apply-timescale-policies": {
        # Reconciles TimescaleDB retention and compression with the settings.
        # Also sent once when beat starts (below), so a changed setting does not
        # wait a day.
        "task": "apply_timescale_policies",
        "schedule": float(settings.timescale_policy_interval_seconds),
    },
}


@beat_init.connect
def _apply_policies_at_beat_start(**_: object) -> None:
    """Send the policy task once at beat start.

    Beat is a single process, so replicas of the API or the worker do not race.
    The task is idempotent. A broker that is down must not stop beat.
    """
    try:
        celery_app.send_task("apply_timescale_policies")
    except Exception:
        logging.getLogger("app.timescale").warning(
            "could not queue the startup policy run", exc_info=True
        )
