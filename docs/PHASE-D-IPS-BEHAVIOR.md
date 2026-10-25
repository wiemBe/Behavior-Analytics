# Phase D — IPS traffic behavior (goal G2)

Status: **built, first shipped in `v0.1.0`** (designed 2026-10-13; build status §D.0a). Every
§D.13 task is built, including the analyst UI and the §D.12 live validation, which ran on
the lab sources on 2026-10-14 to 2026-10-16 (T-054 — see the as-built note in §D.12). It builds on the WAF architecture
([`PHASE-B-WAF-BEHAVIOR.md`](PHASE-B-WAF-BEHAVIOR.md) §27–§30), the search cadence
([`SEARCH-CADENCE.md`](SEARCH-CADENCE.md)) and the field-mapping contract
([`FIELD-MAPPING.md`](FIELD-MAPPING.md) §FM.9).

## D.0a Build status (2026-10-14)

Following the convention of WAF §27, each design item below is marked **[BUILT]** (exists in the
code) or **[DESIGN]** (specified here, not built), with a short note where the built thing
differs from the design. Status was checked against the code in this release.

Of §D.13: **built** — T-049 (§D.3 profile, `/ips` admin API), T-050 (§D.5 collection, §D.4
admission, §D.9 persistence), T-051a (§D.6 pure detectors), T-051b (detection tables `0021`, the
versioned baseline builder, `ips_anomaly_fingerprint`), T-051c (the engine, the collect → evaluate
→ train pipeline, and the capability status — **built**; T-051d bounds each source's training by
that source's own evaluation), T-052 (§D.10 alerting), T-053 (`/admin/ips-sources` UI), T-078 (per-field
status on `GET /ips/sources`), T-079 (the IPS bucket width on `/admin/search-cadence`); **not
built** — T-054 (§D.12 live validation).

> **As built (2026-10-16).** The items that were still open when this was written have followed:
> the analyst view's UI (§D.11, then **[BUILT]**) and the live validation (T-054, listed above
> as not built; it ran and was closed by the merge `e26c683` in the git history; §D.12). Every
> §D.13 task is built.

## D.0 What the user asked

G2 (2026-10-12): detect whether the ports, IPs and endpoints seen on the IPS and the WAF arrive at
their normal rates, and alert on a deviation. For the IPS: track **ports and IPs**. Production IPS
is **Cisco Firepower Threat Defense** and **Palo Alto (THREAT logs)**. On 2026-10-12 the user also
asked that field names be changeable from the UI, "the same for some IPS".

## D.1 Facts that constrain the design

From T-027 (lab IPS discovery), T-029 (vendor-native generator), T-030 (live auto-discovery) and
T-041 (property catalog and AQL syntax):

- **Two lab IPS sources exist and parse** (T-030): `PaSeries @ pa-fw-01` (id 412, type 206) and
  `CiscoFirepowerThreatDefense @ ftd-01` (id 413, type 448). The generator drives six scenarios on
  them (`ips-baseline`, `ips-port-sweep`, `ips-new-port`, `ips-source-concentration`,
  `ips-signature-burst`, `ips-block-shift`).
- **Normalized fields carry what G2 needs.** `sourceip`, `destinationip`, `destinationport`,
  `protocolid` are populated on 100 % of FTD and PAN events; the signature resolves through
  `qid` / `QIDNAME(qid)`; `severity` is normalized.
- **No action anywhere.** Neither DSM separates block from allow in a property, the QID or the
  severity. A block ratio needs an operator-authored custom property, mapped from the UI.
- **Absent values are not NULL** (T-027): an absent port is `0`, an absent protocol is `255`;
  `UNIQUECOUNT` counts the `0`; internal events carry loopback IPs. Population is a `> 0` share.
- **Ariel forms measured accepted:** `SUM(eventcount)`, `COUNT(*)`, `UNIQUECOUNT`, `MAX`,
  multi-key `GROUP BY`, `QIDNAME(qid)`, `WHERE … > 0`, quoted normalized fields next to quoted
  custom properties, and the bucket key `starttime / <bucket_ms>`. **`SUM(CASE …)` is rejected.**
- **`LIMIT` caps the whole result, not each group** (T-032); there is no per-group top-N in AQL.
- **Coalescing:** volume is `SUM(eventcount)`; `COUNT(*)` is provenance only.

## D.2 Scope

