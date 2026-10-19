# Configuration reference

Behavior Analytics reads settings from environment variables. The authoritative schema, defaults
and validation bounds are in `backend/app/core/config.py`; `.env.example` is the deployment
template. Environment names are case-insensitive in the backend, but deployments should use the
uppercase names shown here.

Never put a QRadar token, database password, encryption key, OIDC secret or notification webhook
in source control. Prefer `QRADAR_API_TOKEN_FILE` to the token environment variable.

## Core and providers

| Setting | Default | Meaning |
|---|---:|---|
| `ENVIRONMENT` | `development` | `development`, `staging` or `production` |
| `DEBUG` | `false` | Diagnostic mode; refused in production |
| `LAB_MODE` | `false` | Isolated accelerated lab profile; refused in production |
| `LOG_LEVEL` | `INFO` | Application log level |
| `API_PREFIX` | `/api/v1` | Versioned API prefix |
| `QRADAR_PROVIDER` | `mock` | `mock`, `rest` or `mcp`; production refuses `mock` |
| `QRADAR_BASE_URL` | empty | Preferred QRadar URL; `QRADAR_HOST` is a compatibility alias |
| `QRADAR_API_TOKEN_FILE` | unset | File containing the read-only service token; preferred |
| `QRADAR_SEC_TOKEN` | empty | Token value fallback; avoid in production |
| `QRADAR_API_VERSION` | `20.0` | Default API version for unregistered connections |
| `QRADAR_VERIFY_SSL` | `true` | TLS verification; `false` is refused by the REST provider |
| `QRADAR_CA_BUNDLE` | unset | Internal CA bundle path |
| `QRADAR_TLS_ALLOW_MISSING_AKI` | `false` | Permit only the missing-AKI certificate condition |
| `MCP_ENABLED` | `false` | Enable the optional MCP inventory integration |
| `MCP_BASE_URL` | `http://qradar-mcp:5000` | Internal MCP service URL |
| `MCP_TIMEOUT_SECONDS` | `60` | MCP request timeout |

## Secrets, database and queue

| Setting | Default | Meaning |
|---|---:|---|
| `SECRET_KEY` | empty | Required application secret |
| `ENCRYPTION_KEY` | empty | Fernet key for encrypted columns |
| `ENCRYPTION_KEY_VERSION` | `1` | Active encryption-key version |
| `ENCRYPTION_KEYS_JSON` | empty | JSON map of all keys accepted during rotation |
| `POSTGRES_USER` | `qradar_obs` | Database user |
| `POSTGRES_PASSWORD` | empty | Database password |
| `POSTGRES_DB` | `qradar_obs` | Database name |
| `POSTGRES_HOST` | `postgres` | Database host |
| `POSTGRES_PORT` | `5432` | Database port |
| `REDIS_URL` | `redis://redis:6379/0` | General Redis URL |
| `CELERY_BROKER_URL` | `redis://redis:6379/1` | Celery broker |
| `CELERY_RESULT_BACKEND` | `redis://redis:6379/2` | Celery result backend |

## Ariel guardrails

| Setting | Default | Meaning |
|---|---:|---|
| `ARIEL_MAX_CONCURRENT_SEARCHES` | `3` | Concurrent searches per QRadar instance |
| `ARIEL_GLOBAL_MAX_CONCURRENT_SEARCHES` | `6` | Concurrent searches across instances |
| `ARIEL_DEFAULT_TIMEOUT_SECONDS` | `300` | Default search timeout |
| `ARIEL_MAX_TIMEOUT_SECONDS` | `900` | Maximum accepted timeout |
| `ARIEL_MAX_TIME_RANGE_HOURS` | `168` | Maximum query time range |
| `ARIEL_MAX_RESULT_ROWS` | `10000` | General result-row cap |
| `ARIEL_POLL_INTERVAL_SECONDS` | `3.0` | Search-status polling interval |
| `ARIEL_MAX_RETRIES` | `3` | Retry count for retryable provider failures |
| `ARIEL_RETRY_BASE_SECONDS` | `2.0` | Initial retry delay |
| `ARIEL_RETRY_MAX_SECONDS` | `60.0` | Maximum retry delay |
| `ARIEL_STOP_PAD_SECONDS` | `60` | Padding on the Ariel stop bound; the `WHERE` interval is unchanged |
| `ARIEL_MULTI_BUCKET_MAX_ROWS` | `5000` | Row cap before a keyed time window is split |

