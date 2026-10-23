# Data model and measurement semantics

Behavior Analytics stores inventory, aggregates, baselines, incidents and operational state in
PostgreSQL. Time-series tables use TimescaleDB. It does not persist raw QRadar events.

At this revision the SQLAlchemy metadata contains 78 tables. Fifteen are TimescaleDB hypertables:

- `log_source_metric`
- `search_result_metric`
- `rule_metric`
- `offense_snapshot`
- `rule_health_snapshot`
- `detection_coverage_snapshot`
- `waf_endpoint_metric`
- `waf_source_bucket`
- `rule_match_metric`
- `catchall_bucket`
- `geo_country_bucket`
- `ips_source_bucket`
- `ips_entity_metric`
- `pw_bucket`
- `pw_value_metric`

The authoritative model registry is `backend/app/models/__init__.py`; additive Alembic migrations
materialize and evolve the schema.

## Measurement comes before verdict

Every detector declares the telemetry it requires. A provider or collector cannot infer that an
unset DTO field was measured, and a detector cannot grant itself permission to reason from a lower
bound.

The product uses three essential meanings:

- **Exact:** the bounded query returned the whole measurement.
- **Lower bound:** truncation proves at least the stored amount. It can support some increase
  claims, but never an absence or decrease claim.
- **Not collected:** no measurement row exists. It is unknown and never becomes zero.

`INSUFFICIENT_DATA`, `PARTIAL`, `TRUNCATED` and `NOT_COLLECTED` are product states, not presentation
errors. A measured zero is retained and shown as zero. Missing input fails closed rather than
producing a healthy verdict.

## Log sources and source volume

`qradar_instance`, `log_source`, `log_source_type` and `log_source_group` hold synchronized
inventory. SOC-owned criticality, ownership, monitoring and maintenance fields remain separate from
QRadar-owned values refreshed by sync.

`log_source_metric` stores five-minute aggregate buckets. Event volume is `SUM(eventcount)`, not
`COUNT(*)`, because QRadar can coalesce identical events into one Ariel record. Seasonal baselines
use median and median absolute deviation for each weekday-and-hour cell. The supported direct-REST
source-volume findings are `NO_EVENTS`, `VOLUME_DROP` and `VOLUME_SPIKE`.

A bucket read from QRadar's past is marked in `collection_source` (`ariel_history`, or
`mock_history` from the mock provider). The column keeps its live values for live rows
(`ariel_aggregate`, `mock`); the marker is the only difference between the two provenances.

`log_source_anomaly`, detector state and transition rows hold the incident lifecycle. Explanations
store bounded aggregate contributors and per-dimension completeness; raw events do not leave the
folding functions.

`log_source.health_score` and `health_computed_at` are never written and no API or page shows
them; the columns stay because dropping them needs a migration.

## WAF behavior

`waf_source_profile` holds an enabled, versioned field mapping and validation state for one source.
Collection reads only the profile version that is enabled and valid.

`waf_endpoint` is a bounded admitted endpoint identity. `waf_source_bucket` records the source-wide
measurement and completeness; `waf_endpoint_metric` is sparse and exists where endpoint traffic was
observed. Seasonal endpoint baselines, detector state, incidents and transitions are stored in
their own WAF tables.

`waf_source_bucket.origin` is `LIVE` or `HISTORY`: a bucket read from QRadar's past carries
`HISTORY`. The endpoint metric rows of a history bucket are marked by their source bucket, not by
a column of their own.

The WAF status counts the truncated and the lower-bound buckets of the last 24 hours without an
origin condition, so a history bucket of the last day counts into them.

The WAF detectors cover request-rate spikes, blocked-share spikes, server- and client-error share
spikes, client concentration and newly accessed endpoints. Each requires the corresponding
breakdown and completeness. Alerting can remain in shadow mode while incidents continue through
their lifecycle.

Raw paths, action values, response codes and client addresses exist only while pure folds process a
bounded result. Only explicitly bounded contributor lists may leave those folds.

## IPS behavior

`ips_source_profile` maps logical source IP, destination IP, destination port, protocol, signature,
action and severity fields to normalized QRadar fields or custom properties. Mapping changes are
versioned and invalidate collection until validation succeeds.

Destination ports, destinations and signatures are admitted entities with sparse metrics and
seasonal cells. Source IP concentration, port fan-out and action share are source-level facts.
`ips_source_bucket` carries totals, completeness, coverage and bounded contributor facts;
`ips_entity_metric` carries per-entity aggregate observations.