**In:** per-IPS-source behavior of destination ports, destination IPs, signatures, source-IP
concentration, port fan-out (scans), and — only once an action property is mapped — the block
ratio. Seasonal baselines, the WAF lifecycle and alerting, the field-mapping profile and editor.

**Out:** whole-source event volume (Phase A already judges it on IPS sources); per-source-IP
baselines (cardinality is unbounded; T-027 §4); intent claims (a port sweep is a composition, not
an attack verdict); flows; correlation with offenses.

## D.3 The IPS source profile **[BUILT]**

Built by T-049: migration `0017`, the mapping contract in `app/ips/profile.py`, validation in
`app/ips/validation.py`, upsert and lifecycle in `app/services/ips_profiles.py`. Administration
is the `/api/v1/ips` routes (all guarded by `admin:ips` at the router mount, reads included) and
the `/admin/ips-sources` UI (T-053); unlike the WAF profile there is no IPS CLI. `GET /ips/sources`
also carries each profile's per-field validation status and reasons (T-078).

The unit of IPS configuration is a per-log-source profile, administered exactly as the WAF profile
(§FM.4–§FM.8): versioned, validated read-only and asynchronously, enabled only on a `VALID` current
version, audited, editable in the UI with the same `fieldmapping` components.

```text
ips_source_profile
  id, instance_id, log_source_id (UNIQUE, FK cascade), enabled (false), profile_version (1),
  product_label, preset ("CISCO_FTD" | "PALO_ALTO_THREAT" | null),
  mapping jsonb                  -- {LOGICAL_FIELD: {"kind": "NORMALIZED"|"CUSTOM_PROPERTY", "name": str} | null}
  action_value_map jsonb ('{}')  -- {"BLOCKED": [...], "ALLOWED": [...]}
  validation_status, validated_profile_version, validated_at, validation_report jsonb,
  validation_requested_at, validation_requested_by, updated_by, created_at, updated_at
```

`mapping` is JSONB because IPS references have two kinds; every entry is validated in code:
- `NORMALIZED`: one of a **fixed allowlist** — `sourceip`, `destinationip`, `destinationport`,
  `sourceport`, `protocolid`, `qid`, `severity`, `category`. Never free text.
- `CUSTOM_PROPERTY`: `PROPERTY_NAME_PATTERN`, then existence and population by validation.

| Logical field | Required | Default (both presets) | Feeds |
|---|---|---|---|
| `SOURCE_IP` | yes | `sourceip` | concentration, fan-out, contributor evidence |
| `DESTINATION_IP` | yes | `destinationip` | destination entities |
| `DESTINATION_PORT` | yes | `destinationport` | port entities, novelty, fan-out |
| `PROTOCOL` | no | `protocolid` | port entity key (`443/tcp` ≠ `443/udp`) |
| `SIGNATURE` | yes | `qid` (shown through `QIDNAME`) | signature entities |
| `ACTION` | no | unmapped | block ratio, through the value map |
| `SEVERITY` | no | `severity` | evidence only |

**Validation** follows WAF §27.3 with IPS semantics: population of a port or protocol is the
`> 0` (protocol `< 255`) share; an IP field's population excludes loopback; `SIGNATURE` by QID
reports the share of events whose QID name is generic ("Unknown …") as a warning, not a failure;
`ACTION` discovers its bounded value set for the map. A preset is a prefill and is never trusted.

**Routes:** `/api/v1/ips/…` mirroring §FM.4 (schema, candidates — class `IPS` first, which T-022c
made name-driven for FTD and PA —, profile GET/PUT, validate, enable, disable, history, catalog);
UI `/admin/ips-sources`.

## D.4 Entities and admission **[BUILT]**

Built with T-050 (migration `0018`). The fold bounds each dimension's per-bucket entity list
(`ips_max_entities_per_bucket`); the collector applies the quota, ceiling and retirement controls
and records the outcome per dimension (`admitted`, `below_admission_threshold`,
`rejected_over_quota`, `untracked_entities`, `admission_truncated`) on the bucket row.

An IPS entity is `(instance, log_source, dimension, value_key)`:

| Dimension | `value_key` | Example |
|---|---|---|
| `DESTINATION_PORT` | `<port>/<protocol>` (protocol name when known, else number; `0` never) | `443/tcp` |
| `DESTINATION_IP` | the address, canonical text | `10.10.20.15` |
| `SIGNATURE` | the QID (integer) — the name is display only and may change | `qid:5000123` |

