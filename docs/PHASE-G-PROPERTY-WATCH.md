# Phase G — property watches: track any property's values on a scope (2026-10-17)

Status: **building, 2026-10-17** (designed by T-125; PW-1 foundations built). The build
(PW-1…, §G.16) starts only after the operations work (OPS-5, OPS-6,
OPS-9 round 2, OPS-10), by the user's order of 2026-10-17 (§G.16). It follows the shape of
[`PHASE-D-IPS-BEHAVIOR.md`](PHASE-D-IPS-BEHAVIOR.md) and
[`PHASE-F-CUSTOM-DETECTIONS.md`](PHASE-F-CUSTOM-DETECTIONS.md) and rests on
[`SEARCH-CADENCE.md`](SEARCH-CADENCE.md) (the search cadence), [`PHASE-B-WAF-BEHAVIOR.md`](PHASE-B-WAF-BEHAVIOR.md)
§29–§30 (measurement semantics, admission, novelty, bulk loaders), the dashboard's widget system
and [`PRODUCT-UX-GUIDELINES.md`](PRODUCT-UX-GUIDELINES.md). Every claim about the code carries a
`file:line` citation checked on 2026-10-17.

---

## G.0 What the user asked (2026-10-17)

> "operöttörün herhangi bir log source veya log source type veya log group'u seçip şu custom
> property değerini veya değerlerini takip et diyebilir miyiz bence deriz altyapı hazır"

The operator picks any **log source**, **log source type** or **log source group**, and says "track
this custom property's value, or values". The user believes the infrastructure is ready.

The user's answers at 12:55 on 2026-10-17 (binding):