`ips_source_bucket.origin` is `LIVE` or `HISTORY`, as for the other families. The entity metric
rows of a history bucket are marked by their source bucket.

Detector state and incidents cover port and destination rate spikes, signature bursts, new
destination ports, source concentration, port fan-out and block-share shifts. Collection,
evaluation and training run in that order; a failed source is isolated from the rest of the fleet.
Alerting is independently switchable, so incidents can be evaluated in shadow mode.

## Fleet classification and catch-all sources

Classification uses synchronized inventory plus ordered operator rules. Explicit source override
wins over internal, group, type and name matches. Equal-priority or cross-level disagreement is
recorded for review rather than silently resolved.

An outage policy can target a source or source class, optionally for one instance. A source-specific
policy wins over its class. `ALERT` opens the shared alert; `RECORD_ONLY` keeps the source-volume
incident lifecycle without paging.

Catch-all collection folds sender names from configured device types. Admission is bounded by a
minimum event count, per-bucket new-name quota and per-source active ceiling. `catchall_bucket`
stores totals, distinct-sender count and a bounded top-sender list. An absent bucket is unknown;
truncation makes its counts lower bounds.

`catchall_bucket.origin` is `LIVE` or `HISTORY`.

`catchall_sender.events_24h` is recomputed from the stored top-sender lists of every bucket of the
last 24 hours, whatever its origin, so a history bucket of the last day counts into it. A sender
no stored top list named in that window has `NULL`, never zero.

## Rules and coverage

QRadar's analytics-rule inventory does not expose a reliable last-triggered timestamp or complete
dependency information. Stored offense contributions therefore prove only that a rule fired; no
contribution does not prove a zero. Rule health remains `INSUFFICIENT_DATA` until a complete source
can support a zero-window claim.

Measured rule-match windows, rule metrics, state transitions and health snapshots remain separate
from the inventory. ATT&CK coverage likewise reports `NOT_EVALUATED` rather than zero percent when
technique mappings are unavailable.

`rule_match_window.origin` is `LIVE` or `HISTORY`; a window read from QRadar's past is `HISTORY`
and its `rule_match_metric` rows hang on it.

## Scheduled searches

`scheduled_search` holds validated AQL, its cron schedule, limits and next-run state.
`search_query_version` preserves version history. `search_execution` is idempotent by run key and
records completion, failure and truncation. `search_result_metric` stores aggregate results by
default, not raw events.

Search thresholds and repeated failures feed the common alert lifecycle. A failed run creates a
gap in a result series; it is not a zero. Results record the query version so a chart can show where
the query meaning changed.

## Custom threshold rules

`custom_rule` names one persisted subject and one metric from the code-owned catalog. Evaluation is
database-only: it reads previously collected buckets and never adds an Ariel search.

`custom_rule_breach` is the bounded observation trail. It stores bucket bounds, worst and latest
values, exact/lower-bound semantics, unmeasured-bucket count and a bounded subject-label snapshot.
A partial unique index permits only one non-resolved breach per rule.

`custom_rule_watermark` records progress by subject kind. The first evaluator run seeds at the
latest closed bucket and does not replay old history. Unmeasured or maintenance buckets pause both
sustain and recovery counters. Exact recovery observations are required to resolve.

## Property watches

A property watch follows one or two properties over one log source, source type or source group.
The stored definition uses validated field references: a custom property name or a code-owned
normalized-field allowlist. A watch is created disabled.

The family uses:

- `property_watch` for the versioned definition and scope;
- `pw_value` for bounded admitted or pinned values;
- `pw_bucket` and `pw_value_metric` for aggregate observations;
- `pw_value_baseline` for seasonal cells;
- `pw_detector_state` for hysteresis;
- `pw_anomaly` and `pw_anomaly_transition` for incidents.

Scope membership is resolved from synchronized inventory at each run. Empty, stale or oversized
scopes fail closed without storing a zero. A membership change creates a new fingerprint and a
grace interval excluded from baseline and verdicts.

Each bucket stores total events, parsed events and the unparsed remainder. Coverage is
`parsed / total`; the remainder is not admitted as a value. Absence-based findings require exact
measurement and sufficient coverage.

`pw_bucket.origin` is `LIVE` or `HISTORY`. A watch that asked for a history read keeps its state
and its progress on the watch row itself:

| Column | Meaning |
|---|---|
| `history_days` | days requested; NULL when no history was requested |
| `history_state` | `NOT_REQUESTED`, `READING`, `COMPLETE`, `PARTIAL` or `UNAVAILABLE` |
| `history_target_start` | the start of the window, fixed at the first step |
| `history_floor` | the proved start of what QRadar holds for this scope |
| `history_through` | the start of the last history bucket written; the resume point |
| `history_end` | the boundary at hand-off; nothing is proved past `history_through` up to it |
| `history_started_at`, `history_finished_at` | wall clock |
| `history_progress` | the planner's counters and the bounded gap list, as JSON |

A history bucket is evidence for the learning detectors and is never a judged interval. A
threshold rule on a pinned value does not read it either, so a bootstrapped bucket is never
evaluated.

Property-watch findings cover a value spike, drop, disappearance, newly admitted value and a new
combination on a two-property watch. Novelty claims are limited to complete recorded history at the
admission threshold; the platform does not claim that a value has never existed.

A threshold rule may point only to a pinned watch value. This prevents a rule from depending on an
automatic value that could retire under the active-value ceiling. The foreign key restricts a
watch change or deletion while such a rule exists.

## History backfill

The operator answers once per console whether the platform may search QRadar's stored past. Two
tables hold that answer and the state of each reading.

`history_backfill_decision` is one row per console:

| Column | Meaning |
|---|---|
| `instance_id` | the console |
| `answer` | `YES` or `NO` |
| `days` | the window a Yes reads; NULL for a No |
| `decided_at`, `decided_by` | when and by whom; `installer` or a principal subject |
| `source` | `INSTALLER` or `UI` |
| `updated_at`, `updated_by` | a later change from No to Yes |
| `start_requested_at`, `start_requested_by` | "Read history now" |
| `stop_requested_at`, `stop_requested_by` | "Stop reading" |

The two requests exist only on a `YES`. The installer writes this row once and never changes it;
the admin API may change a `NO` into a `YES` and never reverses a `YES`.

`history_backfill_scope` is one row per console, family and scope key. The key is empty for a
console-wide family (source volume, catch-all senders, rule matches) and is the QRadar log-source
id for WAF and IPS, which read per source:

| Column | Meaning |
|---|---|
| `state` | `WAITING`, `READING`, `COMPLETE`, `PARTIAL`, `UNAVAILABLE` or `NOT_ELIGIBLE` |
| `days_requested`, `days_target` | the window asked for and the window that fits |
| `target_start`, `floor`, `through`, `anchor` | the start of the window, the floor, the resume point and the end |
| `started_at`, `finished_at` | wall clock |
| `reason` | words, never a value text |
| `progress` | the planner's counters, the bounded gap list and the per-source floors, as JSON |

`floor` means different things per family and the API says which: for WAF, IPS and rule matches it
is the floor QRadar's own read proved, so nothing before it is written; for source volume and
catch-all it is only where the read started, and the real per-source floors live in `progress`. A
null `floor` proves nothing at all, whatever the family: the API's `floor_proved` is then false.

A scope that is read again ("Read history now") keeps the earlier reading's `days_target`,
`target_start`, `floor` and `through` until its first step replaces them. A stop before that step
therefore cannot erase what the aggregate tables still hold, and the scope's own counters read
`null` until the new reading has searched something.

The collector is the only writer of the scope rows. The API and the CLI write only the decision
row; each family applies a request at its next collection.

## Geography

The geography collector asks QRadar to resolve source or destination addresses to ISO country codes
inside AQL. It never selects or stores an IP address. `geo_country_bucket` stores one row per
instance, bucket, direction and country or remainder. Countries beyond the cap are summed into
`OVER_CAP`.

No row means not collected. `UNRESOLVED` includes private, internal and unknown addresses because
QRadar returns the same empty lookup for all three.

## Dashboards

`dashboard` and `user_dashboard_pref` store a user's layout and default choice. A layout contains
widget IDs, types, geometry and bounded opaque configuration only. The server does not interpret or
execute layout content. Revision tokens prevent lost updates; visibility and ownership control
which principals can read or modify a record.

## Alerts, notifications and audit

The shared alert lifecycle is open, acknowledged and resolved. A partial unique index on the active
deduplication key prevents duplicate unresolved notifications for the same condition. Behavioral
families retain their own incident tables and map transitions into the common alert service.