Source IPs are **never** entities; they appear only in bounded contributor lists (≤ 10 per
(source, bucket)), as WAF `top_clients` does. Destination IPs and ports are the protected estate's
own assets and services, which is what G2 asks to baseline.

Admission reuses the WAF controls (§28.5): a per-bucket new-entity quota per (source, dimension),
an active ceiling per (source, dimension), retirement after `ips_entity_active_days` without an
observation. A scan that would mint thousands of port entities hits the quota; the overflow is
recorded, the bucket's discovery is incomplete, and the fan-out detector (D.6) is what sees it.

## D.5 Collection: one keyed search per dimension per tick, for the whole IPS fleet **[BUILT]**

Built by T-050b (migration `0018`): the split driver in `app/ips/keyed_search.py`, the per-tick
plan in `app/ips/collection_plan.py`, the folds in `app/ips/fold.py`, the collector in
`app/collectors/ips_collector.py` and the `dispatch_ips_collection` beat task. The family's
cadence row was originally seeded at 1 h and the 2026-10-18 default moves an untouched row to 15 min. Collection
is gated twice — `IPS_COLLECTION_ENABLED` (default
false) and, per source, an enabled profile whose current version validated `VALID`.

The `IPS_COLLECTION` family (cadence 5 min – 8 h, default 15 min; 1 h before 2026-10-18; §SC.4) drives it. Per tick,
for every enabled, validated IPS source at once:

| Search | AQL shape (bucket key `k = starttime / bucket_ms`) | Yields per (source, bucket) |
|---|---|---|
| Totals | `SELECT logsourceid, k, SUM(eventcount), COUNT(*) … GROUP BY logsourceid, k` (all events) | events, records — the denominator of every coverage |
| Ports | `GROUP BY logsourceid, destinationport, protocolid, k` with `WHERE destinationport > 0`, `ORDER BY SUM(eventcount) DESC` | per-port events |
| Destinations | `GROUP BY logsourceid, destinationip, k` | per-destination events |
| Signatures | `GROUP BY logsourceid, qid, k` | per-QID events (names joined from `QIDNAME` in the same search) |
| Source IPs | `GROUP BY logsourceid, sourceip, k` | top-1/top-5 share, distinct sources, contributors |
| Fan-out | `SELECT logsourceid, sourceip, k, UNIQUECOUNT(destinationport) … WHERE destinationport > 0 GROUP BY logsourceid, sourceip, k ORDER BY 4 DESC` | the largest distinct-port count of one source IP |
| Action (only sources with a mapped action, grouped by property name) | `GROUP BY logsourceid, "<action property>", k` | blocked / allowed / unmapped |

Each search is `LIMIT max_rows + 1`. The per-(source, bucket) top-N cut happens **in the fold**
(pure functions; raw values never leave them except the bounded contributor lists).

**Truncation splits time first, then sources.** A truncated search is re-run on half the window;
at one bucket it is re-run on half the sources (`logsourceid IN (…)`); at one source and one bucket
the result is ordered by volume, so its top-N is exact and its tail is a lower bound — the
dimension's measurement for that (source, bucket) is `LOWER_BOUND` (enough to prove a spike, never
enough to call an entity normal), exactly as WAF §29. The per-tick search cap
(`search_cadence_max_searches_per_tick`) bounds the splitting; buckets beyond it stay uncollected
and the watermark holds, as in Phase A.

Fleet-wide grouping is T-027's advice (§D.1): with 20 IPS sources at a 1 h cadence it is about 6
searches an hour instead of about 120 per-source. A source whose mapping uses a custom property for
a dimension (rare: the defaults are normalized) is collected in a per-property group of sources.

**Coverage** per (source, bucket) and dimension is the dimension search's summed events (which
already exclude port `0`, protocol `255` and loopback addresses through its `WHERE`) over the
totals — the same construction as WAF endpoint coverage, so no extra search. When the dimension
search was truncated for that (source, bucket), the coverage is a lower bound. Below
`ips_min_coverage` the dimension's bucket is `PARTIAL` and excluded from baselines, as WAF.

## D.6 Detectors **[BUILT]**

The pure detectors are built (T-051a: `app/ips/detector.py` for the entity spikes,
`app/ips/behavior.py` for the source-level types, capability specs in `IPS_DETECTORS` at
`app/anomaly/capabilities.py`). The engine that feeds them buckets and applies the lifecycle is
built (T-051c: `IpsAnomalyEngine` in `app/ips/engine.py`, `IpsInstancePipeline` in
`app/services/ips_pipeline.py`). The seven detectors are `OPERATIONAL` when
`IPS_COLLECTION_ENABLED` is true and `NOT_ENABLED` otherwise (the default).