## Search cadence and bucket collection

Every QRadar search family defaults to a five-minute cadence, except WAF and IPS, which default to fifteen minutes. Operators can select 5 minutes,
15 minutes, 30 minutes, 1 hour, 2 hours, 4 hours or 8 hours. Cadence controls when a family
searches; `COLLECTION_INTERVAL_SECONDS` controls bucket width. A tick may collect several complete
buckets in one keyed search. Truncated multi-bucket results are split and are never stored as
complete data.

| Setting | Default | Meaning |
|---|---:|---|
| search-family cadence | `5 minutes` | Initial cadence for every family |
| `COLLECTION_INTERVAL_SECONDS` | `300` | Bucket width; it does not schedule collection |
| `COLLECTION_BATCH_SIZE` | `50` | Sources handled in one collection batch |
| `COLLECTION_MAX_BACKFILL_INTERVALS` | `12` | Buckets a metric run may backfill |
| `SEARCH_CADENCE_SETTLE_SECONDS` | `120` | Delay after a slot boundary for late events |
| `SEARCH_CADENCE_MAX_SEARCHES_PER_TICK` | `16` | Searches one family may spend per tick, including splits |
| `ANOMALY_MAX_BUCKETS_PER_EVALUATION` | `120` | Source-volume buckets evaluated per source in one run |
| `RULE_MATCH_WINDOW_SECONDS` | `3600` | Rule-match measurement window |

`EXPLANATION_COLLECTION_INTERVAL_SECONDS` and `RULE_MATCH_COLLECTION_INTERVAL_SECONDS` remain
accepted compatibility settings, but search-family cadence is the scheduler control.

## Source-volume baselines and incidents

| Setting | Default | Meaning |
|---|---:|---|
| `BASELINE_MIN_SAMPLES` | `8` | Samples required in a weekday-and-hour cell |
| `BASELINE_LOOKBACK_DAYS` | `28` | History used to rebuild a seasonal baseline |
| `BASELINE_MAD_FLOOR_RATIO` | `0.1` | Minimum robust scale relative to the median |
| `ANOMALY_OPEN_AFTER_INTERVALS` | `2` | Consecutive anomalous buckets before opening |
| `ANOMALY_RESOLVE_AFTER_INTERVALS` | `3` | Consecutive healthy buckets before resolution |
| `ANOMALY_DEVIATION_THRESHOLD` | `3.5` | Robust deviation threshold |
| `ANOMALY_SPIKE_RATIO` | `2.0` | Minimum spike multiple |
| `ANOMALY_DROP_RATIO` | `0.5` | Maximum drop fraction |
| `ANOMALY_MIN_ABSOLUTE_DELTA_EVENTS` | `100` | Minimum absolute event change |
| `ANOMALY_MIN_BUCKET_EVENTS` | `50` | Minimum material bucket population |
| `ANOMALY_SILENCE_GRACE_BUCKETS` | `1` | Missing buckets tolerated before silence evaluation |
| `OUTAGE_ALERT_DEFAULT` | `ALERT` | Default source-silence policy when no override applies |

## WAF behavior

WAF collection and alerting are separate opt-ins. A source also needs an enabled profile whose
current version has validated successfully.

