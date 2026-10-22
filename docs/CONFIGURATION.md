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
| search-family cadence | `5 minutes` | Initial cadence; `15 minutes` for WAF and IPS |
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
| `ANOMALY_MAX_BUCKET_ATTEMPTS` | `3` | Failed evaluation ticks on one bucket before it is given up. `1`–`20` |
| `ANOMALY_FAULT_RETENTION_DAYS` | `30` | Days a resolved or given-up fault is kept. `1`–`3650` |
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

## History backfill

The operator answers once per console whether the platform may search QRadar's stored past. The
settings below govern that reading. They change nothing until an answer is recorded, and the
answer itself does not depend on them: recording the answer is separate from reading.

| Setting | Default | Meaning |
|---|---:|---|
| `HISTORY_BACKFILL_ENABLED` | `true` | Kill switch for reading. Off: no history step runs, and a scope that was reading hands off `PARTIAL` with "history reading was switched off" |
| `HISTORY_BACKFILL_DAYS` | `28` | The window a Yes reads. `7`–`90`; the per-family window is smaller when the family's own lookback or its retention allows fewer days, and every scope fits its window to the budget |
| `HISTORY_BACKFILL_SEARCHES_PER_TICK` | `10` | Searches one history step may start, under the family's tick cap. `1`–`64`; the history sub-budget is the smaller of this and what the live pass left of `SEARCH_CADENCE_MAX_SEARCHES_PER_TICK` |
| `HISTORY_BACKFILL_SECONDS_PER_TICK` | `120` | No history search starts after this many seconds of a tick. `10`–`600` |
| `HISTORY_BACKFILL_MAX_HOURS` | `72` | The longest wall time a reading may take, and the time the window must leave below the family's retention. `1`–`168`; the window is fitted so that `days x 24 + this` stays below the retention of the family, and so that the reading ends inside this many hours |
| `HISTORY_BACKFILL_FLOOR_MARGIN_HOURS` | `24` | The margin before the start of the window, where the floor of a scope is proved. `1`–`168` |
| `HISTORY_BACKFILL_MAX_FAILURES` | `3` | Consecutive failures at the minimum width before a span becomes a recorded gap. `1`–`20` |
| `HISTORY_BACKFILL_MAX_LOWER_BOUND_SHARE` | `0.05` | The density gate: the share of lower-bound buckets that ends a reading `PARTIAL`. `0.0`–`1.0` |
| `HISTORY_BACKFILL_WAF_BREAKDOWN_DAYS` | `7` | Days of WAF breakdown history (action, response code and source IP). `1`–`90`; must not exceed `HISTORY_BACKFILL_DAYS` |
| `HISTORY_BACKFILL_BASELINE_SOURCES_PER_TICK` | `50` | Source-volume baselines rebuilt per tick after a reading. `1`–`5000` |

The only cross-check among them is `HISTORY_BACKFILL_WAF_BREAKDOWN_DAYS <=
HISTORY_BACKFILL_DAYS`; a larger value is refused at startup. The bounds of the other families
(WAF, IPS, catch-all, source volume) are applied when the reading is planned, not at startup, so
a released configuration keeps starting.

What each costs: the days decide how much of the past is read; the two per-tick settings decide
how many searches that reading may spend per tick and for how long; the max-hours setting decides
whether the window has to shrink (a large estate reads fewer days and the scope says "window
reduced from N to D days"); the floor margin is the time read before the window, where the floor of
a scope is proved; the failure and lower-bound settings decide when a reading stops early with a
gap; the WAF breakdown days are the dominant cost of the WAF family; the baseline batch size
decides how long the baseline rebuild after a reading takes.

Every scope fits its window to that budget. Source volume, catch-all senders and rule matches fit
one window for the console; the catch-all fits after its first step, from the density that step
observed, and reads the whole asked window until it has one. A WAF or an IPS source fits its own,
before its first search: a WAF source from the endpoints its profile tracks and the searches one
source may start in a tick, an IPS source from the entities its profile admits and a fair share of
the searches its tick shares between its sources. A window that does not fit is shortened and the
scope says why ("window reduced from 28 to 20 days: the estimate exceeded 72 hours"); below seven
days the scope is `UNAVAILABLE` — and every family but the catch-all makes no search at all, because
its window is fitted before the first one. A source with nothing to fit yet — a WAF profile that
tracks no endpoint yet, an IPS profile that admits no entity yet — is fitted with one and says so
("fitted with no tracked endpoint yet; the 72-hour limit still bounds the reading").

## Property-watch history bootstrap

A property watch may ask for its own history, separate from the console-wide answer. These
settings govern that bootstrap.

| Setting | Default | Meaning |
|---|---:|---|
| `PW_BOOTSTRAP_ENABLED` | `true` | Kill switch for the bootstrap. Off: a watch that asks for history starts live and says so |
| `PW_BOOTSTRAP_DAYS` | `28` | The largest window a watch may ask for, and the default of a new watch. `>= 1`. A watch judges its volume from the hand-off only after `7 x PW_BASELINE_MIN_VISITS` days of history (14 days at the default of two visits); a shorter history keeps the spike, drop and disappearance detectors at `INSUFFICIENT_DATA` until the live buckets add the missing visits |
| `PW_BOOTSTRAP_SEARCHES_PER_TICK` | `12` | Searches one history step may start, under the property-watch tick budget. `1`–`256`; must not exceed `PW_MAX_SEARCHES_PER_TICK`. A step of a reading costs 2 searches (TOTALS and one GROUPED) and 3 with a pinned value (TOTALS, PINNED and one GROUPED): a smaller setting refuses the watch with `HISTORY_BUDGET`, and a reading that is already running waits and says so |
| `PW_BOOTSTRAP_SECONDS_PER_TICK` | `180` | No history search starts after this many seconds of a tick. `10`–`3600` |
| `PW_BOOTSTRAP_FLOOR_MARGIN_HOURS` | `24` | The margin before the start of the window, where the floor of the scope is proved. `1`–`168` |
| `PW_BOOTSTRAP_MAX_HOURS` | `72` | The longest wall time a bootstrap may take. `1`–`168` |
| `PW_BOOTSTRAP_MAX_FAILURES` | `3` | Consecutive failures at the minimum width before a span becomes a recorded gap. `1`–`20` |
| `PW_BOOTSTRAP_MAX_LOWER_BOUND_SHARE` | `0.05` | The density gate: the share of lower-bound buckets that ends a reading `PARTIAL`. `0.0`–`1.0` |

Where `PW_COLLECTION_ENABLED` is on, these are refused at startup when they contradict each other
or another property-watch setting:

- `PW_BOOTSTRAP_DAYS` must not be below `PW_NEW_VALUE_HISTORY_DAYS` and must not exceed
  `PW_BASELINE_LOOKBACK_DAYS`;
- `PW_BOOTSTRAP_SEARCHES_PER_TICK` must not exceed `PW_MAX_SEARCHES_PER_TICK`;
- `PW_BOOTSTRAP_DAYS x 24 + PW_BOOTSTRAP_MAX_HOURS` must stay below `PW_METRIC_RETENTION_DAYS x 24`,
  so that the history a bootstrap reads is still there when it is judged.

The sub-window width, the 80 % row target and the one-hour floor of the planner are constants, not
settings.

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