Every detector is pure, has a `DetectorSpec` in `app/anomaly/capabilities.py` declaring its inputs,
and fails closed (D.8). A new `IpsAnomalyType` (never `AnomalyType` or `WafAnomalyType`).

| Type | Subject | Claim | Generator scenario |
|---|---|---|---|
| `IPS_PORT_RATE_SPIKE` | port entity | events above the entity's seasonal band (WAF spike math, IPS thresholds) | `ips-new-port` (after admission), baseline spikes |
| `IPS_DESTINATION_RATE_SPIKE` | destination entity | same, per destination | — |
| `IPS_SIGNATURE_BURST` | signature entity | same, per QID | `ips-signature-burst` |
| `IPS_NEW_DESTINATION_PORT` | source | a port with verified absence over the history window now carries traffic above a floor (WAF novelty §30.5: history completeness proven per bucket; a flood at appearance is `INSUFFICIENT_DATA`) | `ips-new-port` |
| `IPS_SOURCE_CONCENTRATION_SPIKE` | source | the top-1 source IP's share above its pooled baseline, with a volume floor | `ips-source-concentration` |
| `IPS_PORT_FANOUT_SPIKE` | source | one source IP reached more distinct destination ports in a bucket than the source's baseline allows (scan shape) | `ips-port-sweep` |
| `IPS_BLOCK_RATIO_SHIFT` | source | blocked share above its pooled baseline — **published as blocked** ("no action property mapped") until the profile maps one with a value map | `ips-block-shift` (needs a lab custom property, a manual console step by the user) |

## D.7 Baselines **[BUILT]**

The versioned baseline builder is built (T-051b: `IpsBaselineBuilder` in `app/ips/baseline.py`,
migration `0021`; entity cells in `ips_entity_baseline`, source-level pooled cells in
`ips_source_baseline`). The scheduled training step is built (T-051c, T-051d:
`IpsInstancePipeline._train` in `app/services/ips_pipeline.py`). Each source trains through its
own evaluated bucket end; a source that failed or has evaluated nothing is skipped alone, so one
stalled source never holds back another's training (T-051d).

Weekday×hour seasonal cells per entity metric, built like WAF (`app/waf/baseline.py`): median and
MAD over COMPLETE buckets only, `baseline_min_samples`, the lookback, versioned by the profile
version and the normalizer version. Ratio and share metrics (concentration, block ratio) use the
pooled-sum baselines of WAF §30.3. Fan-out uses a source-level cell of the per-bucket maximum.
Sparse entities stay `INSUFFICIENT_DATA` (§12.3 of the WAF design).

## D.8 Fail closed **[BUILT]**

Implemented and tested in the pure detectors (T-051a); the engine-side checks (a profile that is
not `VALID`, a stale or in-maintenance source) are built (T-051c: `IpsAnomalyEngine.evaluate_bucket`
in `app/ips/engine.py`, `IpsInstancePipeline._evaluate` in `app/services/ips_pipeline.py`).

`INSUFFICIENT_DATA`, never `NORMAL` and never zero, when: the profile is not `VALID` for its
current version; the source is stale, disabled or in maintenance; a search was rejected, failed or
timed out; a dimension's bucket is `PARTIAL` or `LOWER_BOUND` and the claim would need an exact
count; the cell has too few samples; the action property is unmapped (block ratio); the port is
`0` or the protocol `255`; the QID name is generic and the claim is about a specific signature.
A rejection's text is never stored; it is logged by class.

## D.9 Persistence **[BUILT]**

Built by migrations `0017` (profile), `0018` (collection) and `0021` (detection). In addition to
the tables listed below, `0021` adds `ips_source_baseline` for the source-level ratio and
fan-out cells of §D.7. `ips_source_bucket` and `ips_entity_metric` are Timescale hypertables;
the baseline, state and incident tables are ordinary PostgreSQL. Retention runs as a bounded
scheduled task under the `ips_retention_*` settings.

