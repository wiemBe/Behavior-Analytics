"""Application settings.

Everything is sourced from the environment. No secret is ever hardcoded, and
`Settings` refuses to construct an unsafe configuration outside development.
Staging is the production posture for a pre-production site.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import (
    AliasChoices,
    Field,
    SecretStr,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.models.enums import OutagePolicyMode


class Environment(StrEnum):
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class ProviderKind(StrEnum):
    MOCK = "mock"
    REST = "rest"
    MCP = "mcp"


#: The ``SECRET_KEY`` and ``ENCRYPTION_KEY`` strings shipped in ``.env.example``.
#: A hardened environment refuses these exact values. The unit test
#: ``test_placeholder_constant_matches_env_example`` fails if this map and the file drift apart.
EXAMPLE_PLACEHOLDERS: dict[str, str] = {
    "SECRET_KEY": "change-me-generate-a-64-byte-urlsafe-token",
    "ENCRYPTION_KEY": "change-me-generate-a-fernet-key",
}

# ``./qradar-dash`` writes SECRET_KEY with ``openssl rand -base64 48``. The
# docs also give the ``secrets`` one-liner. Neither value is logged.
_APP_KEY_GENERATE = (
    "Generate one with openssl rand -base64 48 "
    'or python -c "import secrets; print(secrets.token_urlsafe(64))"'
)
# ``./qradar-dash`` generates this inside the backend image. The docs give the
# same one-liner. The value that failed is never included in the message.
_FERNET_KEY_GENERATE = (
    'Generate one with python -c "from cryptography.fernet import Fernet; '
    'print(Fernet.generate_key().decode())"'
)

#: Names ``os.environ`` reads under ``backend/app`` that are not ``Settings`` fields.
#: Exact names, not prefixes: a prefix would also hide a typo of a real ``NOTIFY_*``
#: setting (``notify_per_channel_rate`` and the other ``notify_*`` fields).
#:   ``NOTIFY_TEAMS_WEBHOOK_URL`` and the other ``NOTIFY_*`` names — ``app/alerts/routing.py``
#:   ``SMTP_*`` — ``app/alerts/notifiers/registry.py``
#:   ``GIT_SHA`` — ``app/core/version.py``
SETTINGS_ENV_ALLOWLIST: frozenset[str] = frozenset(
    {
        "NOTIFY_TEAMS_WEBHOOK_URL",
        "NOTIFY_SLACK_WEBHOOK_URL",
        "NOTIFY_GENERIC_WEBHOOK_URL",
        "NOTIFY_EMAIL_RECIPIENTS",
        "NOTIFY_SYSLOG_HOST",
        "NOTIFY_SYSLOG_PORT",
        "SMTP_HOST",
        "SMTP_PORT",
        "SMTP_USERNAME",
        "SMTP_PASSWORD",
        "SMTP_FROM",
        "SMTP_USE_TLS",
        "GIT_SHA",
    }
)

#: Written by ``./qradar-dash install`` or interpolated by the compose files, and not a
#: ``Settings`` field, but sharing a setting's first ``_`` segment. A prefix allowlist
#: would hide typos. The unit test parses ``installer_env_key`` and both compose files
#: and fails if any of those names is reported, so a new installer key cannot slip through.
DEPLOYMENT_ENV_NAMES: frozenset[str] = frozenset(
    {
        "API_LOOPBACK_PORT",
        "QRADAR_CONSOLE_FQDN",
        "QRADAR_DASH_BACKUP_DIR",
        "QRADAR_DASH_IMAGE_PREFIX",
        "QRADAR_DASH_VERSION",
    }
)


def _fernet_accepts(value: str) -> bool:
    """True when ``cryptography.fernet.Fernet`` can construct a key from ``value``."""
    from cryptography.fernet import Fernet

    try:
        Fernet(value)
    except (ValueError, TypeError):
        return False
    return True


def _encryption_keys_json_problems(raw: str) -> list[str]:
    """Problems with ``ENCRYPTION_KEYS_JSON``. The key material is never quoted."""
    text = raw.strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return ["ENCRYPTION_KEYS_JSON is not valid JSON"]
    if not isinstance(parsed, dict):
        return ["ENCRYPTION_KEYS_JSON must be a JSON object of version to Fernet key"]
    not_integers: list[str] = []
    not_fernet: list[str] = []
    for ver, key in parsed.items():
        label = ver if isinstance(ver, str) else str(ver)
        try:
            int(label)
        except (TypeError, ValueError):
            not_integers.append(label)
            continue
        if not isinstance(key, str) or not _fernet_accepts(key):
            not_fernet.append(label)
    problems: list[str] = []
    if not_integers:
        problems.append(
            "ENCRYPTION_KEYS_JSON version " + ", ".join(not_integers) + " is not an integer"
        )
    if not_fernet:
        problems.append(
            "ENCRYPTION_KEYS_JSON has an entry Fernet cannot construct (version "
            + ", ".join(not_fernet)
            + "); "
            + _FERNET_KEY_GENERATE
        )
    return problems


def _hardened_secret_problems(secret: str, encryption: str, keys_json: str) -> list[str]:
    """Placeholder and unusable-key refusals. Messages name the setting, never the value."""
    problems: list[str] = []
    if secret == EXAMPLE_PLACEHOLDERS["SECRET_KEY"]:
        problems.append(
            "SECRET_KEY is the placeholder shipped in .env.example; " + _APP_KEY_GENERATE
        )
    elif len(secret) < 32:
        problems.append("SECRET_KEY must be at least 32 characters")
    if not encryption:
        problems.append("ENCRYPTION_KEY is required to encrypt stored credentials")
    elif encryption == EXAMPLE_PLACEHOLDERS["ENCRYPTION_KEY"]:
        problems.append(
            "ENCRYPTION_KEY is the placeholder shipped in .env.example; " + _FERNET_KEY_GENERATE
        )
    elif not _fernet_accepts(encryption):
        problems.append("ENCRYPTION_KEY is not a Fernet key; " + _FERNET_KEY_GENERATE)
    problems.extend(_encryption_keys_json_problems(keys_json))
    return problems


LAB_PROFILE: dict[str, int] = {
    # One-minute buckets are the smallest interval the production validators
    # permit. Four samples and 1/2 hysteresis make a demonstration complete in
    # minutes while still requiring a baseline and an explicit recovery.
    #
    # This profile may only shorten *timing*: bucket interval, minimum sample
    # count, confirmation count, recovery count, collection schedule. It must
    # never contain a detection threshold. Lowering a threshold here would make
    # the lab demonstrate a detector that production does not run, which is
    # worse than no demonstration at all. The absolute-delta and ratio guards
    # therefore keep their production values in LAB_MODE, and the documented
    # 2 -> 6 EPS acceptance scenario clears them on merit (at 60s buckets that
    # is 120 -> 360 events, a delta of 240 against a floor of 100).
    "collection_interval_seconds": 60,
    "baseline_min_samples": 4,
    "anomaly_open_after_intervals": 1,
    "anomaly_resolve_after_intervals": 2,
    "offense_collection_interval_seconds": 60,
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # Unknown names are ignored, not refused. Startup and doctor report the
        # ones that look like a setting (unknown_setting_names).
        extra="ignore",
        case_sensitive=False,
        # Fields carrying a validation_alias (qradar_host accepts both
        # QRADAR_BASE_URL and QRADAR_HOST) must stay constructible by their
        # own name too, or Settings(qradar_host=...) silently falls back to
        # the default instead of validating what the caller passed.
        populate_by_name=True,
    )

    # --- core ---------------------------------------------------------------
    environment: Environment = Environment.DEVELOPMENT
    debug: bool = False
    # Explicit opt-in for the isolated synthetic-telemetry lab. The profile
    # only accelerates stored-data observation; it never enables QRadar writes.
    lab_mode: bool = False
    log_level: str = "INFO"
    api_prefix: str = "/api/v1"

    secret_key: SecretStr = SecretStr("")
    encryption_key: SecretStr = SecretStr("")

    # --- database -----------------------------------------------------------
    postgres_user: str = "qradar_obs"
    postgres_password: SecretStr = SecretStr("")
    postgres_db: str = "qradar_obs"
    postgres_host: str = "postgres"
    postgres_port: int = 5432

    # --- redis / celery -----------------------------------------------------
    redis_url: str = "redis://redis:6379/0"
    celery_broker_url: str = "redis://redis:6379/1"
    celery_result_backend: str = "redis://redis:6379/2"

    # --- provider selection -------------------------------------------------
    qradar_provider: ProviderKind = ProviderKind.MOCK

    # --- QRadar REST --------------------------------------------------------
    # QRADAR_BASE_URL is the preferred name; QRADAR_HOST is accepted for
    # compatibility with existing deployments.
    qradar_host: str = Field(
        default="", validation_alias=AliasChoices("QRADAR_BASE_URL", "QRADAR_HOST")
    )
    qradar_sec_token: SecretStr = SecretStr("")
    #: Path to a file holding the SEC token. Preferred over QRADAR_SEC_TOKEN:
    #: an env var is readable from /proc, leaks into `docker inspect` and into
    #: any crash reporter that dumps the environment. A file can be mounted
    #: read-only and mode-restricted. When both are set, the file wins.
    qradar_api_token_file: str | None = None
    qradar_api_version: str = "20.0"
    qradar_verify_ssl: bool = True
    qradar_ca_bundle: str | None = None
    #: Tolerate an appliance certificate with no Authority Key Identifier.
    #: Relaxes an RFC 5280 labelling assertion only — chain, expiry and
    #: hostname verification stay on. See app/providers/tls.py.
    qradar_tls_allow_missing_aki: bool = False

    # --- Ariel guardrails ---------------------------------------------------
    ariel_max_concurrent_searches: int = Field(default=3, ge=1, le=20)
    ariel_default_timeout_seconds: int = Field(default=300, ge=10)
    ariel_max_timeout_seconds: int = Field(default=900, ge=10)
    ariel_max_time_range_hours: int = Field(default=168, ge=1)
    ariel_max_result_rows: int = Field(default=10_000, ge=1)
    ariel_poll_interval_seconds: float = Field(default=3.0, ge=0.5)
    ariel_max_retries: int = Field(default=3, ge=0, le=10)
    ariel_retry_base_seconds: float = Field(default=2.0, ge=0.1)
    ariel_retry_max_seconds: float = Field(default=60.0, ge=1.0)
    # Global cap across all instances, plus the per-instance cap above.
    ariel_global_max_concurrent_searches: int = Field(default=6, ge=1, le=50)
    #: PostgreSQL advisory-lock namespaces for the global and per-console Ariel
    #: permit pools. They must be distinct from every other lock namespace.
    ariel_permit_lock_namespace: int = 4713
    ariel_permit_instance_lock_namespace: int = 4714
    ariel_permit_wait_seconds: float = Field(default=30.0, ge=0.0, le=600.0)
    ariel_permit_poll_seconds: float = Field(default=1.0, gt=0.0, le=30.0)
    #: Low-numbered global slots that only live collection may take.
    ariel_permit_reserved_live_slots: int = Field(default=2, ge=0)
    # Pad added to the Ariel STOP bound so late-stored events are not dropped
    # from the last bucket. The WHERE clause is NOT padded; it remains the
    # authoritative half-open bound [start, end). Default 60s, 0 disables.
    ariel_stop_pad_seconds: int = Field(default=60, ge=0, le=300)

    # --- Scheduled-search driver --------------------------------------------
    # Most searches dispatched in one scheduler cycle, so a backlog can never
    # turn a single tick into an unbounded burst of Ariel searches.
    search_max_dispatch_per_cycle: int = Field(default=10, ge=1, le=200)
    # Consecutive failed runs before a scheduled search itself raises an alert.
    # A search that has quietly stopped producing results is exactly the blind
    # spot this platform exists to surface, so it is alertable in its own right.
    search_failure_alert_after: int = Field(default=3, ge=1)
    # Enabled scheduled searches. Creating or enabling one past this is 409.
    # Disabled searches are not counted (design F.8.2).
    scheduled_searches_max_enabled: int = Field(default=25, ge=1, le=10_000)
    # search_result_metric rows older than this, by bucket_start, are deleted
    # by the daily batch task. The optional Timescale chunk policy is the
    # separate retention_search_result_metric_days switch and stays off unless
    # RETENTION_ENABLED is set.
    search_result_metric_retention_days: int = Field(default=90, ge=2)
    search_result_retention_batch_size: int = Field(default=5000, ge=100, le=100_000)
    search_result_retention_max_batches: int = Field(default=20, ge=1, le=1000)
    search_result_retention_interval_seconds: int = Field(default=86400, ge=3600)
    # Delay a cadence slot after its boundary so late QRadar events can arrive.
    search_cadence_settle_seconds: int = Field(default=120, ge=0, le=900)
    search_cadence_max_searches_per_tick: int = Field(default=16, ge=1, le=256)
    #: How long the QRadar custom-property catalog is reused per instance.
    qradar_property_catalog_ttl_seconds: int = Field(default=600, ge=60, le=86400)
    ariel_multi_bucket_max_rows: int = Field(default=5000, ge=1, le=100_000)
    #: IPS collection: sources per keyed search (`logsourceid IN (...)`). A larger
    #: group is chunked; truncation halves it. Design D.5.
    ips_max_sources_per_search: int = Field(default=50, ge=1, le=500)
    #: IPS collection: entities kept per (source, bucket, dimension) after the
    #: fold's cut. The summed `dimension_events` still covers every row.
    ips_max_entities_per_bucket: int = Field(default=200, ge=1, le=5000)
    #: IPS fleet collection is opt-in; individual profiles must also be enabled
    #: and VALID for their current version.
    ips_collection_enabled: bool = False
    #: IPS alerting is opt-in: in shadow mode incidents are recorded and nothing pages.
    ips_alerting_enabled: bool = False
    ips_max_new_entities_per_bucket: int = Field(default=20, ge=0, le=1000)
    ips_max_active_entities_per_source_dimension: int = Field(
        default=2000, ge=1, le=50000
    )
    ips_min_admission_events: int = Field(default=3, ge=1)
    ips_entity_active_days: int = Field(default=7, ge=1, le=90)
    ips_min_coverage: float = Field(default=0.90, ge=0.0, le=1.0)
    ips_retention_enabled: bool = True
    ips_metric_retention_days: int = Field(default=35, ge=2)
    ips_retention_batch_size: int = Field(default=5000, ge=100, le=100000)
    ips_retention_max_batches: int = Field(default=20, ge=1, le=1000)
    ips_retention_interval_seconds: int = Field(default=86400, ge=3600)
    #: Closed IPS incidents (RESOLVED, NORMAL, INSUFFICIENT_DATA). Not the metric
    #: retention above, and not gated by IPS_RETENTION_ENABLED: the daily incident
    #: task runs on its own so a lookback skip cannot freeze incident aging.
    ips_anomaly_retention_days: int = Field(default=365, ge=1, le=3650)
    ips_anomaly_retention_batch_size: int = Field(default=5000, ge=100, le=100_000)
    ips_anomaly_retention_max_batches: int = Field(default=20, ge=1, le=1000)
    ips_anomaly_retention_interval_seconds: int = Field(default=86400, ge=3600)
    #: Days of version-compatible IPS buckets considered by a baseline rebuild.
    ips_baseline_lookback_days: int = Field(default=28, ge=1)
    #: IPS subjects loaded and upserted together during a bounded baseline rebuild.
    ips_baseline_rebuild_batch_size: int = Field(default=50, ge=1, le=5000)
    #: Collected IPS buckets evaluated per source per scheduled run.
    ips_max_evaluation_buckets_per_run: int = Field(default=24, ge=1, le=2016)
    #: Entity spike minimum multiple, absolute rise and bucket population.
    ips_spike_ratio: float = Field(default=2.0, gt=1.0)
    ips_min_absolute_delta_events: float = Field(default=50.0, ge=0.0)
    ips_min_bucket_events: float = Field(default=20.0, ge=0.0)
    #: Port fan-out minimum population, multiple and absolute rise.
    ips_fanout_min_ports: float = Field(default=20.0, ge=1.0)
    ips_fanout_ratio: float = Field(default=2.0, gt=1.0)
    ips_fanout_min_delta_ports: float = Field(default=15.0, ge=0.0)
    #: Block-ratio materiality and change floors.
    ips_ratio_min_denominator: int = Field(default=50, ge=1)
    ips_ratio_min_numerator: int = Field(default=10, ge=1)
    ips_ratio_min_increase: float = Field(default=0.10, gt=0.0, le=1.0)
    ips_ratio_min_multiple: float = Field(default=2.0, gt=1.0)
    #: Source-concentration population and share-rise floors.
    ips_concentration_min_source_events: int = Field(default=50, ge=1)
    ips_concentration_min_top1_events: int = Field(default=20, ge=1)
    ips_concentration_min_increase: float = Field(default=0.20, gt=0.0, le=1.0)
    #: New-port history, novelty window and materiality thresholds.
    ips_new_port_history_days: int = Field(default=7, ge=1, le=60)
    ips_new_port_window_buckets: int = Field(default=12, ge=1, le=2016)
    ips_new_port_min_events: int = Field(default=20, ge=1)
    ips_new_port_min_buckets: int = Field(default=2, ge=1)

    # --- Retention (TimescaleDB) --------------------------------------------
    # NEVER hardcode destructive retention. All default to None = retention
    # DISABLED (data kept indefinitely). Set explicitly to opt into dropping
    # old chunks. Applied by app.services.timescale, never by a schema migration.
    retention_enabled: bool = False
    retention_log_source_metric_days: int | None = None
    retention_search_result_metric_days: int | None = None
    retention_rule_metric_days: int | None = None
    retention_offense_snapshot_days: int | None = None
    compression_after_days: int | None = None
    #: How often the beat task reconciles the policies above (it also runs once at beat start).
    timescale_policy_interval_seconds: int = Field(default=86400, ge=3600)

    # --- Encryption / key rotation ------------------------------------------
    # Active key used for new encryptions. Legacy single-key deployments set
    # ENCRYPTION_KEY only; rotation adds ENCRYPTION_KEYS (JSON {version: key})
    # and bumps ENCRYPTION_KEY_VERSION. Decryption tries all known keys.
    encryption_key_version: int = 1
    # JSON object mapping version(int as string) -> fernet key. Optional.
    encryption_keys_json: str = ""

    # --- Metric collection --------------------------------------------------
    collection_interval_seconds: int = Field(default=300, ge=60)
    collection_batch_size: int = Field(default=50, ge=1)
    collection_max_backfill_intervals: int = Field(default=12, ge=0)
    collection_advisory_lock_namespace: int = 4711

    # --- Log source inventory (Phase 3) -------------------------------------
    # Full inventory pass; log sources are added and renamed rarely, so this is
    # deliberately far less frequent than metric collection.
    log_source_sync_interval_seconds: int = Field(default=3600, ge=60)
    # New, non-internal inventory rows open a deduplicated review alert.
    inventory_new_source_alerts_enabled: bool = True

    # --- Offense collection (Phase 3) ---------------------------------------
    offense_collection_interval_seconds: int = Field(default=300, ge=60)
    # Pages of offenses walked per run. Bounds a single collection cycle so a
    # large offense backlog cannot turn one tick into an unbounded crawl.
    offense_max_pages: int = Field(default=20, ge=1, le=500)
    offense_page_size: int = Field(default=100, ge=1, le=1000)
    # Hard ceiling on how far back a first run or a gap-fill reaches. Never
    # unlimited: a fresh install must not try to ingest years of offenses.
    offense_max_backfill_hours: int = Field(default=168, ge=1, le=8760)
    # An open offense older than this is "exceeding SLA" on the dashboard.
    offense_sla_hours: int = Field(default=24, ge=1)
    # Magnitude at or above which an offense counts as critical.
    offense_critical_magnitude: int = Field(default=7, ge=1, le=10)
    # A collection older than this is surfaced as a deduplicated operational
    # alert. Keep the check cadence separate from the threshold.
    offense_stale_after_seconds: int = Field(default=900, ge=60)
    offense_stale_check_interval_seconds: int = Field(default=300, ge=60)
    offense_aggregate_interval_seconds: int = Field(default=300, ge=60)

    # --- Rule inventory + health (Phase 3) ----------------------------------
    rule_collection_interval_seconds: int = Field(default=3600, ge=60)
    rule_max_pages: int = Field(default=20, ge=1, le=500)
    # Offense contribution is the only defensible rule-firing evidence exposed
    # by the lab appliance. Collection is bounded by both age and row count.
    rule_metric_collection_interval_seconds: int = Field(default=3600, ge=60)
    rule_metric_lookback_days: int = Field(default=30, ge=1, le=365)
    rule_metric_max_offenses: int = Field(default=10_000, ge=1, le=1_000_000)
    # Measured rule matches (Phase E.1a): one Ariel `creEventList` search per
    # window. Off by default -- the cost on a busy console is unmeasured.
    rule_match_collection_enabled: bool = False
    #: Deprecated by `search_cadence`; the beat no longer reads this setting.
    rule_match_collection_interval_seconds: int = Field(default=900, ge=60)
    rule_match_window_seconds: int = Field(default=3600, ge=60, le=86_400)
    # A window is collected only once it ended this long ago, so late-arriving
    # events do not land in a window already recorded as COMPLETE.
    rule_match_settle_lag_seconds: int = Field(default=300, ge=0, le=86_400)
    # Windows per run when the cadence is no longer than the window; beyond that,
    # the extra windows of one cadence are added: ceil(cadence / window) + this - 1.
    rule_match_max_windows_per_run: int = Field(default=6, ge=1, le=168)
    # A window that failed this many times stays recorded as FAILED and the
    # watermark moves past it, so one unanswerable window cannot stall collection.
    rule_match_max_window_attempts: int = Field(default=3, ge=1, le=20)
    # A watermark further behind than this (after downtime) jumps to the horizon;
    # every skipped window is recorded as FAILED, never as an empty COMPLETE one.
    rule_match_max_catchup_seconds: int = Field(default=86_400, ge=60)
    # A full page makes the window TRUNCATED. Capped at 5,000 so one bulk upsert
    # (5 bound values a row) stays under the 32,767-parameter statement limit.
    rule_match_max_rows: int = Field(default=2000, ge=10, le=5000)
    # Bounded repair of recent FAILED windows (RM-2): after the live pass, the
    # collector re-reads windows that failed, oldest first. Inert while
    # RULE_MATCH_COLLECTION_ENABLED is false.
    rule_match_repair_enabled: bool = True
    # A window older than this is never repaired: re-reading data QRadar has
    # already rotated out would return nothing and be written COMPLETE, a false
    # proof of silence. Must stay inside the console's event retention.
    rule_match_repair_max_age_seconds: int = Field(default=86_400, ge=60, le=604_800)
    # FAILED windows re-read per run, oldest first, within the live per-run cap.
    rule_match_repair_max_windows_per_run: int = Field(default=6, ge=1, le=168)
    # Repair attempts per window, on top of the live attempts already spent,
    # before the window is left FAILED for good.
    rule_match_repair_max_attempts: int = Field(default=3, ge=1, le=20)
    # Evaluation window for "has this rule fired recently".
    rule_health_window_days: int = Field(default=30, ge=1, le=365)
    # A rule newer than this is INSUFFICIENT_DATA, never INACTIVE: a detection
    # deployed yesterday has not had a chance to fire yet.
    rule_health_grace_period_days: int = Field(default=7, ge=0, le=365)
    # Minimum observed history before an inactivity verdict is trustworthy.
    rule_health_min_history_days: int = Field(default=3, ge=0, le=365)
    # Silence beyond this many days, for a rule that has fired before.
    rule_health_inactivity_days: int = Field(default=14, ge=1, le=365)
    # Firings per day above which a rule is NOISY, unless the rule carries its
    # own expected_daily_firings.
    rule_health_noisy_daily_firings: float = Field(default=500.0, ge=1.0)
    # Consecutive evaluations agreeing before the stored verdict flips. Rule
    # health drives alerting, and a rule that fires irregularly would otherwise
    # oscillate between HEALTHY and INACTIVE every evaluation.
    rule_health_flap_threshold: int = Field(default=2, ge=1, le=10)
    # Bumped whenever classification logic changes meaning, so historical
    # snapshots stay interpretable. Not operator-tunable in practice.
    # 2: EVENT rules are judged on measured rule matches when they exist (E.1a).
    # 3: an unobserved dependency is INSUFFICIENT_DATA, not healthy (FIX-1).
    rule_health_logic_version: int = 3
    # Health classification reads only stored data, so it can run more often
    # than the inventory sync that feeds it.
    rule_health_evaluation_interval_seconds: int = Field(default=1800, ge=60)
    #: rule_health_snapshot rows older than this, by evaluated_at. Not gated by
    #: RETENTION_ENABLED. Shared batch knobs with the coverage snapshot below.
    rule_health_snapshot_retention_days: int = Field(default=90, ge=1, le=3650)

    # --- Detection coverage (Phase 3) ---------------------------------------
    coverage_evaluation_interval_seconds: int = Field(default=3600, ge=60)
    # Inferred mappings below this confidence are recorded but never counted
    # toward coverage.
    coverage_min_confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    coverage_logic_version: int = 1
    #: detection_coverage_snapshot rows older than this, by captured_at.
    detection_coverage_snapshot_retention_days: int = Field(default=90, ge=1, le=3650)
    snapshot_retention_batch_size: int = Field(default=5000, ge=100, le=100_000)
    snapshot_retention_max_batches: int = Field(default=20, ge=1, le=1000)
    snapshot_retention_interval_seconds: int = Field(default=86400, ge=3600)
    # Measured rule-match rows (Phase E.1a) and their windows. Must exceed the
    # longest reader window (rule_health_window_days = 30, statistics range = 30).
    rule_match_retention_days: int = Field(default=90, ge=30, le=3650)
    rule_match_retention_batch_size: int = Field(default=5000, ge=100, le=100_000)
    rule_match_retention_max_batches: int = Field(default=20, ge=1, le=1000)
    rule_match_retention_interval_seconds: int = Field(default=86400, ge=3600)

    # --- API guardrails -----------------------------------------------------
    api_default_page_size: int = Field(default=50, ge=1, le=500)
    api_max_page_size: int = Field(default=200, ge=1, le=1000)
    # Widest date range any list/history endpoint will honour.
    api_max_range_days: int = Field(default=365, ge=1)

    # --- Baselines ----------------------------------------------------------
    baseline_min_samples: int = Field(default=8, ge=1)
    baseline_lookback_days: int = Field(default=28, ge=1)
    baseline_mad_floor_ratio: float = Field(default=0.1, ge=0.0)

    # --- Anomaly hysteresis (defaults; overridable per type/criticality) ----
    anomaly_open_after_intervals: int = Field(default=2, ge=1)
    anomaly_resolve_after_intervals: int = Field(default=3, ge=1)
    anomaly_deviation_threshold: float = Field(default=3.5, ge=0.0)

    # --- Phase A volume guards ----------------------------------------------
    # Conjunctive with the robust z-score above, never a replacement for it:
    # every guard added here can only make the detector more conservative.
    #
    # The absolute-delta and minimum-volume guards exist to kill the classic
    # false positive where 0.2 EPS becomes 0.4 EPS — a clean 2x ratio and a
    # large robust z-score on a change of no operational significance.
    anomaly_spike_ratio: float = Field(default=2.0, gt=1.0)
    anomaly_drop_ratio: float = Field(default=0.5, gt=0.0, lt=1.0)
    #: Minimum |observed - expected|, in events per bucket, before a volume
    #: verdict is defensible.
    anomaly_min_absolute_delta_events: float = Field(default=100.0, ge=0.0)
    #: A source must move at least this many events in the bucket for a spike,
    #: or have expected at least this many for a drop, to be judged at all.
    anomaly_min_bucket_events: float = Field(default=50.0, ge=0.0)
    #: Bumped whenever detection semantics change. Recorded on every anomaly so
    #: a historical verdict can be read against the rules that produced it.
    anomaly_policy_version: int = Field(default=1, ge=1)
    #: Buckets of expected-but-absent traffic tolerated before NO_EVENTS fires.
    anomaly_silence_grace_buckets: int = Field(default=1, ge=0)
    anomaly_max_buckets_per_evaluation: int = Field(default=120, ge=1, le=10_000)
    #: Failed evaluation ticks tolerated on one bucket before the engine gives
    #: up on it and moves to the next. Expressed in evaluation ticks, not
    #: seconds; at the 60 s tick and 300 s buckets, 3 is under one bucket.
    anomaly_max_bucket_attempts: int = Field(default=3, ge=1, le=20)
    #: Closed fault rows (resolved or given up) older than this are deleted. An
    #: open fault is the source's current state and is never pruned.
    anomaly_fault_retention_days: int = Field(default=30, ge=1, le=3650)
    #: Closed Phase A incidents (RESOLVED, NORMAL, INSUFFICIENT_DATA). Live
    #: states are kept. Not gated by RETENTION_ENABLED.
    log_source_anomaly_retention_days: int = Field(default=365, ge=1, le=3650)
    log_source_anomaly_retention_batch_size: int = Field(default=5000, ge=100, le=100_000)
    log_source_anomaly_retention_max_batches: int = Field(default=20, ge=1, le=1000)
    log_source_anomaly_retention_interval_seconds: int = Field(default=86400, ge=3600)

    # --- Phase C.2: outage alert policy -------------------------------------
    #: What a source's NO_EVENTS does when no `outage_alert_policy` row applies.
    #: ALERT keeps Phase A's behaviour, so an upgrade alerts exactly as before
    #: and operators opt sources or classes into RECORD_ONLY (decision D-9).
    outage_alert_default: OutagePolicyMode = OutagePolicyMode.ALERT

    # --- Phase A explanation evidence ---------------------------------------
    explanation_enabled: bool = True
    #: Values retained per dimension. Bounds both the Ariel result and the
    #: contributor rows written per package.
    explanation_top_values: int = Field(default=20, ge=1, le=200)
    #: Length of the recent-normal comparison window, as a multiple of the
    #: anomaly window. Wider than 1x steadies a short anomaly's comparison.
    explanation_baseline_window_multiple: float = Field(default=3.0, ge=1.0, le=24.0)
    #: Hard ceiling on either explanation window. Ariel cost scales with the
    #: window, and an unbounded investigation query can pin an appliance.
    explanation_max_window_seconds: int = Field(default=6 * 3600, ge=60, le=168 * 3600)
    #: Anomalies explained per task run. Bounds Ariel load: each anomaly costs
    #: two queries per dimension, so a backlog is worked through over several
    #: runs rather than in one burst against the appliance.
    explanation_max_per_run: int = Field(default=5, ge=1, le=100)
    #: Deprecated by `search_cadence`; the beat no longer reads this setting.
    explanation_collection_interval_seconds: int = Field(default=300, ge=30)
    #: Per-dimension Ariel timeout for an explanation job.
    explanation_query_timeout_seconds: int = Field(default=120, ge=10, le=900)
    #: Dimensions to compare, in priority order. Trimmed per source to those
    #: the DSM actually populates; the rest are recorded UNAVAILABLE.
    explanation_dimensions: list[str] = Field(
        default_factory=lambda: [
            "qid",
            "event_name",
            "source_ip",
            "destination_ip",
            "destination_port",
            "source_port",
            "username",
            "action",
            "category",
            "protocol",
        ]
    )

    # --- Phase B: WAF endpoint behavior -------------------------------------
    #: WAF collection is opt-in. Phase B is a development capability until a
    #: real WAF source proves the production field equivalents, so it stays off
    #: unless an operator turns it on for a source they have verified.
    waf_collection_enabled: bool = False
    #: WAF alerting is separately opt-in, so a source can collect and baseline
    #: silently in shadow mode before its endpoint incidents can page anyone.
    waf_alerting_enabled: bool = False
    #: QRadar custom-property display names. Site-authored and NOT derivable
    #: from the LEEF keys a device emits -- assuming they matched cost an entire
    #: round of zero-population measurements. Validated against a strict
    #: allowlist before reaching any query text.
    waf_url_property: str = "URL"
    waf_method_property: str = "HTTP_Method"
    #: No verified WAF DSM exposes a virtual host yet. Empty means "not
    #: available", which the collector represents as NULL -- never "".
    waf_virtual_host_property: str = ""
    #: Groups fetched per source per bucket: the bound on the grouped Ariel
    #: result, which is taken over raw URLs before normalization. Hitting it
    #: makes every endpoint count in the bucket a lower bound (PARTIAL): enough
    #: to prove a spike, never enough to call an endpoint normal. It does not
    #: blind the bucket. See docs/PHASE-B-WAF-BEHAVIOR.md section 29.
    waf_max_groups_per_bucket: int = Field(default=500, ge=1, le=5000)
    #: Below this share of a source's events carrying a parseable endpoint, the
    #: bucket is PARTIAL. A bucket where a third of the traffic had no path is
    #: not a whole observation of any endpoint in it.
    waf_min_endpoint_coverage: float = Field(default=0.90, ge=0.0, le=1.0)
    #: Days without an observation before an endpoint is retired. A retired
    #: endpoint is no longer evaluated and its absence is no longer read as zero.
    waf_endpoint_active_days: int = Field(default=7, ge=1, le=90)
    #: Minimum requests per bucket for a defensible volume verdict. Mirrors the
    #: Phase A guard that kills the 0.2-to-0.4-EPS false positive, at endpoint
    #: scale rather than log-source scale.
    waf_min_bucket_requests: float = Field(default=20.0, ge=0.0)
    #: Minimum absolute change, in requests per bucket.
    waf_min_absolute_delta_requests: float = Field(default=50.0, ge=0.0)
    #: observed/expected must reach this to be a spike candidate.
    waf_spike_ratio: float = Field(default=2.0, gt=1.0)

    # --- Phase B.1: admission, validation, scheduling, retention ------------
    # The per-source property mapping lives on `waf_source_profile`. The three
    # `waf_*_property` settings above are not read by scheduled collection.
    #: New endpoints admitted per source per bucket. Existing tracked endpoints
    #: are never subject to this quota.
    waf_max_new_endpoints_per_bucket: int = Field(default=20, ge=0, le=1000)
    #: Active tracked endpoints per source. At the ceiling nothing new is
    #: admitted until retirement frees room.
    waf_max_active_endpoints_per_source: int = Field(default=2000, ge=1, le=50000)
    #: Requests an unseen endpoint needs in one bucket to be admitted at all.
    waf_min_admission_requests: int = Field(default=3, ge=1)
    #: Profile validation probes this much recent history (seconds).
    waf_validation_window_seconds: int = Field(default=900, ge=60, le=3600)
    #: A pending marker older than this may be replaced after a dead worker.
    waf_validation_pending_timeout_seconds: int = Field(default=900, ge=60)
    #: Delay before validation retries a source held by the collection pipeline.
    waf_validation_lock_retry_delay_seconds: int = Field(default=30, ge=5, le=300)
    #: Lock retries before validation records a fail-closed source-busy outcome.
    waf_validation_lock_max_retries: int = Field(default=20, ge=0, le=100)
    #: Minimum share for a required field's population and value plausibility.
    waf_validation_min_ratio: float = Field(default=0.90, gt=0.0, le=1.0)
    #: Most frequent values fetched per property during validation.
    waf_validation_max_values: int = Field(default=50, ge=1, le=200)
    # --- Phase D: IPS profile validation (collection is introduced later) --
    ips_validation_window_seconds: int = Field(default=900, ge=60, le=3600)
    ips_validation_pending_timeout_seconds: int = Field(default=900, ge=60)
    ips_validation_lock_retry_delay_seconds: int = Field(default=30, ge=5, le=300)
    ips_validation_lock_max_retries: int = Field(default=20, ge=0, le=100)
    ips_validation_min_ratio: float = Field(default=0.90, gt=0.0, le=1.0)
    #: Hard disclosure and Ariel-result bound for discovered ACTION values.
    ips_validation_max_action_values: int = Field(default=20, ge=1, le=20)
    #: Collected buckets evaluated per source per scheduled run.
    waf_max_evaluation_buckets_per_run: int = Field(default=24, ge=1, le=2016)
    #: Endpoints whose baselines are rebuilt per source per scheduled run,
    #: least recently rebuilt first.
    waf_baseline_endpoints_per_run: int = Field(default=50, ge=1, le=5000)
    #: WAF retention runs with WAF collection. Unlike the generic Timescale
    #: policies it is on by default: WAF collection is itself opt-in, and its
    #: row growth is influenced by whoever sends requests.
    waf_retention_enabled: bool = True
    #: Endpoint metrics and source buckets. Must exceed BASELINE_LOOKBACK_DAYS.
    waf_metric_retention_days: int = Field(default=35, ge=2)
    #: Terminal (RESOLVED, closed-candidate) WAF anomalies and their transitions.
    waf_anomaly_retention_days: int = Field(default=180, ge=1)
    waf_retention_batch_size: int = Field(default=5000, ge=100, le=100000)
    waf_retention_max_batches: int = Field(default=20, ge=1, le=1000)
    waf_retention_interval_seconds: int = Field(default=86400, ge=3600)

    # --- Phase C.2b: catch-all senders (SIM Generic and the Universal DSMs) ---
    #: Kill switch. The collector sends one keyed search per cadence tick (about
    #: 4 s on the lab), and the SEARCH_CADENCE gate decides when a tick is due.
    catchall_collection_enabled: bool = True
    #: Device types whose events are searched for senders. Sites have their own
    #: catch-alls. Ints only; each is validated again before it enters a query.
    catchall_type_ids: list[int] = Field(
        default_factory=lambda: [67, 39, 212, 367, 4000], max_length=32
    )
    #: Rows kept per keyed search before truncation splits it.
    catchall_max_rows_per_search: int = Field(default=2000, ge=10, le=100_000)
    #: New senders admitted per catch-all source per bucket. Sender names are
    #: chosen by whoever sends, so admission bounds what they can create.
    catchall_max_new_senders_per_bucket: int = Field(default=20, ge=0, le=1000)
    #: Active senders kept per catch-all source.
    catchall_max_active_senders_per_source: int = Field(default=2000, ge=1, le=50_000)
    #: Events in a bucket below which an unseen sender is not admitted.
    catchall_min_admission_events: int = Field(default=3, ge=1)
    #: A sender not seen for this long is retired (a later sighting reactivates it).
    catchall_sender_active_days: int = Field(default=7, ge=1, le=90)
    #: A newly admitted sender raises a LOW inventory alert. Off by default (D-9):
    #: the review queue is the default surface.
    catchall_new_sender_alerts_enabled: bool = False
    #: Senders kept in a bucket's `top_senders` list.
    catchall_top_senders: int = Field(default=20, ge=1, le=50)
    #: Buckets, and senders retired for longer than this, are deleted.
    catchall_retention_days: int = Field(default=35, ge=2)
    catchall_retention_batch_size: int = Field(default=5000, ge=100, le=100_000)
    catchall_retention_max_batches: int = Field(default=20, ge=1, le=1000)
    catchall_retention_interval_seconds: int = Field(default=86400, ge=3600)

    # --- D-10: geography (events per country, for the home map) --------------
    #: Kill switch. Two keyed searches per cadence tick (one per direction).
    #: GEO_COLLECTION defaults to 5 minutes, so that is 24 searches an hour per instance.
    geo_collection_enabled: bool = True
    #: Which address is looked up, each a separate stored series. SOURCE, DESTINATION.
    geo_directions: list[str] = Field(
        default_factory=lambda: ["SOURCE", "DESTINATION"], min_length=1, max_length=2
    )
    #: Rows kept per keyed search before truncation splits it.
    geo_max_rows_per_search: int = Field(default=5000, ge=10, le=100_000)
    #: Countries kept per bucket and direction; the rest is summed into OVER_CAP.
    geo_max_countries_per_bucket: int = Field(default=64, ge=1, le=300)
    #: Buckets older than this are deleted.
    geo_retention_days: int = Field(default=35, ge=2)
    geo_retention_batch_size: int = Field(default=5000, ge=100, le=100_000)
    geo_retention_max_batches: int = Field(default=20, ge=1, le=1000)
    geo_retention_interval_seconds: int = Field(default=86400, ge=3600)

    # --- Phase F: custom threshold rules (design §F.4, §F.10) ----------------
    #: Kill switch. The evaluator is DB-only and sends no Ariel search. Nothing
    #: runs until this is set, so an upgrade changes no behaviour.
    custom_rules_enabled: bool = False
    #: Shadow mode (D-17). Breaches and rule state are written either way.
    #: Nothing opens, refreshes or resolves an alert, and nothing is enqueued,
    #: until this is set. It sits beside ``custom_rules_enabled``.
    custom_rules_alerting_enabled: bool = False
    #: Enabled rules. Creating or enabling beyond this is refused (409 at the API).
    custom_rules_max_enabled: int = Field(default=100, ge=1, le=10_000)
    #: Closed buckets judged per subject kind per run. 288 is a day of 5-minute buckets.
    custom_rules_max_buckets_per_run: int = Field(default=288, ge=1, le=10_080)
    #: Resolved breaches older than this are deleted. Live breaches are kept.
    custom_rule_breach_retention_days: int = Field(default=180, ge=1)
    custom_rules_retention_batch_size: int = Field(default=5000, ge=100, le=100_000)
    custom_rules_retention_max_batches: int = Field(default=20, ge=1, le=1000)
    custom_rules_retention_interval_seconds: int = Field(default=86400, ge=3600)

    # --- Phase B.1.5: Ariel budget and batching ------------------------------
    #: WAF sources that may have an Ariel search in flight at once, across every
    #: worker. Each source collects one bucket per permit; a source that cannot
    #: get one within `WAF_ARIEL_PERMIT_WAIT_SECONDS` stops, keeps its watermark
    #: and retries on the next dispatch. Deliberately below
    #: ARIEL_GLOBAL_MAX_CONCURRENT_SEARCHES so scheduled WAF work cannot consume
    #: the appliance's whole search budget.
    waf_ariel_max_concurrent_sources: int = Field(default=2, ge=1, le=20)
    waf_ariel_permit_wait_seconds: float = Field(default=30.0, ge=0.0, le=600.0)
    waf_ariel_permit_poll_seconds: float = Field(default=1.0, gt=0.0, le=30.0)
    #: PostgreSQL advisory-lock namespace (first key) for WAF Ariel permits. Must
    #: differ from COLLECTION_ADVISORY_LOCK_NAMESPACE so a permit slot can never
    #: collide with a collector lock.
    waf_ariel_permit_lock_namespace: int = 4712
    #: Endpoints whose history is loaded into memory together during a baseline
    #: rebuild. Bounds memory, not statements per endpoint.
    waf_baseline_rebuild_chunk_size: int = Field(default=25, ge=1, le=500)

    # --- Phase B.2: behavioural breakdowns and detectors ---------------------
    # Each breakdown is ONE more bounded Ariel search per source per bucket,
    # under the same permit as the two volume searches. All are off by default:
    # turning one on is an explicit decision to spend Ariel budget on it.
    #: Collect the action breakdown (blocked / unmapped requests per endpoint)
    #: and the response-code breakdown (4xx / 5xx per endpoint), each only for a
    #: profile whose field validated -- and, for action, whose map is authored.
    waf_outcome_collection_enabled: bool = False
    #: Collect the source-IP breakdown (per-endpoint traffic composition).
    waf_client_collection_enabled: bool = False
    #: Group cap for each breakdown search. A breakdown grouped by (URL, method,
    #: host, field) has more groups than the volume search, so it has its own.
    #: Hitting it makes that breakdown a lower bound for the bucket.
    waf_max_breakdown_groups_per_bucket: int = Field(default=2000, ge=1, le=10000)

    #: Ratio detectors (block, 5xx, 4xx). A verdict needs at least this many
    #: requests carrying the field -- the denominator.
    waf_ratio_min_denominator: int = Field(default=50, ge=1)
    #: ... and at least this many in the numerator. Below it, with an exact
    #: count, no ratio anomaly is possible and the verdict is normal.
    waf_ratio_min_numerator: int = Field(default=10, ge=1)
    #: Minimum rise over the expected proportion, in proportion units (0.10 =
    #: ten percentage points).
    waf_ratio_min_increase: float = Field(default=0.10, gt=0.0, le=1.0)
    #: Observed proportion / expected proportion must reach this. An expected
    #: proportion of zero has no ratio; the percentage-point guard carries it.
    waf_ratio_min_multiple: float = Field(default=2.0, gt=1.0)

    #: Client concentration: endpoint requests needed for a composition verdict.
    waf_concentration_min_endpoint_requests: int = Field(default=50, ge=1)
    #: Requests the largest source IP needs before its share can be material.
    #: Also the floor at which a bucket's top contributors are stored.
    waf_concentration_min_client_requests: int = Field(default=20, ge=1)
    #: Minimum rise in the largest source IP's share, in proportion units.
    waf_concentration_min_increase: float = Field(default=0.20, gt=0.0, le=1.0)

    #: New-endpoint access: the history that must be complete before a first
    #: material appearance may be called new. Must stay below metric retention.
    waf_new_endpoint_history_days: int = Field(default=7, ge=1, le=60)
    #: Buckets after first appearance during which an endpoint is "new".
    waf_new_endpoint_window_buckets: int = Field(default=12, ge=1, le=2016)
    #: Materiality: requests across the window so far, and distinct buckets with
    #: traffic. One-off URIs never reach it.
    waf_new_endpoint_min_requests: int = Field(default=20, ge=1)
    waf_new_endpoint_min_buckets: int = Field(default=2, ge=1)

    # --- Phase G property watches (design §G.14.3) ---------------------------
    #: Family kill switch: the PROPERTY_WATCH dispatch is a no-op while false.
    pw_collection_enabled: bool = False
    #: Shadow mode: incidents are recorded and nothing opens an alert while false.
    pw_alerting_enabled: bool = False
    #: Enabled watches; enabling beyond it is refused.
    pw_max_enabled_watches: int = Field(default=10, ge=1, le=100)
    #: Searches one instance may send per tick, and one watch within it.
    pw_max_searches_per_tick: int = Field(default=40, ge=1, le=256)
    pw_max_searches_per_watch_per_tick: int = Field(default=12, ge=1, le=256)
    #: Sources per search (`logsourceid IN (...)`); a larger group is chunked.
    pw_max_sources_per_search: int = Field(default=50, ge=1, le=500)
    #: Chunks per watch; a scope needing more is SCOPE_TOO_LARGE.
    pw_max_chunks_per_watch: int = Field(default=4, ge=1, le=20)
    #: Rows per search; one more than this is read so a cut is visible (L8).
    pw_max_rows_per_search: int = Field(default=5000, ge=1, le=100_000)
    #: Untracked values the fold keeps per bucket after its top-N cut.
    pw_max_values_per_bucket: int = Field(default=50, ge=1, le=5000)
    pw_max_new_values_per_bucket: int = Field(default=20, ge=0, le=1000)
    pw_max_active_values_per_watch: int = Field(default=200, ge=1, le=5000)
    #: Pinned values per watch. The pinned search reads at most this many squared
    #: tuples a bucket, so the bound keeps it inside `pw_max_rows_per_search`.
    pw_max_pinned_values: int = Field(default=10, ge=1, le=20)
    #: The stored width of a value text; longer text is cut by the fold.
    pw_value_max_length: int = Field(default=255, ge=1, le=255)
    pw_min_admission_events: int = Field(default=3, ge=1)
    pw_value_active_days: int = Field(default=7, ge=1, le=90)
    pw_min_coverage: float = Field(default=0.90, ge=0.0, le=1.0)
    pw_scope_stale_after_hours: int = Field(default=24, ge=1, le=720)
    pw_scope_change_grace_buckets: int = Field(default=12, ge=0, le=2016)
    pw_baseline_lookback_days: int = Field(default=28, ge=1)
    pw_baseline_min_visits: int = Field(default=2, ge=1, le=52)
    pw_baseline_values_per_run: int = Field(default=100, ge=1, le=5000)
    pw_max_evaluation_buckets_per_run: int = Field(default=24, ge=1, le=2016)
    #: Spike: minimum multiple of the median, absolute rise and bucket population.
    pw_spike_ratio: float = Field(default=2.0, gt=1.0)
    pw_min_absolute_delta_events: float = Field(default=50.0, ge=0.0)
    pw_min_bucket_events: float = Field(default=20.0, ge=0.0)
    #: Drop: the median a value needs to be judged, and the fraction of it that is a drop.
    pw_drop_min_median: float = Field(default=20.0, ge=0.0)
    pw_drop_ratio: float = Field(default=0.5, gt=0.0, lt=1.0)
    #: Disappearance: the median a value needs and the measured-zero buckets in a row.
    pw_disappear_min_median: float = Field(default=5.0, ge=0.0)
    pw_disappear_buckets: int = Field(default=6, ge=1, le=2016)
    #: New value: the history that must be complete, the window it stays new for, and
    #: the materiality floors. The history must stay below metric retention.
    pw_new_value_history_days: int = Field(default=7, ge=1, le=60)
    pw_new_value_window_buckets: int = Field(default=12, ge=1, le=2016)
    pw_new_value_min_events: int = Field(default=20, ge=1)
    pw_new_value_min_buckets: int = Field(default=2, ge=1)
    #: One-time, learning-only history bootstrap (design §H.10; D-29 defaults it on).
    pw_bootstrap_enabled: bool = True
    pw_bootstrap_days: int = Field(default=28, ge=1)
    pw_bootstrap_searches_per_tick: int = Field(default=12, ge=1, le=256)
    pw_bootstrap_seconds_per_tick: int = Field(default=180, ge=10, le=3600)
    pw_bootstrap_floor_margin_hours: int = Field(default=24, ge=1, le=168)
    pw_bootstrap_max_hours: int = Field(default=72, ge=1, le=168)
    pw_bootstrap_max_failures: int = Field(default=3, ge=1, le=20)
    pw_bootstrap_max_lower_bound_share: float = Field(default=0.05, ge=0.0, le=1.0)
    pw_metric_retention_days: int = Field(default=35, ge=2)
    pw_anomaly_retention_days: int = Field(default=180, ge=2)
    pw_retention_batch_size: int = Field(default=5000, ge=100, le=100_000)
    pw_retention_max_batches: int = Field(default=20, ge=1, le=1000)
    pw_retention_interval_seconds: int = Field(default=86400, ge=3600)

    # --- History backfill (HISTORY-BACKFILL §B.6.1) -------------------------
    #: Kill switch for *reading*: off, no history step runs. Recording the operator's
    #: answer does not depend on it.
    history_backfill_enabled: bool = True
    #: The window a Yes reads. The upper bound is static: 90 days is far past every
    #: family's default window (28; rule health 30) and keeps a reading's search count
    #: bounded; the per-family lookback and retention limits are applied at run time by
    #: `app.history.families.fit_family_window`, never as a startup refusal, so a released
    #: configuration keeps starting.
    history_backfill_days: int = Field(default=28, ge=7, le=90)
    #: Searches one history step may start; the sub-budget under the family's tick cap.
    history_backfill_searches_per_tick: int = Field(default=10, ge=1, le=64)
    #: No history search starts after this many seconds of a tick.
    history_backfill_seconds_per_tick: int = Field(default=120, ge=10, le=600)
    #: Longest wall time a reading may take; also the reading margin below retention.
    history_backfill_max_hours: int = Field(default=72, ge=1, le=168)
    history_backfill_floor_margin_hours: int = Field(default=24, ge=1, le=168)
    #: Consecutive failures at the minimum width before the span becomes a gap.
    history_backfill_max_failures: int = Field(default=3, ge=1, le=20)
    #: The density gate (§B.6.4): the share of lower-bound buckets that ends a reading.
    history_backfill_max_lower_bound_share: float = Field(default=0.05, ge=0.0, le=1.0)
    #: Days of WAF breakdown history (§B.7.3).
    history_backfill_waf_breakdown_days: int = Field(default=7, ge=1, le=90)
    #: Phase A sources rebuilt per tick after a reading (§B.8.5).
    history_backfill_baseline_sources_per_tick: int = Field(default=50, ge=1, le=5000)

    # --- Notifications ------------------------------------------------------
    notify_max_retries: int = Field(default=5, ge=0, le=20)
    notify_retry_base_seconds: float = Field(default=2.0, ge=0.1)
    notify_retry_max_seconds: float = Field(default=300.0, ge=1.0)
    notify_send_recovery: bool = True
    notify_per_channel_rate: str = "30/minute"

    # --- MCP ----------------------------------------------------------------
    mcp_enabled: bool = False
    mcp_base_url: str = "http://qradar-mcp:5000"
    mcp_timeout_seconds: int = 60

    # --- auth ---------------------------------------------------------------
    # "local" is the development convenience (a local-dev principal, no cookie).
    # "session" reads the server-side session cookie. Every environment except
    # development reads the cookie, whichever of those two is set. "oidc" is
    # refused at startup outside development; in development its behaviour is
    # unchanged.
    auth_provider: Literal["local", "oidc", "session"] = "local"
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: SecretStr = SecretStr("")
    oidc_redirect_uri: str = ""
    # Idle is measured from last_seen_at. Absolute is measured from created_at
    # and is also stored on the row as expires_at.
    session_idle_minutes: int = Field(default=480, ge=1, le=10080)
    session_max_hours: int = Field(default=12, ge=1, le=720)
    # Consecutive failures of one username. The per-IP limit on the login route
    # is separate and does not reset this counter.
    login_lockout_threshold: int = Field(default=5, ge=1, le=50)
    login_lockout_minutes: int = Field(default=15, ge=1, le=1440)
    # Browser origin of the UI. CORS and the CSRF check both use it.
    public_origin: str = "http://localhost:3000"
    # How many expired session rows one login may delete.
    session_cleanup_batch: int = Field(default=500, ge=1, le=5000)

    # --- rate limiting ------------------------------------------------------
    rate_limit_default: str = "120/minute"
    rate_limit_search_execution: str = "10/minute"
    rate_limit_login: str = "300/minute"

    # --- LLM (interface only) ----------------------------------------------
    llm_enabled: bool = False
    llm_provider: str = "null"
    llm_api_key: SecretStr = SecretStr("")
    llm_model: str = "claude-opus-4-8"
    llm_allow_autonomous_actions: bool = False

    # ------------------------------------------------------------------ urls
    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:"
            f"{self.postgres_password.get_secret_value()}@"
            f"{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def sync_database_url(self) -> str:
        """Alembic and Celery use the sync driver."""
        return (
            f"postgresql+psycopg://{self.postgres_user}:"
            f"{self.postgres_password.get_secret_value()}@"
            f"{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def is_production(self) -> bool:
        return self.environment is Environment.PRODUCTION

    @property
    def is_hardened(self) -> bool:
        """True for every environment except development.

        Staging is the production posture for a pre-production site: the same
        startup refusals, session cookie, CSRF check and local-principal refusal.
        """
        return self.environment is not Environment.DEVELOPMENT

    def session_cookies_enforced(self) -> bool:
        """True when a request must carry the session cookie.

        Development with ``auth_provider=local`` keeps the local-dev principal.
        Every other environment requires the cookie, and so does development
        when ``auth_provider=session``, so the session suite still runs there.
        """
        return self.is_hardened or self.auth_provider == "session"

    def encryption_keyring(self) -> dict[int, str]:
        """All known Fernet keys by version, active version included.

        Legacy deployments provide only ENCRYPTION_KEY (version becomes
        `encryption_key_version`). Rotation adds ENCRYPTION_KEYS_JSON. The union
        is used to build a MultiFernet so ciphertext written under any historical
        key still decrypts.
        """
        keys: dict[int, str] = {}
        primary = self.encryption_key.get_secret_value()
        if primary:
            keys[self.encryption_key_version] = primary
        if self.encryption_keys_json.strip():
            try:
                extra = json.loads(self.encryption_keys_json)
            except json.JSONDecodeError as exc:
                raise ValueError("ENCRYPTION_KEYS_JSON is not valid JSON") from exc
            for ver, key in extra.items():
                keys[int(ver)] = key
        return keys

    def retention_days(self) -> dict[str, int | None]:
        """Configured retention per hypertable. None = disabled (non-destructive)."""
        if not self.retention_enabled:
            return {}
        return {
            "log_source_metric": self.retention_log_source_metric_days,
            "search_result_metric": self.retention_search_result_metric_days,
            "rule_metric": self.retention_rule_metric_days,
            "offense_snapshot": self.retention_offense_snapshot_days,
        }

    # ------------------------------------------------------------- secrets
    def resolve_qradar_token(self) -> SecretStr:
        """Return the SEC token, preferring the file over the environment.

        Read on demand rather than cached at startup so rotating the mounted
        file takes effect on the next provider build without a restart. Errors
        name the path but never the contents.
        """
        if self.qradar_api_token_file:
            path = Path(self.qradar_api_token_file)
            try:
                # .strip() because an editor-authored token file almost always
                # ends in a newline, and a trailing \n in a SEC header is a 401
                # that looks exactly like a wrong token.
                token = path.read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise ValueError(
                    f"QRADAR_API_TOKEN_FILE could not be read: {path} ({exc.strerror})"
                ) from None
            if not token:
                raise ValueError(f"QRADAR_API_TOKEN_FILE is empty: {path}")
            return SecretStr(token)
        return self.qradar_sec_token

    @property
    def has_qradar_token(self) -> bool:
        return bool(self.qradar_api_token_file or self.qradar_sec_token.get_secret_value())

    # ----------------------------------------------------------- validation
    @field_validator("geo_directions")
    @classmethod
    def _geo_directions_are_known(cls, value: list[str]) -> list[str]:
        if any(item not in ("SOURCE", "DESTINATION") for item in value):
            raise ValueError("GEO_DIRECTIONS holds SOURCE and DESTINATION")
        return list(dict.fromkeys(value))

    @field_validator("catchall_type_ids")
    @classmethod
    def _catchall_type_ids_are_ids(cls, value: list[int]) -> list[int]:
        if not value:
            raise ValueError("CATCHALL_TYPE_IDS needs at least one device type")
        for item in value:
            if isinstance(item, bool) or not isinstance(item, int) or not 1 <= item <= 100_000:
                raise ValueError("CATCHALL_TYPE_IDS holds integers from 1 to 100000")
        return value

    @field_validator("qradar_host")
    @classmethod
    def _reject_plaintext_qradar(cls, v: str) -> str:
        if v and not v.startswith("https://"):
            raise ValueError(
                "QRADAR_HOST must use https:// — a SIEM token must never cross plaintext HTTP"
            )
        return v

    @field_validator("ariel_max_timeout_seconds")
    @classmethod
    def _timeout_ordering(cls, v: int, info: ValidationInfo) -> int:
        default = info.data.get("ariel_default_timeout_seconds")
        if default is not None and v < default:
            raise ValueError("ARIEL_MAX_TIMEOUT_SECONDS must be >= ARIEL_DEFAULT_TIMEOUT_SECONDS")
        return v

    @model_validator(mode="after")
    def _apply_lab_profile(self) -> Settings:
        if not self.lab_mode:
            return self
        if self.is_production:
            raise ValueError("LAB_MODE is not permitted in production")
        for field_name, value in LAB_PROFILE.items():
            setattr(self, field_name, value)
        return self

    @model_validator(mode="after")
    def _waf_retention_covers_the_baseline(self) -> Settings:
        """Deleting metrics the baseline still needs would silently thin it.

        A baseline rebuild reads `baseline_lookback_days` of history, so metric
        retention at or below that removes samples every rebuild relies on --
        cells would fall below their minimum and report INSUFFICIENT_DATA for a
        reason no operator could see.
        """
        # Enforced only where it can bite: with WAF collection and WAF retention
        # both on. A deployment that does not collect WAF telemetry -- every
        # Phase A deployment today -- must not be refused startup over a WAF
        # setting it does not use. The retention task repeats the check at run
        # time, so a later settings change cannot slip past it.
        if not (self.waf_collection_enabled and self.waf_retention_enabled):
            return self
        if self.waf_metric_retention_days <= self.baseline_lookback_days:
            raise ValueError(
                "WAF_METRIC_RETENTION_DAYS must exceed BASELINE_LOOKBACK_DAYS "
                f"({self.waf_metric_retention_days} <= {self.baseline_lookback_days})"
            )
        return self

    @model_validator(mode="after")
    def _ips_retention_covers_the_baseline(self) -> Settings:
        """Deleting metrics the IPS baseline still needs would silently thin it.

        A baseline rebuild reads `ips_baseline_lookback_days` of history, so
        metric retention at or below that removes samples every rebuild relies
        on. Same rule as WAF: refused only when collection and retention are
        both on. The retention task repeats the check even if collection is off.
        """
        if not (self.ips_collection_enabled and self.ips_retention_enabled):
            return self
        if self.ips_metric_retention_days <= self.ips_baseline_lookback_days:
            raise ValueError(
                "IPS_METRIC_RETENTION_DAYS must exceed IPS_BASELINE_LOOKBACK_DAYS "
                f"({self.ips_metric_retention_days} <= {self.ips_baseline_lookback_days})"
            )
        return self

    @model_validator(mode="after")
    def _rule_match_retention_covers_readers(self) -> Settings:
        """Deleting measured matches a reader still needs would silently thin health.

        Rule health reads `rule_health_window_days` of match windows and
        metrics, the `/rule-statistics` range is capped at 30 days by the
        endpoint, and the history backfill's window is the smaller of
        `history_backfill_days` and `rule_health_window_days` (the family's
        lookback setting). Refusing below `rule_health_window_days` covers
        all three.
        """
        # Not gated by any collection switch: the rows exist independently of
        # whether any collection is on today.
        if self.rule_match_retention_days <= self.rule_health_window_days:
            raise ValueError(
                "RULE_MATCH_RETENTION_DAYS must exceed RULE_HEALTH_WINDOW_DAYS "
                f"({self.rule_match_retention_days} <= {self.rule_health_window_days})"
            )
        return self

    @model_validator(mode="after")
    def _rule_match_repair_bounds_are_consistent(self) -> Settings:
        """Repair settings that contradict the live ones would fail closed at runtime.

        An age bound below one window could select nothing; an age bound at or
        beyond retention could re-read a window whose data is gone; a per-run
        repair cap above the live cap is a configuration mistake. Refused only
        where they can bite — collection and repair both on — as the WAF checks
        above do: a deployment that collects no rule matches must not be refused
        startup over a repair setting it never uses.
        """
        if not (self.rule_match_collection_enabled and self.rule_match_repair_enabled):
            return self
        if self.rule_match_repair_max_age_seconds < self.rule_match_window_seconds:
            raise ValueError(
                "RULE_MATCH_REPAIR_MAX_AGE_SECONDS must be at least one window "
                f"({self.rule_match_repair_max_age_seconds} < "
                f"{self.rule_match_window_seconds})"
            )
        if (
            self.rule_match_repair_max_age_seconds
            >= self.rule_match_retention_days * 86400
        ):
            raise ValueError(
                "RULE_MATCH_REPAIR_MAX_AGE_SECONDS must stay inside "
                "RULE_MATCH_RETENTION_DAYS "
                f"({self.rule_match_repair_max_age_seconds} >= "
                f"{self.rule_match_retention_days * 86400})"
            )
        if self.rule_match_repair_max_windows_per_run > self.rule_match_max_windows_per_run:
            raise ValueError(
                "RULE_MATCH_REPAIR_MAX_WINDOWS_PER_RUN must not exceed "
                "RULE_MATCH_MAX_WINDOWS_PER_RUN "
                f"({self.rule_match_repair_max_windows_per_run} > "
                f"{self.rule_match_max_windows_per_run})"
            )
        return self

    @model_validator(mode="after")
    def _waf_novelty_history_is_retained(self) -> Settings:
        """A novelty history longer than retention could never be complete.

        It would fail closed -- every new endpoint INSUFFICIENT_DATA forever --
        but silently. Refused only where WAF collection is on, as above.
        """
        if not self.waf_collection_enabled:
            return self
        if self.waf_new_endpoint_history_days >= self.waf_metric_retention_days:
            raise ValueError(
                "WAF_NEW_ENDPOINT_HISTORY_DAYS must be below WAF_METRIC_RETENTION_DAYS "
                f"({self.waf_new_endpoint_history_days} >= {self.waf_metric_retention_days})"
            )
        return self

    @model_validator(mode="after")
    def _pw_bounds_are_consistent(self) -> Settings:
        """Property-watch settings that contradict one another fail closed at startup.

        Refused only where PW collection is on, like the WAF checks above: a deployment
        that does not collect property watches must not be refused startup over a setting
        it does not use.
        """
        if not self.pw_collection_enabled:
            return self
        problems: list[str] = []
        if self.pw_max_rows_per_search > self.ariel_multi_bucket_max_rows:
            problems.append(
                "PW_MAX_ROWS_PER_SEARCH must not exceed ARIEL_MULTI_BUCKET_MAX_ROWS "
                f"({self.pw_max_rows_per_search} > {self.ariel_multi_bucket_max_rows})"
            )
        if self.pw_max_searches_per_watch_per_tick > self.pw_max_searches_per_tick:
            problems.append(
                "PW_MAX_SEARCHES_PER_WATCH_PER_TICK must not exceed PW_MAX_SEARCHES_PER_TICK "
                f"({self.pw_max_searches_per_watch_per_tick} > {self.pw_max_searches_per_tick})"
            )
        if self.pw_max_pinned_values > self.pw_max_active_values_per_watch:
            problems.append(
                "PW_MAX_PINNED_VALUES must not exceed PW_MAX_ACTIVE_VALUES_PER_WATCH "
                f"({self.pw_max_pinned_values} > {self.pw_max_active_values_per_watch})"
            )
        if self.pw_baseline_lookback_days > self.pw_metric_retention_days:
            problems.append(
                "PW_BASELINE_LOOKBACK_DAYS must not exceed PW_METRIC_RETENTION_DAYS "
                f"({self.pw_baseline_lookback_days} > {self.pw_metric_retention_days})"
            )
        # One visit asks for no recurrence, so it needs no week of lookback.
        if (
            self.pw_baseline_min_visits > 1
            and self.pw_baseline_min_visits * 7 > self.pw_baseline_lookback_days
        ):
            problems.append(
                "PW_BASELINE_MIN_VISITS weekly visits must fit in PW_BASELINE_LOOKBACK_DAYS "
                f"({self.pw_baseline_min_visits} x 7 > {self.pw_baseline_lookback_days})"
            )
        if self.pw_new_value_history_days >= self.pw_metric_retention_days:
            problems.append(
                "PW_NEW_VALUE_HISTORY_DAYS must be below PW_METRIC_RETENTION_DAYS "
                f"({self.pw_new_value_history_days} >= {self.pw_metric_retention_days})"
            )
        if self.pw_bootstrap_days < self.pw_new_value_history_days:
            problems.append(
                "PW_BOOTSTRAP_DAYS must not be below PW_NEW_VALUE_HISTORY_DAYS "
                f"({self.pw_bootstrap_days} < {self.pw_new_value_history_days})"
            )
        if self.pw_bootstrap_days > self.pw_baseline_lookback_days:
            problems.append(
                "PW_BOOTSTRAP_DAYS must not exceed PW_BASELINE_LOOKBACK_DAYS "
                f"({self.pw_bootstrap_days} > {self.pw_baseline_lookback_days})"
            )
        if self.pw_bootstrap_searches_per_tick > self.pw_max_searches_per_tick:
            problems.append(
                "PW_BOOTSTRAP_SEARCHES_PER_TICK must not exceed PW_MAX_SEARCHES_PER_TICK "
                f"({self.pw_bootstrap_searches_per_tick} > {self.pw_max_searches_per_tick})"
            )
        bootstrap_hours = self.pw_bootstrap_days * 24 + self.pw_bootstrap_max_hours
        retention_hours = self.pw_metric_retention_days * 24
        if bootstrap_hours >= retention_hours:
            problems.append(
                "PW_BOOTSTRAP_DAYS and PW_BOOTSTRAP_MAX_HOURS must fit below "
                "PW_METRIC_RETENTION_DAYS "
                f"({self.pw_bootstrap_days} * 24 + {self.pw_bootstrap_max_hours} "
                f">= {self.pw_metric_retention_days} * 24)"
            )
        if problems:
            raise ValueError("; ".join(problems))
        return self

    @model_validator(mode="after")
    def _history_backfill_bounds_are_consistent(self) -> Settings:
        """Cross-checks among the history settings themselves.

        Nothing here reads another family's settings: WAF, IPS, catch-all and Phase A are
        released, and a refusal tied to their values could stop an install that starts
        today. Those bounds are applied at run time (`fit_family_window`).
        """
        if self.history_backfill_waf_breakdown_days > self.history_backfill_days:
            raise ValueError(
                "HISTORY_BACKFILL_WAF_BREAKDOWN_DAYS must not exceed HISTORY_BACKFILL_DAYS "
                f"({self.history_backfill_waf_breakdown_days} > {self.history_backfill_days})"
            )
        return self

    @model_validator(mode="after")
    def _permit_namespaces_are_distinct(self) -> Settings:
        namespaces = {
            "COLLECTION_ADVISORY_LOCK_NAMESPACE": self.collection_advisory_lock_namespace,
            "WAF_ARIEL_PERMIT_LOCK_NAMESPACE": self.waf_ariel_permit_lock_namespace,
            "ARIEL_PERMIT_LOCK_NAMESPACE": self.ariel_permit_lock_namespace,
            "ARIEL_PERMIT_INSTANCE_LOCK_NAMESPACE": (
                self.ariel_permit_instance_lock_namespace
            ),
        }
        if len(set(namespaces.values())) != len(namespaces):
            collisions = ", ".join(
                name
                for name, value in namespaces.items()
                if list(namespaces.values()).count(value) > 1
            )
            raise ValueError(f"Advisory-lock namespaces must be pairwise distinct: {collisions}")
        return self

    @model_validator(mode="after")
    def _ariel_live_reservation_fits_global_pool(self) -> Settings:
        if self.ariel_permit_reserved_live_slots >= self.ariel_global_max_concurrent_searches:
            raise ValueError(
                "ARIEL_PERMIT_RESERVED_LIVE_SLOTS must be below "
                "ARIEL_GLOBAL_MAX_CONCURRENT_SEARCHES "
                f"({self.ariel_permit_reserved_live_slots} >= "
                f"{self.ariel_global_max_concurrent_searches})"
            )
        return self

    @field_validator("public_origin")
    @classmethod
    def _normalise_public_origin(cls, value: str) -> str:
        return value.strip().rstrip("/")

    @model_validator(mode="after")
    def _production_hardening(self) -> Settings:
        """Refuse to boot a non-development deployment with a development posture.

        Staging uses this posture. These are startup failures rather than
        warnings: every one of them is a finding an auditor would raise, and a
        warning in a log nobody reads is not a control.
        """
        if not self.is_hardened:
            return self

        problems: list[str] = []

        if self.debug:
            problems.append("DEBUG must be false outside development")
        if not self.qradar_verify_ssl:
            problems.append("QRADAR_VERIFY_SSL must be true outside development")
        problems.extend(
            _hardened_secret_problems(
                self.secret_key.get_secret_value(),
                self.encryption_key.get_secret_value(),
                self.encryption_keys_json,
            )
        )
        if self.qradar_provider is ProviderKind.MOCK:
            problems.append("QRADAR_PROVIDER=mock is not permitted outside development")
        if self.qradar_provider is ProviderKind.REST and not self.has_qradar_token:
            problems.append(
                "QRADAR_API_TOKEN_FILE or QRADAR_SEC_TOKEN is required when QRADAR_PROVIDER=rest"
            )
        if self.qradar_provider is ProviderKind.MCP and not self.has_qradar_token:
            # The pinned server authenticates every request with the SEC header.
            problems.append(
                "QRADAR_API_TOKEN_FILE or QRADAR_SEC_TOKEN is required when QRADAR_PROVIDER=mcp"
            )
        if self.llm_allow_autonomous_actions:
            problems.append(
                "LLM_ALLOW_AUTONOMOUS_ACTIONS must be false; autonomous response is not implemented"
            )
        origin = urlsplit(self.public_origin)
        if (
            origin.scheme != "https"
            or not origin.hostname
            or origin.username
            or origin.password
            or origin.path not in ("", "/")
            or origin.query
            or origin.fragment
            or "*" in self.public_origin
        ):
            problems.append(
                "PUBLIC_ORIGIN must be an https origin with a host and no path, query, or wildcard"
            )
        if self.auth_provider == "oidc":
            problems.append(
                "AUTH_PROVIDER=oidc is not available; OIDC is out of scope. "
                "Set AUTH_PROVIDER=session"
            )

        if problems:
            # OPS-R17 names this sentence, including the environment. Staging raises
            # it too: staging is the production posture for a pre-production site.
            raise ValueError(
                f"Unsafe configuration for ENVIRONMENT={self.environment.value}:\n  - "
                + "\n  - ".join(problems)
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache(maxsize=1)
def _known_setting_env_names() -> frozenset[str]:
    """Upper-case field names and aliases ``Settings`` accepts from the environment."""
    names: set[str] = set()
    for field_name, field in Settings.model_fields.items():
        names.add(field_name.upper())
        alias = field.validation_alias
        if isinstance(alias, str):
            names.add(alias.upper())
        elif isinstance(alias, AliasChoices):
            for choice in alias.choices:
                if isinstance(choice, str):
                    names.add(choice.upper())
    return frozenset(names)


@lru_cache(maxsize=1)
def _setting_env_prefixes() -> frozenset[str]:
    return frozenset(name.split("_", 1)[0] for name in _known_setting_env_names())


def unknown_setting_names(environ: Mapping[str, str]) -> tuple[str, ...]:
    """Environment names that look like settings and are not.

    A name looks like a setting when the segment before its first ``_`` is the
    first segment of a ``Settings`` field or alias. The name itself must not be
    a field or alias, and must not be on ``SETTINGS_ENV_ALLOWLIST`` or
    ``DEPLOYMENT_ENV_NAMES``. The result is uppercase and sorted. Values are never read.
    """
    known = _known_setting_env_names()
    prefixes = _setting_env_prefixes()
    ignored = SETTINGS_ENV_ALLOWLIST | DEPLOYMENT_ENV_NAMES
    found: set[str] = set()
    for raw in environ:
        name = raw.upper()
        if not name or name in known or name in ignored:
            continue
        if name.split("_", 1)[0] in prefixes:
            found.add(name)
    return tuple(sorted(found))
