# Data model and measurement semantics

Behavior Analytics stores inventory, aggregates, baselines, incidents and operational state in
PostgreSQL. Time-series tables use TimescaleDB. It does not persist raw QRadar events.

At this revision the SQLAlchemy metadata contains 77 tables. Fifteen are TimescaleDB hypertables:

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

`log_source_anomaly`, detector state and transition rows hold the incident lifecycle. Explanations
store bounded aggregate contributors and per-dimension completeness; raw events do not leave the
folding functions.

A pure 0–100 health-score function exists for freshness, volume, parsing and collection inputs, but
the live collector does not populate all of those inputs and does not call that score. It must not
be interpreted as a collected live health measure.

## WAF behavior

`waf_source_profile` holds an enabled, versioned field mapping and validation state for one source.
Collection reads only the profile version that is enabled and valid.

`waf_endpoint` is a bounded admitted endpoint identity. `waf_source_bucket` records the source-wide
measurement and completeness; `waf_endpoint_metric` is sparse and exists where endpoint traffic was
observed. Seasonal endpoint baselines, detector state, incidents and transitions are stored in
their own WAF tables.

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

## Rules and coverage

QRadar's analytics-rule inventory does not expose a reliable last-triggered timestamp or complete
dependency information. Stored offense contributions therefore prove only that a rule fired; no
contribution does not prove a zero. Rule health remains `INSUFFICIENT_DATA` until a complete source
can support a zero-window claim.

Measured rule-match windows, rule metrics, state transitions and health snapshots remain separate
from the inventory. ATT&CK coverage likewise reports `NOT_EVALUATED` rather than zero percent when
technique mappings are unavailable.

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

Property-watch findings cover a value spike, drop, disappearance, newly admitted value and a new
combination on a two-property watch. Novelty claims are limited to complete recorded history at the
admission threshold; the platform does not claim that a value has never existed.

A threshold rule may point only to a pinned watch value. This prevents a rule from depending on an
automatic value that could retire under the active-value ceiling. The foreign key restricts a
watch change or deletion while such a rule exists.

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

General Timescale chunk retention is disabled unless the operator enables it and supplies explicit
day counts. WAF, IPS, catch-all, geography, search-result, custom-rule and property-watch families
have separate bounded batch-retention settings. A retention task never interprets a missing bucket
as an observation.

Property-watch resolved-incident retention is declared in configuration but is not currently
applied by a task; those incident rows accumulate until that implementation exists.