| Setting | Default | Meaning |
|---|---:|---|
| `WAF_COLLECTION_ENABLED` | `false` | Family collection switch |
| `WAF_ALERTING_ENABLED` | `false` | Alerting switch; incidents remain in shadow mode while off |
| `WAF_OUTCOME_COLLECTION_ENABLED` | `false` | Collect action and response-code breakdowns |
| `WAF_CLIENT_COLLECTION_ENABLED` | `false` | Collect source-client breakdowns |
| `WAF_MAX_GROUPS_PER_BUCKET` | `500` | Endpoint groups retained from a bucket search |
| `WAF_MAX_BREAKDOWN_GROUPS_PER_BUCKET` | `2000` | Groups retained for an optional breakdown |
| `WAF_MIN_ENDPOINT_COVERAGE` | `0.90` | Coverage floor for absence-based conclusions |
| `WAF_MAX_NEW_ENDPOINTS_PER_BUCKET` | `20` | New endpoints admitted per source and bucket |
| `WAF_MAX_ACTIVE_ENDPOINTS_PER_SOURCE` | `2000` | Active endpoint ceiling per source |
| `WAF_MIN_ADMISSION_REQUESTS` | `3` | Requests required to admit an unseen endpoint |
| `WAF_ARIEL_MAX_CONCURRENT_SOURCES` | `2` | WAF sources allowed to search Ariel concurrently |
| `WAF_MAX_EVALUATION_BUCKETS_PER_RUN` | `24` | Buckets evaluated per source and run |
| `WAF_BASELINE_ENDPOINTS_PER_RUN` | `50` | Endpoints rebuilt per baseline run |
| `WAF_ENDPOINT_ACTIVE_DAYS` | `7` | Idle days before an endpoint retires |
| `WAF_METRIC_RETENTION_DAYS` | `35` | Endpoint metric retention |
| `WAF_ANOMALY_RETENTION_DAYS` | `180` | Resolved WAF incident retention |

Field names and blocked/allowed values belong to each source's versioned profile. See
[`FIELD-MAPPING.md`](FIELD-MAPPING.md).

## Catch-all senders

| Setting | Default | Meaning |
|---|---:|---|
| `CATCHALL_COLLECTION_ENABLED` | `true` | Family collection switch |
| `CATCHALL_TYPE_IDS` | `67, 39, 212, 367, 4000` | Device types searched for sender names |
| `CATCHALL_MAX_ROWS_PER_SEARCH` | `2000` | Rows accepted before time splitting |
| `CATCHALL_MAX_NEW_SENDERS_PER_BUCKET` | `20` | New senders admitted per source and bucket |
| `CATCHALL_MAX_ACTIVE_SENDERS_PER_SOURCE` | `2000` | Active sender ceiling |
| `CATCHALL_MIN_ADMISSION_EVENTS` | `3` | Events required to admit an unseen sender |
| `CATCHALL_SENDER_ACTIVE_DAYS` | `7` | Idle days before a sender retires |
| `CATCHALL_NEW_SENDER_ALERTS_ENABLED` | `false` | Open an inventory alert for a new sender |
| `CATCHALL_TOP_SENDERS` | `20` | Bounded contributors stored on a bucket |
| `CATCHALL_RETENTION_DAYS` | `35` | Bucket and retired-sender retention |
| `CATCHALL_RETENTION_BATCH_SIZE` | `5000` | Rows deleted per batch |
| `CATCHALL_RETENTION_MAX_BATCHES` | `20` | Batches per retention run |
| `CATCHALL_RETENTION_INTERVAL_SECONDS` | `86400` | Retention schedule |

## IPS behavior