1. **Outputs: all four.** The per-value distribution (a table, a time-series chart on the watch's
   page and a dashboard widget); **threshold alerts** as a new F.1 subject kind ("value DENY above
   500 per bucket, sustained 3 buckets"); **new-value detection** (the WAF new-endpoint idea); and
   **baseline deviation per value** — spike, drop, disappearance (the Phase A idea).
2. **Values: both.** Pinned values are always counted (a measured zero when the search can prove
   it); the most frequent other values are tracked automatically, top-N.
3. **Combination and correlation: wanted** ("korelasyon ve kombinasyon iyi olur"). Combination: one
   watch groups by more than one property (`Username` × `Action`). Correlation: undefined by the
   user — §G.9 defines it precisely, proposes a bounded v1 and puts the alternatives in the open
   questions (§G.17).
4. **Order:** design now, build after OPS-5, OPS-6, OPS-9 round 2 and OPS-10.

The one-sentence answer to the user's question is **yes**: every ingredient exists — the synced
inventory with groups, the per-type custom-property catalog, validated field references, keyed
fleet-wide searches with truncation splits, entity admission, seasonal baselines, the lifecycle,
shadow-mode alerting and the F.1 rule engine. What is missing is the watch itself: the model, the
collector, the detectors, the API and the UI (§G.16 builds exactly those).

---

## G.1 Facts that constrain the design

Verified on this branch, `file:line`. Facts about the QRadar console were settled by the PW-0 lab probe
(L1 to L8, summarised in §G.17).

### G.1.1 The inventory already knows sources, types and groups

- `LogSource` mirrors QRadar's per-source facts: `qradar_id`, `type_id`, `type_name`
  (`backend/app/models/log_source.py:59-63`) and **`group_ids`** as a JSONB list
  (`backend/app/models/log_source.py:73`).
- `LogSourceType` is synced per instance with `type_id` (the QRadar `devicetype` id), `name`,
  `internal` and `custom` flags (`backend/app/models/fleet.py:21-33`).
- `LogSourceGroup` is synced per instance with `qradar_id`, `name`, `description`,
  `parent_qradar_id` and `assignable` (`backend/app/models/fleet.py:36-49`). Groups are therefore a tree. Whether a source's `group_ids` lists the ancestors of its groups could not be shown on the lab (L2): §G.4 resolves descendants explicitly, with a cycle-safe walk. The classifier already
  reads sources, types and groups together in one pass, one statement each (`backend/app/fleet/service.py:232-253`).

### G.1.2 The custom-property catalog is per log-source type, names only

- `QRadarProvider.list_custom_properties` returns names and **the log-source types each property's
  enabled expressions cover**, never a regex or payload body
  (`backend/app/providers/base.py:239-249`). The REST implementation joins `regex_properties` to
  `property_expressions` with `fields=` so no expression body is ever requested
  (`backend/app/providers/qradar_rest.py:567-594`) and exposes `log_source_type_ids` per property
  (`backend/app/providers/qradar_rest.py:623`).
- The catalog is cached per instance for a TTL (`qradar_property_catalog_ttl_seconds`, 600 s,
  `backend/app/core/config.py:161`) in a process-local cache
  (`backend/app/services/property_catalog.py:27-36,85-95`), and is **a suggestion, never a gate**
  (`backend/app/services/property_catalog.py:4-7`): any name may be typed and validation decides.
- Two admin routes already serve it filtered by one log source's type:
  `GET /waf/log-sources/{id}/property-catalog` (`backend/app/api/routes/waf_catalog.py:24-28`) and
  the IPS twin (`backend/app/api/routes/ips.py:488-493`). The searchable picker is the T-058
  `PropertyInput` component (`frontend/src/components/fieldmapping/PropertyInput.tsx`).

### G.1.3 Field references of two kinds are validated and quoted before they reach AQL

- The IPS profile introduced `(kind, name)` references: `NORMALIZED` (exact membership in a fixed
  allowlist) or `CUSTOM_PROPERTY` (the name pattern), validated in the dataclass constructor
  (`backend/app/ips/profile.py:100-116`).
- `PROPERTY_NAME_PATTERN` is the only shape a site-authored property name may take before
  interpolation into AQL — letters, digits, underscore, space, hyphen, at most 64 characters;
  anything else is **rejected, not escaped** (`backend/app/providers/base.py:60-65`).
  `_quote_property` enforces it and double-quotes the name
  (`backend/app/providers/qradar_rest.py:1056-1062`); `_ips_reference` turns a validated reference
  into a quoted expression (`backend/app/providers/qradar_rest.py:1516-1530`).
- The IPS normalized allowlist is `sourceip, destinationip, destinationport, sourceport,
  protocolid, qid, severity, category` (`backend/app/providers/base.py:73-82`). Notably it contains
  no `username`. Grouping by the normalized `username` is, however, already generated by the Phase A
  explanation path (`backend/app/providers/qradar_rest.py:787-798`; the dimension is listed at
  `docs/PHASE-A-SOURCE-VOLUME-ANOMALY.md:146-148`). No document in the repository records a live run
  of that dimension; the lab probe has since run it (L6, §G.17).

### G.1.4 Keyed, bucketed, fleet-wide searches with truncation splits and a source cap exist

- `get_ips_keyed` is the precedent: one search for a whole group of sources over a window of whole
  buckets, `LIMIT cap + 1`, more than `cap` rows ⇒ `truncated`
  (`backend/app/providers/base.py:488-514`; the REST query with `WHERE logsourceid IN (…)` and
  `GROUP BY … , starttime / bucket_ms` at `backend/app/providers/qradar_rest.py:1587-1599`).
- The split driver keeps nothing from a truncated search: **time is split first, then sources; at
  one source and one bucket the top rows are exact and the tail missing, so the cell is a
  `LOWER_BOUND`; a failed search is not split; a spent budget leaves cells `NOT_COLLECTED`, never
  zero** (`backend/app/ips/keyed_search.py:1-18`, the budget at `:35-52`).
- Sources per keyed search are capped (`ips_max_sources_per_search`, 50; a larger group is chunked,
  `backend/app/core/config.py:163-165`; enforced at
  `backend/app/providers/qradar_rest.py:1620-1622`).
- A **`devicetype` filter** is in use by the catch-all senders search
  (`WHERE devicetype IN (…)`, `backend/app/providers/qradar_rest.py:1665-1670`; the device-type id
  setting is described at [CONFIGURATION.md](CONFIGURATION.md) "Catch-all senders"). The lab probe ran `devicetype = <id>` together with a
  quoted custom property and the bucket key in one query and lost no events (L3).
- `LIMIT` caps the **whole result, not each bucket** — there is no per-group top-N in AQL
  (`docs/SEARCH-CADENCE.md` §SC.2). `GROUP BY` a quoted custom property together with the bucket
  key works live (`docs/SEARCH-CADENCE.md` §SC.10).
- **Group filter (L1).** The repository generates only `logsourceid`
  (`backend/app/providers/qradar_rest.py:1589`) and `devicetype`
  (`backend/app/providers/qradar_rest.py:1668`) filters. The lab accepted
  `WHERE LOGSOURCEGROUPNAME(logsourceid) = '<name>'` (by group **name**; five other forms were
  rejected). §G.4 keeps the inventory-resolved `logsourceid IN (…)` as the design anyway.
- **String literals (L4).** Equality and `IN ('a','b')` against a string custom property are
  accepted and exact on the lab. A backslash escape is rejected; a doubled quote is accepted but
  whether it matches a real value containing a quote is unproven. §G.6 therefore generates `IN`
  and refuses, rather than escapes, a pinned value that contains a quote, a backslash or a control
  character. The only literal in the generated AQL today is the IPS loopback filter `!= '::1'` on an
  IP-typed field (`backend/app/providers/qradar_rest.py:1494-1497`).
- **A `LIMIT` cut is invisible (L8).** A search cut by `LIMIT` finishes `COMPLETED` with no
  truncation flag; the only signal is `rows == LIMIT`. Every PW search therefore carries
  `LIMIT cap + 1`, and more than `cap` rows is `truncated` (§G.6).
- **Counts arrive as floats (L6).** The lab returns `SUM(eventcount)` and `COUNT(*)` as JSON
  floats. The §G.6 fold accepts only integer-valued floats.

### G.1.5 Bounded admission, bounded contributor lists and measured zeros are solved problems

- `IpsEntity` is the entity pattern: natural key `(instance, log source, dimension, value_key)` with
  a non-empty `value_key` of at most 255 characters (`backend/app/models/ips.py:206-241`,
  `value_key` at `:233`), admitted under a per-bucket quota, an active ceiling, a minimum event
  count and retirement after idle days (`backend/app/core/config.py:174-179`). WAF endpoint
  admission records its outcome counts on the bucket row (`backend/app/models/waf.py:217-231`).
- Bounded top-N lists are already persisted: `catchall_bucket.top_senders`, at most
  `catchall_top_senders` entries busiest first (`backend/app/models/catchall.py:120-121`), and the
  geo `OVER_CAP` remainder that sums what the per-bucket cap refused
  (`backend/app/models/geo.py:32-56`). Sender identifiers are sanitized to printable, stripped,
  at most 255 (`backend/app/models/catchall.py:71-72`).
- A **measured zero** is reconstructed, not stored: a tracked entity absent from an exact bucket has
  zero; absent from a lower-bound bucket it is unknown (WAF sparse storage,
  `docs/PHASE-B-WAF-BEHAVIOR.md` §29.4; the tracking window `tracked_since`/`tracked_until` at
  `backend/app/models/waf.py:371-403`). A whole source volume and a whole grouped result are exact
  whenever the row exists (`backend/app/models/waf.py:149-159`).
- Per-dimension completeness with `EXACT`/`LOWER_BOUND` semantics and which verdicts a lower bound
  may support are first-class (`docs/PHASE-B-WAF-BEHAVIOR.md` §29.2).

### G.1.6 The detection vocabulary, the lifecycle and shadow-mode alerting are shared

- `DetectorSpec` declares a detector's telemetry, baseline metrics, schedule, per-input
  measurement requirements and limitation, and `DetectorStatus` publishes the deployment truth
  (`backend/app/anomaly/capabilities.py:66-77,103-147`). A verdict is only as strong as the
  narrowest input; the engine computes the scope, the detector never grants it to itself.
- The incident lifecycle `CANDIDATE → OPEN → RECOVERING → RESOLVED` with per-transition rows and
  an UNKNOWN bucket holding state is the Phase A/WAF/IPS contract
  (`docs/PHASE-A-SOURCE-VOLUME-ANOMALY.md` §5).
- Shadow-mode alerting is the pattern: `ips_alerting_enabled` default **false**
  (`backend/app/core/config.py:172-173`), WAF and F.1 the same
  (`docs/PHASE-B-WAF-BEHAVIOR.md` §30.7; `docs/PHASE-F-CUSTOM-DETECTIONS.md` §F.6). Fingerprints
  are per-family natural keys: `waf_anomaly_fingerprint`
  (`backend/app/alerts/fingerprint.py:95-122`), `ips_anomaly_fingerprint`
  (`backend/app/alerts/fingerprint.py:125-144`), `custom_rule_breach_fingerprint`
  (`backend/app/alerts/fingerprint.py:155-167`).

### G.1.7 F.1 and F.2 are built and extendable

- F.1 rules pin one subject from `CustomRuleSubjectKind` (`LOG_SOURCE`, `FLEET_CLASS`,
  `WAF_ENDPOINT`, `IPS_ENTITY`, `IPS_SOURCE`, `backend/app/models/enums.py:198-211`), enforced by a
  column-pairing `CHECK` (`backend/app/models/custom_rule.py:46-57`), against a ten-entry metric
  catalog that states unit, columns, measurement rule and bulk loader per metric
  (`backend/app/custom_rules/catalog.py:36-52,55-207`). Evaluation is DB-only on a 60 s beat behind
  `CUSTOM_RULES_ENABLED` (`backend/app/workers/celery_app.py:186-193`).
- The unified anomaly list is `subject_kind`-shaped and was designed to grow a family: "A new
  family (design Phase D: IPS subjects) adds a member and a branch in the repository's union"
  (`backend/app/schemas/anomaly.py:40-51`; the query filter at
  `backend/app/api/routes/anomalies.py:149`). F.1 breaches deliberately stay **out** of
  `/anomalies` (`docs/PHASE-F-CUSTOM-DETECTIONS.md` §F.16, decided 2026-10-16).
- F.2 scheduled searches store validated, versioned AQL (`backend/app/models/search.py:29-110`;
  the validator's banned-keyword and dataset rules at
  `backend/app/services/aql_validator.py:32-40`) and store aggregated results only
  (`backend/app/models/search.py:167-193`). They are operator AQL on a cron with a threshold on a
  total — they have no buckets, no watermark, no truncation split, no admission, no baselines and
  no lifecycle (§G.6 uses them for nothing).

### G.1.8 Cadence, watermark and cost machinery

- Every family claims its slot through `SearchCadenceService.claim_slot`
  (`backend/app/services/search_cadence.py:61`) and records its run (`:134`); the IPS dispatch is
  the template (`backend/app/workers/tasks.py:1335-1351`). The family set is
  `SearchFamily` (`backend/app/models/enums.py:100-107`); a family added later seeds its row at
  1 h in its own migration, and migration `0028` moves an untouched seed
  (`updated_by IS NULL`) to the 5-minute default
  (`backend/alembic/versions/0028_default_search_cadence.py`).
- The settle delay is 120 s (`backend/app/core/config.py:157-158`), the per-tick search budget 16
  (`backend/app/core/config.py:159`), the bucket width `collection_interval_seconds` = 300 s
  (`backend/app/core/config.py:237`). WAF's steady-state run costs a constant 36 SQL statements at 100 to 2,000 endpoints, measured on a
  test database with no Ariel time (`docs/PHASE-B-WAF-BEHAVIOR.md` §29.7) — the bar a PW run must meet.
- The migration head on this branch is `0025_auth_sessions` (`backend/alembic/versions/`); Phase G
  takes the next number at build time.

---

## G.2 Scope

**In:** the watch model and its administration; scope resolution for one log source, one type or
one group; one or two property references per watch; pinned values plus an automatic top-N of the
rest; collection under a new `PROPERTY_WATCH` search family; the per-value distribution (table,
chart, widget); baseline deviation per value (spike, drop, disappearance); new-value detection;
new-combination detection as the bounded v1 correlation; F.1 threshold rules on a watch value;
shadow-mode alerting; the API, permissions and audit; the UI and the widget; retention.

**Out:** raw events (only value aggregates and the bounded lists leave the
folds); frontend-supplied AQL (the server generates every query from validated
parameters); extending `AnomalyType` (a new `PwAnomalyType`); QRadar-side correlation
rules, reference-set logic or any write to QRadar; per-source-IP or unbounded-cardinality
subjects (the same exclusion as IPS, `docs/PHASE-D-IPS-BEHAVIOR.md` D.2); intent claims — a new
value may be a deployment, a new combination may be a role change; alerting within seconds
(the cadence trade, `docs/PHASE-F-CUSTOM-DETECTIONS.md` §F.1.3); co-movement across watches
(§G.9 weighs it and leaves it out of v1).

---

## G.3 The watch model (question 1) **[BUILT — PW-1 foundation]**

> **As built by PW-1** (`backend/app/models/property_watch.py`,
> `backend/app/models/enums.py`, `backend/app/property_watch/reference.py`, migration `0026`):
> the row, scope and one-or-two-properties `CHECK`s, separate PW enum vocabulary,
> `watch_version`, the `username`-only normalized allowlist and the reject-not-escape pin pattern
> are present. The severity, version, enabled and value-state defaults are SQLAlchemy creation
> defaults rather than persistent database defaults, preserving the repository's migration
> invariant that fresh tables leave no non-timestamp server defaults behind. The API rules that
> decide when to bump `watch_version` remain PW-5.

```text
property_watch
  id UUID PK, created_at, updated_at
  instance_id UUID FK qradar_instance (cascade)
  name String(80) UNIQUE NOT NULL, description Text NULL
  scope_kind String(16) NOT NULL   -- LOG_SOURCE | LOG_SOURCE_TYPE | LOG_SOURCE_GROUP
  log_source_id UUID FK log_source NULL        -- set iff LOG_SOURCE
  scope_type_id Integer NULL                   -- set iff LOG_SOURCE_TYPE (a QRadar devicetype id)
  scope_group_id Integer NULL                  -- set iff LOG_SOURCE_GROUP (a QRadar group id)
  properties JSONB NOT NULL      -- ordered [{kind: "CUSTOM_PROPERTY"|"NORMALIZED", name: str}, …]
  pinned_values JSONB NOT NULL   -- list[list[str]]: one entry per pinned value, one string per property
                                 --   (a single-property watch pins [["DENY"]]); each passes PW_PINNED_VALUE_PATTERN
  detectors JSONB NOT NULL       -- the PwAnomalyType values this watch evaluates (default: all that apply)
  severity String(16) NOT NULL DEFAULT 'MEDIUM'   -- Severity, app/models/enums.py:92-98, for its incidents
  watch_version Integer NOT NULL DEFAULT 1
  enabled Boolean NOT NULL DEFAULT false
  collected_through DateTime(tz) NULL         -- bucket start of the last collected bucket
  evaluated_through DateTime(tz) NULL         -- bucket start of the last evaluated bucket
  last_status String(16) NULL, last_status_reason Text NULL, last_run_at DateTime(tz) NULL
  created_by, updated_by String(255)
  CHECK (exactly one scope column set), CHECK (jsonb_array_length(properties) BETWEEN 1 AND 2)
```

Design decisions, each against the facts of §G.1:

- **Scope is exactly one of source, type or group**, enforced by a `CHECK` in the
  `CUSTOM_RULE_SUBJECT_CHECK` pattern (`backend/app/models/custom_rule.py:46-57`). A type is stored
  as the QRadar `devicetype` id (`LogSourceType.type_id`, `backend/app/models/fleet.py:29`), a
  group as its QRadar id (`LogSourceGroup.qradar_id`, `backend/app/models/fleet.py:44`) — both are
  what the synced inventory and the generated AQL speak.
- **Property references are the IPS `(kind, name)` shape** (`backend/app/ips/profile.py:100-116`):
  `CUSTOM_PROPERTY` validated by `PROPERTY_NAME_PATTERN`
  (`backend/app/providers/base.py:60-65`) and `NORMALIZED` by exact membership in a **new, separate** allowlist `PW_NORMALIZED_FIELD_ALLOWLIST`.
  **Normalized fields belong in v1, string-valued ones only, and v1 ships exactly one: `username`.**
  It is the natural watch target and the Phase A explanation path already generates a `GROUP BY` on it
  (`backend/app/providers/qradar_rest.py:794`, the dimension map at `:787-798`;
  `docs/PHASE-A-SOURCE-VOLUME-ANOMALY.md:146-148`), though its live behaviour is
  verified on the lab, L6 (§G.1.4).
  `category` is deliberately not offered: it is a
  numeric QRadar id (the explanation map pairs it with `CATEGORYNAME(category)` to get a label,
  `backend/app/providers/qradar_rest.py:796`; the IPS allowlist keeps it beside the other numeric fields,
  `backend/app/providers/base.py:73-82`), and IP-typed and numeric normalized fields are excluded
  because absent values are `0`/`255`, not NULL, and IP fields reject `LIKE`
  (`docs/PHASE-D-IPS-BEHAVIOR.md` D.1; the per-field population filters IPS needs exist because of
  this, `backend/app/providers/qradar_rest.py:1492-1505`). `event_name` (`QIDNAME(qid)`, a string
  expression, `backend/app/providers/qradar_rest.py:789`) is a candidate for v1.1, not v1: it is an
  expression and not a field, so its population filter is unproven (§G.17 Q9). The PW allowlist never
  shares the IPS one: different domain, different failure modes. Custom properties of any
  `property_type` may be watched; their values are text in the fold either way, and a catalog
  entry whose `name_allowed` is false (`backend/app/providers/dto.py:83-86`,
  `backend/app/providers/qradar_rest.py:624`) is shown disabled with the reason, because its name
  could never pass `PROPERTY_NAME_PATTERN`.
- **The catalog is a suggestion, never a gate** (`backend/app/services/property_catalog.py:4-7`):
  the editor offers the per-type catalog and any name may be typed; the reference pattern decides,
  and the preview (§G.13) shows whether the property actually parses on the scope.
- **At most 2 properties per watch in v1** (`CHECK`, and `pw_max_properties_per_watch` = 2). One
  property is the base case; two is the user's combination (`Username` × `Action`). Three or more
  are refused: the combination space is the product of the per-property cardinalities, so each cell
  thins out and its weekday×hour cells starve below `baseline_min_samples` exactly as 1-hour buckets
  would (the argument of `docs/SEARCH-CADENCE.md` §SC.1) — a watch that can never train is a trap,
  not a feature. Raising the cap is an open question (§G.17 Q3), not a setting.
- **The cardinality caps that follow** (all settings, §G.14): values kept per bucket
  `pw_max_values_per_bucket` = 50 after the fold's top-N cut; new values admitted per bucket
  `pw_max_new_values_per_bucket` = 20; active values per watch `pw_max_active_values_per_watch` =
  200; pinned values `pw_max_pinned_values` = 10. On a combination watch the "value" is the tuple,
  so the same caps bound the tuple space.
- **`watch_version` is the semantics identity.** Changing the scope or the property set bumps it
  (the WAF metric-semantics rule, `docs/PHASE-B-WAF-BEHAVIOR.md` §28.8): a value of `Username`
  means something else after the property set changes, so baselines, novelty history and value
  entities are stamped with the version and never compare across it. Changing only `pinned_values`
  or `enabled` does not bump it.
- **Top-N is a settings-level bound, not a per-watch field.** The user asked for "the most frequent
  other values, top-N"; one bound for every watch keeps the per-run row budget and the storage
  ceiling computable (§G.14). A per-watch override would let one watch quietly consume the tick's
  search budget.
- **Cadence is the family's, not the watch's.** The task lists cadence among the watch fields, but
  Cadence is set on the search *family* (decided 2026-10-12), one row in `search_cadence` per family, claimed
  atomically per slot (`backend/app/services/search_cadence.py:61`, `docs/SEARCH-CADENCE.md` §SC.4).
  A per-watch cadence would need a slot per watch and would let one watch multiply the Ariel
  schedule. The operator sets `PROPERTY_WATCH` on `/admin/search-cadence` (5 min to 8 h, default
  5 min) and the watch page shows the cadence that applies. §G.17 Q13 asks whether that is enough.
- **Status vocabulary** (`last_status`): `NOT_ENABLED` (family switch off), `DISABLED` (the
  watch's own switch), `SCOPE_EMPTY` (a group or type whose synced membership resolved to no
  sources), `SCOPE_STALE` (§G.4), `SCOPE_TOO_LARGE` (§G.14), `NOT_APPLICABLE` (§G.5), `STALE` (the
  latest search failed and stored its bucket `NOT_COLLECTED`; the bucket is named in the reason, and
  a later collected bucket clears the status), `OPEN`/`RECOVERING`
  (a live incident exists), `OK` (the last collected bucket was judged). A never-run watch is
  `NULL`, which is not `OK`. Every state is a reason, never silence — the detector-status contract
  (`backend/app/anomaly/capabilities.py:66-77`).

## G.4 Scope resolution (question 2) **[BUILT — PW-2a: AQL] [BUILT — PW-2b] [BUILT — PW-3c: staleness]**

> **As built by PW-2a** (`backend/app/property_watch/aql.py`): the `<scope>` filter of the table
> below, from an already-resolved input: `LOG_SOURCE` and `LOG_SOURCE_TYPE` take exactly one id,
> `LOG_SOURCE_GROUP` takes one chunk of at most `pw_max_sources_per_search` ids (sorted,
> de-duplicated). Scope ids must be non-negative integers and are re-checked in the provider.
> Resolution from the inventory (the cycle-safe walk, `SCOPE_EMPTY`/`SCOPE_STALE`/
> `SCOPE_TOO_LARGE`, the fingerprint, the chunking itself) is built by **PW-2b**. The chunk sums
> are built (`keyed_search.combine_chunks`): a bucket is `COMPLETE` only when every chunk was whole,
> `LOWER_BOUND` when any chunk was cut at one bucket, and **unmeasured when any chunk was failed or
> not collected** (a decision PW-2a made: a sum over part of the scope is not a statement about the
> scope, so the other chunks' rows are dropped rather than kept as a lower bound).

At each cadence tick, per enabled watch, the collector resolves the scope to a source-id list and
builds the searches of §G.6:

| Scope kind | Resolution | AQL |
|---|---|---|
| `LOG_SOURCE` | the source's `qradar_id` (`backend/app/models/log_source.py:59`) | `WHERE logsourceid IN (<id>)` |
| `LOG_SOURCE_TYPE` | the type id as stored (`backend/app/models/fleet.py:29`) | `WHERE devicetype = <id>` — the catch-all precedent (`backend/app/providers/qradar_rest.py:1668`) |
| `LOG_SOURCE_GROUP` | member `qradar_id`s from the synced inventory: every `LogSource` of the instance whose `group_ids` (`backend/app/models/log_source.py:73`) contains the group id **or the id of any descendant group** (the tree is `LogSourceGroup.parent_qradar_id`, `backend/app/models/fleet.py:36-49`; descendants are computed in memory from the one groups read) | `WHERE logsourceid IN (<ids>)`, chunked |

The descendant rule is a choice made under uncertainty: the lab could not show whether QRadar lists
a parent group in a source's `group_ids` (L2: not testable, indicative no). Including
descendants is correct either way, and the editor shows the resolved member count and names the
groups it walked. **The walk is cycle-safe**: it keeps a visited set of group ids. The lab's own two
groups name each other as parent (id 1 has parent 0, id 0 has parent 1), and id 0 is both a real group
and a `parent_id` value; an unguarded walk would never end. The group must exist in the synced
inventory when the watch is saved and when it runs; a vanished group id is `SCOPE_EMPTY` (below).

- **Groups: resolved from the inventory, by id (L1).** The lab accepts
  `WHERE LOGSOURCEGROUPNAME(logsourceid) = '<name>'` and rejects five other forms, but it matches by
  **name** (not unique, broken by a rename), and the lab has one populated group so it cannot show
  nested or multi-group behaviour. The default is therefore the inventory-resolved
  `logsourceid IN (<ids>)`: by id, rename-proof, re-resolved at every run, and needed anyway for
  descendants and chunking. `LOGSOURCEGROUPNAME` is at most a later provider-internal optimisation,
  invisible to the model.
- **Chunking under the source cap.** A resolved group larger than `pw_max_sources_per_search`
  (50, the `ips_max_sources_per_search` precedent, `backend/app/core/config.py:163-165`) is
  chunked into consecutive searches, as IPS plans source groups
  (`backend/app/ips/collection_plan.py:20-37,88`). Unlike IPS, the PW searches do **not** group by
  `logsourceid` (§G.6): a watch reports the scope's values, not each source's, and a per-source
  key would multiply the result by the member count for no output. The chunks' per-value sums are
  added in the fold; a value is exact only if every chunk's search was whole, otherwise the bucket
  is a `LOWER_BOUND` (a chunk's missing tail can only add events). A type scope is one filter and is
  never chunked. A scope that outgrows the cap therefore costs more searches, never wrong numbers.
  Whether one `IN (…)` list of 50 ids is the right size on the lab console is
  The lab accepted a 54-id list of 453 characters (L7); the setting is the knob.
- **Membership churn.** A source gained or lost by the group changes the population the watch
  measures. That is honest: the watch measures the scope *as currently synced*. It must not look like
  behavior, though. The bucket row stores `scope_member_count` and `scope_fingerprint` (a hash of
  the sorted resolved ids; a type scope stores the count of synced sources of that type and no
  fingerprint, because membership of a type is decided on the appliance and a new source of the type
  is part of what the scope means). The first bucket under a new fingerprint, and the
  `pw_scope_change_grace_buckets` (12) after it, are flagged `scope_changed`: no spike, drop,
  disappearance or new-value verdict is concluded in them (`INSUFFICIENT_DATA`, reason
  "membership changed"), and the baseline builder excludes them and tallies them as `scope_changed`
  (the `incompatible_semantics` pattern, `docs/PHASE-B-WAF-BEHAVIOR.md` §30.3). Baselines are
  **not** thrown away: after the grace the watch is judged against the old cells again, so a large
  membership change keeps showing as a shift until the cells retrain (§G.17 Q10 offers a relearn
  switch). The watch page shows the current member count and when it last changed.
- **Stale sync fails closed.** Inventory freshness is the **oldest** `synced_at` of the groups the
  walk visited (`backend/app/models/fleet.py:49`; **PW-3c**, the interrupted-sync fix). One sync pass
  stamps every type and group row with the same time (`backend/app/services/inventory_sync.py:50,79-150`),
  and a failed group listing is **swallowed** (`backend/app/services/inventory_sync.py:73-75`):
  sources keep syncing while the group rows keep their old `synced_at`, and a group deleted on the
  console is never deleted here (the pass only upserts). An age check on `synced_at` therefore
  catches exactly the cases in which `group_ids` can no longer be trusted. An interrupted sync can
  leave a descendant older than the named group, so the check reads the oldest walked row — the same
  row `/scope` reports — and a group scope whose oldest walked row is older than
  `pw_scope_stale_after_hours` (default 24) writes **no bucket**; the watch takes `SCOPE_STALE`
  with the inventory age, existing incidents hold state (missing data is not recovery, Phase A §5),
  and the next tick retries. A type scope is one appliance-side filter and needs no membership
  freshness; its type row's `synced_at` (`backend/app/models/fleet.py:33`) is only checked when the
  watch is saved. A group that resolves to **zero** member sources yields `SCOPE_EMPTY`: no bucket,
  no zero, with the reason named.
- **The source cap versus the budget.** Every chunk consumes the family's per-tick search budget
  (`pw_max_searches_per_tick`, §G.14). When the budget is spent, the remaining buckets are
  `NOT_COLLECTED`, never zero, and the watermark holds — the IPS `IpsSearchBudget` behaviour
  (`backend/app/ips/keyed_search.py:14-15,35-52`). The next tick resumes from the watermark.

## G.5 Property applicability and the unparsed remainder (question 3)

> **[BUILT — PW-2a: the collection side].** `backend/app/property_watch/fold.py` (`fold_bucket`)
> produces the coverage inputs of this section from the grouped and totals outcomes of a bucket:
> `total_events` is the totals search's sum and is `None` unless that search was whole (a whole
> search with no row is a measured silent scope, `0`; a failed, cut or not-collected one is
> unknown, never `0`); `parsed_events` is the sum of every row of the grouped search whose
> properties are all values (a row with a NULL, empty or whitespace-only component is the
> remainder and is never a value row); `unparsed_events = total - parsed` and its direction come
> from the existing `PwBucketFact` properties. **Where the two searches disagree** (`parsed >
> total`, an event stored between them) the fold leaves the total unknown, so coverage is unknown
> rather than above 1. The `NOT_APPLICABLE` status and the coverage floor are applied by PW-2b/PW-3.

A custom property is defined by its parsing expressions per log-source type
(`backend/app/providers/qradar_rest.py:595-606`); a scope that mixes types will contain events the
property never parsed.

- **Coverage is a first-class column.** Each collected bucket stores `total_events` (every event
  in scope, its own ungrouped aggregate — exact whenever the row exists, the WAF rule,
  `backend/app/models/waf.py:149-153`) and `parsed_events` (the events the grouped search saw
  carrying every property). Coverage is `parsed / total`, and it is a **lower bound** whenever the
  grouped search was truncated (`coverage_measurement`, the WAF §29.2 construction).
- **The remainder is counted separately, never as a value and never as a zero.** The bucket row
  carries `unparsed_events = total − parsed`, with its direction stated by the bucket's
  measurement: exact when the grouped search was whole, an upper bound when it was a lower bound
  (a truncated grouped search under-counts `parsed`, so the subtraction over-states the remainder).
  It is never emitted as a value row, never admitted, and never used as a denominator for a share
  — the same "bounds nothing" reasoning as the ratio-coverage decision of 2026-10-12
  (`docs/PHASE-B-WAF-BEHAVIOR.md` §30.6).
- **Which verdicts low coverage blocks:** any verdict that reads an **absence** as evidence —
  disappearance, a drop towards zero, a "below" threshold, a `NORMAL` verdict — additionally
  requires coverage ≥ `pw_min_coverage` (0.90, the `ips_min_coverage` precedent,
  `backend/app/core/config.py:180`). A positive per-value count is its own group's `SUM(eventcount)`
  and is judged on its own measurement regardless of coverage (the monotonicity argument of WAF
  §29.2). Baseline training excludes buckets below the floor (`low_coverage`, the exclusion-tally
  pattern of `docs/PHASE-B-WAF-BEHAVIOR.md` §30.3).
- **A scope where no source defines the property** is an authoring-time and a runtime answer:
  - *Authoring*: the property picker intersects the catalog's `log_source_type_ids`
    (`backend/app/providers/qradar_rest.py:623`) with the scope's types and shows, per property,
    "has a type-wide expression on N of M types in this scope". It is **a warning, never a
    refusal**. A save is not refused when no type in scope matches, because that would contradict
    the catalog being a suggestion and not a gate (`backend/app/services/property_catalog.py:4-7`),
    and it would be wrong in a second way: the catalog counts only **enabled, type-wide**
    expressions (`log_source_id == -1`, `backend/app/providers/qradar_rest.py:567-575,602-606`), so a
    property defined by a per-source expression has an empty `log_source_type_ids` and parses
    perfectly well. Whether the property parses on the scope is answered by the preview (§G.6),
    which is a real run. A property that passes `PROPERTY_NAME_PATTERN` can always be saved;
    validation returns every reason at once (422, the F.1 pattern, `docs/PHASE-F-CUSTOM-DETECTIONS.md`
    §F.11) for malformed input only.
  - *Runtime*: a catalog row can be stale or the property can be typed outside the catalog. A watch
    whose collected buckets carry `parsed_events = 0` across 12 consecutive collected buckets
    (counted across ticks) takes
    `NOT_APPLICABLE` — "the property parsed on no event in scope; the values below are not
    measured" — and every value verdict is `INSUFFICIENT_DATA`, never zero and never normal.
    The watch page shows the coverage per bucket precisely so a parsing break
    is visible as a coverage collapse, not as a fleet of disappearing values. `NOT_APPLICABLE` needs
    `total_events > 0`: a scope that sent nothing is a silent scope (its buckets are `COMPLETE`
    with zero events), not a property that fails to parse.

## G.6 Collection (question 4) **[BUILT — PW-2b]**

> **[BUILT — PW-1 fact contract; PW-2a provider, AQL, fold and split driver].**
> `backend/app/property_watch/facts.py` (PW-1) defines the pure bucket, value/tuple, coverage,
> unparsed-remainder, measurement and reading facts, the measured-zero boundary, strict
> integer-count coercion (including PW-0's integer-valued floats), `cap + 1` truncation recognition
> and NULL/empty unparsed classification. **PW-2a** adds, all pure except the provider methods:
>
> - `app/property_watch/aql.py`: `validate_query_inputs` and `build_query`, the three searches
>   exactly as written below (`LIMIT cap + 1`, `IS NOT NULL` on every property, the pinned `IN`
>   form over the distinct components, the `p0`/`p1`/`k`/`events` aliases). Every reference and pin
>   is re-validated there (`PwFieldReference`, `validate_pinned_value`), whoever the caller is.
> - `QRadarProvider.get_pw_keyed(search, ...)` in `app/providers/base.py`, implemented on
>   `QRadarRestProvider` (which appends the padded `START`/`STOP`) and on `MockQRadarProvider`
>   (which builds the real query, parses with the real parser and keeps test hooks `pw_fault`,
>   `pw_force_truncated`, `pw_script`). MCP inherits the base refusal (`AQL_EXECUTION`). One method
>   with a `PwSearchKind` (`GROUPED`, `TOTALS`, `PINNED`), as `get_ips_keyed` has a dimension.
> - `app/property_watch/keyed_rows.py`: the shared row parser. A key outside the span, a
>   non-integer key and any count `coerce_count` refuses fail the whole search with a
>   `ProviderError`. Raw values stay raw in `PwKeyedRow.values`.
> - `app/property_watch/keyed_search.py`: `PwSearchBudget(max, max_per_watch=...)`, the time-first
>   split driver `run_search`, and `combine_chunks`. The row cap per search is computed here:
>   grouped `pw_max_rows_per_search`; totals one row per bucket of the (half) window; pinned the
>   product of the distinct pinned components per bucket times the buckets, never above the grouped
>   cap (so at most `pw_max_pinned_values`^2 per bucket, as designed).
> - `app/property_watch/fold.py`: `fold_bucket`, which builds a `PwBucketFact` from a bucket's
>   three outcomes. The admission columns keep their defaults for PW-2b.
>
> **PW-2b** built scope resolution, admission, persistence, the dispatch task and the
> statement-count pins. `providers/factory.py` passes
> `pw_max_sources_per_search` to the mock and REST providers.

**Decision: a dedicated collector in the IPS keyed style — not F.2 scheduled searches.**

Against rule 4 first: the browser never sends AQL. The watch is stored as validated parameters
(scope columns, `(kind, name)` references, pinned values); the server generates the AQL the same
way the WAF and IPS collectors generate theirs from profile fields
(`backend/app/providers/qradar_rest.py:1532-1600`), and the generated text is stored read-only in
the bucket's `query_provenance` (the catch-all/WAF pattern,
`backend/app/models/catchall.py:127`) and rendered in a collapsed `CodePanel` on the watch page.
Nothing an operator types ever reaches Ariel except a property name that survived
`PROPERTY_NAME_PATTERN` and a pinned value that is parameterized into a quoted literal comparison.

Why not F.2: a scheduled search is operator AQL on its own cron whose executor stores one aggregate
row per GROUP BY key per run and thresholds a total (`backend/app/models/search.py:29-110,167-193`).
It has no per-bucket judged rows, no watermark, no truncation split, no admission, no seasonal
baselines, no lifecycle and no honesty states — every one of which the four outputs need. Reusing
its machinery would mean re-implementing IPS collection inside the search executor; the IPS keyed
style is the established machinery for exactly this shape of problem
(`backend/app/ips/keyed_search.py:1-18`).

**The family and the tick.** A new `SearchFamily.PROPERTY_WATCH` (`backend/app/models/enums.py:100-107`)
is seeded at 1 h by Phase G's migration; migration `0028` moves that untouched seed to the 5-minute
default (decided 2026-10-18; the `IPS_COLLECTION` precedent). The
dispatch task claims the slot (`backend/app/services/search_cadence.py:61`), is a no-op while
`PW_COLLECTION_ENABLED` is false (the `IPS_COLLECTION_ENABLED` pattern,
`backend/app/workers/tasks.py:1335-1339`), and per instance runs one plan: resolve every enabled
watch's scope (§G.4), then per watch collect → evaluate → train (the WAF per-source ordering,
`docs/PHASE-B-WAF-BEHAVIOR.md` §28.7), under one `PwSearchBudget(pw_max_searches_per_tick)` shared
by every watch of the instance (the `IpsSearchBudget` shape,
`backend/app/ips/keyed_search.py:35-52`). The whole per-instance pipeline holds the family's
collector advisory lock (`CollectorAdvisoryLock` with the collector name `property_watch`, T-136),
as every collector family does, so a tick that outlasts the cadence slot skips instead of
overlapping the next one and reports `skipped_locked` (the geo collector's skip). Bucket width is
`collection_interval_seconds` (300 s,
`backend/app/core/config.py:237`) — the cadence decides when the family searches, never how finely
it measures (`docs/SEARCH-CADENCE.md` §SC.1). Watermarks: `collected_through` advances only past
buckets that completed; the **first run seeds the watermark at the latest closed bucket and replays no history** (the
rule Phase A states for its evaluation watermark, `docs/SEARCH-CADENCE.md` §SC.6, lines 199-201:
"An upgrade must not replay history into alerts"). A new watch has no baseline anyway, so its
first buckets are judged `INSUFFICIENT_DATA` (§G.8). **A watch without a history request learns
forward and never backfills.** A watch that asked for one reads its past from Ariel, for learning
only (`docs/PHASE-G-HISTORY-BOOTSTRAP.md`); the §SC.6 rule is the reason both are safe: nothing
read from the past is judged.
Settle: 120 s (`backend/app/core/config.py:157-158`), the family default.

**The searches.** Per watch per tick, over every complete bucket since its watermark. None of
them groups by `logsourceid`: the watch reports the scope's values, so the result is values x
buckets, not values x buckets x members (§G.4). With `k = starttime / bucket_ms`:

1. **Grouped search** (always):
   ```sql
   SELECT "<prop1>" AS "p0"[, "<prop2>" AS "p1"],
          starttime / <bucket_ms> AS "k", SUM(eventcount) AS "events"
   FROM events
   WHERE <scope> AND starttime >= <a> AND starttime < <b>
     AND "<prop1>" IS NOT NULL [AND "<prop2>" IS NOT NULL]
   GROUP BY "<prop1>"[, "<prop2>"], starttime / <bucket_ms>
   ORDER BY SUM(eventcount) DESC
   LIMIT <pw_max_rows_per_search + 1>
   ```
   `<scope>` is the §G.4 filter; normalized references are quoted after exact allowlist membership
   and custom properties after `PROPERTY_NAME_PATTERN`
   (`backend/app/providers/qradar_rest.py:1516-1530`). The `IS NOT NULL` population condition is
   the string-field form of `_ips_population_condition`
   (`backend/app/providers/qradar_rest.py:1505`). `LIMIT` caps the whole result, not each bucket
   (`docs/SEARCH-CADENCE.md` §SC.2), so a busy bucket can crowd a quiet one out of the result: that
   is what the truncation split below is for. A `LIMIT` cut is invisible on the console (status
   `COMPLETED`, no flag; L8), so truncation is detected **only** by the `cap + 1`
   construction above: more than `pw_max_rows_per_search` rows means truncated. The same
   construction is on the totals and pinned searches (their own caps, below). An unparsed property
   comes back as NULL in a `GROUP BY` (L5; no empty strings seen); the fold counts NULL as
   unparsed and also counts an empty string as unparsed, never as a value.
2. **Totals search** (always): the same scope and window with no property condition, `GROUP BY k`:
   the exact denominator of every coverage claim (`LIMIT` buckets + 1: one row per bucket). It is not redundant, for the reason the WAF
   collector states: grouping drops events whose property did not extract
   (`backend/app/providers/qradar_rest.py:1075-1079`). Twelve rows an hour at a 5-minute bucket.
3. **Pinned search** (only when the watch pins values): the grouped search restricted to the
   pinned values, `("<prop1>" = 'v1' AND "<prop2>" = 'w1') OR ...`. A pin on a combination watch is
   a **full tuple**: pinning only the first component (every `Action` of one `Username`) would
   need a marginal series and is §G.17 Q4. Each value is a single-quoted literal. Because the
   literal comes from an operator and not from a fixed list, a pinned value must match
   `PW_PINNED_VALUE_PATTERN` (printable, 1-255 characters, **no** single quote, double quote,
   backslash or control character) and is **rejected, not escaped**: the same allowlist-over-quoting
   argument as `PROPERTY_NAME_PATTERN` (`backend/app/providers/base.py:60-65`). A value such as
   `O'Brien` therefore cannot be pinned in v1 (the editor says why; §G.17 Q8). The generator emits
   `"<prop1>" IN ('v1','v2',...)`, never OR-ed equality (L4). `IN` cannot express a tuple, so for a
   combination watch it emits `"<prop1>" IN (...) AND "<prop2>" IN (...)` over the distinct components
   of the pinned tuples and the **fold keeps only the pinned tuples**; at most 10 x 10 combinations a
   bucket, so the result stays small. Its `LIMIT` is `pw_max_pinned_values`^2 x buckets + 1.
   The search is small by construction (at most `pw_max_pinned_values` values x buckets), so it stays whole when the
   open-ended grouped search cannot, and it is what makes a pinned value's **measured zero**
   provable under heavy cardinality: a whole pinned search that does not mention a pinned value is
   that value's zero in that bucket (the measured-zero rule below), the way a whole grouped search is for a tracked one
   (`docs/PHASE-B-WAF-BEHAVIOR.md` §29.4). The zero is a zero **of parsed events** (§G.5): the
   verdicts that read it also require coverage at the floor.

So: **2 searches per watch per tick, 3 when values are pinned**, plus splits (§G.14).

**The measured-zero rule (stated once; everything else refers here).** An aggregate that matches
nothing returns **zero rows**, not a row holding 0 (the lab saw this). So the
collector applies exactly this: **zero rows, or an absent key, from a search that ended `COMPLETED`
and was not truncated (at most `cap` rows) is a measured zero** for whatever that search was asked
about (a total of 0 events in a bucket, a pinned value's 0, a tracked value's 0 in an `EXACT`
bucket). **Any other end state is unmeasured, never 0**: failed, cancelled, timed out, still
running, or truncated; an empty result from such a search is not a zero. That rule is what makes a
pinned value's 0 `EXACT`, what lets a silent scope store a `COMPLETE` bucket with 0 events (§G.5), and
what §G.8.0 and §G.12 rely on.

**Truncation and splitting** follow IPS (`backend/app/ips/keyed_search.py:1-18`) with one change:
there is no per-source split, because there is no per-source key. A truncated search keeps
nothing and is re-run on the two halves of its window, whole buckets, left first. At one bucket a
truncated search is **not** discarded: its rows are the top values by `SUM(eventcount)`, each a
real, exact count of that value; only the tail is missing. The bucket is then stored with
`values_measurement = LOWER_BOUND`: stored counts are lower bounds in the WAF sense (they can
understate, never overstate, `docs/PHASE-B-WAF-BEHAVIOR.md` §29.2 "Lower-bound guarantee") and an
absent tracked value is *unknown*, not zero. A failed search is not split; the buckets it covered
are `NOT_COLLECTED` and the watermark holds. **A bucket is never written from a truncated
multi-bucket search**. The pure split driver is `keyed_search.py`'s shape
applied to a scope key instead of a source group; reusing its budget class under a PW name is
PW-2's decision, and the contract (time first, never zero) is fixed here.

**The fold. [BUILT — PW-2a, `app/property_watch/fold.py`.]** A pure function (the `app/ips/fold.py` /
`app/waf/breakdown.py` layer) maps rows to buckets and values. Raw values never leave it except as the bounded lists the design allows.
Per bucket it:

- validates the bucket key (a key outside the span fails the whole search,
  `backend/app/providers/base.py:506-509`) and every count: the console returns counts as floats
  (L6), so only an **integer-valued** float (or an integer) is accepted; a fractional, negative, NaN
  or non-numeric count fails the whole search, which is then unmeasured (the rule above);
- sanitizes each value (printable, stripped, at most `pw_value_max_length` = 255 characters, the
  sender-identifier rule, `backend/app/models/catchall.py:71-72`) and never raises on a hostile one
  (the T-013 rule: an odd value is counted, never an error, `docs/PHASE-B-WAF-BEHAVIOR.md` §30.2);
  two raw strings that sanitize to one value are one value;
- builds the tuple key of a combination watch as the components joined by U+001F, a separator that
  sanitized text cannot contain;
- keeps, for every value **already tracked** (§G.7), a row whenever the result mentions it,
  whatever its rank, and keeps pinned values always; of the **untracked** values it keeps the top
  `pw_max_values_per_bucket` by events, then by value; the events of the cut tail are summed into
  `untracked_events` when the search was whole (the `OVER_CAP` idea,
  `backend/app/models/geo.py:32-39`). Never cutting a tracked value is what keeps the sparse-zero
  reconstruction sound (WAF §29.4);
- applies admission (§G.7) and emits per-value `events` rows plus the bucket's totals, coverage and
  measurement.

**As built (PW-2a) where the text above leaves a choice.**

- *Sanitizing.* A value is stripped, then every non-printable character (control characters,
  U+001F, a lone surrogate, a non-space separator) becomes U+FFFD, then the text is cut to the
  limit and right-stripped. Text that is empty or whitespace-only, and anything that is not text,
  is unparsed. It never raises. Two raw strings that sanitize alike are one value.
- *The width of a tuple.* One property: the limit is `pw_value_max_length`. Two properties:
  `(pw_value_max_length - 1) // 2` per component (127 at the default), so the joined `value_key`
  fits the stored 255 and still splits back into its components. Pinned tuples are cut the same
  way before they are compared.
- *Pinned counts.* A whole grouped search stays the authority for every count, pinned values
  included. Only when the grouped search is cut or unmeasured does the pinned search fill in the
  pinned values (the larger count wins, and a `LOWER_BOUND` `parsed_events` is raised to cover
  them, still a lower bound). When the grouped search was not collected, the bucket is
  `NOT_COLLECTED` with the pinned values alone, and `pinned_measurement` carries the pinned
  search's own state, so a whole pinned search still proves a pinned value's zero.
- *A combination pin* keeps only the pinned tuples: the `IN` x `IN` cross terms are dropped.

**Preview (decided 2026-10-16).** `POST /api/v1/property-watches/{id}/preview` runs **one real bounded
Ariel search of the stored watch**, enabled or not: the grouped search of this section with the
watch's scope and properties over the last closed hour, plus the totals search (two searches).
The browser submits no AQL and no unsaved draft: as with F.2's "Run now"
(`docs/PHASE-F-CUSTOM-DETECTIONS.md` §F.8.1), the editor first saves the watch **disabled**, then
previews the stored version. The preview persists nothing, admits nothing and alerts nothing; it
carries the tighter search limiter (`backend/app/core/rate_limit.py:1-8`, [CONFIGURATION.md](CONFIGURATION.md) "API, sessions and rate limits"); and it
is audited as `property_watch.preview` with counts only (never the values). It answers, with real
data, "does this property parse on this scope, and what are its current values" before the watch
is ever enabled. It returns the same per-value rows, the coverage and the unparsed remainder the
page shows, marked `PREVIEW`, plus the generated AQL text read-only. It needs `property_watch:write`
(it spends an Ariel search), not the read permission.


---

## G.7 Persistence, admission, retention and the migration (question 5)

### G.7.1 What is stored, and what is never stored **[BUILT — PW-1 schema]**

> **As built by PW-1:** `backend/app/models/property_watch.py` and migration `0026` create only
> the aggregate definition, bucket, admitted/pinned value, sparse metric, baseline and lifecycle
> tables below. `pw_bucket` and `pw_value_metric` are TimescaleDB hypertables. No raw-event table
> or unadmitted-value table exists.

Values become **aggregate keys**, as IPS entities and WAF endpoints already are
(`backend/app/models/ips.py:206-241`, `backend/app/models/waf.py:217-231`). The tables mirror the
Phase D set (`docs/PHASE-D-IPS-BEHAVIOR.md` D.9), additive, Phase A, WAF and IPS tables untouched:

| Table | Holds | Mirrors |
|---|---|---|
| `property_watch` | the definition of §G.3 and its watermarks and status | `custom_rule` (`backend/app/models/custom_rule.py:46-57`) |
| `pw_bucket` (hypertable on `bucket_start`) | one row per (watch, bucket): `watch_version`, `total_events`, `parsed_events`, `unparsed_events`, `untracked_events`, `values_measurement`, `pinned_measurement` (`EXACT`, `LOWER_BOUND`, `NOT_COLLECTED` or `NOT_REQUESTED`), `totals_complete`, admission counts (`observed_values`, `tracked_values_observed`, `admitted_values`, `below_admission_threshold`, `rejected_over_quota`, `admission_truncated`), `scope_member_count`, `scope_fingerprint`, `scope_changed`, `in_maintenance`, `query_provenance` (the generated AQL, read-only), `collected_at` | `ips_source_bucket` (`backend/app/models/ips.py:88-100`), `catchall_bucket.query_provenance` (`backend/app/models/catchall.py:127`) |
| `pw_value` | the tracked value: natural key `(watch_id, watch_version, value_key)`, `value_key` 1-255 characters, `component_0`, `component_1` (the split of a tuple, nullable, indexed), `pinned`, `first_seen_at`, `last_seen_at`, `tracked_since`, `tracked_until`, `active`, `retired_at` | `ips_entity` (`backend/app/models/ips.py:206-241`), tracking window (`backend/app/models/waf.py:371-403`) |
| `pw_value_metric` (hypertable) | sparse: `(pw_value_id, bucket_start, events)`; no row means unknown or measured zero, decided by the bucket row (§G.8.0) | `ips_entity_metric` (`backend/app/models/ips.py:244-258`) |
| `pw_value_baseline` | weekday x hour cells: median, MAD, p05, p95, `sample_count`, `is_reliable`, `observations` (bounded numbers), `baseline_version`, `exclusion_counts` | `ips_entity_baseline` (`backend/app/models/ips.py:261-304`) |
| `pw_detector_state`, `pw_anomaly`, `pw_anomaly_transition` | the lifecycle of §G.8 | `ips_detector_state`, `ips_anomaly`, `ips_anomaly_transition` (`backend/app/models/ips.py:361-467`) |

**Never stored:** a raw event; a source address; a value that was not admitted (a value below the
admission threshold or beyond a quota exists only inside the fold, and its events are summed into
`untracked_events`); the generated AQL's *parameters beyond the pinned values the operator typed*.
The only value texts that persist are admitted values and pinned values.

### G.7.2 Values as personal data (rule 3) **[BUILT — PW-2b; PW-3c: the open-incident retention guard]**

> **As built.** `PwRetention` deletes aged `pw_value_metric` and `pw_bucket` rows, retired
> `pw_value` rows, and terminal `pw_anomaly` incidents older than `pw_anomaly_retention_days`
> (with their transitions by cascade). It never deletes a value whose incident is still OPEN or
> RECOVERING (PW-3c item 2), an incident a `pw_detector_state` still points at, or one an
> unresolved alert depends on; what it holds back is counted in `blocked`. T-135 closed PW-11's
> gap: the "anomalies 180 days" sentence below is now applied by
> `apply_property_watch_retention`, and §G.14.3's row for that setting says so.

A property such as `Username` carries personal data, and a free-text field can carry worse: a
user who types a password into a username box on a failed login produces a "username" that is a
secret. **The user chose option (b) on 2026-10-17: values are stored in clear text and readable by anyone with `read:*`, as offenses are.** The bounds that remain:

- **Length.** At most `pw_value_max_length` = 255 characters, the stored width of
  `ips_entity.value_key` and the sender identifier (`backend/app/models/ips.py:233`,
  `backend/app/models/catchall.py:71-72`); longer text is truncated by the fold and flagged on the
  value (`truncated`), never rejected. A tuple key is bounded the same, so a combination of two
  long values is truncated, not stored whole.
- **Clear text, not hashed.** A hash cannot be displayed, pinned or searched, so a hashed watch
  would be a counter with no names. The cost of clear text is bounded instead by *admission*: a
  one-off mistyped secret has one event in one bucket and never reaches
  `pw_min_admission_events` = 3 (the IPS and WAF default, `backend/app/core/config.py:178`), so it
  is never stored. A value that does repeat three times in five minutes is stored.
- **Retention.** Bucket and metric rows 35 days (the WAF, IPS, catch-all and geo default,
  `backend/app/core/config.py:182,488,521,539`); a value row is deleted when it is retired and its
  last metric has aged out, **never while an incident on it is still OPEN or RECOVERING** (PW-3c
  item 2: deleting the subject would strand the investigation, so the row is skipped and counted in
  the run's `blocked` report); anomalies 180 days (the WAF default,
  `backend/app/core/config.py:490`). **Deleting a watch deletes every value it holds** (cascade),
  which is the erasure path for "stop tracking this property"; its open alerts are resolved first,
  as F.1 does (`docs/PHASE-F-CUSTOM-DETECTIONS.md` §F.4.3). An alert's context holds the text of the
  value that opened it, for as long as alerts are kept; that is what an analyst needs and the same
  as an offense's usernames.
- **Who reads values.** Anyone holding `read:property_watches`, which `read:*` satisfies, exactly as
  offenses carry usernames to any `read:*` holder (`backend/app/security/rbac.py:48-54`). There is
  **no separate values permission** (decided 2026-10-17): the viewer, analyst, detection-engineer
  and admin roles all see value text.
- **Never in logs or audit.** Logs and audit rows name a watch by id. An audit snapshot of a watch
  stores the number of pinned values and a SHA-256 digest of the sorted pinned list, so a change is
  visible without the audit table becoming a second copy of the values.
- **Masking** (show `al***` instead of `alice`) is not built.

### G.7.3 Admission, the two kinds of value, and retirement **[BUILT — PW-2b; PW-3c: the retired-value resolve]**

> **[BUILT — PW-1 persistence contract; PW-2b behavior].** The bucket admission-outcome columns and
> the value tracking-window/pinned/retirement columns are in model metadata and migration `0026`.

- **Pinned values** (user answer 2) are `pw_value` rows from the moment the watch is saved:
  `pinned = true`, `tracked_since` = the first bucket collected after enable, never subject to
  admission, never retired, counted as a measured zero when the pinned search is whole and absent (§G.6 rule).
  At most `pw_max_pinned_values` = 10, so a watch cannot buy unbounded storage with a long list.
- **Automatic values** (the top-N) are admitted by the WAF rules (`docs/PHASE-B-WAF-BEHAVIOR.md`
  §28.5): a value is admitted in a bucket when it has at least `pw_min_admission_events` = 3 events
  there, subject to `pw_max_new_values_per_bucket` = 20 and the active ceiling
  `pw_max_active_values_per_watch` = 200 (the IPS settings, `backend/app/core/config.py:174-179`).
  "Top-N" is therefore twice bounded: the fold keeps at most `pw_max_values_per_bucket` = 50
  untracked values per bucket (§G.6) and admission turns at most 20 of them per bucket into
  tracked values. A tracked value that goes `pw_value_active_days` = 7 (the
  `ips_entity_active_days` default, `backend/app/core/config.py:179`) without an observation is
  retired (`active = false`, `tracked_until` set so a lagging backfill cannot read its absence as
  zero, WAF §29.4); a returning value starts a new tracking period and keeps `first_seen_at`.
  **A retired value is judged no further, so an incident still open on it is resolved** (PW-3c
  item 1: the engine's next run ends it with actor `pw-engine` and reason `value retired; no
  longer evaluated`, resolving the alert too when alerting is on).
- **Every admission outcome is a count on the bucket row** (`admitted_values`,
  `below_admission_threshold`, `rejected_over_quota`, `admission_truncated`), so the new-value
  detector can tell "not new" from "refused" (§G.8.3).
- **A pinned value cannot be refused by the ceiling**, but it counts toward it; a watch whose
  pinned values plus tracked values would exceed the ceiling stops admitting automatic values and
  says so (`admission_truncated`).

### G.7.4 The migration **[BUILT — PW-1 half; PW-6 half is `0027`]**

> **As built by PW-1:** `0026_property_watch.py` revises `0025`, creates the eight PW tables,
> registers both hypertables and seeds `PROPERTY_WATCH` at 3,600 seconds with `ON CONFLICT DO
> NOTHING`. Its integration test upgrades, downgrades and upgrades again, checks both
> hypertables, the seed, the scope/property `CHECK`s and `alembic check`. In accordance with the
> PW-1 task split below, `custom_rule.pw_value_id` and the recreated subject `CHECK` are not in
> `0026`; they remain PW-6's next migration.
>
> **As built by PW-6:** `0027_custom_rule_pw_value.py` revises `0026`: it adds
> `custom_rule.pw_value_id` (FK to `pw_value.id`, `ON DELETE RESTRICT`, indexed) and drops and
> recreates `ck_custom_rule_subject_matches_kind` with `PW_VALUE`. The downgrade deletes the
> `PW_VALUE` rules (their breaches cascade) before restoring the old `CHECK`. Its test is
> `tests/integration/test_migration_0027.py`.

One additive migration, number "next at build time" (head is `0025_auth_sessions`,
`backend/alembic/versions/`). It creates the tables above, registers `pw_bucket` and
`pw_value_metric` as hypertables with the `0018`/`0022` helper
(`backend/alembic/versions/0018_ips_collection.py:181-205`,
`backend/alembic/versions/0022_geo_collection.py:93-112`), seeds `search_cadence` for
`PROPERTY_WATCH` at 3600 s with `ON CONFLICT DO NOTHING`
(`backend/alembic/versions/0022_geo_collection.py:85-90`; the family column is `String(32)` and its
only check is on the cadence value, `backend/app/models/search_cadence.py:14-25`), and, for the F.1
extension of §G.10, **adds** `pw_value_id` to `custom_rule` and **drops and recreates** the
subject-pairing `CHECK` with the new kind. Recreating a constraint in a *new* migration does not
edit `0024`, which stays as it is. It must upgrade, downgrade and upgrade
again, and `alembic check` must report no drift; the F.1 migration test is the template
(`docs/PHASE-F-CUSTOM-DETECTIONS.md` §F.14, `test_migration_0024`). The split of this migration over
PW-1 and PW-6 is in §G.16.


---

## G.8 Detection: the four outputs (question 6)

### G.8.0 Shared machinery

> **[BUILT — PW-3a pure detection; PW-1 enums and reading contract].** The separate PW anomaly,
> scope, reference, bucket/pinned measurement and watch-status enums are in
> `app/models/enums.py`; `app/property_watch/facts.py` decides exact counts, lower bounds,
> measured zeros, unknowns and non-observations without I/O. PW-3a added the declared capability
> specs and shared verdict-scope mapping (`app/anomaly/capabilities.py`,
> `app/property_watch/inputs.py`), the pure seasonal builder (`property_watch/baseline.py`), the
> pure history verifier (`property_watch/history.py`) and all five pure verdict functions
> (`property_watch/detectors.py`).
>
> **[BUILT — PW-3b; PW-3c: the retired-value resolve and the stale-cell delete].**
> `property_watch/engine.py` (`PwAnomalyEngine`) loads everything for all
> enabled watches of an instance in bulk (the buckets after each watch's `evaluated_through`
> up to `collected_through`, at most `pw_max_evaluation_buckets_per_run` per watch; tracked
> values; their metric rows; the baseline cells of the pending hours of the week; the detector
> states; the open incidents; the bucket history and the component lookup only while a value is
> inside its novelty window), judges them with the pure detectors, and writes once per table
> (states, incidents, transitions, watch status). The lifecycle is the shared
> `anomaly/lifecycle.py` with `anomaly_open_after_intervals` and
> `anomaly_resolve_after_intervals`; **this is where the spike's "required consecutive buckets"
> live.** Disappearance keeps its own rule in the detector (`pw_disappear_buckets` measured zeros
> before the first anomalous verdict); the lifecycle then needs `anomaly_open_after_intervals`
> consecutive anomalous verdicts, so the incident opens at the `pw_disappear_buckets +
> anomaly_open_after_intervals - 1`-th zero bucket (6 + 2 - 1 = 7 at the defaults). The two are
> not counted twice: the detector counts zeros, the lifecycle counts verdicts. A bucket that is
> unknown or not judgeable holds state; a failed collection stores no judged bucket and the
> evaluation watermark does not move. A value that was not tracked at a bucket is not judged at
> all. A `watch_version` bump resolves the old version's active incident at the next run with the
> transition reason "watch definition changed" (the hook where PW-4 resolves the alert); the new
> version's values are new rows with no baseline. **A value this run retired resolves its still-open
> incident the same way** (PW-3c item 1), reason "value retired; no longer evaluated", read from
> values already in memory so it adds no query. `property_watch/training.py`
> (`PwBaselineTrainer`) runs after evaluation in the same run: the
> `pw_baseline_values_per_run` least recently computed current values, only up to the
> evaluation watermark, the due read plus five bounded statements per chunk — the last of which
> deletes every cell of those values the rebuild did not write, so a cell whose samples aged out
> can no longer judge old data (PW-3c item 3, detectors then read `INSUFFICIENT_DATA`).
> `services/pw_pipeline.py` runs collect, evaluate, train per instance; evaluation and training
> each run in a savepoint, so a failure keeps what collection stored. `PW_DETECTORS` are in `all_detectors()` now that
> `dispatch_property_watch_collection` is scheduled.

**New enums, `AnomalyType` untouched** (the reason is stated at
`backend/app/models/enums.py:139-145` for WAF and `:172-179` for IPS):

- `PwAnomalyType`: `PW_VALUE_SPIKE`, `PW_VALUE_DROP`, `PW_VALUE_DISAPPEARED`, `PW_NEW_VALUE`,
  `PW_NEW_COMBINATION`. Persisted in `pw_anomaly.detector_type` and in alert fingerprints, so it is
  its own enum and neither `AnomalyType`, `WafAnomalyType` nor `IpsAnomalyType` ever learns a `PW_`
  member.
- `PwScopeKind` (`LOG_SOURCE`, `LOG_SOURCE_TYPE`, `LOG_SOURCE_GROUP`), `PwReferenceKind`
  (`NORMALIZED`, `CUSTOM_PROPERTY`; the IPS enum is IPS-only by its own use,
  `backend/app/models/enums.py:311-313`), `PwBucketMeasurement` (`EXACT`, `LOWER_BOUND`,
  `NOT_COLLECTED`, mapped to the shared `WafMeasurement` the way `ips/inputs.py` does,
  `backend/app/ips/inputs.py:76-79`), `PwWatchStatus` (§G.3).
- `BehaviorDomain` gains `PW_VALUE` (`backend/app/anomaly/capabilities.py:59-63`) and
  `capabilities.py` gains `PW_DETECTORS: dict[PwAnomalyType, DetectorSpec]` with
  `PW_BASELINE_METRICS` and a `PW_VALUE` branch in `detector_status`
  (`backend/app/anomaly/capabilities.py:687-722`), enabling setting `pw_collection_enabled`. The
  verdict arithmetic is **reused, not copied**: `WafMeasurementRequirement`, `verdict_scope`,
  `narrowest` and `ScopeDecision` (`backend/app/waf/measurement.py:28-83`), exactly as the IPS
  specs do (`backend/app/anomaly/capabilities.py:467-491,512-685`). The engine computes the scope from the
  spec and passes it to the pure detector; a detector never grants itself lower-bound reasoning
  (`backend/app/anomaly/capabilities.py:1-14`).

**What one (value, bucket) reading is.** The measurement of a reading is decided by the bucket
and by how the value was searched, never by the detector:

| Situation | Reading |
|---|---|
| value has a `pw_value_metric` row | the row's `events`, under the value's measurement: `EXACT` if the value is pinned and the bucket's `pinned_measurement` is `EXACT`, else the bucket's `values_measurement` |
| no row, value tracked at the bucket, bucket's measurement for it is `EXACT` | a **measured zero** under the §G.6 rule (WAF sparse storage, `docs/PHASE-B-WAF-BEHAVIOR.md` §29.4) |
| no row, value tracked, measurement `LOWER_BOUND` | **unknown**: lower bound 0, `INSUFFICIENT_DATA`, trains as `incomplete` |
| no row, value not tracked at the bucket | not an observation |
| no bucket row (collection failed, never ran, `SCOPE_STALE`, budget spent) | nothing; never zero |

Pinned values are exact whenever the small pinned search was whole (§G.6), even when the open-ended
search was not. That is the concrete benefit of pinning, and why an F.1 rule may pin only a pinned
value (§G.10). A zero is a zero **of parsed events**: it also needs the coverage floor (§G.5).

**Baselines.** One seasonal metric, `PW_VALUE_EVENTS_METRIC` (events per bucket), per value,
weekday x hour cells (ISO weekday 1-7 x hour 0-23), median and MAD with p05/p95 as the band, the
Phase A construction (`docs/PHASE-A-SOURCE-VOLUME-ANOMALY.md` §3-§4.2), the IPS entity cell shape
(`backend/app/models/ips.py:261-304`) and the IPS builder's exclusion tally
(`backend/app/ips/baseline.py:76-110`). Excluded from training: `LOWER_BOUND` and unknown buckets,
buckets below the coverage floor (`low_coverage`), `scope_changed` buckets, maintenance buckets,
buckets under another `watch_version`, buckets inside a known incident span. A cell below
`baseline_min_samples` (8, `backend/app/core/config.py:335`) is stored and never drives a verdict.
A cell the next rebuild no longer produces — its samples aged out of the window, or every one was
excluded — is **deleted**, so a detector reads `INSUFFICIENT_DATA` rather than statistics from data
that is gone (PW-3c item 3). Lookback `pw_baseline_lookback_days` = 28 (`ips_baseline_lookback_days`,
`backend/app/core/config.py:187`). Training runs after evaluation in the same run (the WAF order,
`docs/PHASE-B-WAF-BEHAVIOR.md` §28.7), a bounded number of values per run
(`pw_baseline_values_per_run` = 100, the IPS batch idea, `backend/app/core/config.py:189`).

**Learning period, stated honestly.** A cell is judged only once it has 8 clean samples. A visit to
an hour-of-week gives up to 12 five-minute samples, but the same cell is visited once a week, so the
first verdict on a cell comes **one week after the value was first tracked**, and a value is fully
judged after a week of recurring hours. A cell is also reliable only when its retained samples
come from at least `pw_baseline_min_visits` (default 2) distinct weekly visits, counted as UTC
calendar dates of the bucket start (decided 2026-10-18): the 12 samples of one hour are correlated, not a baseline.
The watch page shows, per value, "baseline: N of 168
hour-of-week cells reliable", never a silent `NORMAL`. `PW_NEW_VALUE` has its own, shorter period
(history, §G.8.3). This week-long wait is what the history bootstrap is designed to shorten: it
reads past buckets from QRadar for learning only
(`docs/PHASE-G-HISTORY-BOOTSTRAP.md` and `docs/HISTORY-BACKFILL.md`; decided 2026-10-18: learning only, never evaluated).
**This is the wait of a watch without a history request.** After a bootstrap of at least 7 days the
cells are judged at the hand-off, because the history window already holds the visits
(`docs/PHASE-G-HISTORY-BOOTSTRAP.md` §H.8.2).

**Lifecycle.** The shared contract: `INSUFFICIENT_DATA -> NORMAL -> CANDIDATE -> OPEN ->
RECOVERING -> RESOLVED` with a transition row per change, one active incident per (watch,
detector, `value_key`), a bucket that is unknown or incomplete **holding state**, and a collection
failure never resolving an incident (`docs/PHASE-A-SOURCE-VOLUME-ANOMALY.md` §5;
`AnomalyState`, `backend/app/models/enums.py:378`). Confirmation and recovery counts are the Phase A
settings `anomaly_open_after_intervals` (2) and `anomaly_resolve_after_intervals` (3)
(`backend/app/core/config.py:340-341`).

**Where incidents surface.** Spike, drop, disappearance, new value and new combination are
**baseline and novelty incidents**, the same class as WAF and IPS incidents, and they appear in
`/anomalies` through a new `SubjectKind.PW_VALUE` (the extension point is documented at
`backend/app/schemas/anomaly.py:40-51`; the list filter at `backend/app/api/routes/anomalies.py:149`
gains `PwAnomalyType` in its `anomaly_type` union). F.1 **threshold breaches stay out**, as decided on 2026-10-16 (`docs/PHASE-F-CUSTOM-DETECTIONS.md` §F.16, §F.4.3): "the platform's detectors found
this" and "my number crossed my line" stay separate. A `PW_VALUE` row carries the value text and is shown to every caller who can read `/anomalies`,
the same as an offense's usernames (decided 2026-10-17).

### G.8.1 Output 1: the value distribution **[BUILT — PW-2b: the bucket and value reads; PW-7 and PW-9: the table, chart and widget]**

No verdict. Per bucket and value: events, the reading's measurement, share of **parsed** events
(shown only when the bucket is `EXACT`, since a share over a lower-bound total bounds nothing,
`docs/PHASE-B-WAF-BEHAVIOR.md` §30.6), and the bucket's coverage and unparsed remainder. A
`LOWER_BOUND` bucket is drawn as lower bounds ("at least n") and its unknown zeros as gaps
(`docs/PRODUCT-UX-GUIDELINES.md` §4: Null is not measured zero; the chart must not invent
continuity). Requirement: none beyond the bucket existing. The table, the chart and the widget read
the same bulk response (§G.11).

### G.8.2 Output 4a: baseline deviation, three verdicts **[BUILT — PW-3a] [BUILT — PW-3b]**

> **As built.** One ordered precondition path fails closed on watch state, missing/uncollected
> buckets, membership changes, a property that parsed on no event (`NOT_APPLICABLE`), thin or
> incompatible cells and unknown readings. Maintenance is not a verdict exit (it only keeps a bucket
> out of training; suppression is alerting's, PW-4). Consecutive-bucket confirmation of the spike
> is the lifecycle's `anomaly_open_after_intervals` (built in PW-3b, §G.8.0). A missing bucket inside the
> disappearance window is `INSUFFICIENT_DATA`, never a broken streak. The
> spike consumes the capability-granted increase-only scope; drop and disappearance never infer
> absence from a lower bound. Disappearance reads an explicit sequence of exact, covered readings,
> so an absent metric row becomes zero only through the PW-1 reading contract.

> **[BUILT — PW-3b].** The engine feeds these three detectors the cell of the bucket's weekday and
> hour, the value's reading (the §G.8.0 table through `facts.reading_for`), and the trailing
> `pw_disappear_buckets` readings of the current `watch_version` only. Their thresholds come from
> `PwAnomalyEngine._thresholds()`; a unit test fails if a `PwThresholds` field has no source in
> the settings.

All three read the value's `PW_VALUE_EVENTS_METRIC` cell for the bucket's weekday and hour.

| Type | Claim | Verdict rule (every guard must pass) | Measurement requirement | Inputs |
|---|---|---|---|---|
| `PW_VALUE_SPIKE` | the value's events are above its seasonal band | reliable cell; minimum bucket events (`pw_min_bucket_events` 20); ratio to median at least `pw_spike_ratio` 2.0; absolute rise at least `pw_min_absolute_delta_events` 50; robust z, or the degenerate-MAD p95 fallback with confidence capped at 0.7 (Phase A §4.2); required consecutive buckets; the bucket is not `scope_changed` | `LOWER_BOUND_PROVES_INCREASE` | `volume`: `LOWER_BOUND_PROVES_INCREASE` (every guard is non-decreasing in the count: WAF §29.2, `backend/app/anomaly/capabilities.py:309-323`) |
| `PW_VALUE_DROP` | the value's events are exactly below its band while the value normally carries traffic | reliable cell; median at least `pw_drop_min_median` (20 events); count at most `pw_drop_ratio` (the Phase A `anomaly_drop_ratio`, 0.5, `backend/app/core/config.py:352`) of the median; absolute fall at least `pw_min_absolute_delta_events`; robust z or the p05 fallback; coverage at the floor | `EXACT` | `volume`: `EXACT` (an undercount cannot prove a drop); `coverage`: `LOWER_BOUND_PROVES_INCREASE` (a lower bound that reaches the floor proves true coverage does, the IPS reasoning at `backend/app/anomaly/capabilities.py:493-510`) |
| `PW_VALUE_DISAPPEARED` | the value, normally present, has a measured zero for several buckets | the value was observed within `pw_value_active_days`; the cell median is at least `pw_disappear_min_median` (5); a **measured zero** for `pw_disappear_buckets` (6, 30 minutes) consecutive evaluated buckets; coverage at the floor; none of them unknown. Resolved by the first non-zero exact reading and `anomaly_resolve_after_intervals` | `EXACT` | as `PW_VALUE_DROP` |

What a `LOWER_BOUND` bucket may prove: **only the spike**. It can never prove a drop, a
disappearance or normal behaviour, because the hidden events may be exactly the ones that make the
bucket normal. In a lower-bound bucket the drop and the disappearance are `INSUFFICIENT_DATA` with
the reason from `verdict_scope` ("the detector needs exact counts and this count is a lower bound",
`backend/app/waf/measurement.py:65-67`). These are the defaults a build may tune against the
lab; the names follow `ips_spike_ratio` and its neighbours
(`backend/app/core/config.py:193-195`).

Fail-closed exits, in order, shared by the three: the watch is `DISABLED`, `NOT_APPLICABLE`,
`SCOPE_STALE`, `SCOPE_EMPTY` or `SCOPE_TOO_LARGE`; the bucket is missing or `NOT_COLLECTED`; the bucket
is `scope_changed` (reason "membership changed"); the cell is missing or below
`baseline_min_samples`; the value's measurement is unknown; then the detector's own guards. Each
exit is `INSUFFICIENT_DATA` with a reason, never `NORMAL` and never zero.

### G.8.3 Output 3: new-value detection **[BUILT — PW-3a] [BUILT — PW-3b]**

> **As built.** `verify_value_history` reports the first ordered completeness failure (missing or
> duplicate buckets, width, `NOT_COLLECTED`, lower-bound discovery, truncated admission or another
> watch version). The pure verdict then applies the learning, membership-change, pin, established,
> history, appearance-flood and materiality exits without consulting storage.

> **[BUILT — PW-3b].** The novelty facts (verified history, first-appearance bucket, events and
> buckets with traffic since first appearance, materiality measurement, the watch's first
> collected bucket, membership change around the first appearance) are built by the engine only
> for values whose novelty window still covers a pending bucket, and only for watches that
> selected a novelty detector. Values outside the window are passed an "established" fact the
> detector leaves on at once. The watch's first collected bucket of the current version rides on
> the pending-bucket read, so it costs no statement.

`PW_NEW_VALUE`, the analogue of `WAF_NEW_ENDPOINT_ACCESS` (`docs/PHASE-B-WAF-BEHAVIOR.md` §30.5;
spec `backend/app/anomaly/capabilities.py:394-414`; settings
`backend/app/core/config.py:618-624`).

**The claim, exactly.** In every bucket of the history window before the value's first
appearance, the value was not observed at least `pw_min_admission_events` (3) times in any one
bucket, each bucket being recorded, with whole discovery and whole admission, under the same
`watch_version`. Admission is how a value becomes tracked, so a value at or above the threshold in
such a bucket would already exist. **"Never seen" is not claimed**: traffic below the threshold is
never stored. The evidence states the claim in those words, as WAF does.

**History completeness** (a pure `verify_value_history`, the `verify_endpoint_history` shape):
the history is `[first_seen_at - pw_new_value_history_days, first_seen_at)`, default 7 days (the WAF
default, `backend/app/core/config.py:618`); the window divides by the bucket width; every expected
bucket exists; none has `admission_truncated` or an incomplete discovery (`values_measurement` and
the quota counters); every bucket carries the same `watch_version`; none is `NOT_COLLECTED`. A
startup validator refuses a history at or above `pw_metric_retention_days`, because such a history
could never be complete (the WAF no-extra-search decision of 2026-10-12, `backend/app/core/config.py:843-846`). A reactivated value keeps
its `first_seen_at` and is established, not new. **Pinned values are never "new"**: the operator
named them, and an F.1 threshold is the tool for "alert when DENY appears".

**Learning period:** `pw_new_value_history_days` from the watch's first collected bucket. Until the
history is recorded, every first appearance is `INSUFFICIENT_DATA` ("learning: day 3 of 7"), so a
fresh watch cannot report its entire vocabulary as new. A membership change inside the grace window
(§G.4) around the first appearance is `INSUFFICIENT_DATA` too: the value may have come with the new
source. **When the window was read from QRadar's past, the first collected bucket is the oldest
history bucket, so the period is already over at the hand-off**
(`docs/PHASE-G-HISTORY-BOOTSTRAP.md` §H.1.1).

**Exits, in order:** (1) first appearance not established; (2) the bucket is at or past
`first_seen_at + pw_new_value_window_buckets` (12): `HEALTHY`, "established value"; (3) history
scope `NONE`: `INSUFFICIENT_DATA` naming the missing bucket; (4) **appearance flood**: admission
was truncated in the first-appearance bucket (`rejected_over_quota > 0` or the ceiling reached):
`INSUFFICIENT_DATA`, because a flood of raw values can make an old value look newly admitted; (5)
fewer than `pw_new_value_min_events` (20) events or `pw_new_value_min_buckets` (2) buckets with
traffic since first appearance: `HEALTHY` with exact counts, `INSUFFICIENT_DATA` with lower bounds;
otherwise `ANOMALOUS`.

**Measurement requirement:** `history`: `EXACT` (an absence claim: "not seen before"); `materiality`:
`LOWER_BOUND_PROVES_INCREASE` (minimum events and buckets are non-decreasing in the counts). So a
`LOWER_BOUND` bucket may prove that a new value is material ("at least 40 events") and can never
prove that it is not new. No enabling setting beyond `pw_collection_enabled`: it adds no search
(the WAF no-extra-search decision of 2026-10-12, `docs/PHASE-B-WAF-BEHAVIOR.md` §30.5).

**The flood is a signal, not a hole.** A spray of 300 new usernames in five minutes leaves 20
admitted and 280 refused; the detector correctly refuses to call any of them new. The bucket row
still records `observed_values`, `admitted_values` and `rejected_over_quota`, and the watch page
shows "280 values were refused admission in this bucket". A detector that alerts on the flood itself
is §G.17 Q11, not v1.

### G.8.4 Output 2: threshold alerts **[BUILT — PW-6, PW-6b]**

Not an anomaly type. An F.1 rule on a pinned value (§G.10). Its verdict rule and measurement
requirement are the F.1 ones: `LOWER_BOUND_PROVES_INCREASE` for `GT`/`GE` (a lower bound that
already exceeds the line proves the breach), `EXACT_ONLY` for `LT`/`LE`/`EQ`, sustain and recovery
buckets as configured, unmeasured buckets counting neither way (`docs/PHASE-F-CUSTOM-DETECTIONS.md`
§F.14 pins). No learning period: a rule needs no baseline. Latency is the F.1 latency, cadence plus
bucket plus settle plus 60 s (`docs/PHASE-F-CUSTOM-DETECTIONS.md` §F.13).


---

## G.9 Combination and correlation (question 7)

### G.9.1 Combination **[BUILT — PW-1 model; PW-2b collection; PW-3a/PW-3b detection; PW-7 page]**

A watch with two property references (`Username` x `Action`) tracks **tuples**. Everything in §G.6
to §G.8 applies to a tuple exactly as to a single value; nothing is special-cased except what is
listed here.

- The searches group by both properties and require both to be non-NULL (§G.6). `parsed_events` is
  the events carrying **both**, so coverage is the share of events that had both, and a scope in
  which one property rarely parses shows low coverage rather than a thin tuple list.
- The tuple is the `value_key` (components joined by U+001F, §G.6), with `component_0` and
  `component_1` stored beside it and indexed (§G.7), so a component can be looked up without
  parsing keys.
- The cap that follows from two properties is already in §G.3: 50 untracked tuples kept per bucket,
  20 admitted, 200 active. With two properties of a few hundred values each the space is far larger
  than 200, and the ceiling, not the product, decides what is tracked; the page says "200 of at least
  N combinations are tracked" whenever admission was truncated.
- A pin is a full tuple (§G.6). A marginal ("every `Action` of `alice`") needs a second series per
  component; it is §G.17 Q4.
- A third property is refused (§G.3, §G.17 Q3).

### G.9.2 Correlation, defined **[BUILT — candidate A, the new combination (PW-3a/PW-3b); B, C and D are not built]**

The user wrote "korelasyon ve kombinasyon iyi olur" and did not define the first word. In this
design, **correlation means a claim about the joint behaviour of values that a single property
cannot make**. Four candidates were weighed against what the platform can prove without a new
Ariel search and without leaving read-only:

| Candidate | What it claims | Evidence base | Cost | Decision |
|---|---|---|---|---|
| **A. New combination** | the pair (a, b) never occurred together in the history window, although a and b each occurred before | the tuple series of a 2-property watch, the component index of §G.7 | **0 Ariel**, +1 DB statement when some tuple is in its window | **v1** |
| B. Co-movement | two watches' value series rise and fall together in the same buckets | two existing series per pair | 0 Ariel, but pairs grow with the square of the series, and a bounded pair list needs its own admission | open question (§G.17 Q2) |
| C. Conditional shift | the share of b among a's events moved (`DENY` 5% to 60% for `alice`) | tuple counts **and** the exact marginal of a | needs the marginal series (Q4) | after Q4, not v1 |
| D. QRadar's own correlation | "A then B within N minutes", stateful, per offense | QRadar's CRE and reference data | none here | **stays out**: QRadar does it in real time and on raw events; this platform is read-only against QRadar and aggregates only; `docs/PHASE-F-CUSTOM-DETECTIONS.md` §F.12 already sends "alerting within seconds" to QRadar's own rules |

**Why A.** It is the classic behaviour signal (a known user on a host never used before; a known
account performing an action never performed), it needs only data a combination watch already
collects, it has a precise and checkable absence claim (the WAF/IPS novelty construction,
`docs/PHASE-B-WAF-BEHAVIOR.md` §30.5), and it composes with the rest: a new combination is an
incident in the existing lifecycle, in `/anomalies`, with a fingerprint and a shadow-mode switch.
B is noisier by construction (many pairs, sparse series, a high chance that some pair moves together
by accident) and has no precise claim to fail closed on. C is the better statistic and is blocked
only by a missing marginal.

### G.9.3 `PW_NEW_COMBINATION`, precisely **[BUILT — PW-3a] [BUILT — PW-3b]**

> **As built.** Component histories are explicit pure inputs. A tracked occurrence in a
> lower-bound bucket proves presence; a component that was not tracked, including one seen only
> below admission, classifies conservatively as `PW_NEW_VALUE`. The two novelty verdicts are
> mutually exclusive, and a one-property watch fails closed before combination evaluation.

> **[BUILT — PW-3b].** The component lookup is the one conditional read: a single aggregated
> statement (earliest counted occurrence of each component, per position and per bucket
> measurement, before the tuple's first appearance), issued only while a tuple is inside its novelty
> window. A test pins that it adds exactly one statement.

On a 2-property watch every first-appearing tuple is classified once, at its first appearance, as
exactly one of:

- `PW_NEW_COMBINATION`: **both components are known** and the tuple is new;
- `PW_NEW_VALUE`: at least one component is itself new to the watch, or not known (§G.8.3).

*Known component*: the component value occurs as that component of at least one tuple the watch
tracked in a bucket **before** the tuple's first appearance, under the same `watch_version`. This
is a **presence** claim, so a `LOWER_BOUND` bucket can prove it. *New tuple*: the §G.8.3 claim,
an **absence** claim, so its history needs `EXACT`.

Specified like any detector (`backend/app/anomaly/capabilities.py:394-414` is the template):

| Input | Role | Requirement | Why |
|---|---|---|---|
| tuple history | history | `EXACT` | "never together" is an absence claim: every history bucket recorded, whole discovery, whole admission, same version |
| component history | components known | `LOWER_BOUND_PROVES_INCREASE` | "each seen before" is a presence claim; any counted occurrence proves it |
| tuple events | materiality | `LOWER_BOUND_PROVES_INCREASE` | minimum events and buckets are non-decreasing in the counts |

Fail-closed rules, in addition to the §G.8.3 exits: a component seen only below the admission
threshold is **not known** (the value was never stored), so the tuple falls to `PW_NEW_VALUE`, the
more conservative claim; a pinned tuple is never new; an appearance flood is `INSUFFICIENT_DATA`;
the learning period is the history window; a membership change inside the grace window is
`INSUFFICIENT_DATA`; and the whole detector is `INSUFFICIENT_DATA` on a one-property watch (it has
nothing to combine, and `detectors` may not list it for such a watch).

**Cost.** No Ariel search: the grouped search of §G.6 already returns the tuples. One extra DB
statement per evaluation run, and only when some tuple is inside its window, loads the known
components of every in-window tuple of every watch in one bulk read over the component indexes
(the shape of WAF's "+2 statements only when some endpoint is inside its window",
`docs/PHASE-B-WAF-BEHAVIOR.md` §30.5). Never a query per tuple.

**Wording** (`docs/PRODUCT-UX-GUIDELINES.md` §7): the evidence says "`alice` and `10.1.4.7` were each
observed before; this pair was not observed together in the 7 days before 2026-10-20 09:05 UTC",
never "suspicious" and never "because". A new combination may be a role change, a new device or a
renamed host; the page does not claim intent (§G.2).


---

## G.10 Alerting and the F.1 subject kind (question 8)

### G.10.1 Incident alerts (spike, drop, disappearance, new value, new combination)

- **Fingerprint. [BUILT — PW-4]** A new `pw_anomaly_fingerprint(instance_id, watch_id, detector_type, value_key)`
  beside `ips_anomaly_fingerprint` (`backend/app/alerts/fingerprint.py:125-144`), through
  `compute_fingerprint` with `source_type="pw_value"`, `condition="pw_anomaly"` and the detector
  type as `subtype` (`backend/app/alerts/fingerprint.py:35-50`). The natural key is the stable
  `(instance, watch, value_key)`, not the row id, so an incident survives a value being retired and
  reactivated; the digest is a hash, so the fingerprint never carries the value text. It is a new
  kind, so it cannot deduplicate into a Phase A, WAF, IPS, F.1 or search alert. `watch_version` is
  **not** in the fingerprint: bumping the version resolves the watch's open incidents and alerts
  (actor `pw-engine`, reason `watch definition changed`) because their values no longer exist under
  the new semantics (§G.3).
- **Shadow mode. [BUILT — PW-4]** `PW_COLLECTION_ENABLED` (default **false**, the family switch) and
  `PW_ALERTING_ENABLED` (default **false**), the IPS pair
  (`backend/app/core/config.py:171-173`). With alerting off, incidents are recorded and the engine
  opens, refreshes, resolves and enqueues nothing; turning it on is a decision taken after a live
  validation (the shadow-mode-first pattern of 2026-10-16, `docs/PHASE-F-CUSTOM-DETECTIONS.md` §F.16). An OPEN incident
  opens one alert, a still-anomalous incident refreshes it, RESOLVED resolves it with actor
  `pw-engine` and reason `condition recovered`, and notifications are enqueued per transition
  (`docs/PHASE-D-IPS-BEHAVIOR.md` D.10).
- **Context. [BUILT — PW-4]** Watch name, scope label, detector, severity (the watch's), the value text, the
  reading with its measurement, the band, the bucket bounds. The value text is in the alert context
  and so in notifications, the same exposure as an IPS entity's destination IP.
- **Maintenance. [BUILT — PW-4]** A `LOG_SOURCE` watch honours the source's maintenance state, stored on the
  bucket as `in_maintenance` (`backend/app/custom_rules/loaders.py:207,294` read the same flag for
  WAF and IPS): nothing opens or refreshes, and following IPS a maintenance bucket does not resolve
  an alert (`docs/PHASE-D-IPS-BEHAVIOR.md` D.10). A group watch is in maintenance only when **every**
  resolved member is; a type watch has no maintenance state in v1 (the membership is decided on the
  appliance). That limitation is stated on the watch page.

> **[BUILT — PW-4]** Built as written, with these decisions the text left open. A bucket in
> maintenance still gets its verdict and its incident, but a verdict that would **resolve** an
> incident is held (counters and watermark move, the state does not) until a bucket outside
> maintenance, so an open alert is never stranded by a resolved incident. A version bump resolves
> the alert whatever the bucket's maintenance flag. `DELETE` resolves the watch's open alerts
> (actor: the deleting principal, reason `the watch was deleted`), found through the watch id in
> the alert context, after the 409 check and in the delete's own transaction; a refused delete changes no alert.

> **[BUILT — PW-3c].** A `PATCH` that turns `enabled` to false no longer leaves an incident
> stranded either: in the disable's own transaction the route resolves the watch's OPEN and
> RECOVERING incidents through the engine (`PwAnomalyEngine.resolve_watch_incidents`, reason
> `watch disabled; no longer evaluated`) and their alerts through `resolve_watch_alerts` with the
> changing principal as the actor. Enabling again starts fresh (built exactly so, T-136): the
> first tick after `enabled` turns back to true seeds at the latest closed bucket, so the disabled
> period is never collected and no stored bucket is evaluated a second time, while novelty history
> across the gap is incomplete — a value that appears after it is judged `INSUFFICIENT_DATA`. A
> `PATCH` that leaves `enabled`
> alone resolves nothing.

### G.10.2 Threshold alerts: the F.1 subject kind **[BUILT — PW-6]**

Threshold alerts are an F.1 rule, not a PW detector (user answer 1, "value DENY above 500 per
bucket, sustained 3 buckets"). The extension, all additive:

- `CustomRuleSubjectKind.PW_VALUE` (`backend/app/models/enums.py:198-211`) and
  `CustomRuleMetric.PW_VALUE_EVENTS`; a catalog entry in
  `backend/app/custom_rules/catalog.py:55-207`: unit `events/bucket`, reads
  `pw_value_metric.events`, measurement `LOWER_BOUND_PROVES_INCREASE`, not a ratio, bulk loader
  `PW_VALUE`, and the note "exact for a pinned value when its search was whole; a lower-bound bucket
  proves 'above' only". The existing `EVENTS` entry for IPS entities is the model
  (`backend/app/custom_rules/catalog.py:159-171`).
- A column `custom_rule.pw_value_id` and the recreated pairing `CHECK` (§G.7.4), in the pattern of
  `CUSTOM_RULE_SUBJECT_CHECK` (`backend/app/models/custom_rule.py:46-57`).
- One bulk loader for the kind, one statement per tick however many rules, in
  `backend/app/custom_rules/loaders.py` (the contract at `:1-9`; `implemented_loaders()` at `:33`
  pins that every subject kind has one). Unlike the IPS entity loader, it reads the bucket row too:
  it returns a **measured zero** for a pinned value with no metric row in a bucket whose
  `pinned_measurement` is `EXACT`, because that is the only case in which the zero is proven (§G.8.0).
  An automatic value with no row stays unmeasured.
- Two branches the engine does not have yet: `_family_collected` returns `True` for any kind it does
  not name (`backend/app/custom_rules/engine.py:643-652`), so an unhandled `PW_VALUE` would be
  evaluated while `pw_collection_enabled` is off; and `_retired`/`_label`
  (`backend/app/custom_rules/engine.py:655-698`) need the value's branch. Both are pinned by a
  guard test that every subject kind has a family branch, a retirement branch and a label.
- **A rule pins a pinned value only.** The rule editor's subject picker lists pinned values; to
  alert on an automatic value, the analyst pins it first (one `PATCH` of the watch, shown in the
  editor as a step). Reasons: a pinned value is never retired, so the rule cannot go
  `SUBJECT_RETIRED` through idle retirement; its zero is provable, so `LT` and "silence" rules are
  usable; and a rule cannot silently depend on the 200-value ceiling. Deleting a watch is **refused with 409
  while an F.1 rule pins one of its values**, naming the rules, rather than silently deleting an
  operator's rules by cascade (the existing foreign keys to `waf_endpoint` and `ips_entity` are
  `ON DELETE CASCADE`, `backend/app/models/custom_rule.py:99,103`, which is right for entities that
  retire but wrong for a watch an operator deletes on purpose). The new foreign key is therefore
  `ON DELETE RESTRICT`, and the orphan sweep of F.6 needs no PW case. For the same reason a change that bumps
  `watch_version` (scope or properties, §G.3) is refused with 409 while a rule pins one of the
  watch's values: the rule's value would stop receiving data under the old version. The operator
  detaches the rules or creates a new watch.
- **[BUILT — PW-6b]** The F.1 preview (`custom_rules/preview.py`) reads a `PW_VALUE` rule through
  `load_pw_values` with the engine's coverage floor, one metric statement; the bucket's stored
  `in_maintenance` is its only maintenance. `PUT /property-watches/{id}/pins` returns
  `rules: [{id, name, pw_value_id}]` (the rules that pin a value of the watch after the change).
  A rule created after the 409 check reaches the database's `RESTRICT` key as a 409 (watch delete)
  and a rule create or patch whose value row was deleted meanwhile is a 409 as well, never a 500.
  `PropertyWatchOut.cadence_seconds` is the `PROPERTY_WATCH` family cadence, read once per request
  (the widget's `stale` is older than twice this, §G.13.5).
- The rule's fingerprint is unchanged, `custom_rule_breach_fingerprint(rule_id)`
  (`backend/app/alerts/fingerprint.py:155-167`), and its switches are the F.1 ones
  (`CUSTOM_RULES_ENABLED`, `CUSTOM_RULES_ALERTING_ENABLED`, `backend/app/core/config.py:545-551`).
  The F.1 engine seeds one watermark per subject kind that has an evaluable rule
  (`docs/PHASE-F-CUSTOM-DETECTIONS.md` §F.4 note); `PW_VALUE` is a sixth.
- Latency is the F.1 statement: the rule is judged on collected buckets, about 13 minutes at the
  default 5-minute family cadence (about 23 minutes when the subject is a WAF or IPS metric)
  (`docs/PHASE-F-CUSTOM-DETECTIONS.md` §F.4.4); not a real-time engine
  (`docs/PHASE-F-CUSTOM-DETECTIONS.md` §F.12). The editor repeats the F.12 sentence.
- **[BUILT — PW-6b]** One race remains and is accepted. A rule created on a value that is **unpinned
  between the 409 check and the create's commit** can pass the check; the engine then reads the value
  as no longer pinned and reports the rule `SUBJECT_RETIRED` with reason "the value is no longer
  pinned" (`backend/app/custom_rules/engine.py:714-721`), so the rule judges nothing rather than
  judging a value it may not depend on. No data is lost and no wrong verdict is written; the operator
  re-pins the value or deletes the rule. The database's `ON DELETE RESTRICT` key is the backstop for
  the delete path.


---

## G.11 Permissions, audit and API (question 10) **[BUILT — PW-5, except what the note below defers]**

**Permissions** (`backend/app/security/rbac.py`; `Principal.has` grants a permission by exact name,
by its `prefix:*` wildcard, or by `admin:*`, `backend/app/security/rbac.py:33-38`):

| Permission | Grants | Held by |
|---|---|---|
| `read:property_watches` | list, read, the catalog, scope and property pickers, the values list, per-value series, the widget, PW rows in `/anomalies` | `read:*` satisfies it: viewer, analyst, detection engineer, admin |
| `property_watch:write` | create, edit, enable, disable, delete, preview | detection engineer; admin through `admin:*` (the string is not listed on the admin role, as for `custom_rule:write`, `backend/app/services/seed.py:41-49`) |

Seeding is additive (`ensure_default_roles`, `backend/app/services/seed.py:52-65`): the detection
engineer role gains `property_watch:write`. No other role changes.
The guard sits on the router mount, not on handlers, so an endpoint added later cannot ship
unguarded (`backend/app/api/router.py:146-150` mounts `/custom-rules` this way; `README.md` lines 483-485).

**Audit** (`record_audit`, `backend/app/repositories/audit.py`, used as at
`backend/app/api/routes/searches.py:261-265`): `property_watch.create | .update | .delete |
.enable | .disable | .preview`, with before/after snapshots that hold the **count and digest** of
pinned values and never the values (§G.7.2). `.enable` and `.disable` are used only when `enabled`
is the only field that changed, as for rules. The preview is audited (it spends an Ariel search),
with counts only. Reads are not audited.

**API** (prefix `/api/v1`):

| Method and path | Purpose | Permission |
|---|---|---|
| `GET /property-watches/catalog` | the editor's switches and bounds: `collection_enabled`, `alerting_mode` (`shadow` or `alerting`), `max_enabled`, `enabled_count`, `search_budget`, `budget_used`, the NORMALIZED allowlist, value caps, preview limits | `read:property_watches` |
| `GET /property-watches/scope-options?kind=&q=&limit=` | search the synced inventory for sources, types or groups: id, name, and for a type the number of synced sources, for a group its path and resolved member count. **New**: no route lists types or groups today (`backend/app/api/routes/log_sources.py:66-77`, `fleet.py:34-85` serve sources and fleet aggregates) | `read:property_watches` |
| `GET /property-watches/property-options?scope_kind=&scope_id=&q=` | the custom-property catalog filtered by the scope's types, each property with the types in scope that define it. The existing routes filter by one source's type (`backend/app/api/routes/waf_catalog.py:24-28`, `ips.py:488-493`), and `PropertyCatalogService.catalog` takes one `source_type_id` (`backend/app/services/property_catalog.py:45-52`), so PW-5 adds a many-types filter to the same cached service | `read:property_watches` |
| `GET /property-watches` | list with status, reason, coverage of the last bucket, last collected bucket (UTC), open incident count | `read:property_watches` |
| `POST /property-watches` | create, always **disabled**; validation returns every reason at once (422 `{"detail": {"message", "reasons"}}`, `docs/PHASE-F-CUSTOM-DETECTIONS.md` §F.11) | `property_watch:write` |
| `GET /property-watches/{id}` | one watch with status, resolved scope summary and per-detector state | `read:property_watches` |
| `PATCH /property-watches/{id}` | edit; carries `expected_updated_at`, 409 with `current_version` and `updated_by` on mismatch; enabling is refused with 409 naming the cap or the budget (§G.14) | `property_watch:write` |
| `DELETE /property-watches/{id}` | resolve open alerts, then delete with all values (204); 409 while an F.1 rule pins a value | `property_watch:write` |
| `GET /property-watches/{id}/scope` | resolved member count, groups walked, inventory age, the membership-change history, up to 200 member names | `read:property_watches` |
| `GET /property-watches/{id}/buckets?hours=` | per bucket: total, parsed, unparsed, untracked, measurements, admission counts, truncation flags, `scope_changed`, `in_maintenance`; gaps are absent buckets, never zero | `read:property_watches` |
| `GET /property-watches/{id}/values?hours=&sort=&limit=&offset=` | tracked values, pinned first: latest reading with measurement, band, baseline state, learning progress, `first_seen_at`. Addressed by id, so a value is never in a URL | `read:property_watches` |
| `GET /property-watches/{id}/series?value_ids=...&hours=` | per-bucket series for at most 12 values in **one** bulk read (rule 9), with the expected band | `read:property_watches` |
| `POST /property-watches/{id}/preview` | one real bounded Ariel run of the stored watch, rate-limited (§G.6) | `property_watch:write` |
| `PUT /property-watches/{id}/pins` | add or remove pinned values (each checked against `PW_PINNED_VALUE_PATTERN`) and report which F.1 rules depend on them | `property_watch:write` |

`hours` is 1 to 168 with default 24, the F.1 preview bound
(`docs/PHASE-F-CUSTOM-DETECTIONS.md` §F.7). A value outside an enum stays FastAPI's list 422. Errors
never carry QRadar text, a console address or a secret.


> **As built by PW-5** (`backend/app/api/routes/property_watches.py`,
> `backend/app/services/property_watches.py`, `backend/app/schemas/property_watch.py`). Every
> route above exists **except** `GET /property-watches/{id}/scope` and the resolved member count of
> a group in `scope-options`: both need the pure scope resolver of PW-2b and come in **PW-5b**
> (`scope-options` returns ids, names, a type's synced-source count and a group's path). Two parts of
> `DELETE` wait for their owners: resolving open alerts first is **PW-4** (alerting does not exist),
> and the 409 while an F.1 rule pins a value is **PW-6** (the column does not exist). Likewise
> `PUT /pins` does not yet report the F.1 rules that depend on a pin (PW-6). The delete itself is
> the cascade of the schema: every value, bucket, baseline and incident goes with the watch.
>
> Decisions the table leaves open: the optimistic token is `updated_at` (`expected_updated_at`,
> 409 with `current_version` and `updated_by`), as for rules; `PUT /pins` replaces the whole list
> and carries the same token; the scope travels as a unit in a `PATCH`; pins, severity, detectors
> and `enabled` never bump `watch_version`. A `PATCH` or `PUT /pins` that makes an enabled watch
> dearer re-runs the §G.14 checks. The enable 409s name the cap, the tick budget (with both
> numbers) or the group that is too large; a group's size here comes from `resolve_groups`, the
> minimum the API needs, until PW-5b swaps in the resolver. The preview sends the totals search and
> then the grouped one under a two-search budget: a grouped result cut at the row limit is not
> split (a split would be a third search), so its buckets read `NOT_COLLECTED` and the response
> says so. It refuses (409) a group that is stale (`SCOPE_STALE`), empty (`SCOPE_EMPTY`) or wider
> than one search chunk. It is limited by `rate_limit_search_execution` per client address. Audit
> snapshots hold `pinned_count` and `pinned_digest` (`sha256:` plus the hex digest of the sorted
> pins) and the scope as `KIND:id`; a preview's audit row holds counts and states only. `/anomalies`
> gains `subject_kind=PW_VALUE`, `anomaly_type` accepting `PwAnomalyType`, a `property_watch_id`
> filter and a `pw_value` object; `log_source_id` is `null` for a type- or group-scoped watch.
> The anomaly histogram does not count PW incidents yet (its repository is outside PW-5).

> **[BUILT — PW-5b: `GET /property-watches/{id}/scope` and the resolved member count of a
> group in `scope-options`]** (`backend/app/api/routes/property_watches.py`,
> `backend/app/services/property_watches.py`, `backend/app/schemas/property_watch.py`,
> `backend/app/property_watch/scope.py`). PW-5's minimal `resolve_groups` is gone: the API, the
> enable caps, the preview and the collector all resolve through PW-2b's pure resolver over the
> one bulk inventory read (`resolve_subjects`), so they can never disagree about a scope, and a
> group walk is computed before the freshness check, so an unresolved scope still carries the
> `groups_walked`, `member_count` and `members` the last sync held. `GET /{id}/scope` answers with
> the resolved `member_count`, the cycle-safe walk's group ids and names, the walked group rows'
> oldest `synced_at` as the inventory age in seconds (UTC), the state (resolved, or `SCOPE_EMPTY`,
> `SCOPE_STALE` or `SCOPE_TOO_LARGE`) and its reason, the stored membership changes of the watch's
> current version (a stored bucket whose `scope_fingerprint` differs from the one before it,
> newest first, with the count before and after and whether it is in the grace window; a
> `NOT_COLLECTED` bucket is skipped), and up to `SCOPE_MEMBERS_LIMIT` (200) member names, sorted,
> with the true total. A scope that does not resolve still returns the last sync's members, marked
> with its state, never a zero (§G.4). `scope-options` reports a group's resolved member count and
> a type's synced-source count through the same resolver; a group that is stale or too large shows
> no count and names why in `scope_state`/`scope_reason`, while an empty group's measured zero is
> reported as `0` with `SCOPE_EMPTY`. Every read is bulk and flat in size: `/{id}/scope` is a fixed
> 6 statements for a group (4 for a source or a type), and `scope-options` 3 for groups and 2 for
> types, at any number of groups or sources, one more when the caller does not name the instance
> (rule 9). `property-options` for a group now takes the in-scope types
> from the resolver's own walk (404 when the group row is absent, 409 when it does not resolve).

---

## G.12 Fail-closed rules, in one place **[BUILT — the rules of §G.3, §G.4, §G.6, §G.8 and §G.10]**

Every row is `INSUFFICIENT_DATA` or a named watch status with a reason. None is a zero, none is
`NORMAL`, none lets a detector grant itself lower-bound reasoning.

| Condition | Result |
|---|---|
| `pw_collection_enabled` false | watch `NOT_ENABLED`; no search sent; F.1 rules on its values `NOT_COLLECTED` with the setting named |
| the watch is disabled | `DISABLED`; counters and incidents kept |
| a group's `synced_at` older than `pw_scope_stale_after_hours` | `SCOPE_STALE`; no bucket; incidents hold |
| the scope resolves to no source | `SCOPE_EMPTY`; no bucket |
| the scope needs more than `pw_max_chunks_per_watch` chunks | `SCOPE_TOO_LARGE` (§G.14); no bucket; the editor refuses to enable it and says to split the group |
| the search budget is spent | remaining buckets `NOT_COLLECTED`; watermark holds |
| a search failed or timed out | its buckets `NOT_COLLECTED`; not split; no incident resolves |
| the latest search failed and stored its bucket `NOT_COLLECTED` | `STALE`, naming the bucket; cleared by the next collected bucket |
| zero rows or an absent key from a search that did not complete, or was cut at `cap + 1` | unmeasured, never 0; only a `COMPLETED`, untruncated search yields a measured zero (§G.6 rule) |
| a count that is not an integer-valued float | the whole search fails; unmeasured |
| a multi-bucket search truncated | nothing kept; split by time; at one bucket stored as `LOWER_BOUND` |
| bucket `LOWER_BOUND` | only spike and the F.1 `GT`/`GE` and the materiality inputs may use it; an absent tracked value is unknown |
| `parsed_events = 0` with `total_events > 0` over 12 consecutive collected buckets (counted across ticks) | `NOT_APPLICABLE`; every value verdict `INSUFFICIENT_DATA` |
| coverage below `pw_min_coverage` | drop, disappearance and "below" thresholds `INSUFFICIENT_DATA`; the bucket is not trained |
| the bucket is `scope_changed` (the grace window) | spike, drop, disappearance, new value, new combination `INSUFFICIENT_DATA` ("membership changed"); not trained |
| a cell below `baseline_min_samples` | `INSUFFICIENT_DATA` ("learning") |
| `watch_version` differs | never compared; baselines and history under another version are ignored |
| new-value history incomplete, or admission truncated at first appearance | `INSUFFICIENT_DATA` |
| a value contains a quote, a backslash or a control character at pin time | the pin is rejected (422), never escaped |
| a hostile value in the data | counted, sanitized, never an error (T-013) |
| an unknown `CustomRuleSubjectKind` in the F.1 engine | a guard test fails the build (§G.10.2) |


---

## G.13 UI (question 11)

`docs/PRODUCT-UX-GUIDELINES.md` and `frontend/src/app/tokens.css` are normative; no new colour,
spacing or type literal; existing components only (`PageHeader`, `Tabs`, `Disclosure`, `CodePanel`,
`TableScroll`, `EvidenceBanner`, `LoadingState`/`ErrorState`, `WidgetFrame`;
`docs/PRODUCT-UX-GUIDELINES.md` §8). No dark theme switch, text-only navigation, no emoji, no
gradients.

### G.13.1 Where things live **[BUILT — PW-7]**

**Navigation: the Behavior group**, not Detection. The group is the product's purpose, "detecting
that a log source changed, and explaining what changed" (`frontend/src/lib/nav.ts:3-6`), and a
watch is a behavioral subject with a baseline, a band and incidents. F.1 rules went to Detection
because analysts triage their breaches through `/alerts` (`docs/PHASE-F-CUSTOM-DETECTIONS.md` §F.12);
a watch's incidents go to `/anomalies`, which is in Behavior. One entry, **Property watches**, at
`/behavior/watches`; the pages under it win by longest match like `/behavior/sources`
(`frontend/src/lib/nav.ts:38-48,99-130`). Alternative (Detection, beside Custom rules) is §G.17 Q12.

| Route | The one question it answers |
|---|---|
| `/behavior/watches` | Which property watches exist, and which need attention? |
| `/behavior/watches/new` | What do I want to track, on which scope, and does it parse there? |
| `/behavior/watches/[id]` | What values does this property take on this scope, and has anything about them changed? |
| `/anomalies`, `/anomalies/[id]` (existing) | gain the `PW_VALUE` subject kind: filter option, row, and an investigation view |
| `/custom-rules/new` (existing) | the subject picker gains "Property watch value" (§G.10.2) |
| `/admin/search-cadence` (existing) | lists the `PROPERTY_WATCH` family |

### G.13.2 The list, `/behavior/watches` **[BUILT — PW-7]**

A table, no KPI row: Name, **Status and reason** (second column, as on `/custom-rules`), Scope
("Group, DMZ firewalls, 37 sources"), Properties ("Username x Action"), Coverage of the last bucket
("96 %", or "at least 88 %" when lower-bound), Open incidents, Last bucket (UTC, with the age after
it), Enabled. Rows needing attention (`OPEN`, `STALE`, `NOT_APPLICABLE`, `SCOPE_STALE`,
`SCOPE_TOO_LARGE`) sort first. The latency sentence appears once: "Watches are judged on collected
5-minute buckets, up to about an hour after the events arrive. This is not real-time correlation;
for alerting within seconds, use QRadar's own rules." Writers see **New watch**; others see the list
and are told which permission would add it.

### G.13.3 The watch page, `/behavior/watches/[id]` **[BUILT — PW-7; completed by PW-7b]**

> **[BUILT — PW-7b]** The Threshold rules tab (from `threshold_rules` on the single-watch read),
> the generated AQL in collapsed read-only `CodePanel`s on the Definition tab (from
> `GET /property-watches/{id}/aql`), the values table at 1024 px (baseline and incidents moved under
> the value; a long value takes two lines with the full text behind a disclosure) and the
> investigation view on the real `GET /anomalies/{id}` detail (the band as one rectangle, the
> new-value figures with their units, `pw_value.value_id`). The `/custom-rules/new` link carries no
> preselected kind: the rule editor's subject picker takes no query parameter.

First screen, top to bottom:

1. **Header** (`PageHeader`): the name, then one line: scope, properties, cadence, status pill with
   its reason, last collected bucket in UTC. Edit is a `Disclosure` that reuses the editor, as the
   rule page does (`docs/PHASE-F-CUSTOM-DETECTIONS.md` §F.12).
2. **The series chart**: events per 5-minute bucket for up to **five** values at once, in the
   categorical tokens `--chart-cat-1` to `--chart-cat-5`, never a status hue
   (`docs/PRODUCT-UX-GUIDELINES.md` §11; `frontend/src/app/tokens.css:218-223`). The axis carries its
   unit, "events per 5 min". The default selection is the pinned values, then the top values of the
   latest bucket. **The expected range is one band, and only for a single selected value**: five
   overlapping bands are unreadable. With several values selected the band is off and the line beneath says so. The
   mechanics are those of `VolumeChart` and `WafRequestChart`
   (`frontend/src/components/behavior/VolumeChart.tsx:250,306-326`,
   `frontend/src/components/waf/WafRequestChart.tsx:107-154`): `connectNulls: false` so an
   uncollected bucket is a gap, a `LOWER_BOUND` point carries "at least n", an unknown zero is a gap,
   a measured zero is drawn as 0, incident spans are shaded. Everything the chart carries is also in
   the table below it as text, for assistive technology (§10).
3. **The values table** (`TableScroll`, numbers right-aligned, tabular figures): Value (pinned first,
   marked "pinned"), Events in the latest bucket (with "at least" when lower-bound), Expected range
   for this hour of the week, Share of parsed events (only when the bucket is exact), First seen
   (UTC), State pill: `Learning (n of 168 cells)`, `Normal`, `Spike`, `Drop`, `Disappeared`, `New`,
   `New combination`, `Insufficient data`. A selected row feeds the chart. Paged, sorted server-side.
4. **Coverage and remainder**, directly under the chart on the same time axis: parsed share per
   bucket with the `pw_min_coverage` floor as one labelled line, and a plain sentence for the last
   bucket: "4 % of events in scope carried no `Username`; they are counted here, not as a value." The
   unparsed remainder is **never a table row and never a zero**.
5. **Honesty line** (`EvidenceBanner`): `PARTIAL` or `TRUNCATED` when buckets were lower-bound ("12 of
   288 buckets in this window are lower bounds; absent values in them are unknown, not zero");
   admission counts ("280 values were refused admission at 09:05 UTC"); `SCOPE_STALE` or a
   membership change ("membership changed at 08:00 UTC: 37 to 40 sources; judged against the old
   baseline after 12 buckets").

Tabs below: **Values** (the above, default), **Incidents** (this watch's `/anomalies` rows and the
lifecycle), **Threshold rules** (the F.1 rules pinning its values, their status, a link to create
one), **Definition** (scope membership with the groups walked and the inventory age, the cardinality
caps in force, and the generated AQL in a collapsed `CodePanel`, read-only, rendered as text never
markup). The bucket provenance lives under `TechnicalDetails`.

Windows: 1 h, 6 h, 24 h, 7 d (the F.1 preview bound is 168 h, §G.11). All times show UTC; a relative
age may follow.

### G.13.4 The editor, `/behavior/watches/new` and the disclosure **[BUILT — PW-8]**

One form, five labelled sections, with the preview beside it as the F.1 rule editor does:

1. **Scope.** A kind control (Log source, Log source type, Log source group) and a searchable
   combobox over `GET /property-watches/scope-options`. Each option shows its identity and its
   size ("Firewall type: 14 sources"; "DMZ firewalls: 37 sources, 2 sub-groups"). On choosing, the
   editor shows the resolved member count and, for a group, the sub-groups walked (§G.4). A group
   needing more than `pw_max_chunks_per_watch` chunks is shown disabled with the number and "split
   the group or use a type scope".
2. **Properties.** One or two, each a searchable picker (the T-058 `PropertyInput` pattern,
   `frontend/src/components/fieldmapping/PropertyInput.tsx`) over
   `GET /property-watches/property-options`, showing **which types in scope define each property**
   ("defined on 2 of 3 types in this scope") and disabled with the reason when `name_allowed` is
   false. Normalized `username` is offered beside them. Any other name may be typed (the catalog is a
   suggestion) and is marked "not in the catalog; the preview will show whether it parses". A third
   property is not offered, and the control says that two is the limit and why.
3. **Values.** "Always counted" is a list of pinned values (at most 10): on a one-property watch one
   text field per value; on a two-property watch one pair of fields per tuple. Each is validated
   inline against the pin pattern with the reason ("quotes and backslashes cannot be pinned"). Below
   it, read-only: "The most frequent other values are tracked automatically: up to 20 new values per
   5 minutes and 200 in total", the numbers taken from the catalog response.
4. **Detection.** The detectors this watch evaluates (checkboxes, with a one-line claim each and
   "needs 7 days of history" or "needs a week of baselines") and the incident severity. The
   threshold alerts are a link: "Threshold alerts are custom rules on a pinned value." **Those
   claims are conditional on the history option**: without it a watch learns forward and waits a
   week; with it the history is read first (`docs/PHASE-G-HISTORY-BOOTSTRAP.md` §H.9.3).
5. **Preview.** "Save as disabled and preview" stores the watch and runs the one real bounded
   search of §G.6 over the last closed hour. The result is what the page will show: the values found
   with their counts, the **coverage and the unparsed remainder**, the per-type reading ("`Username`
   parsed on 41 % of events: the 3 types in scope define it on 1"), and the generated AQL read-only.
   An empty result says whether the scope sent nothing or the property parsed on nothing. Enabling is
   a separate, explicit action that states the family switch, the cap and the search budget.

A deterministic summary sentence sits above the actions ("Tracks Username x Action on the 37 sources
of group DMZ firewalls, counted every hour; 2 values always counted; deviation, new-value and
new-combination detection on"), regenerated from the form, never free text. The latency sentence of
§G.13.2 appears once. There is **no AQL field anywhere**.

### G.13.5 The widget

> **[BUILT — PW-9]** `chart.property_watch` in `frontend/src/widgets/` (`PropertyWatchWidget.tsx`,
> `pwWidgetState.ts`). Window fixed at 24 h. `forbidden` and a deleted watch use the widget's own note
> body so the text names who grants `read:property_watches`; `sources` lists every path read.
> **[BUILT — PW-7b]** `stale` is older than `2 x cadence_seconds` from the watch read (an 8-hour family
> is not stale at 3 hours), no longer a fixed 2 hours.

A configurable widget **`chart.property_watch`** in the registry
(`frontend/src/widgets/registry.ts:62-76,105`, the `chart.search_results` precedent at `:365`,
`docs/PHASE-F-CUSTOM-DETECTIONS.md` §F.9.2): `requires: ["read:property_watches"]`, `sources: ["/property-watches/{id}/series"]`, `config: { watch_id }`
enumerated through an option source over `GET /property-watches` (the registry rule: pickers over
ids, never free text, `frontend/src/widgets/optionSources.ts:1-3`), drill-down `/behavior/watches/{id}`
in every state. It draws the pinned values and the top values of the last bucket, at most five, and
the coverage pill. States (`docs/PRODUCT-UX-GUIDELINES.md` §8 `WidgetFrame`; UR.5.4):

| State | Rendering |
|---|---|
| `loading` | the text-only placeholder |
| `ok` | the series with its unit, UTC time of the last bucket, the coverage pill |
| `ok` + `PARTIAL`/`TRUNCATED` | the pill says how many buckets are lower bounds; their points read "at least n" |
| `stale` | the last bucket is older than twice the family cadence; shown with its time and age |
| `empty` | "No bucket has been collected for this watch yet", why (just created, disabled, or the family is off) and when it was created |
| `no_data` | collected, but the property parsed on no event (`NOT_APPLICABLE`): says so, shows the coverage |
| `not_collected` | `PW_COLLECTION_ENABLED` is off, or the scope is stale or too large; names the setting or the reason |
| `error` | what failed (the read), that the previous answer stays on screen, and what to do |
| `forbidden` | names the missing permission, `read:property_watches`, and says who can grant it |

### G.13.6 Everything else every view designs **[BUILT — PW-7, PW-8, PW-9]**

Loading, empty (says what a watch is and that none exists), error (what failed and what to do), the
honesty states `INSUFFICIENT_DATA`, `PARTIAL`, `TRUNCATED`, `NOT_COLLECTED` (guidelines §4 and §6),
and forbidden for writers (the editor hidden, the permission named). The words "live" and
"real-time" appear nowhere except in the sentence that denies it. Acceptance needs Vitest for each
state, §9 (no page overflow at 1440x900, 1280x800 and 1024x768) and §10, and screenshots of every
changed route at the three viewports against a deterministic fixture API.


---

## G.14 Cost, caps and settings (question 9)

Everything below is a design figure. **No Ariel time was measured for this design**, and the storage
numbers are estimates from the WAF row sizes measured in `docs/PHASE-B-WAF-BEHAVIOR.md` §29.7
(164-173 bytes per sparse metric row with its time index, about 1,219 bytes per full baseline cell,
about 600 bytes per bucket record). PW-10 (§G.16) measures the real ones on the lab.

### G.14.1 Ariel **[BUILT — PW-2a: the budget and the caps]**

> **As built by PW-2a:** `PwSearchBudget(pw_max_searches_per_tick, max_per_watch=
> pw_max_searches_per_watch_per_tick)` in `app/property_watch/keyed_search.py` (the
> `IpsSearchBudget` shape). `try_take(watch)` refuses once the tick's total or that watch's share
> is spent, and the driver then leaves the remaining buckets `NOT_COLLECTED`. Every search the
> split driver sends, including the splits, takes one unit. The static-cost check at enable time
> is PW-2b/PW-5.

Per watch per tick the cost is `chunks x (2 or 3)`: the grouped search, the totals search, and the
pinned search when values are pinned (§G.6), where `chunks = ceil(members / pw_max_sources_per_search)`
for a group and 1 for a source or a type. This is the steady-state cost. A watch that is reading
its history also draws on the history budget of §H.4.4 of the bootstrap design
(`docs/PHASE-G-HISTORY-BOOTSTRAP.md`).

| Watch | Searches per tick |
|---|---|
| source or type scope, nothing pinned | 2 |
| source or type scope, values pinned | 3 |
| group of 37 sources, values pinned | 3 (one chunk) |
| group of 120 sources, values pinned | 9 (three chunks) |
| group of 200 sources, values pinned (the largest allowed) | 12 (four chunks) |
| a group above 200 sources | none: `SCOPE_TOO_LARGE`, no bucket |

- **Enabled watches: at most `pw_max_enabled_watches` = 10.** Enabling beyond it is 409 naming the
  limit (the F.1 and F.2 cap pattern, `docs/PHASE-F-CUSTOM-DETECTIONS.md` §F.8.2, §F.11).
- **Tick budget: `pw_max_searches_per_tick` = 40**, one `PwSearchBudget` per instance per tick, a
  family budget of its own beside the generic default of 16
  (`backend/app/core/config.py:159`). **Enabling is also refused (409) when the sum of the enabled
  watches' static costs would exceed it**, so a configuration that cannot fit never starts: ten
  watches at 3 searches is 30 of 40, leaving 10 for splits; two 12-search group watches and six
  3-search watches would be 42 and is refused with the numbers.
- **Per-watch cap: `pw_max_searches_per_watch_per_tick` = 12**, so that one persistently truncated
  watch cannot spend the whole budget. A truncated one-hour search can need up to `2n - 1` = 23
  searches to split down to single buckets (the bound `docs/SEARCH-CADENCE.md` §SC.6 states for a
  failing multi-window search); the cap leaves its remaining buckets `NOT_COLLECTED`, a visible gap,
  never a zero.
- **Rows.** `pw_max_rows_per_search` = 5,000, the largest value T-032 measured
  (`docs/SEARCH-CADENCE.md` §SC.5, `backend/app/core/config.py:162`), about 54 bytes a row
  (`docs/SEARCH-CADENCE.md` §SC.2), so a whole result is about 270 KB. At one hour (12 buckets) and
  the default caps (200 tracked plus 50 untracked values) a search returns at most about 3,000
  rows, so it is whole unless the scope has more than about 400 distinct values per bucket; above
  that the search splits, then stores `LOWER_BOUND`.
- **Time.** Searches measured 0.4 to 3.5 s on the lab (`docs/SEARCH-CADENCE.md` §SC.10, a nearly
  idle console). Forty searches run sequentially per instance, so a tick is about 2.5 minutes at
  the worst measured time and usually well under a minute; at a 1-hour cadence it is inside the
  slot. At a 5-minute cadence a full tick could overrun the slot, and the next slot's
  `claim_slot` simply loses until the run ends (`backend/app/services/search_cadence.py:61`); the
  admin page notes the PW tick's duration.
- **Concurrency.** The PW dispatch sends one search at a time per instance, through the same
  provider limiter as every other family, so the existing budget holds: 3 concurrent per instance
  and 6 globally (`backend/app/core/config.py:122,132`). PW never raises it.
- **Preview:** two searches, rate-limited (§G.6).

### G.14.2 Database **[BUILT — PW-2b] [BUILT — PW-3b]**

> **[BUILT — PW-3b].** Pinned by `tests/integration/test_pw_engine_scale.py`: collection plus
> evaluation is 20 statements per tick at 1 and 10 watches and at 50 and 200 values (the number
> is equal at every size; the ceiling is 40), the component lookup adds one statement only
> while a tuple is in its window, and training is 5 statements per chunk of
> `pw_baseline_values_per_run` values whatever the number of values.

- **Statements per tick: a design target of at most 40**, **constant in the number of watches
  (1 against 10) and in the number of values (50 against 200)**, excluding opening an alert (a few
  statements per new alert through `AlertService.apply_bulk`, the ruled exception of
  `docs/PHASE-D-IPS-BEHAVIOR.md` D.10) and baseline training (5 statements per chunk of
  `pw_baseline_values_per_run` values, `docs/PHASE-B-WAF-BEHAVIOR.md` §30.9). The reads are bulk:
  enabled watches, the instance's groups and sources once for every group-scoped watch, tracked
  values and their latest state for all watches, baseline cells for the current hour of the week,
  detector states, the novelty history and the component lookup. The writes are one statement per
  table per tick: buckets, values, metrics, states, anomalies, transitions, watch status
  (the bulk loaders of `docs/PHASE-B-WAF-BEHAVIOR.md` §29.5, §29.11). Never a query
  per watch, per value or per tuple. The number is pinned by a test in the
  `test_collection_at_the_default_ceiling_is_bulk` style (`docs/PHASE-B-WAF-BEHAVIOR.md` §30.9) and
  is **a target until that test exists**.
- **F.1:** one more bulk loader statement per tick for the `PW_VALUE` kind, constant in rule count
  (`docs/PHASE-F-CUSTOM-DETECTIONS.md` §F.13).
- **Storage per watch at 300 s buckets (288 a day), 35-day retention:**

| | Typical (50 tracked values) | Worst (200 tracked values, every value in every bucket) |
|---|---|---|
| `pw_value_metric` rows a day | 14,400 | 57,600 |
| size a day (173 bytes a row) | about 2.5 MB | about 10 MB |
| size at 35 days | about 87 MB | about 349 MB |
| `pw_bucket` at 35 days (288 x 35 x about 600 bytes) | about 6 MB | about 6 MB |
| baseline cells (values x 168 x about 1.2 KB) | up to about 10 MB | up to about 41 MB |
| ten watches | about 1 GB | about 4 GB |

The ceiling of 200 active values is what bounds the worst row. A query on a hypertable of this size
is by `bucket_start` and watch, both indexed; the table is registered as a hypertable like
`ips_entity_metric` (`backend/alembic/versions/0018_ips_collection.py:181-182`).

### G.14.3 Settings **[BUILT — PW-1]**

> **As built by PW-1** (`backend/app/core/config.py`): every setting below is present with the
> stated default and a field bound; collection and alerting both default false. Startup
> cross-checks, when PW collection is enabled, keep rows within the Ariel cap, the per-watch
> budget within the tick budget, pins within the active ceiling, the baseline lookback within
> retention, and novelty history strictly below retention.

All in `backend/app/core/config.py`, validated, with the defaults below. Names follow the
`ips_*` and `waf_*` families they copy; a `:N` in the Notes column is a line of
`backend/app/core/config.py`. Defaults marked "proposed" are starting points for lab
tuning, not measurements.

| Setting | Default | Notes |
|---|---|---|
| `pw_collection_enabled` | false | family kill switch, the IPS pattern (`:171`) |
| `pw_alerting_enabled` | false | shadow mode (`:173`) |
| `pw_max_enabled_watches` | 10 | 409 above |
| `pw_max_searches_per_tick` | 40 | and the static-cost check at enable |
| `pw_max_searches_per_watch_per_tick` | 12 | |
| `pw_max_sources_per_search` | 50 | `ips_max_sources_per_search` (`:165`) |
| `pw_max_chunks_per_watch` | 4 | 200 sources; above is `SCOPE_TOO_LARGE` |
| `pw_max_rows_per_search` | 5,000 | not above `ariel_multi_bucket_max_rows` (`:162`) |
| `pw_max_values_per_bucket` | 50 | untracked values kept by the fold |
| `pw_max_new_values_per_bucket` | 20 | `ips_max_new_entities_per_bucket` (`:174`) |
| `pw_max_active_values_per_watch` | 200 | |
| `pw_max_pinned_values` | 10 | |
| `pw_value_max_length` | 255 | the stored width |
| `pw_min_admission_events` | 3 | `ips_min_admission_events` (`:178`) |
| `pw_value_active_days` | 7 | `ips_entity_active_days` (`:179`) |
| `pw_min_coverage` | 0.90 | `ips_min_coverage` (`:180`) |
| `pw_scope_stale_after_hours` | 24 | |
| `pw_scope_change_grace_buckets` | 12 | |
| `pw_baseline_lookback_days` | 28 | not above the metric retention |
| `pw_baseline_min_visits` | 2 | 1 to 52; above 1, `min_visits x 7` not above `pw_baseline_lookback_days` |
| `pw_baseline_values_per_run` | 100 | |
| `pw_max_evaluation_buckets_per_run` | 24 | `ips_max_evaluation_buckets_per_run` (`:191`) |
| `pw_spike_ratio`, `pw_min_absolute_delta_events`, `pw_min_bucket_events` | 2.0, 50, 20 | `ips_spike_ratio` and neighbours (`:193-195`), proposed |
| `pw_drop_min_median`, `pw_disappear_min_median`, `pw_disappear_buckets` | 20, 5, 6 | proposed |
| `pw_new_value_history_days` | 7 | `waf_new_endpoint_history_days` (`:618`); validated below the retention |
| `pw_new_value_window_buckets`, `pw_new_value_min_events`, `pw_new_value_min_buckets` | 12, 20, 2 | the WAF novelty defaults (`:620-624`) |
| `pw_metric_retention_days` | 35 | bucket and metric rows |
| `pw_anomaly_retention_days` | 180 | terminal incidents and their transitions; applied by `apply_property_watch_retention` (§G.7.2) |
| `pw_retention_batch_size`, `_max_batches`, `_interval_seconds` | 5,000, 20, 86,400 | the other retention tasks |

Retention is one bounded daily task, `apply_pw_retention`, on the beat entry pattern of
`apply_ips_retention` (`backend/app/workers/celery_app.py:172-175`,
`backend/app/workers/tasks.py:1385-1388`).


---

## G.15 The tests that pin it

Every new test is checked by breaking the code on purpose; none is hard-coded to a
path, a database name or a venv.

**Backend unit.**

- *References and pins.* NORMALIZED allowlist membership; `PROPERTY_NAME_PATTERN` accept and reject;
  three properties refused; `PW_PINNED_VALUE_PATTERN` rejects a quote, a backslash and a control
  character and accepts Unicode letters, digits and common punctuation such as `@`, `.`, `-` and `\`-free paths; a hostile-input property test
  proves the generated AQL contains operator text only inside a validated, quoted position and
  that a rejected value never reaches the generator. Golden AQL strings for source, type and group
  scopes, one and two properties, with and without pins.
- *Scope.* Source, type and group resolution; descendants of a group; **a two-group parent cycle** (group A names B as parent and B names A, as the lab's groups do) terminates, visits each group once and resolves the union of members (the visited set); chunking at the cap; `SCOPE_EMPTY`,
  `SCOPE_STALE` (a group `synced_at` older than the setting, with the swallowed group-list failure
  simulated), `SCOPE_TOO_LARGE`; a fingerprint change opens the grace window.
- *Fold and split.* A key outside the span fails the search; hostile values are counted, never
  raised; the tuple key; a tracked value is kept whatever its rank and an untracked one only inside
  the cut; the cut tail lands in `untracked_events` only when the search was whole; a truncated
  multi-bucket search keeps nothing and splits by time; at one bucket it stores `LOWER_BOUND`; a
  failed search is not split; a spent budget leaves `NOT_COLLECTED`; the per-watch cap; chunk sums
  are exact only when every chunk was whole.
- *Measured zero and counts.* Zero rows from a `COMPLETED`, untruncated search is a measured zero; the same from a failed, cancelled, timed-out or truncated search is unmeasured; a result of exactly `cap` rows is whole and `cap + 1` is truncated; an integer-valued float (`12.0`) is accepted and `12.5`, `-1`, NaN and a string fail the search; NULL and `''` are both unparsed; the pin generator emits `IN` and never OR-ed equality.
- *Coverage.* The remainder is never a value row and never a zero; `parsed = 0` with `total > 0` is
  `NOT_APPLICABLE`, `total = 0` is not; a lower-bound `parsed` over-states the remainder only.
- *Detectors, one test per exit of §G.8.* Spike, drop, disappearance with measured zero against
  unknown zero; the **monotonicity property test** for the spike over randomized lower-bound counts
  (the `test_monotonicity_property` shape, `docs/PHASE-B-WAF-BEHAVIOR.md` §29.2) and its converse
  (a lower bound never opens a drop or a disappearance); coverage gating; the learning state; the
  `scope_changed` exit. New value: every history-completeness failure (missing bucket, truncated
  admission, version mismatch, `NOT_COLLECTED`), the flood exit, the established exit, pinned never
  new, the learning period. New combination: exclusive classification, a component known by a
  lower-bound bucket, a component seen only below the admission threshold, the one-property watch.
- *Capability truth.* Every `PwAnomalyType` has a spec; `detector_status` is `NOT_ENABLED` while
  `pw_collection_enabled` is false and `UNSUPPORTED` for a spec with an unknown baseline metric; no
  `PW_` value is a member of `AnomalyType`, `WafAnomalyType` or `IpsAnomalyType` (rule 6).
- *Fingerprints.* `pw_anomaly_fingerprint` is stable across retirement and reactivation, never
  contains the value text, and differs across detector types and from the IPS, WAF, F.1 and search
  fingerprints.
- *F.1 extension.* Catalog guard: the `PW_VALUE` kind has a loader, a family branch, a retirement
  branch and a label, and an unknown kind fails the build (§G.10.2); `LOWER_BOUND` with `GT` opens
  and never resolves, with `LT` it is `INSUFFICIENT_DATA`; a measured zero only for a pinned value
  whose `pinned_measurement` is `EXACT`; a rule pinning an unpinned value is refused.

**Backend integration.**

- Migration round trip (upgrade, check, downgrade, upgrade, check) with data carried, the family
  seed, the recreated `CHECK`; `alembic check` clean.
- **Statement-count pins** (the `test_waf_scale` shape): collection plus evaluation at 1 and at 10
  watches and at 50 and at 200 values give the same count and stay at or under 40; the novelty
  component lookup adds one statement only when a tuple is in its window; training scales with
  chunks, not values; the `PW_VALUE` loader is constant at 10 and at 100 rules.
- Lifecycle end to end: `INSUFFICIENT_DATA` to `RESOLVED`, an unknown bucket holding state, a
  failed collection never resolving, one active incident per (watch, detector, `value_key`), a
  `watch_version` bump resolving incidents.
- Alerting: nothing opens while `PW_ALERTING_ENABLED` is false or the family is off or the
  `LOG_SOURCE` watch's source is in maintenance; open, refresh, resolve with the fingerprint, and
  two incidents deduplicated into one alert.
- Retention in bounded batches; a value row leaves after retirement and aging; deleting a watch
  removes every value and resolves its alerts; a startup validator refuses a novelty history at or
  above the retention.
- **PW-3c follow-ups.** A retired value's incident and alert resolve (and shadow mode touches no
  alert row); a `PATCH` disable resolves the watch's incident and alert as the principal, a `PATCH`
  that leaves `enabled` alone resolves nothing, and re-enabling starts fresh; retention never
  deletes a value whose incident is still OPEN or RECOVERING, and counts it; a stale baseline cell
  is deleted while training stays a fixed per-chunk cost; the scope staleness reads the oldest
  walked group at the resolver, the collector and `/scope`; `property-options` is flat at 2 and 20
  groups and 10 and 200 sources.
- **Privacy:** a value that never reached admission is found in no table; log capture and audit rows
  of a create, a preview and an incident contain no seeded value; `/anomalies` shows the value text to a `read:*` caller (decided 2026-10-17); the permission matrix
  (401/403/404/409/422) on every route; a test that every route on the PW router carries the mount
  guard.
- The preview sends exactly two searches, persists nothing, is audited with counts only and is
  rate-limited. The mock provider implements the same keyed methods, so the lab profile exercises
  the real fold (`docs/SEARCH-CADENCE.md` §SC.5 item 6).

**Frontend (Vitest).** Every state of §G.13 on the list, the page, the editor and the widget
(loading, empty, error, forbidden naming the permission, `INSUFFICIENT_DATA`, `PARTIAL`,
`TRUNCATED`, `NOT_COLLECTED`, stale, learning, `NOT_APPLICABLE`, scope stale, scope too large); the
chart's gap rules (no zero at an unknown bucket, a measured zero drawn as 0, "at least n" on a lower
bound, the band only for a single selected value, at most five series, categorical tokens only and no
status hue); table figures right-aligned with tabular figures and a unit; the editor's inline
reasons, the disabled `name_allowed = false` entries, the deterministic summary sentence, and that
**no input of the editor accepts or displays AQL as an editable field**; the nav entry and its
longest-match behavior; `colourLiterals.test.ts` and the contrast tests stay green; the F.1 subject
picker's new kind. Acceptance needs screenshots at 1440x900, 1280x800 and 1024x768 of `/behavior/watches`,
`/behavior/watches/new`, a watch page in each honesty state, `/anomalies` with a PW row, and a
dashboard holding the widget, with `scrollWidth === clientWidth` (guidelines §9).

**Live validation (PW-10)** is not a unit test and is listed in §G.16.

---

## G.16 Task split (question 12)

Named PW-1 and on because `G-` is geo's.

**Precondition, from the user on 2026-10-17: no PW task starts until OPS-5,
OPS-6, OPS-9 round 2 and OPS-10 are accepted.** **PW-0 is the one exception and runs now (the user's
answer of 2026-10-17, Q14):** it is read-only, lab only, a
throwaway script outside the repository.

**Migration order.** The head today is `0025_auth_sessions`. PW-1 takes the next number **at build
time** (whatever the head then is), PW-6 the number after it. Only these two tasks add migrations,
and they are sequential; no other PW task may.

| ID | Task | Depends on | Parallel with |
|---|---|---|---|
| PW-0 | Lab probe (read-only): **DONE 2026-10-17**: the L1 to L8 list of §G.17: group filter, `devicetype` with a quoted property, string equality and `IN`, empty versus NULL for an unparsed property, descendant groups in `group_ids`, the `IN` list size, `GROUP BY username`. A throwaway script outside the repository; a report with the raw results | none (runs now, decided 2026-10-17) | OPS work |
| PW-1 | Foundations: enums, models, the first migration (tables of §G.7, family seed, hypertables), all `pw_*` settings with validators, the pure fact dataclasses (the contract PW-2 and PW-3 build against), `PW_PINNED_VALUE_PATTERN` and the reference validation | OPS-5, OPS-6, OPS-9 r2, OPS-10 | none |
| PW-2a | Provider: the keyed `get_pw_values` and totals/pinned searches on the base, REST and mock providers; the AQL generator; the pure fold; the time-first split driver and budget | PW-1, PW-0 | PW-3a, PW-5 |
| PW-2b | Collection: scope resolution (pure, then the one bulk read), admission, persistence by bulk statements, the `dispatch_property_watch_collection` task behind the family gate and `PW_COLLECTION_ENABLED`, the retention task, the statement-count pins | PW-2a | PW-3b |
| PW-3a | Detection (pure): `PW_VALUE_EVENTS_METRIC` baseline builder, the five detectors, capability specs and `detector_status`, history verification, the monotonicity property tests | PW-1 | PW-2a, PW-5 |
| PW-3b | Detection (engine): bulk loaders, the lifecycle, the pipeline step (collect, evaluate, train), `watch_version` handling, cost pins | PW-2b, PW-3a | PW-5, PW-7 |
| PW-4 | Alerting: `pw_anomaly_fingerprint`, shadow mode, `AlertService` wiring, maintenance rules, resolution on version bump and delete | PW-3b | PW-6, PW-7 |
| PW-5 | API: the router and its guard, `property_watch:write`, audit, the catalog, scope and property options (the many-types catalog filter), values and series reads, preview, pins, the `PW_VALUE` subject kind in `/anomalies` | PW-1 (the preview needs PW-2a) | PW-2a, PW-3a, PW-3b |
| PW-6 | F.1 extension: `PW_VALUE` subject kind and metric, the second migration (column and recreated `CHECK`), the loader with measured zeros for pinned values, the engine branches and their guard test, the pin-only rule | PW-2b, PW-5 | PW-4, PW-7 |
| PW-7 | UI read side: nav entry, `/behavior/watches` list, the watch page (chart, values, coverage, honesty line, tabs), the `/anomalies` filter and PW investigation view; every state; screenshots at three viewports | PW-5 | PW-3b, PW-4, PW-6 |
| PW-8 | UI editor: scope picker, property picker with applicability, pins, detectors, preview panel, the F.1 subject-picker kind; every state; screenshots | PW-5, PW-7 (shared chart component); PW-6 for the picker | PW-4 |
| PW-9 | Widget `chart.property_watch`: registry entry, option source, states, tests, screenshot on a dashboard | PW-5, PW-7 | PW-8 |
| PW-10 | Live validation on the lab: shadow mode, a generator-driven value change firing each detector, a new value, a new combination, an F.1 threshold; measure the real Ariel time and the statement counts; turn alerting on only if the user says so | PW-4, PW-6, PW-8, PW-9 | none |
| PW-11 | Docs: README routes, data model and settings, this file's `[BUILT]` markers | PW-1 to PW-10 | none |

Order: PW-1, then PW-2a, PW-3a and PW-5 in parallel, then PW-2b, then PW-3b with PW-7 starting,
then PW-4, PW-6 and PW-8, then PW-9, then PW-10, then PW-11. Each task touches the files it names; PW-7, PW-8 and PW-9 own different frontend directories and share only the chart
component PW-7 creates, which is why PW-8 and PW-9 wait for it. The longest chain is
PW-1, PW-2a, PW-2b, PW-3b, PW-4, PW-10.

**Measured in the lab, 2026-10-18, PW-10** (these real numbers replace §G.14's design estimates
where they differ): a collecting tick's searches took 0.03–0.13 s; that tick ran 31–33 SQL
statements with collection, evaluation and writes together; training wrote 7 statements in one
chunk; and a run spent 50 of 80 searches and 6 of 40 GETs. The caps of §G.14 are unchanged.


---

## G.17 Open questions for the user

The user answered four questions on 2026-10-17 (the property-watch request, §G.0); those answers are binding.

**Answered on 2026-10-17 ~13:35:**

| Question | Answer |
|---|---|
| Q1 value privacy | **(b)**: clear text, readable by anyone with `read:*`, as offenses are. No `property_watch_values:read` permission (§G.7.2) |
| Q2 correlation | **New combination only** in v1; conditional shift and co-movement stay out (§G.9) |
| Q3 properties per watch | **Two** |
| Q14 PW-0 lab probe | **Runs now**, before the OPS work: read-only, lab only, a throwaway script outside the repository (§G.16) |

**Q4 to Q13 and Q15: default accepted (2026-10-17)**, each as the recommendation printed with it below.

### What the lab probe answered (PW-0)

PW-0 ran on 2026-10-17; each row states the lab's answer. The lab could not show nested or multi-group behaviour or the semantics of a doubled quote.

| # | Fact | Lab answer |
|---|---|---|
| L1 | Native AQL filter by log-source group | **Yes, by name only** (`LOGSOURCEGROUPNAME(logsourceid) = '<name>'`; five other forms rejected). Design keeps the inventory-resolved `IN (<ids>)`; the function is a possible later optimisation. |
| L2 | Does a parent group appear in a source's `group_ids` | **Not testable** on this lab (indicative no). The lab's two groups name each other as parent (a cycle), so the descendant walk is cycle-safe (§G.4, §G.15). |
| L3 | `devicetype = <id>` with a quoted custom property | **Yes**; no events lost. No type-scope fallback needed. |
| L4 | String equality, `IN`, escaping | **Equality and `IN` work.** Backslash escape rejected; a doubled quote accepted, matching unproven. Pins use `IN`; a pin with a quote stays refused (Q8). |
| L5 | Unparsed property in a `GROUP BY` | **NULL** (no empty strings seen); the fold counts NULL and `''` as unparsed. |
| L6 | `GROUP BY username` and `QIDNAME(qid)` with the bucket key | **Yes, both.** Counts come back as floats: the fold accepts only integer-valued floats. |
| L7 | A 50-id `IN` list | **Yes** (54 ids, 453 characters). The cap stays a setting. |
| L8 | Real Ariel time and rows | **About 1 s per search** at about 240k events/hour; production not measured (PW-10). **A `LIMIT` cut is invisible**, so `cap + 1` detection is mandatory. |


### Questions

**Q1. Value privacy: answered (2026-10-17), option (b).** Clear text, readable by anyone with `read:*` as offenses are; no separate permission. Retained bounds: admission after three events in one bucket, 35 days of metrics, deletion with the watch (§G.7.2).

**Q2. Correlation: answered (2026-10-17).** New combination only in v1 (§G.9). Co-movement and the conditional shift stay out of v1.

**Q3. Properties per watch: answered (2026-10-17).** Two (§G.3).

**Q4. [default accepted (2026-10-17)] Should a combination watch also keep a series for each component (a "marginal")?** (§G.6,
§G.9) It would allow pinning "every `Action` of `alice`" and the conditional shift of Q2, at the cost
of a second series and a second admission per watch. **Recommendation: not in v1.** A pin is a full
tuple; revisit with Q2.

**Q5. [default accepted (2026-10-17)] Do the baseline and novelty incidents appear in `/anomalies`, and does alerting start in
shadow mode?** (§G.8, §G.10) The design says yes to both: PW incidents join the unified list as
`PW_VALUE`, the value shown as in any list a `read:*` caller sees; F.1
threshold breaches stay out as decided on 2026-10-16; `PW_ALERTING_ENABLED` defaults to false. **Recommendation:
confirm both.**

**Q6. [default accepted (2026-10-17)] The caps.** (§G.14) Ten enabled watches, a tick budget of 40 searches, 200 tracked values and
10 pinned values per watch, 35 days of retention, 180 days of resolved incidents. **Recommendation:
accept as the setting defaults;** they are settings, and PW-10 measures the real cost before anything
is raised.

**Q7. [default accepted (2026-10-17)] May a watch have more than one scope?** (§G.3) The user said "a log source or a type or a
group". One scope per watch keeps resolution, membership churn and the cost bound simple; a group is
already the way to name several sources. **Recommendation: one scope per watch in v1.**

**Q8. [default accepted (2026-10-17)] May a pinned value contain a quote?** (§G.6) Today no: `O'Brien` is refused because the
repository has no proven escape for a string literal; PW-0 (L4) showed a doubled quote is accepted but
not that it matches. **Recommendation: refuse until a value containing a quote is shown to match,** then widen the pattern in one place.

**Q9. [default accepted (2026-10-17)] Which other normalized fields besides `username`?** (§G.3) `event_name` (`QIDNAME(qid)`) is a
string and useful; it is an expression; the lab accepted it with the bucket key (L6), but its population filter is unproven. Numeric and
IP-typed fields stay out. **Recommendation: `username` only in v1; add `event_name` in a v1.1 after
PW-0.**

**Q10. [default accepted (2026-10-17)] After a group's membership changes, should the baselines be thrown away?** (§G.4) The design
skips verdicts for 12 buckets and then judges against the old baselines, so a large membership
change keeps showing as a shift until the cells retrain. Alternative: a per-watch "relearn on
membership change" switch that discards the baselines and starts a new learning period.
**Recommendation: the default as designed, with the switch added only if the lab shows churn that
makes the old baselines misleading.**

**Q11. [default accepted (2026-10-17)] Should a flood of new values raise its own incident?** (§G.8.3) At the admission quota the
detector refuses to call any value new (correct), but a spray of hundreds of new usernames is itself
a signal. A `PW_NEW_VALUE_FLOOD` detector over the counts already stored needs no new search.
**Recommendation: yes, in v1.1,** once the lab shows what a normal admission rate is.

**Q12. [default accepted (2026-10-17)] Navigation group.** (§G.13.1) The design puts **Property watches** under Behavior; the
alternative is Detection beside Custom rules, where the authoring lives. **Recommendation: Behavior**,
because the page is about what the values did and its incidents are in `/anomalies`.

**Q13. [default accepted (2026-10-17)] Is a per-watch cadence needed?** (§G.3) Cadence is set on the search family (`PROPERTY_WATCH`, default 5 minutes, decided 2026-10-18);
a watch cannot be faster than its family. **Recommendation: no;**
if one watch must be checked faster, put it in the 15-minute family setting for everyone and watch
the Ariel budget.

**Q14. PW-0: answered (2026-10-17).** It runs now, before the OPS work, read-only on the lab (§G.16).

**Q15. [default accepted (2026-10-17)] A share-based threshold ("`DENY` above 20 % of parsed events") in F.1?** (§G.8.4) It needs
an exact denominator and a ratio metric, and the user's example is a count. **Recommendation: not in
v1;** add `PW_VALUE_SHARE` to the catalog in v1.1 if analysts ask.

### Limitations the design states, not questions

- No intent claim: a spike, a new value or a new combination is described, never explained
  (`docs/PRODUCT-UX-GUIDELINES.md` §7).
- No per-source breakdown of a value in v1: the searches do not group by source (§G.4).
- A watch learns forward from its first bucket unless history was requested
  (`docs/PHASE-G-HISTORY-BOOTSTRAP.md`); a history read is for learning only and is never judged
  (§G.6).
- A type watch has no maintenance state and a group watch only when every member is in maintenance
  (§G.10.1).
- A bucket is 5 minutes and the family's cadence decides only when the platform looks; an incident
  can arrive up to a cadence later than the events (§G.13.2).