Notification intent is stored separately from delivery. Workers apply retry and dead-letter
behavior. Administrative changes, stored-search actions, rule changes, profile validation and
operator lifecycle actions append audit rows.

Sensitive configuration uses the custom encrypted-string type. Logs pass through structured
redaction, API schemas exclude secrets, and the frontend receives no QRadar credential.

## Retention

`RETENTION_ENABLED` gates only the four Timescale chunk policies reconciled by
`apply_timescale_policies`: `log_source_metric`, `search_result_metric`, `rule_metric` and
`offense_snapshot`. It defaults to off. Daily batch deleters ignore that flag. A retention task
never interprets a missing bucket as an observation.

| Table | Task | Default | Own gate |
|---|---|---:|---|
| `search_result_metric` | `apply_search_result_retention` | 90 d | none |
| `catchall_bucket`, `catchall_sender` | `apply_catchall_retention` | 35 d | none |
| `geo_country_bucket` | `apply_geo_retention` | 35 d | none |
| `waf_endpoint_metric`, `waf_source_bucket`, `waf_endpoint_baseline`, retired `waf_endpoint` | `apply_waf_retention` | 35 d | `WAF_RETENTION_ENABLED` |
| `waf_anomaly` (terminal) | `apply_waf_retention` | 180 d | `WAF_RETENTION_ENABLED` |
| `ips_entity_metric`, `ips_source_bucket`, retired unreferenced `ips_entity` | `apply_ips_retention` | 35 d | `IPS_RETENTION_ENABLED` |
| `ips_anomaly` (terminal) | `apply_ips_anomaly_retention` | 365 d | none |
| `pw_value_metric`, `pw_bucket`, retired `pw_value` | `apply_property_watch_retention` | 35 d | none |
| `pw_anomaly` (terminal) | `apply_property_watch_retention` | 180 d | none |
| `anomaly_evaluation_fault` (closed) | `apply_anomaly_fault_retention` | 30 d | none |
| `custom_rule_breach` (resolved) | `apply_custom_rule_retention` | 180 d | none |
| `log_source_anomaly` (terminal) | `apply_log_source_anomaly_retention` | 365 d | none |
| `rule_health_snapshot` | `apply_snapshot_retention` | 90 d | none |
| `detection_coverage_snapshot` | `apply_snapshot_retention` | 90 d | none |
| `rule_match_metric`, `rule_match_window` | `apply_rule_match_retention` | 90 d | none |

Terminal incident states are RESOLVED, NORMAL and INSUFFICIENT_DATA. Age is
`COALESCE(resolved_at, anomaly_end, detected_at)`. CANDIDATE, OPEN, RECOVERING and SUPPRESSED
are kept. Phase A and IPS incident deletes skip a row while a detector state still points at it
(`active_anomaly_id` or `open_anomaly_id`, any detector state) or any alert lists the id in
`source_anomaly_ids`, whatever the alert status. Nothing deletes an alert, and the alert detail
links to the incident through that list, so the incident is kept for the life of the alert.
`Alert.evidence_snapshot` is the alert's own copy and is not a guard. `Alert.source_id` is the
log source, IPS entity or IPS source, not the incident, and is not a guard. Property-watch
incidents still use an unresolved-alert guard plus a detector guard; that task does run
(`apply_property_watch_retention`).

Children that exist only for the incident leave in the same delete by `ON DELETE CASCADE`, not as
a skip: `anomaly_state_transition`, `anomaly_explanation`, `anomaly_explanation_dimension`,
`anomaly_explanation_contributor` for Phase A; `ips_anomaly_transition` for IPS. IPS has no
separate evidence table. Snapshot deletes have no reference guard. Their foreign keys point at
the rule or the instance, so a snapshot delete does not delete the parent.

`rule_match_window` and `rule_match_metric` are deleted by age by `apply_rule_match_retention`,
like every other measured table: windows by `window_start`, metrics by `bucket_start`. A startup
check refuses a `RULE_MATCH_RETENTION_DAYS` that does not exceed `RULE_HEALTH_WINDOW_DAYS`, the
longest reader's window, so the fitted history window stays inside the retention.

Retention deletes a bucket by age whatever its origin, so a history bucket is deleted with the
live ones of the same age. The history tables themselves (`history_backfill_decision`,
`history_backfill_scope`) are not deleted by age.