| Setting | Default | Meaning |
|---|---:|---|
| `IPS_COLLECTION_ENABLED` | `false` | Fleet collection switch; profiles must also be enabled and valid |
| `IPS_ALERTING_ENABLED` | `false` | Shadow-mode alerting switch |
| `IPS_MAX_SOURCES_PER_SEARCH` | `50` | Sources grouped into one keyed search |
| `IPS_MAX_ENTITIES_PER_BUCKET` | `200` | Entities retained per source, bucket and dimension |
| `IPS_MAX_NEW_ENTITIES_PER_BUCKET` | `20` | New entities admitted per source and bucket |
| `IPS_MAX_ACTIVE_ENTITIES_PER_SOURCE_DIMENSION` | `2000` | Active entity ceiling |
| `IPS_MIN_ADMISSION_EVENTS` | `3` | Events required to admit an unseen entity |
| `IPS_ENTITY_ACTIVE_DAYS` | `7` | Idle days before an entity retires |
| `IPS_MIN_COVERAGE` | `0.90` | Coverage floor for absence-based conclusions |
| `IPS_VALIDATION_WINDOW_SECONDS` | `900` | Recent interval inspected by validation |
| `IPS_VALIDATION_PENDING_TIMEOUT_SECONDS` | `900` | Age after which a dead pending validation can be replaced |
| `IPS_VALIDATION_LOCK_RETRY_DELAY_SECONDS` | `30` | Delay between profile-lock retries |
| `IPS_VALIDATION_LOCK_MAX_RETRIES` | `20` | Retry ceiling before a source-busy result |
| `IPS_VALIDATION_MIN_RATIO` | `0.90` | Minimum population share for a required field |
| `IPS_VALIDATION_MAX_ACTION_VALUES` | `20` | Bound on discovered action values |
| `IPS_MAX_EVALUATION_BUCKETS_PER_RUN` | `24` | Buckets evaluated per source and run |
| `IPS_BASELINE_LOOKBACK_DAYS` | `28` | Compatible history used for a baseline |
| `IPS_BASELINE_REBUILD_BATCH_SIZE` | `50` | Subjects rebuilt in one batch |
| `IPS_RETENTION_ENABLED` | `true` | Run bounded IPS retention |
| `IPS_METRIC_RETENTION_DAYS` | `35` | Bucket and entity-metric retention |
| `IPS_RETENTION_BATCH_SIZE` | `5000` | Rows deleted per batch |
| `IPS_RETENTION_MAX_BATCHES` | `20` | Batches per retention run |
| `IPS_RETENTION_INTERVAL_SECONDS` | `86400` | Retention schedule |

## Geography

| Setting | Default | Meaning |
|---|---:|---|
| `GEO_COLLECTION_ENABLED` | `true` | Family collection switch |
| `GEO_DIRECTIONS` | `SOURCE, DESTINATION` | Address directions stored as separate series |
| `GEO_MAX_ROWS_PER_SEARCH` | `5000` | Rows accepted before time splitting |
| `GEO_MAX_COUNTRIES_PER_BUCKET` | `64` | Countries retained; the rest becomes `OVER_CAP` |
| `GEO_RETENTION_DAYS` | `35` | Bucket retention |
| `GEO_RETENTION_BATCH_SIZE` | `5000` | Rows deleted per batch |
| `GEO_RETENTION_MAX_BATCHES` | `20` | Batches per retention run |
| `GEO_RETENTION_INTERVAL_SECONDS` | `86400` | Retention schedule |

No IP address is selected or stored by geography collection. Country lookup occurs inside the
QRadar query.

## Custom threshold rules

| Setting | Default | Meaning |
|---|---:|---|
| `CUSTOM_RULES_ENABLED` | `false` | Run the database-only evaluator |
| `CUSTOM_RULES_ALERTING_ENABLED` | `false` | Open alerts for breaches; otherwise shadow mode |
| `CUSTOM_RULES_MAX_ENABLED` | `100` | Enabled-rule ceiling |
| `CUSTOM_RULES_MAX_BUCKETS_PER_RUN` | `288` | Buckets judged per subject kind and run |
| `CUSTOM_RULE_BREACH_RETENTION_DAYS` | `180` | Retention for resolved breach rows |
| `CUSTOM_RULES_RETENTION_BATCH_SIZE` | `5000` | Rows deleted per batch |
| `CUSTOM_RULES_RETENTION_MAX_BATCHES` | `20` | Batches per retention run |
| `CUSTOM_RULES_RETENTION_INTERVAL_SECONDS` | `86400` | Retention schedule |