Additive migration; Phase A and WAF tables untouched. Mirrors WAF:
`ips_source_profile`, `ips_source_bucket` (per source per bucket: totals, per-dimension
completeness and coverage, concentration and fan-out facts, contributors ≤ 10, truncation flags),
`ips_entity`, `ips_entity_metric` (hypertable), `ips_entity_baseline`, `ips_detector_state`,
`ips_anomaly`, `ips_anomaly_transition`; a seeded `search_cadence` row for `IPS_COLLECTION`.
Retention follows WAF's settings pattern. Per-run SQL stays constant (bulk loaders, as WAF).

## D.10 Alerting **[BUILT]**

Built by T-052. Behind `IPS_ALERTING_ENABLED` (default **false**: shadow mode, as WAF),
an OPEN incident opens one alert with the `ips_anomaly` fingerprint (entity incidents keyed on the
dimension and the entity's stable `value_key`; source incidents on the source), a still-anomalous
OPEN incident refreshes it, RESOLVED resolves it (actor `ips-anomaly-engine`, reason `condition
recovered`), and notifications are enqueued per transition from the scheduled run. The context
carries `source_class` when the source is classified (class routing). Nothing opens or refreshes
while the source is in maintenance.

Two ruled differences from WAF:
- Opening an alert costs a few statements per new alert through the shared
  `AlertService.apply_bulk` (one savepoint per new alert); WAF behaves the same, and this is the
  accepted exception to "constant per (source, bucket)".
- A maintenance bucket does not resolve an IPS alert (the evaluation loop skips maintenance
  buckets entirely); WAF's maintenance path does resolve.

## D.11 API and UI

1. **Profile administration** (§D.3) — **[BUILT]** API and `/admin/ips-sources`, from the field-mapping
   components (T-049, T-053; per-field status on `/ips/sources` from T-078).
2. **Analyst view** — API **[BUILT]** (T-085), UI **[BUILT]** (the analyst UI; the link and filter that show every active IPS anomaly). With the WAF analyst API and UI
   (design §27.14–§27.15), one subject model: anomaly list with `subject_kind` `IPS_ENTITY`/`IPS_SOURCE`, investigation
   detail with the seasonal band, contributors and every honesty state
   (`app/api/routes/anomalies.py`, `frontend/src/components/ips`).

## D.12 Live validation **[BUILT — run 2026-10-14 to 2026-10-16]**

> **As built.** T-054 ran the scenarios below on lab sources 412 and 413; its parts are recorded in
> the git history (`ac44bf6` the port sweep, `0bdc334` signature-burst with alerting on and
> source-concentration, `31bdd47` block-shift with alerts opened and resolved; closed by the merge
> `e26c683`). The block-ratio scenario ran once the lab console had an action custom property to
> map — so the design text's last sentence below stopped holding. Two recorded caveats: hours 07–09
> of one run were voided by a generator defect (D-1, fixed after the defect was found), and a fan-out lead did not
> reproduce (`8be8770`). The rest of the design text below is what was to be validated.

On the lab sources 412 and 413, at ≤ 5 EPS per the lab guide: validate both profiles; run
`ips-baseline` long enough to train the cells the scenario hours need (or a documented shortened
lookback in the validation database only); then each change scenario, and check the matching
detector opens, siblings stay `NORMAL`, and recovery resolves. Bounded Ariel budget, read-only.
The block-ratio scenario needs an action custom property on the lab console, which only the user
can create; it is skipped until then.

## D.13 Task split

| ID | Task |
|---|---|
| T-049 | D.3 profile: model, migration, service (upsert, validate async, enable), the `/ips` admin API, presets, IPS validation semantics — **[BUILT]** |
| T-050 | D.5 collection: provider keyed fleet searches (base, REST, mock), folds, the time-then-source split, `IPS_COLLECTION` family and gate, buckets, entities, admission, metrics — **[BUILT]** |
| T-051 | D.6–D.7 baselines and detectors, capability specs, engine with bulk loaders, lifecycle — **[BUILT]**: T-051a pure detectors, T-051b tables `0021`, baseline builder and fingerprint, T-051c engine, lifecycle and pipeline step, T-051d per-source training bound |
| T-052 | D.10 alerting — **[BUILT]** |
| T-053 | `/admin/ips-sources` UI from the field-mapping components; screenshots — **[BUILT]** |
| T-054 | D.12 live validation — **[BUILT]** (ran 2026-10-14 to 2026-10-16; §D.12's as-built note) |
| T-055 | Docs: README, this design's [BUILT] markers — **[BUILT]** (this revision) |

Order: T-049 → T-050 → T-051 → T-052; T-053 after T-049; T-054 last.
