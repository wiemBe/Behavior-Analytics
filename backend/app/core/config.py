"""Application settings.

Everything is sourced from the environment. No secret is ever hardcoded, and
`Settings` refuses to construct an unsafe production configuration.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import (
    AliasChoices,
    Field,
    SecretStr,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class ProviderKind(StrEnum):
    MOCK = "mock"
    REST = "rest"
    MCP = "mcp"


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

    # --- Scheduled-search driver --------------------------------------------
    # Most searches dispatched in one scheduler cycle, so a backlog can never
    # turn a single tick into an unbounded burst of Ariel searches.
    search_max_dispatch_per_cycle: int = Field(default=10, ge=1, le=200)
    # Consecutive failed runs before a scheduled search itself raises an alert.
    # A search that has quietly stopped producing results is exactly the blind
    # spot this platform exists to surface, so it is alertable in its own right.
    search_failure_alert_after: int = Field(default=3, ge=1)
    # Delay a cadence slot after its boundary so late QRadar events can arrive.
    search_cadence_settle_seconds: int = Field(default=120, ge=0, le=900)
    search_cadence_max_searches_per_tick: int = Field(default=16, ge=1, le=256)
    #: How long the QRadar custom-property catalog is reused per instance.
    qradar_property_catalog_ttl_seconds: int = Field(default=600, ge=60, le=86400)
    ariel_multi_bucket_max_rows: int = Field(default=5000, ge=1, le=100_000)

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
    rule_health_logic_version: int = 2
    # Health classification reads only stored data, so it can run more often
    # than the inventory sync that feeds it.
    rule_health_evaluation_interval_seconds: int = Field(default=1800, ge=60)

    # --- Detection coverage (Phase 3) ---------------------------------------
    coverage_evaluation_interval_seconds: int = Field(default=3600, ge=60)
    # Inferred mappings below this confidence are recorded but never counted
    # toward coverage.
    coverage_min_confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    coverage_logic_version: int = 1

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
    auth_provider: Literal["local", "oidc"] = "local"
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: SecretStr = SecretStr("")
    oidc_redirect_uri: str = ""

    # --- rate limiting ------------------------------------------------------
    rate_limit_default: str = "120/minute"
    rate_limit_search_execution: str = "10/minute"

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

    def encryption_keyring(self) -> dict[int, str]:
        """All known Fernet keys by version, active version included.

        Legacy deployments provide only ENCRYPTION_KEY (version becomes
        `encryption_key_version`). Rotation adds ENCRYPTION_KEYS_JSON. The union
        is used to build a MultiFernet so ciphertext written under any historical
        key still decrypts.
        """
        import json

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
    def _waf_permit_namespace_is_its_own(self) -> Settings:
        if self.waf_ariel_permit_lock_namespace == self.collection_advisory_lock_namespace:
            raise ValueError(
                "WAF_ARIEL_PERMIT_LOCK_NAMESPACE must differ from "
                "COLLECTION_ADVISORY_LOCK_NAMESPACE"
            )
        return self

    @model_validator(mode="after")
    def _production_hardening(self) -> Settings:
        """Refuse to boot a production deployment with a development posture.

        These are startup failures rather than warnings: every one of them is a
        finding an auditor would raise, and a warning in a log nobody reads is
        not a control.
        """
        if not self.is_production:
            return self

        problems: list[str] = []

        if self.debug:
            problems.append("DEBUG must be false in production")
        if not self.qradar_verify_ssl:
            problems.append("QRADAR_VERIFY_SSL must be true in production")
        if len(self.secret_key.get_secret_value()) < 32:
            problems.append("SECRET_KEY must be at least 32 characters")
        if not self.encryption_key.get_secret_value():
            problems.append("ENCRYPTION_KEY is required to encrypt stored credentials")
        if self.qradar_provider is ProviderKind.MOCK:
            problems.append("QRADAR_PROVIDER=mock is not permitted in production")
        if self.qradar_provider is ProviderKind.REST and not self.has_qradar_token:
            problems.append(
                "QRADAR_API_TOKEN_FILE or QRADAR_SEC_TOKEN is required when QRADAR_PROVIDER=rest"
            )
        if self.llm_allow_autonomous_actions:
            problems.append(
                "LLM_ALLOW_AUTONOMOUS_ACTIONS must be false; autonomous response is not implemented"
            )

        if problems:
            raise ValueError("Unsafe production configuration:\n  - " + "\n  - ".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