Custom rules evaluate aggregates already stored by the platform. They never add an Ariel query.

## Property watches

| Setting | Default | Meaning |
|---|---:|---|
| `PW_COLLECTION_ENABLED` | `false` | Family collection switch |
| `PW_ALERTING_ENABLED` | `false` | Alerting switch; otherwise incidents remain in shadow mode |
| `PW_MAX_ENABLED_WATCHES` | `10` | Enabled-watch ceiling |
| `PW_MAX_SEARCHES_PER_TICK` | `40` | Search budget per instance and tick |
| `PW_MAX_SEARCHES_PER_WATCH_PER_TICK` | `12` | One watch's share of the tick budget |
| `PW_MAX_SOURCES_PER_SEARCH` | `50` | Sources grouped in one keyed search |
| `PW_MAX_CHUNKS_PER_WATCH` | `4` | Maximum source chunks for a watch |
| `PW_MAX_ROWS_PER_SEARCH` | `5000` | Rows accepted before splitting |
| `PW_MAX_VALUES_PER_BUCKET` | `50` | Untracked values retained after folding |
| `PW_MAX_NEW_VALUES_PER_BUCKET` | `20` | New values admitted per bucket |
| `PW_MAX_ACTIVE_VALUES_PER_WATCH` | `200` | Active-value ceiling |
| `PW_MAX_PINNED_VALUES` | `10` | Pinned-value ceiling |
| `PW_VALUE_MAX_LENGTH` | `255` | Stored value width |
| `PW_MIN_ADMISSION_EVENTS` | `3` | Events required to admit an unseen value |
| `PW_VALUE_ACTIVE_DAYS` | `7` | Idle days before an automatic value retires |
| `PW_MIN_COVERAGE` | `0.90` | Coverage floor for absence-based conclusions |
| `PW_SCOPE_STALE_AFTER_HOURS` | `24` | Inventory age at which a group scope fails closed |
| `PW_SCOPE_CHANGE_GRACE_BUCKETS` | `12` | Buckets withheld after scope membership changes |
| `PW_BASELINE_LOOKBACK_DAYS` | `28` | Compatible history used for baselines |
| `PW_BASELINE_MIN_VISITS` | `2` | Distinct weekly visits a weekday-and-hour cell needs before a property-watch volume detector may judge it |
| `PW_BASELINE_VALUES_PER_RUN` | `100` | Values trained in one run |
| `PW_MAX_EVALUATION_BUCKETS_PER_RUN` | `24` | Buckets evaluated per watch and run |
| `PW_SPIKE_RATIO` | `2.0` | Minimum spike multiple |
| `PW_MIN_ABSOLUTE_DELTA_EVENTS` | `50` | Minimum absolute spike or drop |
| `PW_MIN_BUCKET_EVENTS` | `20` | Minimum population for a spike verdict |
| `PW_DROP_MIN_MEDIAN` | `20` | Minimum normal median for a drop verdict |
| `PW_DROP_RATIO` | `0.5` | Maximum drop fraction |
| `PW_DISAPPEAR_MIN_MEDIAN` | `5` | Minimum normal median for disappearance |
| `PW_DISAPPEAR_BUCKETS` | `6` | Consecutive measured-zero buckets for disappearance |
| `PW_NEW_VALUE_HISTORY_DAYS` | `7` | Required complete history for novelty |
| `PW_NEW_VALUE_WINDOW_BUCKETS` | `12` | Materiality window after first appearance |
| `PW_NEW_VALUE_MIN_EVENTS` | `20` | Minimum events for a new value |
| `PW_NEW_VALUE_MIN_BUCKETS` | `2` | Minimum buckets for a new value |
| `PW_METRIC_RETENTION_DAYS` | `35` | Bucket and value-metric retention |
| `PW_ANOMALY_RETENTION_DAYS` | `180` | Age at which a terminal property-watch incident (resolved, or a candidate closed back) and its transitions are deleted; an incident a detector state or an unresolved alert depends on is kept |
| `PW_RETENTION_BATCH_SIZE` | `5000` | Rows deleted per batch |
| `PW_RETENTION_MAX_BATCHES` | `20` | Batches per retention run |
| `PW_RETENTION_INTERVAL_SECONDS` | `86400` | Retention schedule |

## Scheduled searches and result retention

| Setting | Default | Meaning |
|---|---:|---|
| `SEARCH_MAX_DISPATCH_PER_CYCLE` | `10` | Scheduled searches dispatched in one cycle |
| `SEARCH_FAILURE_ALERT_AFTER` | `3` | Consecutive failed runs before an alert |
| `SCHEDULED_SEARCHES_MAX_ENABLED` | `25` | Enabled-search ceiling |
| `SEARCH_RESULT_METRIC_RETENTION_DAYS` | `90` | Stored aggregate-result retention |
| `SEARCH_RESULT_RETENTION_BATCH_SIZE` | `5000` | Rows deleted per batch |
| `SEARCH_RESULT_RETENTION_MAX_BATCHES` | `20` | Batches per retention run |
| `SEARCH_RESULT_RETENTION_INTERVAL_SECONDS` | `86400` | Retention schedule |

Operator-defined scheduled searches keep their own cron expression. New or changed schedules must
have at least five minutes between fires.

## API, sessions and rate limits

| Setting | Default | Meaning |
|---|---:|---|
| `API_DEFAULT_PAGE_SIZE` | `50` | Default list page size |
| `API_MAX_PAGE_SIZE` | `200` | Maximum list page size |
| `API_MAX_RANGE_DAYS` | `365` | Maximum accepted history range |
| `AUTH_PROVIDER` | `local` | Authentication provider |
| `SESSION_IDLE_MINUTES` | `480` | Idle session lifetime |
| `SESSION_MAX_HOURS` | `12` | Absolute session lifetime |
| `LOGIN_LOCKOUT_THRESHOLD` | `5` | Failed attempts before lockout |
| `LOGIN_LOCKOUT_MINUTES` | `15` | Lockout duration |
| `PUBLIC_ORIGIN` | `http://localhost:3000` | Browser origin accepted for session protection |
| `RATE_LIMIT_DEFAULT` | `120/minute` | Default per-client and per-path API limit |
| `RATE_LIMIT_SEARCH_EXECUTION` | `10/minute` | Manual search-execution limit |
| `RATE_LIMIT_LOGIN` | `300/minute` | Login endpoint limit |

## Retention and notifications

General Timescale retention is non-destructive by default: `RETENTION_ENABLED=false`, and each
general retention day setting defaults to unset. Family-specific bounded retention switches are
listed above.

| Setting | Default | Meaning |
|---|---:|---|
| `TIMESCALE_POLICY_INTERVAL_SECONDS` | `86400` | General policy reconciliation interval |
| `NOTIFY_MAX_RETRIES` | `5` | Notification retry count |
| `NOTIFY_RETRY_BASE_SECONDS` | `2.0` | Initial retry delay |
| `NOTIFY_RETRY_MAX_SECONDS` | `300.0` | Maximum retry delay |
| `NOTIFY_SEND_RECOVERY` | `true` | Deliver recovery transitions |
| `NOTIFY_PER_CHANNEL_RATE` | `30/minute` | Per-channel delivery limit |

## LLM interface

| Setting | Default | Meaning |
|---|---:|---|
| `LLM_ENABLED` | `false` | Enable the future investigation-provider interface |
| `LLM_PROVIDER` | `null` | Provider selector |
| `LLM_API_KEY` | empty | Provider secret |
| `LLM_MODEL` | `claude-opus-4-8` | Provider model name |
| `LLM_ALLOW_AUTONOMOUS_ACTIONS` | `false` | Must remain false; production refuses true |

The interface accepts only a redacted, normalized investigation package and returns structured
analysis. It cannot close offenses, block addresses, disable users, modify rules or update QRadar
reference data.
