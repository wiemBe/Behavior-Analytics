# Phase B — WAF Behavioral Analytics

**Status: design and discovery (Phase B0). Not implemented.** No Phase B production code,
migration, model, collector, detector, API or UI exists, and none may be written from the roadmap
alone. This document is the contract that Phase B1 must be built against, and it ends with a
go/no-go gate in §24.

**The gate outcome is NO-GO.** The design below is complete and implementable, but the live QRadar
environment does not currently expose the fields it needs. §24 lists exactly what must change
before Phase B1 begins. Nothing here is blocked on further design work.

Evidence in this document is labelled:

| Label | Meaning |
|---|---|
| **[REPO]** | Verified by reading the current working tree at `ecee6ed`. |
| **[LIVE]** | Measured against live QRadar 7.6.0.0 on 2026-08-03 by bounded read-only Ariel. |
| **[DECISION]** | A design choice made here, with its rationale. |
| **[ASSUMPTION]** | Not verified. Must be confirmed before it is relied on. |
| **[OPEN]** | Unresolved question or blocker. |

---

## 1. Status and scope

### 1.1 In scope for this workstream

A canonical Phase B design: entity model, endpoint normalization contract, observation contract,
completeness semantics, baseline strategy, detector selection, evidence dimensions, persistence
proposal, AQL templates, staged plan, and the acceptance gate.

### 1.2 Explicitly out of scope

No migration, model, collector, detector, API route, frontend component, Celery task, dependency,
or change to Phase A detector mathematics. No WAF or QRadar write of any kind. No raw event
ingestion. No LLM. No Phase C–F work. The common behavior engine is **not** refactored: §16 records
the exact blockers found, and refactoring is deferred until a concrete Phase B1 requirement proves
one of them is real.

### 1.3 Phase A status, restated

Phase A backend, frontend, UI refinement, automated gates and live validation are **complete**
(live-validated 2026-08-02; see `LAB-DEMO-RESULTS.md`). Phase B design is in progress. Phase B
production implementation has not started.

---

## 2. Product objective

Phase A answers *"is this log source still behaving?"*. Phase B narrows the question one level, to
the web-facing surface a WAF protects:

> **Which web endpoint started behaving differently, and what changed during that interval?**

The operationally useful finding is not "WAF traffic doubled" — Phase A's `VOLUME_SPIKE` on the WAF
log source already says that. It is:

> "`/api/v1/report` served an expected 40–60 requests per 5-minute bucket for this weekday and hour
> and served 890. 71% of the increase is attributable to 12 client addresses that do not appear in
> the baseline window, and the blocked share rose from 2% to 78%."

Phase B is therefore justified only where the WAF telemetry can actually separate *endpoint*,
*outcome* and *client*. Where it cannot, Phase B adds a second, noisier way of saying what Phase A
already says correctly, and must not be built. That is the central finding of §5.

---

## 3. Repository findings **[REPO]**

### 3.1 Behavioral engine — what is generic and what is not

| Component | File | Phase B reusability |
|---|---|---|
| Robust statistics | `backend/app/anomaly/statistics.py` | **Reusable unchanged.** Pure functions over `list[float]`. No entity concept at all. |
| Lifecycle state machine | `backend/app/anomaly/lifecycle.py` | **Reusable unchanged.** `next_state(LifecycleInput)` is pure logic over counters, states and two hysteresis integers. Nothing log-source-shaped. |
| Evidence value object | `backend/app/anomaly/evidence.py` | **Reusable unchanged**, once `AnomalyType` gains Phase B members. `AnomalyEvidence` carries only observed/expected/score/threshold/interval/reason. |
| Threshold resolver | `backend/app/anomaly/thresholds.py` | **Reusable with additive fields.** `Thresholds` is a frozen dataclass of scalars; `ThresholdResolver.resolve()` keys on `Criticality` plus a `custom_thresholds` dict. Phase B needs new *fields*, not a new resolver. |
| Baseline builder | `backend/app/anomaly/baseline.py` | **Not reusable as written.** `BaselineBuilder.rebuild_for_source()` takes a `LogSource`, reads `LogSourceMetric`, writes `LogSourceBaseline`, and hard-codes `BASELINE_METRICS = ("average_eps", "event_count")` and the two `LogSource` schedule columns. The *algorithm* — group by `(metric, isoweekday, hour)`, median/MAD/p05/p95, exclusion tally, completeness ratio — is fully generic and is what Phase B should reuse. |
| Detectors | `backend/app/anomaly/detectors.py` | **Not reusable.** `MetricPoint` has 16 log-source-specific fields (`parsed_username_ratio`, `payload_signature`, `distinct_qid_count`, …). Phase B needs its own detector functions over its own point type. |
| Engine | `backend/app/anomaly/engine.py` | **Not reusable as written.** `AnomalyEngine` is typed on `LogSource`, `LogSourceMetric`, `LogSourceBaseline`, `LogSourceDetectorState`, `LogSourceAnomaly` throughout. The *orchestration shape* — build context, run detectors, apply hysteresis, persist transition, drive alerts — is what Phase B mirrors. |

The important conclusion: the parts that are genuinely policy (statistics, lifecycle, evidence,
thresholds) are already generic. The parts that are not generic are the ones bound to the
persistence types, and those are bound by **static typing**, not by hidden assumptions. That is a
good position to extend from and a bad one to rewrite speculatively.

### 3.2 Collection and provider path

- `MetricCollector.collect()` holds a PostgreSQL advisory lock keyed on `(instance, collector)`,
  resumes from a `CollectionWatermark`, and caps backfill at
  `collection_max_backfill_intervals` (default 12). Buckets are floored to the interval grid in UTC
  by `floor_to_interval()`. Upsert is `ON CONFLICT (log_source_id, bucket_start) DO UPDATE`, so
  re-collection overwrites rather than duplicates.
- The watermark advances only past an interval that completed; a partial failure raises out of the
  loop and leaves the watermark on the last whole interval.
- `_zero_filled_rows()` writes explicit `COMPLETE` zero buckets for **monitored sources the Ariel
  result did not mention**. The docstring is explicit that this zero is a measured observation: the
  query succeeded and returned nothing for that source. A failed collection raises before this
  point, so no zero row is written for an unobserved window. §10 explains why this pattern cannot
  be copied to WAF endpoints unchanged.
- `QRadarRestProvider.get_log_source_metrics()` — one Ariel search per interval,
  `GROUP BY logsourceid`, 168-hour window safety bound, 120-second poll ceiling with an explicit
  `cancel_ariel_search()` on timeout, results capped at `page_size × max_pages`.
- Every AQL carries `START <ms> STOP <ms>` in addition to the `starttime` predicate. Commit
  `dc9aa34` records why: without `START/STOP`, QRadar scopes the search to a recent default window
  and intersects it with the `WHERE` clause, so any historical bucket silently returns zero rows —
  indistinguishable from real silence. **Phase B AQL must carry `START/STOP` for the same reason.**
- `_run_bounded_aggregate()` is the sanctioned shape for evidence queries: create, bounded poll,
  cancel on timeout, capped fetch.
- `ProviderCapability.AQL_EXECUTION` is asserted by `self.require(...)` on every Ariel entry point.
  The MCP provider deliberately lacks it. **AQL must continue to run only through the REST
  provider.**

### 3.3 Persistence

- `LogSourceMetric` — TimescaleDB hypertable on `bucket_start`, composite PK
  `(log_source_id, bucket_start)`. Carries `completeness`, `collection_source`, `query_provenance`,
  `collection_duration_ms`, `collected_at`, `watermark_at`.
- `LogSourceBaseline` — unique on `(log_source_id, metric_name, weekday, hour)`, with
  `CHECK weekday BETWEEN 1 AND 7` and `CHECK hour BETWEEN 0 AND 23`. Carries `baseline_version`
  (monotonic), `completeness`, `excluded_sample_count`, `exclusion_counts`, and a bounded
  `observations` sample (last 200).
- `LogSourceDetectorState` — unique on `(log_source_id, anomaly_type)`; holds the hysteresis
  counters, `last_interval_start` (which is what makes evaluation idempotent), `state`,
  `active_anomaly_id`, `open_anomaly_id`.
- `LogSourceAnomaly` — the incident, with `state`, `detected_at`/`opened_at`/`anomaly_start`/
  `anomaly_end`/`resolved_at`, `baseline_version`, `policy_version`, `evidence_status`.
- `AnomalyStateTransition` — one row per lifecycle move, with from/to state, reason, triggering
  bucket and actor.
- `AnomalyExplanation` / `…Dimension` / `…Contributor` — three typed tables, not a JSON blob.
  Contributor uniqueness is `(explanation_id, dimension, value)` with a
  `CHECK contribution_share BETWEEN -1.0 AND 1.0`.
- Migrations: four Alembic revisions, additive, hypertables created with `if_not_exists`, and no
  retention or compression policy set in migration. Retention is applied outside the schema
  migration.

### 3.4 API and frontend

- Three anomaly routes: `GET /anomalies`, `GET /anomalies/summary`, `GET /anomalies/{id}`.
- `AnomalyListItem` declares `log_source_id: uuid.UUID` as **required**, plus
  `log_source_name: str | None`. `AnomalyDetailOut` extends it. `SourceBehaviorOut` is likewise
  keyed on `log_source_id`.
- Frontend investigation surfaces already exist and are generic over *evidence*, not over
  *log sources*: `EvidenceBanner`, `DimensionExplorer`, `ContributorTable`, `LifecycleTimeline`,
  `TechnicalDetails`, `IncidentHeader`, `AnomalyTabs`, `AnomalyRows`, `AnomalyFilters`.
- `EvidenceBanner` and `DimensionExplorer` already render all six `EvidenceStatus` values and all
  four `DimensionAvailability` values, and already withhold new/disappeared counts on a `TRUNCATED`
  dimension (commit `404bc39`).

**Finding:** the *presentation* layer is reusable for Phase B essentially unchanged. The *contract*
is not: a Phase B anomaly has no single `log_source_id` semantics identical to Phase A's, and the
list item has no field for an endpoint. §18 specifies the minimum additive change.

### 3.5 Tests and lab tooling

- `backend/tests/unit/` — 20+ modules including `test_anomaly_lifecycle.py`,
  `test_anomaly_explanation.py`, `test_mock_provider.py`, `test_lab_mode.py`,
  `test_qradar_lab_loggen_phase_a.py`.
- `backend/tests/integration/` — includes `test_metric_collection.py`, `test_baseline.py`,
  `test_anomaly_engine.py`, `test_phase_a_lifecycle.py`, `test_phase_a_api.py`,
  `test_db_constraints.py`, `test_migrations.py`, plus `factories.py`.
- `MockQRadarProvider` (`backend/app/providers/mock.py`) is the fixture path for deterministic
  collector and engine tests. It implements `get_log_source_metrics` and
  `get_dimension_aggregates`; it has **no WAF-shaped surface**.
- `tools/qradar_lab_loggen.py` is the synthetic telemetry generator and **already emits a WAF
  profile**. See §4.1 — it is the single richest WAF field specimen in the repository.
- `tools/lloggen.py` is untracked and is not part of this workstream. It was not read, modified,
  staged or committed.

---

## 4. QRadar/WAF field capability matrix

### 4.1 Repository WAF specimens **[REPO]**

`tools/qradar_lab_loggen.py` emits, for the `waf` profile (`lab-waf-volume-01`), a LEEF 2.0 event
whose detail block is:

```text
httpMethod=POST   url=/api/v1/report?id=1%27+OR+%271%27%3D%271   virtualHost=portal.lab.test
userAgent=sqlmap/1.8   responseCode=403   bytesIn=1360   bytesOut=0   ruleId=942100
```

plus the common envelope: `devTime` (epoch ms), `eventId`, `eventName`, `deviceHostName`,
`deviceAddress`, `src`, `dst`, `srcPort`, `dstPort`, `proto`, `action`, `severity`, `category`.

Two event IDs exist: `WAF_REQUEST_PASSED` and `WAF_REQUEST_BLOCKED`. A `--format vendor` renderer
also emits a ModSecurity line carrying `[client …] [id …] [msg …] [hostname …] [uri …]`.

This is the ideal case. Every Phase B field the roadmap asks for is present *in the payload*.

**The critical caveat, already documented in the repository:** `LAB-SYNTHETIC-TELEMETRY.md` warns
that "URL/request may require a Universal LEEF custom-property mapping on some DSM versions", and
`LAB-DEMO-RESULTS.md` records that `waf-prod-01` was auto-detected as **F5 FirePass** — a VPN DSM,
not a WAF DSM. A field being in the payload is not a field being queryable in AQL. §4.2 measures
the difference.

The Phase A lab table also shows `lab-waf-volume-01` with **no live QRadar ID**, i.e. the
purpose-built WAF lab source was never configured on the appliance.

### 4.2 Live field availability **[LIVE]**

Measured on log source **117** (`F5FirePass @ waf-prod-01`, the highest-volume WAF source), window
`2026-08-02T00:00Z – 2026-08-03T00:00Z`, **11,493 records**.

| Candidate field | QRadar property | Populated | Distinct | Verdict |
|---|---|---:|---:|---|
| Client address | `sourceip` (normalized) | **100.0%** | 763 | **AVAILABLE** |
| Server address | `destinationip` (normalized) | 100.0% | 2 | AVAILABLE but degenerate — 99.8% is one value |
| Event name | `QIDNAME(qid)` | 100.0% | 2 | AVAILABLE but degenerate |
| Category | `CATEGORYNAME(category)` | 100.0% | 2 | AVAILABLE but degenerate |
| Direction | `eventdirection` | 100.0% | 2 | AVAILABLE — `R2L`, not a WAF action |
| Username | `username` (normalized) | 66.0% | 6 | PARTIAL |
| Request URI | `"URL"` (custom property) | **0.0%** | 0 | **UNAVAILABLE** — 11,493/11,493 null |
| Request path | `"URL Path"` (custom property) | **0.0%** | 0 | **UNAVAILABLE** — 11,493/11,493 null |
| Request URI (alt) | `"WAF_URL"` (custom property) | **67.0%** | **3** | **PARTIAL** — 3,799 null; see below |
| HTTP method | *no such property* | — | — | **UNAVAILABLE** — AQL rejected the query |
| Response status | *no such property* | — | — | **UNAVAILABLE** — no status/response property exists |
| WAF action (block/pass) | *not distinguishable* | — | — | **UNAVAILABLE** — see below |
| Application | `"Application"` | 0.0% | 0 | UNAVAILABLE |
| Virtual host | `"Hostname"` | 0.0% | 0 | UNAVAILABLE |
| WAF policy | `"Policy Name"` | 0.0% | 0 | UNAVAILABLE |

Four of these deserve the detail:

**HTTP method does not exist as a QRadar property in this deployment.** The 115 regex custom
properties returned by `/config/event_sources/custom_properties/regex_properties` contain no
`HTTP Method`, no `Request Method`, and no equivalent. A probe query selecting `"HTTP Method"` was
**rejected by QRadar as invalid** — the strongest possible evidence of absence, distinct from a
query that succeeds and returns nulls.

**Response status does not exist either.** The same property list contains `Completion Status`,
`Error Code` and `Access Allowed`, but nothing carrying an HTTP status code. No ratio detector
(`WAF_ERROR_RATIO_SPIKE`) has a numerator.

**Block versus pass is not distinguishable.** 11,468 of 11,493 events (99.8%) carry the single
event name `Web Attack Detected`, category `Web Exploit`, direction `R2L`. The generator's
`WAF_REQUEST_PASSED` / `WAF_REQUEST_BLOCKED` distinction did not survive the F5 FirePass DSM. No
block-ratio detector has a numerator either.

**`WAF_URL` is a partial, attack-only field.** It is populated for 7,694 of 11,493 events (67.0%)
and carries exactly **three** distinct values, all of them attack payloads that include a query
string:

```text
/download?file=../../etc/passwd
/search?q=%3Cscript%3Elab%3C/script%3E
/login?id=1%27OR%271%27=%271
```

The remaining 33.0% of events have no path at all. This is not a WAF request log; it is an attack
alert log with the offending URI attached. Baselining a request rate per endpoint from it would
baseline *attacks per endpoint*, which is a different and much sparser signal than the roadmap
describes.

### 4.3 Cross-source consistency **[LIVE]**

| Log source | Name | Type | 48h events | `WAF_URL` populated |
|---:|---|---:|---:|---|
| 117 | `F5FirePass @ waf-prod-01` | 232 | 17,238 | 67.0% |
| 114 | `F5FirePass @ reverse-proxy-01` | 232 | 9,489 | **0.0%** |
| 128 | `F5FirePass @ portal-waf-01` | 232 | 9,428 | **0.0%** |
| 129 | `F5FirePass @ waf-prod-02` | 232 | 9,419 | **0.0%** |
| 132 | `F5FirePass @ dmz-waf-01` | 232 | 9,421 | **0.0%** |
| 135 | `F5FirePass @ api-waf-01` | 232 | 9,393 | **0.0%** |
| 115 | `F5FirePass @ modsec-waf-01` | 232 | 9,366 | **0.0%** |
| 214 | `waf-prod-01@F5-WAF` | 213 | 0 | never received an event |

**Only one of seven WAF log sources exposes an endpoint at all**, and only for two-thirds of its
events. All seven share the same DSM (type 232) and the same single event name, so the field
*semantics* are consistent — consistently impoverished. Source 214 is the only source configured
with a distinct WAF type and it has never received traffic.

Answering §5's twelve discovery questions directly:

1. **Which WAF sources are available?** Seven with traffic, one configured but silent.
2. **Which normalized fields are consistently populated?** `sourceip`, `destinationip`, `qid`,
   `category`, `eventdirection`. Only `sourceip` is non-degenerate.
3. **Which fields exist only as custom properties?** `URL`, `URL Path`, `WAF_URL`, `URL Host`,
   `Application`, `Hostname`, `Policy Name`, `Rule Name`, `Rule ID`. Of these only `WAF_URL` is
   populated, on one source.
4. **How is the client address represented?** `sourceip`. There is no X-Forwarded-For property and
   no `ip_client`. `sourceip` is the immediate peer.
5. **Is HTTP method consistently parsed?** No — the property does not exist.
6. **Is the request URI consistently parsed?** No — one source, 67% of its events, 3 values.
7. **Is response status numeric?** There is no response status field at all.
8. **How is block/pass represented?** It is not. 99.8% of events share one event name.
9. **Can application / virtual server / policy / hostname / tenant be identified?** No — all null.
   The only available proxy for "application" is the log source itself.
10. **Are semantics consistent across sources?** Yes, but only because all seven use one
    inadequate DSM.
11. **What percentage is missing each field?** Tabulated in §4.2.
12. **Multiple WAF technologies or DSM mappings?** One in practice (type 232). A second
    (type 213) is configured but idle.

### 4.4 Live discovery method and budget **[LIVE]**

- **Path:** `QRadarRestProvider` only. MCP was not used for AQL at any point.
- **Mode:** strictly read-only. No reference set, rule, saved search, log source, offense or any
  other QRadar object was created, modified or deleted.
- **REST config reads:** 5 (`/system/about`, log source list, three custom-property collections).
- **Ariel searches:** **20 issued, 19 succeeded, 1 failed.** The single failure is
  `B0-Q15_http_method_probe`, rejected as invalid because `"HTTP Method"` does not exist — a
  result, not an error.
- **Windows:** 48 h (`2026-08-01T00:00Z – 2026-08-03T00:00Z`) for the two inventory queries;
  24 h (`2026-08-02T00:00Z – 2026-08-03T00:00Z`) for all field, cardinality and density queries.
- **Durations:** 0.1 s – 2.1 s per search. Total Ariel wall time under 25 seconds.
- **Row limits:** 5 to 5,000; every query aggregated with an explicit `LIMIT`.
- **Persistence:** none. No raw event payload was retrieved or stored. No query output is committed;
  only the derived counts and sanitized example paths in this document.
- **Sanitization:** the console hostname, API token and CA path appear nowhere in this document.
  All example addresses are RFC 5737 documentation ranges; all example paths are synthetic lab
  values.

---

## 5. Live discovery findings — the decisive result

> The measured WAF telemetry in this environment supports **neither** the roadmap entity model
> **nor** any of the roadmap's ratio detectors.

- `http_method` — **absent**, so it cannot be part of any entity key.
- `normalized_endpoint` — present on **1 of 7** sources, for **67%** of that source's events, with
  **3** distinct values that are all attack URIs.
- Block ratio, error ratio — **no numerator exists**.
- Application / virtual host / policy — **absent**, so Candidate B has no key.

The roadmap's `client_ip × normalized_endpoint × http_method` is not merely expensive here; two of
its three components do not exist. This document therefore selects a model that the data *could*
support (§7) and reports honestly that the data does not yet support it either (§24), rather than
selecting a model that cannot be built and calling the design complete.

---

## 6. Cardinality and density measurements **[LIVE]**

Log source 117, 24 h, 11,493 records.

| Measurement | Value |
|---|---:|
| Distinct client IPs | **763** |
| Distinct raw paths (non-null) | **3** |
| Distinct normalized endpoints (after §8 rules) | **3** |
| Distinct HTTP methods | **unavailable** |
| Distinct `(client_ip, raw_path)` tuples | **2,958** |
| Distinct `(application, endpoint, method)` tuples | **0** — no application field |
| Mean events per `(client, path)` tuple / 24 h | **3.9** |
| Maximum events for any single tuple / 24 h | **13** |
| Tuples seen exactly once | **278 (9.4%)** |
| Tuples with a non-null path | 2,205 |
| Tuples with a null path | 753 |
| Hours (of 24) containing any traffic | **5** — 10, 11, 12, 17, 18 UTC |

Traffic is bursty lab traffic, not a production diurnal curve: 19 of 24 hours are completely empty,
and within an active minute the source moves up to 272 events across 239 distinct clients. A
`(client, endpoint)` entity therefore sees roughly **one** event in the one bucket it appears in and
zero in every other bucket of the day.

### 6.1 Candidate A — `(instance, waf_log_source, client_ip, normalized_endpoint, http_method)`

Method is unavailable, so the best case is the 4-tuple without it.

| Projection (source 117 alone) | Value |
|---|---:|
| Entity ceiling (763 clients × 4 path values) | ~3,052 |
| Zero-materialized 5-min bucket rows / day | 3,052 × 288 = **878,976** |
| Rows over a 28-day retention | **~24.6 M** |
| Extrapolated across 7 WAF sources | **~172 M** |
| Baseline cells (entities × 168 seasonal cells × 1 metric) | **512,736** per source |
| Baseline cells across 7 sources | **~3.6 M** |

Even without zero materialization the baseline arithmetic alone rules this out. And the statistics
would be worthless: with 19 of 24 hours empty, roughly 79% of every entity's 168 seasonal cells
would carry a median of 0 and a MAD of 0 — precisely the degenerate case `robust_z_with_status()`
exists to refuse to draw conclusions from. Phase A's `BASELINE_MIN_SAMPLES` of 8 would be reached
by almost no cell, so almost every verdict would be `INSUFFICIENT_DATA`.

**Rejected.** Unbounded in the client dimension, attacker-influenced (every new scanning IP mints a
new permanent entity), and statistically hollow.

### 6.2 Candidate B — `(instance, waf_application, normalized_endpoint, http_method)`

`Application`, `Hostname` and `Policy Name` are 100% null; `http_method` does not exist. Candidate B
has **no key at all** in this environment.

**Rejected on availability**, not on cardinality. Were the fields present, this would be the
strongest model: application is operator-owned and therefore bounded, and it survives a client
population that churns.

### 6.3 Candidate C — `(instance, waf_log_source_id, normalized_endpoint)` **[DECISION — selected]**

The log source stands in for the application, because it is the only bounded, operator-owned
identifier the telemetry actually carries. Client IP is demoted to a metric and to contributor
evidence.

| Projection | Value |
|---|---:|
| Entities today (source 117) | **3** |
| Entities across all 7 WAF sources | **3** |
| 5-min bucket rows / day / entity | 288 |
| Bucket rows / day (all entities) | **864** |
| Rows over 28 days | **~24,192** |
| Baseline cells (3 × 168 × 4 metrics) | **2,016** |

Three orders of magnitude smaller than Candidate A, bounded by an operator-controlled quantity, and
the seasonal cells that do fill up would fill up honestly.

---

## 7. Selected entity model **[DECISION]**

```text
(qradar_instance_id, waf_log_source_id, normalized_endpoint)
```

**Why it is baselineable.** The entity count is bounded by the number of endpoints an application
actually publishes, which is a property of the application and not of its attackers. A published
endpoint receives traffic in most buckets of its active hours, so `weekday × hour` cells accumulate
real samples and reach `BASELINE_MIN_SAMPLES`.

**Why it is operationally useful.** "`/api/v1/report` is behaving abnormally" is directly
actionable by an application owner. "Client 203.0.113.7 on `/api/v1/report` is behaving abnormally"
is one row of the evidence that explains it, and §12 keeps that available without paying for it in
the entity key.

**How it bounds cardinality.** Both key components are operator-owned. A log source is created by
an administrator. An endpoint is created by an application deployment. Neither is attacker-settable
— provided §8's normalizer collapses attacker-controlled path variation, which is its primary job.

**What becomes a contributor rather than a key.**

| Demoted from the roadmap key | Where it goes instead |
|---|---|
| `client_ip` | A `distinct_client_count` metric on the bucket, **and** a bounded `client_ip` contributor dimension on the evidence package. |
| `http_method` | Deferred entirely — the field does not exist. If it later appears, §7.1. |
| Application / virtual host | Represented by `waf_log_source_id` until a real application field exists. |

**What is deferred to peer-group or novelty analysis.** Per-client behavior — `WAF_NEW_CLIENT_ENDPOINT`
and peer-group deviation for a new client — is deferred to a later Phase B increment (§19). Both
need a per-client structure, and neither is worth its storage until the per-endpoint baseline is
proven to work.

### 7.1 If `http_method` later becomes available **[DECISION]**

Method is added as a **fourth key column**, not as a separate table, and the normalizer version
(§8.6) is bumped so historical rows stay interpretable. A three-element key with a
`method = 'UNKNOWN'` sentinel is explicitly rejected: it would silently map an unavailable field to
a value, which §9 forbids. Rows written before method existed keep a NULL method and are excluded
from method-keyed baselines rather than merged into them.

---

## 8. Endpoint normalization contract **[DECISION]**

Deterministic, versioned, and defined here in full. **The normalizer is not written in this
workstream** — this is the specification Phase B1 implements and tests against.

### 8.1 Rules, in order

| # | Rule | Behavior |
|---:|---|---|
| 1 | Scheme and authority | Stripped. An absolute-form request target `https://host/a/b` yields `/a/b`. The host is **not** part of the normalized path; it belongs to the entity (§7). |
| 2 | Fragment | Everything from the first `#` is discarded. Fragments are never sent to a server; their presence means a broken parser. |
| 3 | Query string | Everything from the first `?` is discarded **and never persisted anywhere** (§20). |
| 4 | Percent decoding | Decoded **exactly once**, then re-normalized. Double decoding is forbidden — it would make `%252e%252e` collapse to `..` and merge unrelated endpoints. Invalid escapes are left literal. |
| 5 | Invalid bytes / Unicode | Decoded as UTF-8 with `errors="replace"`. Applied NFC. A segment that is not valid UTF-8 after replacement is replaced by `{invalid}`. |
| 6 | Repeated slashes | `//+` collapses to `/`. |
| 7 | Dot segments | `.` removed; `..` resolved, never above the root. A path that attempts to escape the root normalizes to what remains and is flagged `traversal_attempt` in evidence — it is a finding, not a path. |
| 8 | Leading slash | Enforced. A path not starting with `/` gets one. |
| 9 | Trailing slash | Removed, except for the root, which is exactly `/`. `/a/` and `/a` are the same endpoint. |
| 10 | Case | **Preserved.** Lowercasing would merge genuinely distinct endpoints on case-sensitive servers. Case-insensitive matching is a per-log-source configuration option, off by default. |
| 11 | Numeric segments | A segment matching `^\d{1,19}$` becomes `{id}`. |
| 12 | UUID / GUID segments | A segment matching the RFC 4122 shape (with or without braces) becomes `{uuid}`. |
| 13 | Long hex segments | A segment matching `^[0-9a-fA-F]{8,}$` becomes `{hex}`. Covers hashes, object IDs and session tokens. |
| 14 | High-entropy segments | A segment ≥ 16 chars, containing both letters and digits, with Shannon entropy ≥ 3.5 bits/char, becomes `{opaque}`. This is the catch-all for base64url tokens and account identifiers. |
| 15 | API version segments | `^v\d+(\.\d+)?$` is **preserved verbatim**. `/api/v1/x` and `/api/v2/x` are different endpoints and merging them destroys the signal. |
| 16 | File extensions | **Preserved.** `/app.css` and `/app.js` are different endpoints. Extensions are never templated. |
| 17 | Segment length | A segment longer than 64 characters is replaced by `{long}`. |
| 18 | Path length | A path longer than 12 segments is truncated to 12 and suffixed `/{truncated}`. |
| 19 | Empty or malformed | An empty, whitespace-only or unparseable path yields `/` only when the raw value was genuinely `/` or empty-with-a-request. Otherwise the observation is **`MISSING`, not `/`** (§9). |
| 20 | Result cap | If the normalized form still exceeds 255 characters after rule 18, it is `{overlong}`. |

### 8.2 Worked examples, including the difficult ones

| Raw path | Normalized endpoint | Rule applied |
|---|---|---|
| `/download?file=../../etc/passwd` | `/download` | 3 — query stripped before traversal is ever considered |
| `/search?q=%3Cscript%3Elab%3C/script%3E` | `/search` | 3 |
| `/login?id=1%27OR%271%27=%271` | `/login` | 3 |
| `/api/v1/report` | `/api/v1/report` | 15 — version preserved |
| `/api/v2/report` | `/api/v2/report` | 15 — deliberately distinct from v1 |
| `//portal///login/` | `/portal/login` | 6, 9 |
| `/users/12345/orders/98` | `/users/{id}/orders/{id}` | 11 |
| `/o/3f2504e0-4f89-11d3-9a0c-0305e82c3301/edit` | `/o/{uuid}/edit` | 12 |
| `/blob/a3f19c8e4d2b7f60` | `/blob/{hex}` | 13 |
| `/s/eyJhbGciOiJIUzI1NiJ9x7` | `/s/{opaque}` | 14 |
| `/static/app.css` | `/static/app.css` | 16 — extension kept |
| `/%2e%2e/%2e%2e/admin` | `/admin` + `traversal_attempt` | 4, 7 |
| `/%252e%252e/admin` | `/{invalid}/admin`-safe literal `/%2e%2e/admin` | 4 — decoded once only |
| `/caf%C3%A9/men%C3%BC` | `/café/menü` | 4, 5 |
| `/%FF%FE/x` | `/{invalid}/x` | 5 |
| `/a/b/c/d/e/f/g/h/i/j/k/l/m/n` | `/a/b/c/d/e/f/g/h/i/j/k/l/{truncated}` | 18 |
| `/` + 300 chars | `{overlong}` | 20 |
| `/Admin` vs `/admin` | two endpoints | 10 — case preserved by default |
| *(field absent)* | **not `/`** — bucket is `MISSING` | 19 |

### 8.3 What is never persisted

Query strings, fragments, request headers, request bodies, cookies, authorization values, session
identifiers and raw event payloads. Phase B stores the **normalized endpoint only**. The raw path is
not stored in any behavioral table; it may appear transiently inside a bounded evidence query result
and is normalized before it reaches a column.

### 8.4 Attacker-controlled cardinality

Rules 11–14 and 17–18 exist specifically so that a scanner enumerating
`/x/aaaa`, `/x/aaab`, `/x/aaac`, … cannot mint unbounded entities. A per-log-source cap on distinct
new endpoints admitted per bucket (proposed default: 200) is an additional circuit breaker; when it
trips, the bucket is marked `PARTIAL` and the overflow is recorded as a count, never as rows.

### 8.5 Over-normalization is also a failure

Templating file extensions or API versions would collapse `/app.css` with `/app.js`, and `/api/v1/x`
with `/api/v2/x`. Both are forbidden above. Product-specific route templates
(`/orders/{orderId}/items/{itemId}`) are **not** inferred; they may only be supplied by explicit
per-log-source configuration, and that configuration is out of Phase B1 scope.

### 8.6 Versioning

`ENDPOINT_NORMALIZER_VERSION` is an integer constant, starting at **1**, stored on every observation
row and every baseline row. Any change to the rules above bumps it. A baseline is computed only over
observations sharing its normalizer version; a version change makes existing cells stale rather than
silently wrong, and a rebuild repopulates them. Historical rows keep their old version and remain
interpretable — this is why the version is a column and not a configuration value.

---

## 9. Canonical observation contract **[DECISION]**

One row per `(instance, waf_log_source, normalized_endpoint, bucket_start)`.

| Field | Source property | Req. | Missing-field semantics | Persisted |
|---|---|---|---|---|
| `qradar_instance_id` | internal | yes | n/a | yes |
| `waf_log_source_id` | internal FK | yes | n/a | yes |
| `normalized_endpoint` | §8 over `"WAF_URL"` (per-source configured) | yes | absent → **bucket not written**; the source's endpoint coverage ratio is recorded instead | yes |
| `bucket_start` / `bucket_seconds` | collector grid | yes | n/a | yes |
| `request_count` | `SUM(eventcount)` | yes | query failure → **`MISSING`**; successful empty result → measured zero | yes |
| `blocked_count` | *no source field* | no | **NULL, never 0** — field is unavailable | yes, nullable |
| `error_count` | *no source field* | no | **NULL, never 0** | yes, nullable |
| `distinct_client_count` | `UNIQUECOUNT(sourceip)` | no | NULL when `sourceip` is unavailable | yes, nullable |
| `endpoint_coverage_ratio` | derived | yes | fraction of the source's events in this bucket that carried a parseable endpoint | yes |
| `completeness` | collector | yes | `COMPLETE` / `PARTIAL` / `MISSING` per §10 | yes |
| `collection_source` | collector | yes | `"ariel_aggregate"` or `"mock"` | yes |
| `query_provenance` | collector | yes | AQL text, window bounds, row count, truncation flag. Never a token or header. | yes |
| `collection_duration_ms`, `collected_at`, `watermark_at` | collector | yes | mirrors `LogSourceMetric` | yes |
| `normalizer_version` | constant | yes | n/a | yes |
| `policy_version` | settings | yes | n/a | yes |
| `client_ip` | `sourceip` | — | **evidence-only.** Never a column on this table. | no |
| `http_method` | *unavailable* | — | not modelled until §7.1 applies | no |
| `waf_application` | *unavailable* | — | not modelled | no |

**`SUM(eventcount)`, not `COUNT(*)`.** `LAB-DEMO-RESULTS.md` records that QRadar coalesces identical
events and that raw record counts therefore understate true volume — it also records that the Phase A
metric AQL still uses `COUNT(*)` and that correcting it was deliberately deferred. Phase B must not
inherit that defect: it starts with `SUM(eventcount)`. **[OPEN-4]** tracks whether Phase A should be
corrected to match.

**Nothing unavailable is mapped to zero or to an empty string.** `blocked_count = 0` would assert
"we measured zero blocks"; the truth is "this DSM cannot tell us". That is NULL, and the API renders
it as unavailable rather than as a number.

---

## 10. Bucket and completeness semantics **[DECISION]**

Phase A's invariant is preserved exactly: **`COMPLETE` zero is a measured zero; `MISSING` is no
reliable observation; `PARTIAL` is an incomplete one; NULL is not a measured zero.**

### 10.1 Why Phase A's zero materialization cannot be copied

`MetricCollector._zero_filled_rows()` writes a zero row for every *monitored source* absent from the
Ariel result. That works because the set of log sources is small, operator-owned, and enumerable
before the query runs. **WAF endpoints are none of those things.** Copying the pattern would write a
zero row for every endpoint ever observed, in every bucket, forever — the 878,976 rows/day of §6.1.

### 10.2 Entity monitoring lifecycle

| Concept | Rule |
|---|---|
| **Activation** | An endpoint becomes monitored the first time it is observed with `request_count > 0` in a `COMPLETE` bucket. `first_seen_at` is that bucket's start. |
| **Zero materialization precondition** | Zero buckets are written **only** for endpoints that are already monitored **and** currently active. An endpoint is never zero-filled before it has been seen. |
| **Active window** | An endpoint is active while it has been observed within `WAF_ENDPOINT_ACTIVE_DAYS` (proposed default **7**). |
| **Retirement** | After `WAF_ENDPOINT_ACTIVE_DAYS` with no observation, the endpoint is marked retired. Zero materialization stops immediately. Its rows and baselines are retained until retention expires. |
| **Reactivation** | A retired endpoint observed again is reactivated with a **new** `first_seen_at` segment; §14's novelty semantics treat this as a reappearance, not a first sighting. |
| **Bounding** | Zero rows are bounded by (active endpoints × buckets), and active endpoints are bounded by the retirement policy. This is what keeps §6.3's projection true over time. |

### 10.3 Zero, missing and failure

| Situation | Result |
|---|---|
| Query succeeded; this active endpoint returned no rows | `request_count = 0`, `completeness = COMPLETE`, `query_provenance.zero_filled = true` |
| Query succeeded; endpoint is not yet monitored | **no row** — absence is not an observation |
| Query failed, timed out, or was cancelled | **no row**, and the watermark does not advance. On a later forced write: `completeness = MISSING` |
| Result hit the row cap | `completeness = PARTIAL`, `query_provenance.truncated = true` |
| New-endpoint circuit breaker tripped (§8.4) | `completeness = PARTIAL` for that bucket |
| Endpoint coverage ratio below `WAF_MIN_ENDPOINT_COVERAGE` (proposed **0.90**) | `completeness = PARTIAL` — a bucket where a third of events had no parseable path is not a whole observation of that endpoint's traffic |

That last row is what §4.2's 67% coverage actually means in practice: under this contract, **every
bucket on source 117 today would be `PARTIAL`**, and `PARTIAL` buckets never enter a baseline and
never produce a verdict. The design refuses to pretend, and that refusal is the mechanism behind
§24's NO-GO.

### 10.4 Other required behaviors

- **Endpoint absence is not anomalous.** An endpoint receiving no requests is normal. Phase A
  already detects total log-source silence (`NO_EVENTS`), and Phase B must not raise a second alert
  for the same physical condition. Request-rate **drop** is therefore excluded from the MVP (§11).
- **Late-arriving events.** Buckets are re-collectable; the upsert overwrites. A baseline computed
  before a late correction lags until the next rebuild — the same documented limitation as Phase A.
- **Idempotency.** `ON CONFLICT (waf_log_source_id, normalized_endpoint, bucket_start) DO UPDATE`,
  and the detector state's `last_interval_start` makes evaluation a no-op on replay.
- **Deleted or renamed applications.** A removed log source cascades. A renamed endpoint is simply a
  retirement plus a new activation; the design makes no attempt to correlate them, because guessing
  that `/v1/x` "became" `/v2/x` would be an invention.
- **Watermark lag.** A bucket collected while the watermark is behind is `PARTIAL` until re-collected
  at or after `bucket_end + WAF_LATE_ARRIVAL_GRACE_SECONDS`.

---

## 11. Initial metric and detector scope **[DECISION]**

### 11.1 Candidate metrics assessed against measured availability

| Metric | Numerator source | Available now | MVP |
|---|---|---|---|
| `request_count` | `SUM(eventcount)` | **yes** | **yes** |
| `request_rate` | derived from `request_count` | yes | yes (derived, not stored twice) |
| `distinct_client_count` | `UNIQUECOUNT(sourceip)` | yes | yes — collected, not yet a detector |
| `blocked_count` | *none* | **no** | no |
| `block_ratio` | *none* | **no** | no |
| `error_count` | *none* | **no** | no |
| `error_ratio` | *none* | **no** | no |

### 11.2 Candidate detectors

| Detector | Verdict | Why |
|---|---|---|
| **`WAF_REQUEST_RATE_SPIKE`** | **MVP** | The only detector whose metric exists, whose direction is unambiguous, and which Phase A's proven conjunctive guard structure ports to directly. |
| `WAF_REQUEST_RATE_DROP` | Deferred | Duplicates Phase A `VOLUME_DROP`/`NO_EVENTS` for the dominant real cause (the source stopped reporting), and §10.4 forbids treating endpoint silence as anomalous. Revisit once per-endpoint baselines are proven. |
| `WAF_BLOCK_RATIO_SPIKE` | **Blocked** | No numerator exists (§4.2). |
| `WAF_ERROR_RATIO_SPIKE` | **Blocked** | No numerator exists (§4.2). |
| `WAF_NEW_ENDPOINT` | Deferred to B4 | Cheap once §10.2's `first_seen_at` exists, but it is a novelty finding, not a behavioral one; shipping it alongside the first baseline detector muddies the validation. |
| `WAF_NEW_CLIENT_ENDPOINT` | Deferred to B4 | Requires a per-client structure that §7 deliberately does not build yet. |
| Peer-group deviation for a new client | Deferred to B5 | Needs a peer definition that the current field set cannot express. |

**MVP: one detector family — `WAF_REQUEST_RATE_SPIKE`, with bounded contributor evidence.** This is
the candidate §11 of the task brief anticipated, and the cardinality and field analysis confirms it
rather than assuming it.

### 11.3 Ratio-metric contract, for when the fields arrive **[DECISION]**

Specified now so a later increment cannot invent it ad hoc.

- **Numerator / denominator.** `block_ratio = blocked_count / request_count`;
  `error_ratio = error_count / request_count`. Denominator is always the same bucket's
  `request_count`.
- **Minimum denominator guard.** `request_count >= WAF_MIN_RATIO_DENOMINATOR` (proposed **50**).
  Below it the ratio is computed for display but **may not produce a verdict** — 1 block out of 2
  requests is a 50% block ratio and means nothing.
- **Zero denominator.** Ratio is **NULL**, never 0 and never 1. This mirrors
  `statistics.ratio()`, which already returns `None` rather than dividing by zero, and its docstring
  states why: "no ratio exists" is a different finding from a very large ratio.
- **Completeness.** A ratio requires both counts from the same `COMPLETE` bucket. If either is NULL
  the ratio is NULL.
- **Baseline.** Robust seasonal baselines **are** appropriate for ratios, but on the ratio itself,
  not on the counts. A ratio is already scale-free, so median/MAD over `weekday × hour` behaves
  well.
- **Both conditions must hold.** A ratio detector fires only when the seasonal deviation guard **and**
  a fixed absolute floor (e.g. block ratio ≥ 0.20) are both satisfied. A jump from 0.1% to 0.4% is a
  4× seasonal deviation and is operationally nothing.
- **Representation.** Stored as a float in `[0, 1]`. Rendered as a percentage rounded to one decimal
  place. Never rounded before storage.

### 11.4 Novelty-detector contract, for B4 **[DECISION]**

- **Lookback:** `WAF_NOVELTY_LOOKBACK_DAYS`, proposed **28**, aligned with `baseline_lookback_days`.
- **First-seen:** an endpoint or `(client, endpoint)` pair with no observation in the lookback.
- **Reappearance:** a pair seen before the lookback but not within it is `REAPPEARED`, not `NEW`.
  The two are distinct findings and must not be collapsed.
- **Retention:** novelty is only as trustworthy as the retention behind it. If retention is shorter
  than the lookback, every value is "new" and the detector must report `INSUFFICIENT_DATA` instead.
- **NAT and proxies:** `sourceip` is the immediate peer. Behind a NAT or CDN it identifies the
  intermediary, so a "new client" may be one NAT pool rotating. Any claim must be worded as
  *"client address not observed in the baseline window"*, never *"new user"*.
- **X-Forwarded-For:** no XFF property exists here (§4.2). If one is later configured it is
  **untrusted by default** — XFF is attacker-settable — and may be used only for a log source
  explicitly flagged as sitting behind a trusted proxy.
- **Allow/deny lists:** interact at the *presentation* layer only. A denylisted client is annotated,
  never suppressed from evidence, and never triggers an action (§20).
- **Classification:** `WAF_NEW_ENDPOINT` is a **behavioral anomaly** (an application published a
  surface it did not have). `WAF_NEW_CLIENT_ENDPOINT` is **contextual evidence** — on its own it is
  the normal condition of any internet-facing service.

---

## 12. Baseline strategy **[DECISION]**

### 12.1 Reused from Phase A, unchanged

| Element | Phase A value | Phase B |
|---|---|---|
| Seasonality | `weekday × hour`, 168 cells | **same** |
| Lookback | `baseline_lookback_days`, 28 | **same** |
| Minimum samples | `baseline_min_samples`, 8 prod / 4 lab | **same** |
| Estimator | median + MAD, p05/p95 as the displayed band | **same** |
| Zero-MAD handling | `effective_scale()` floor + `RobustScoreStatus.DEGENERATE` + deterministic expected-bound fallback | **same** |
| Minimum absolute delta | conjunctive guard | **same structure**, WAF-specific value |
| Consecutive buckets | `open_after` / `resolve_after` | **same** |
| Exclusion of anomalous intervals | span ends at `anomaly_end`, per `4892f8e` | **same** |
| Baseline versioning | monotonic `baseline_version` | **same** |
| Policy versioning | `anomaly_policy_version` on every anomaly | **same** |

**No Phase A default is changed by Phase B.** Phase B introduces its own settings keys
(`waf_*`) so that tuning one can never move the other.

### 12.2 Phase B differences

1. **A fourth exclusion reason.** Phase A excludes maintenance, off-hours, known-anomaly, incomplete
   and unfinished buckets. Phase B adds **`low_endpoint_coverage`** (§10.3), tracked in the same
   `ExclusionTally` shape so an operator can see exactly why a thin baseline is thin.
2. **Entity activity gating.** A retired endpoint's buckets are not rebuilt.
3. **Normalizer-version scoping.** A baseline is built only from observations sharing its
   `normalizer_version` (§8.6).
4. **Metric set.** `request_count` and `distinct_client_count`, not `average_eps` and `event_count`.

### 12.3 Sparse entities — the explicit decision

For an endpoint that is genuinely sparse — fewer than `baseline_min_samples` observations in a
seasonal cell over the lookback — the decision is:

**Return `INSUFFICIENT_DATA`. Do not substitute a different model.**

- A **non-seasonal rolling baseline** is rejected for the MVP: it would let a quiet endpoint produce
  verdicts that the seasonal model correctly refuses, which is a regression in honesty dressed up as
  coverage.
- A **peer-group model** is rejected for the MVP: peers cannot be defined without an application or
  method field.
- **Novelty** is the right answer for genuinely sparse endpoints, and it is exactly what B4 adds.
- **Deferral** is the answer in the meantime.

No new statistical model is proposed. Everything above is median/MAD over the existing seasonal key,
which is deterministic, reproducible from stored samples, and explainable to an analyst — the three
properties `statistics.py` was written to preserve.

---

## 13. Evidence and investigation design **[DECISION]**

The question is unchanged: **what changed during the anomalous WAF interval?**

### 13.1 Contributor dimensions

| Dimension | QRadar property | Available **[LIVE]** | Query cost | Cardinality risk | Cap | Claims permitted |
|---|---|---|---|---|---:|---|
| `client_ip` | `sourceip` | **yes**, 100% | 1 aggregate | high (763/day) | 20 | contribution share; "not observed in baseline window" |
| `event_name` | `QIDNAME(qid)` | yes, degenerate | 1 aggregate | very low | 20 | contribution share only |
| `category` | `CATEGORYNAME(category)` | yes, degenerate | 1 aggregate | very low | 20 | contribution share only |
| `username` | `username` | partial (66%) | 1 aggregate | low | 20 | contribution share, with the 34%-missing caveat |
| `destination_ip` | `destinationip` | yes, degenerate (2) | 1 aggregate | very low | 20 | contribution share |
| `endpoint` | — | n/a | — | — | — | **excluded** — it is the entity key, not a dimension |
| `waf_action` | — | **no** | — | — | — | `UNAVAILABLE` |
| `response_status` | — | **no** | — | — | — | `UNAVAILABLE` |
| `signature` / `rule_id` | `"Rule ID"` | **no** (0%) | — | — | — | `UNAVAILABLE` |
| `policy` | `"Policy Name"` | **no** (0%) | — | — | — | `UNAVAILABLE` |
| `application` / `virtual_host` | `"Hostname"` | **no** (0%) | — | — | — | `UNAVAILABLE` |
| `country` | — | not verified | — | — | — | **excluded** — §13.4 |
| `user_agent_family` | — | not verified | — | — | — | **excluded** — §13.4 |

Only the first five are requested by the MVP evidence policy. The rest are requested and recorded as
`UNAVAILABLE`, because §3.4's `DimensionExplorer` already renders that correctly and omitting a
dimension reads to an analyst as one that was examined and came back clean.

### 13.2 Preserved evidence semantics

`EvidenceStatus` (`NOT_REQUESTED` / `PENDING` / `COMPLETE` / `PARTIAL` / `UNAVAILABLE` / `FAILED`)
and `DimensionAvailability` (`AVAILABLE` / `TRUNCATED` / `UNAVAILABLE` / `FAILED`) are reused
unchanged. No new evidence state is introduced.

### 13.3 Truncation discipline

From a `TRUNCATED` dimension the platform **may** state each returned value's count and share, and
**may not** state total distinct cardinality, a complete list of new values, or a complete list of
disappeared values. This is already enforced end to end (`404bc39`) and Phase B inherits it rather
than re-deriving it.

Every dimension result carries bounded AQL provenance: the exact query text, window bounds, row
count, truncation flag, duration. Evidence without provenance is not evidence.

### 13.4 Excluded dimensions

`country` and `user_agent_family` are excluded from the MVP because neither was verified available.
Adding a dimension on the assumption that a GeoIP enrichment or a normalized user-agent property
exists would be exactly the invention §8.2 of the architecture document forbids.

---

## 14. Lifecycle reuse **[DECISION]**

`CANDIDATE → OPEN → RECOVERING → RESOLVED` is reused unchanged. **No second lifecycle system is
introduced.** No blocker was found.

- **Active states remain exactly** `CANDIDATE`, `OPEN`, `RECOVERING` — the `ACTIVE_ANOMALY_STATES`
  frozenset. `resolved_at` remains metadata, not the definition of activity. Commit `ec5d352` fixed
  precisely this and the fix is not to be undone.
- **Fingerprint / natural key:**
  `(qradar_instance_id, waf_log_source_id, normalized_endpoint, detector_type)`.
- **Detector type is part of the fingerprint** — as in Phase A, so a rate spike and a future block-ratio
  spike on the same endpoint are two coexisting incidents, each with its own recovery. Merging them
  would make one condition's recovery close the other.
- **Deduplication:** one active incident per fingerprint, enforced by a
  `WafDetectorState` row unique on the same key. A recurrence after `RESOLVED` opens a **new**
  incident.
- **Recovery:** `resolve_after` consecutive `COMPLETE`, genuinely-normal buckets. An `UNKNOWN` or
  `PARTIAL` bucket advances nothing — a collection failure must not resolve an anomaly.
- **Notification:** reuses `AlertService.open_or_update()` / `resolve_by_fingerprint()`, which
  already emit a transition only on a genuine state change. Phase B needs a WAF-shaped
  `AlertInput` title and context; nothing else.
- **API and UI:** yes — Phase B anomalies live in the existing unified anomaly list and detail
  system (§18).

---

## 15. Persistence proposal **[DECISION]**

**No migration is created in this workstream.** This is the specification a Phase B1 migration
implements.

### 15.1 Options compared

| Option | Assessment |
|---|---|
| **(a) Reuse Phase A tables with a generic entity representation** — replace `log_source_id` with an opaque `entity_key` string or JSONB | **Rejected.** It would move stable, typed, indexed Phase A columns into an opaque form purely to claim genericity, which §15 of the brief and §7.4 of the architecture document both forbid. It also breaks every Phase A foreign key, index and query, for a phase that currently has three entities. |
| **(b) WAF-specific tables** | **Selected.** Typed columns, real foreign keys, no nullable-forest, no discriminator branching. Phase A stays untouched, so a Phase B defect cannot regress a live-validated pipeline. |
| **(c) Minimal additive genericization** — extract the shared baseline arithmetic into a pure module both phases call | **Selected as a companion to (b), for B1.** This is genuinely small: the algorithm in `BaselineBuilder` is already pure apart from its I/O. Extracting `compute_cell(values) -> CellStats` and the exclusion-tally shape gives real reuse without touching a single stable column. |

**Prefer the smallest change that preserves clear typed contracts** → **(b) + (c)**.

### 15.2 Proposed tables

```text
waf_endpoint                          -- the entity, and its activity lifecycle
  id                     uuid  PK
  instance_id            uuid  FK qradar_instance(id) ON DELETE CASCADE  NOT NULL
  log_source_id          uuid  FK log_source(id)      ON DELETE CASCADE  NOT NULL
  normalized_endpoint    varchar(255)                                    NOT NULL
  normalizer_version     int                                            NOT NULL
  first_seen_at          timestamptz                                    NOT NULL
  last_seen_at           timestamptz                                    NOT NULL
  is_active              bool                        DEFAULT true       NOT NULL
  retired_at             timestamptz
  UNIQUE (log_source_id, normalized_endpoint, normalizer_version)
  INDEX  (log_source_id, is_active)

waf_endpoint_metric                   -- TimescaleDB hypertable on bucket_start
  waf_endpoint_id        uuid  FK waf_endpoint(id) ON DELETE CASCADE   PK part
  bucket_start           timestamptz                                    PK part
  bucket_seconds         int                         DEFAULT 300        NOT NULL
  request_count          bigint                      DEFAULT 0          NOT NULL
  distinct_client_count  int                                            NULL
  blocked_count          bigint                                         NULL
  error_count            bigint                                         NULL
  endpoint_coverage_ratio double precision                              NOT NULL
  completeness           varchar(16)                                    NOT NULL
  collection_source      varchar(32)
  query_provenance       jsonb                       DEFAULT '{}'       NOT NULL
  collection_duration_ms int
  collected_at           timestamptz
  watermark_at           timestamptz
  normalizer_version     int                                            NOT NULL
  INDEX (waf_endpoint_id, bucket_start)

waf_endpoint_baseline
  id                     uuid  PK
  waf_endpoint_id        uuid  FK waf_endpoint(id) ON DELETE CASCADE   NOT NULL
  metric_name            varchar(64)                                    NOT NULL
  weekday                int   CHECK (weekday BETWEEN 1 AND 7)          NOT NULL
  hour                   int   CHECK (hour BETWEEN 0 AND 23)            NOT NULL
  median, mad, p05, p95, sample_count, is_reliable
  window_start, window_end, computed_at
  baseline_version       int                         DEFAULT 1          NOT NULL
  normalizer_version     int                                            NOT NULL
  completeness           double precision            DEFAULT 0.0        NOT NULL
  excluded_sample_count  int                         DEFAULT 0          NOT NULL
  exclusion_counts       jsonb                       DEFAULT '{}'       NOT NULL
  observations           jsonb                       DEFAULT '[]'       NOT NULL
  UNIQUE (waf_endpoint_id, metric_name, weekday, hour, normalizer_version)

waf_detector_state
  id                     uuid  PK
  waf_endpoint_id        uuid  FK waf_endpoint(id) ON DELETE CASCADE   NOT NULL
  detector_type          varchar(40)                                    NOT NULL
  consecutive_anomalous, consecutive_healthy  int    DEFAULT 0          NOT NULL
  state                  varchar(24)  DEFAULT 'NORMAL'                  NOT NULL
  last_interval_start    timestamptz
  active_anomaly_id, open_anomaly_id  uuid
  UNIQUE (waf_endpoint_id, detector_type)

waf_anomaly
  -- column-for-column the Phase A LogSourceAnomaly shape, with
  -- waf_endpoint_id replacing log_source_id, plus normalizer_version.
  INDEX (waf_endpoint_id, detector_type, state)
  INDEX (detected_at)
```

Lifecycle transitions and the evidence package **reuse the existing tables**:
`AnomalyStateTransition`, `AnomalyExplanation`, `AnomalyExplanationDimension`,
`AnomalyExplanationContributor`. Their FK to `log_source_anomaly` must become polymorphic-by-nullable
(`log_source_anomaly_id` **or** `waf_anomaly_id`, with a CHECK that exactly one is set). That is the
one schema change Phase B forces on an existing table, and it is additive and constraint-guarded.
**[OPEN-5]** flags the alternative — parallel `waf_anomaly_*` evidence tables — which is more
duplication but zero risk to Phase A.

### 15.3 Operational properties

- **Hypertable:** `waf_endpoint_metric` on `bucket_start`, matching the Phase A pattern
  (`create_hypertable(..., if_not_exists => TRUE)`); the composite PK includes the partition column
  because Timescale requires it.
- **Retention:** `WAF_METRIC_RETENTION_DAYS`, proposed **35** — the 28-day baseline lookback plus a
  week of margin, so a rebuild never runs off the end of its own data. Applied outside the schema
  migration, as Phase A does.
- **Idempotent upsert:** `ON CONFLICT (waf_endpoint_id, bucket_start) DO UPDATE`.
- **Late arrival:** re-collection overwrites; baselines lag until the next rebuild.
- **Entity retirement:** a scheduled pass flips `is_active` per §10.2; rows survive to retention.
- **Estimated growth** (§6.3, current data): ~864 metric rows/day, ~24 K over retention, ~2 K
  baseline cells. Under the §8.4 circuit breaker the worst case is bounded by
  (active endpoints × 288) rows/day.

---

## 16. Exact generic-engine blockers found **[REPO]**

The brief asks for *exact* blockers, not a general call for genericity. Three were found, and none
of them justifies a broad rewrite:

1. **`BaselineBuilder` is typed on `LogSource`/`LogSourceMetric`/`LogSourceBaseline`** and hard-codes
   `BASELINE_METRICS = ("average_eps", "event_count")` plus the `LogSource` schedule columns.
   *Minimal fix:* extract the pure arithmetic (§15.1c). Not a rewrite.
2. **`MetricPoint` carries 16 log-source-specific fields.** Phase B cannot construct one.
   *Minimal fix:* Phase B defines its own point type. `Thresholds`, `AnomalyEvidence`,
   `LifecycleInput` and every function in `statistics.py` are already generic and need nothing.
3. **`AnomalyListItem.log_source_id` is a required field on the API contract.**
   *Minimal fix:* §18. Not an engine problem.

`lifecycle.py`, `statistics.py`, `evidence.py` and `thresholds.py` presented **no blocker at all** —
the architecture document's §10.1 constraint that the Phase A abstraction must accommodate Phase B
holds for every part of the engine that is genuinely policy. Per the brief, the engine is **not**
refactored in this workstream.

---

## 17. AQL templates **[DECISION]**

All templates are read-only, `START`/`STOP`-bounded, aggregated, `LIMIT`-capped, scoped to a single
selected WAF log source, and safe against unbounded raw-path cardinality. `<ls>` is an integer log
source ID; `<s>`/`<e>` are integer epoch milliseconds; nothing caller-supplied is interpolated as a
string. **`SUM(eventcount)`, not `COUNT(*)`** (§9).

**Confirmed against live QRadar** — the shapes below were executed during §4's discovery:
availability, cardinality and evidence. **Proposed only** — collection and new-value comparison;
they follow the same shape but were not executed, because writing behavioral rows is Phase B1 work.

### 17.1 Collection **[proposed]**

```sql
SELECT "WAF_URL"            AS "raw_path",
       SUM(eventcount)      AS "request_count",
       UNIQUECOUNT(sourceip) AS "distinct_clients"
FROM events
WHERE logsourceid = <ls>
  AND starttime >= <s> AND starttime < <e>
GROUP BY "WAF_URL"
ORDER BY SUM(eventcount) DESC
LIMIT 500
START <s> STOP <e>
```

Normalization happens in the application, never in AQL — the rules in §8 are not expressible in AQL
and must be deterministic and unit-testable. The `LIMIT 500` is the §8.4 circuit breaker; hitting it
marks the bucket `PARTIAL`.

### 17.2 Endpoint coverage, for completeness **[proposed]**

```sql
SELECT SUM(eventcount) AS "total_events"
FROM events
WHERE logsourceid = <ls>
  AND starttime >= <s> AND starttime < <e>
LIMIT 1
START <s> STOP <e>
```

`endpoint_coverage_ratio` = (sum of §17.1's `request_count`) / `total_events`.

### 17.3 Field availability **[confirmed]**

```sql
SELECT <field> AS "v", COUNT(*) AS "c"
FROM events
WHERE logsourceid = <ls>
GROUP BY <field>
ORDER BY COUNT(*) DESC
LIMIT 30
START <s> STOP <e>
```

A NULL group row gives the missing-percentage directly. A query **rejected** by QRadar means the
property does not exist — a stronger result than one that returns nulls, and the two must be
recorded differently.

### 17.4 Cardinality **[confirmed]**

```sql
SELECT UNIQUECOUNT(sourceip)  AS "distinct_client",
       UNIQUECOUNT("WAF_URL") AS "distinct_raw_path",
       COUNT(*)               AS "records"
FROM events
WHERE logsourceid = <ls>
LIMIT 5
START <s> STOP <e>
```

`UNIQUECOUNT` counts NULL as a value; subtract one when the field has any null rows.

### 17.5 Contributor evidence **[confirmed]**

```sql
SELECT sourceip AS "value", SUM(eventcount) AS "count"
FROM events
WHERE logsourceid = <ls>
  AND "WAF_URL" = <raw_path_of_endpoint>
  AND starttime >= <s> AND starttime < <e>
GROUP BY sourceip
ORDER BY SUM(eventcount) DESC
LIMIT 20
START <s> STOP <e>
```

**[OPEN-3]:** binding an endpoint back to a raw path in the `WHERE` clause is unsound once §8's
normalizer is many-to-one (`/users/1` and `/users/2` both normalize to `/users/{id}`). The evidence
query must then filter on a *set* of raw paths, or accept a superset and re-normalize in the
application. Phase B1 must resolve this before writing the evidence collector.

### 17.6 New-value comparison **[proposed]**

Run §17.5 twice — once over the anomaly window, once over the recent-normal window — and diff in the
application, exactly as `explanation_collector.py` already does. Because both results are capped,
"new" and "disappeared" claims are only valid when **neither** side truncated (§13.3).

---

## 18. API and UI impact **[DECISION]**

Minimal and additive. **No new dashboard is designed.**

### 18.1 Reused unchanged

Unified anomaly list, unified anomaly detail, lifecycle timeline, evidence banner, dimension
explorer, technical details, and the existing contributor package schemas. All are generic over
evidence rather than over log sources (§3.4).

### 18.2 Minimum additive contract

```text
AnomalyListItem / AnomalyDetailOut
  + subject_kind        : "LOG_SOURCE" | "WAF_ENDPOINT"     (required, defaulted for Phase A)
  + waf_endpoint        : str | None                        (the normalized endpoint)
  + waf_log_source_name : str | None
  ~ log_source_id       : uuid | None                       (relaxed from required)
```

Relaxing `log_source_id` to optional is the only non-additive change, and it is
backward-compatible for every existing consumer, which reads the field rather than asserting its
presence. `subject_kind` is what the list row and the detail header switch on to render "WAF
endpoint `/api/v1/report` on `portal-waf-01`" instead of a log-source name.

Filtering gains `?subject_kind=` and `?waf_endpoint=`. Nothing else changes.

### 18.3 Deferred

A WAF-specific overview route and an endpoint inventory are **deferred**. The MVP is fully operable
through the existing investigation surfaces: an operator finds the anomaly in the unified list,
opens it, and sees expected vs observed, the lifecycle timeline and the contributor evidence.

If a route is later justified, its purpose would be *endpoint inventory and coverage* — which
endpoints are monitored, which are retired, and what each one's baseline adequacy and endpoint
coverage ratio is. Minimum hierarchy: `/waf` (log-source list with endpoint counts) →
`/waf/[logSourceId]` (endpoint inventory with per-endpoint baseline status). It is not implemented
and not designed further here.

---

## 19. Staged implementation plan **[DECISION]**

### Phase B0 — discovery and contract *(this workstream)*

- **Scope:** this document; minimal factual Phase A/B status corrections; one discoverability link.
- **Files:** `docs/PHASE-B-WAF-BEHAVIOR.md`, `README.md`,
  `docs/BEHAVIORAL-ANALYTICS-ARCHITECTURE.md`.
- **Tests:** none — documentation only.
- **Acceptance:** §24's gate is answered with evidence, either way.
- **Non-goals:** any production code.
- **Commit:** `docs(phase-b): define WAF behavioral analytics contract`.

### Phase B1 — canonicalization and collection foundation

- **Scope:** the §8 endpoint normalizer; the §9 typed observation contract; the §15 tables and one
  additive migration; the collector; the §15.1c pure-arithmetic extraction. **No detector, no UI.**
- **Contexts:** `backend/app/waf/normalize.py` (new), `backend/app/models/waf.py` (new),
  `backend/alembic/versions/0005_*` (new), `backend/app/collectors/waf_endpoint_collector.py` (new),
  `backend/app/providers/` (additive method + mock), `backend/app/core/config.py` (additive `waf_*`
  settings).
- **Tests:** exhaustive unit tests for every §8.2 example and every §8.1 rule; normalizer determinism
  and version-stability tests; collector idempotency (re-collect overwrites); completeness matrix
  from §10.3 including the `PARTIAL`-on-low-coverage path; the zero-materialization precondition
  (never zero-fill an unmonitored endpoint); retirement and reactivation.
- **Acceptance:** a mock-provider run produces correct, idempotent, completeness-tagged buckets, and
  a `low_endpoint_coverage` source produces `PARTIAL` buckets that never enter a baseline.
- **Non-goals:** detectors, anomalies, alerts, API, UI.
- **Commit:** `feat(waf): canonicalize endpoints and collect bounded observations`.

### Phase B2 — first detector vertical slice

- **Scope:** `request_count` baseline; `WAF_REQUEST_RATE_SPIKE`; lifecycle; bounded evidence; the
  §18.2 additive API fields; reuse of the existing UI.
- **Contexts:** `backend/app/waf/` (baseline, detector, engine),
  `backend/app/collectors/waf_explanation_collector.py`, `backend/app/api/routes/anomalies.py`,
  `backend/app/schemas/anomaly.py`, `frontend/src/components/behavior/` (rendering only),
  `backend/app/workers/tasks.py`.
- **Tests:** detector guard matrix (every conjunctive guard, in both directions); degenerate-MAD
  fallback; `INSUFFICIENT_DATA` for a sparse endpoint; full lifecycle
  `CANDIDATE → OPEN → RECOVERING → RESOLVED` plus relapse; no duplicate active incident per
  fingerprint; a collection failure does not resolve; evidence truncation withholding; API contract
  tests for both `subject_kind` values.
- **Acceptance:** end-to-end mock/integration run opens and resolves one WAF endpoint incident with
  bounded contributor evidence and correct provenance.
- **Non-goals:** ratio detectors, novelty, peer groups, new routes.
- **Commit:** `feat(waf): detect endpoint request-rate spikes with bounded evidence`.

### Phase B3 — live validation

- **Scope:** bounded synthetic WAF telemetry via the existing generator; live collection through
  the REST/Ariel path; observed open → recover → resolve; multi-endpoint isolation; cleanup and
  production restoration.
- **Prerequisite:** §24's blockers cleared — otherwise there is nothing to validate.
- **Acceptance:** a `LAB-*`-style sanitized results document recording the live run, exactly as
  Phase A did.
- **Non-goals:** any QRadar write beyond syslog ingestion by the operator-configured lab source.
- **Commit:** `docs(lab): record the Phase B live validation`.

### Phase B4 — novelty

`WAF_NEW_ENDPOINT`, then `WAF_NEW_CLIENT_ENDPOINT`, under §11.4's contract.

### Phase B5 — ratios, peer groups, additional WAF products

`WAF_BLOCK_RATIO_SPIKE` and `WAF_ERROR_RATIO_SPIKE` under §11.3, once the fields exist. Peer groups
once an application dimension exists. A dedicated overview route only if §18.3's justification is
actually met.

---

## 20. Security and privacy constraints **[DECISION]**

| Risk | Control |
|---|---|
| Query strings carrying secrets, tokens or personal data | Stripped before normalization (§8.1 rule 3) and **never persisted in any Phase B table**. |
| Path parameters carrying customer or account identifiers | Templated to `{id}` / `{uuid}` / `{hex}` / `{opaque}` (§8.1 rules 11–14) before storage. |
| X-Forwarded-For spoofing | No XFF property exists here. If configured later, untrusted by default; usable only for a log source explicitly flagged as behind a trusted proxy (§11.4). |
| Reverse-proxy chains | `sourceip` identifies the immediate peer. All client claims worded as "client address", never "user" or "attacker" (§11.4). |
| Attacker-controlled high-cardinality paths | §8.1 rules 11–14, 17–18 plus the §8.4 per-bucket new-endpoint circuit breaker. |
| Log injection / unsafe display strings | Normalized endpoints are length-capped at 255, control characters removed, and rendered as text — never as HTML or as a link. |
| Internal hostname exposure | Host is not part of the normalized path (§8.1 rule 1). Console hostnames appear in no document or fixture. |
| Excessive raw event retention | Phase B stores aggregates only. No raw event, payload, header, cookie or body is retrieved or stored (§8.3). |
| Sensitive IP addresses in committed fixtures | Fixtures use RFC 5737 documentation ranges only. |
| AQL query budget exhaustion | Per-run caps mirroring `explanation_max_per_run`; bounded 120-second polling with an explicit cancel; `LIMIT` on every query; one collection query per source per bucket, not one per endpoint. |

**The product remains strictly read-only against QRadar.** No Phase B feature may block an IP,
modify a WAF policy, add a reference-set entry, close an offense, change any QRadar object, or
trigger an autonomous remediation action. No mutation endpoint exists and none may be added.

**No LLM.** Phase B is deterministic. Summarization remains Phase F.

---

## 21. Testing strategy **[DECISION]**

| Layer | Coverage |
|---|---|
| Normalizer (unit) | Every rule in §8.1; every example in §8.2; idempotence (`n(n(x)) == n(x)`); determinism across runs; version-stability (a fixed corpus must produce byte-identical output at version 1). |
| Observation contract (unit) | Unavailable field → NULL, never 0 or `""`; `endpoint_coverage_ratio` arithmetic; `SUM(eventcount)` not `COUNT(*)`. |
| Collector (integration) | Idempotent re-collection; watermark does not advance past a failure; `PARTIAL` on truncation, on low coverage and on circuit-breaker trip; zero-fill only for monitored **and** active endpoints; retirement and reactivation. |
| Baseline (integration) | Seasonal cell assignment; `INSUFFICIENT_DATA` below `baseline_min_samples`; the new `low_endpoint_coverage` exclusion appears in `exclusion_counts`; `normalizer_version` scoping. |
| Detector (unit) | Every conjunctive guard passing and failing independently; degenerate-MAD fallback both ways; ratio-undefined path; severity and confidence boundaries. |
| Lifecycle (integration) | Full path plus relapse; `UNKNOWN` advances nothing; no duplicate active incident; recurrence after `RESOLVED` opens a new incident. |
| Evidence (integration) | `UNAVAILABLE` dimensions are recorded, not omitted; truncation withholds new/disappeared counts; provenance present and secret-free. |
| API (integration) | Both `subject_kind` values; filters; pagination; auth. |
| Frontend (unit) | Rendering a `WAF_ENDPOINT` subject in the existing list and detail components. |

**Fixtures.** `MockQRadarProvider` gains a WAF surface returning deterministic `(raw_path, count,
distinct_clients)` tuples, seeded, using RFC 5737 addresses and the §8.2 example paths. A committed
normalizer corpus file pairs raw paths with expected output and rule IDs — that file **is** the
normalizer contract, and changing it must require bumping `ENDPOINT_NORMALIZER_VERSION`.

---

## 22. Live-validation plan **[DECISION]**

Blocked until §24 clears. When it does:

1. **Prerequisite.** A WAF log source whose DSM parses HTTP request fields — `LAB Phase A WAF`
   (`lab-waf-volume-01`) configured per `LAB-SYNTHETIC-TELEMETRY.md`, with a Universal LEEF
   custom-property mapping for `url` (and `httpMethod` / `responseCode` if a method- or ratio-capable
   increment is in scope). Created by an operator in the console; never by this application.
2. **Field re-verification.** Re-run §17.3 and §17.4 against the new source and confirm endpoint
   coverage ≥ `WAF_MIN_ENDPOINT_COVERAGE`.
3. **Baseline build.** Bounded generator traffic across a coherent `weekday × hour` cell, sized to
   finish inside one UTC hour, at ≤ 20 EPS, per the Phase A lab discipline. Rebuild the baseline
   *while traffic is still flowing*.
4. **Spike.** Raise the rate on one endpoint only. Observe `CANDIDATE → OPEN`, then evidence
   collection, then recovery to `RESOLVED`.
5. **Isolation.** Confirm sibling endpoints on the same log source stayed `NORMAL` throughout —
   the multi-source isolation proof, one level down.
6. **Cleanup.** Stop the generator; restore production settings; record the run in a sanitized
   `LAB-*` document with no raw output and no internal identifiers.

---

## 23. Explicit deferred scope

Deferred by decision, with the reason:

| Deferred | Reason |
|---|---|
| `http_method` in the entity key | Field does not exist (§4.2). Reinstated per §7.1 if it appears. |
| `WAF_REQUEST_RATE_DROP` | Duplicates Phase A for the dominant cause; endpoint silence is not anomalous (§10.4). |
| `WAF_BLOCK_RATIO_SPIKE`, `WAF_ERROR_RATIO_SPIKE` | No numerator exists (§4.2). Contract pre-specified in §11.3. |
| `WAF_NEW_ENDPOINT`, `WAF_NEW_CLIENT_ENDPOINT` | Phase B4. Contract pre-specified in §11.4. |
| Peer-group baselines | No peer dimension exists. Phase B5. |
| Per-client entities | §6.1 — unbounded and statistically hollow. Client is a metric and a contributor. |
| `country`, `user_agent_family` dimensions | Availability not verified (§13.4). |
| Configured route templates | Requires per-log-source configuration; out of B1 scope (§8.5). |
| WAF overview / endpoint inventory route | Operable through existing surfaces (§18.3). |
| Non-seasonal rolling and peer-group baselines for sparse entities | §12.3 — `INSUFFICIENT_DATA` is the honest answer. |
| Generic-engine rewrite | §16 — three narrow blockers, none justifying it. |
| Phase A `COUNT(*)` → `SUM(eventcount)` correction | Pre-existing, separately reviewed. **[OPEN-4]** |

---

## 24. Open questions, blockers, and the Phase B1 acceptance gate

### 24.1 Open questions

| # | Question |
|---|---|
| **OPEN-1** | Can the WAF log sources be re-mapped to a DSM that parses HTTP request fields (F5 BIG-IP ASM, ModSecurity/CEF), or must Universal LEEF custom properties be authored for `url`, `httpMethod` and `responseCode`? This is an operator decision and it determines everything below. |
| **OPEN-2** | Is the observed telemetry representative of the intended production estate, or is it entirely synthetic lab traffic? Every WAF source here is lab-shaped; a real deployment may expose a far better field set. |
| **OPEN-3** | How does a contributor evidence query filter to an endpoint once normalization is many-to-one (§17.5)? Must be resolved before the B2 evidence collector. |
| **OPEN-4** | Should the Phase A metric AQL be corrected from `COUNT(*)` to `SUM(eventcount)`? Phase B starts correct; Phase A's deferral is documented but still live. |
| **OPEN-5** | Polymorphic evidence FK versus parallel WAF evidence tables (§15.2). |
| **OPEN-6** | Is `WAF_URL` a site-authored custom property or a stock one? Its 67% coverage and attack-only values suggest a regex that matches only alert-shaped payloads. |

### 24.2 The gate

Phase B1 must not begin until every row below is satisfied.

| # | Requirement | Status | Evidence |
|---:|---|---|---|
| 1 | At least one usable WAF source | **✗ FAIL** | 7 sources, but 6 expose no endpoint and the 7th only at 67% (§4.3) |
| 2 | Required field availability | **✗ FAIL** | `http_method`, response status, and block/pass action do not exist (§4.2) |
| 3 | Selected and bounded entity key | **✓ PASS** | §7 — `(instance, log_source, normalized_endpoint)` |
| 4 | Measured cardinality | **✓ PASS** | §6 — measured live, not estimated |
| 5 | Defined normalizer behavior | **✓ PASS** | §8, 20 rules and 18 worked examples |
| 6 | Defined completeness semantics | **✓ PASS** | §10 |
| 7 | Defined persistence natural key | **✓ PASS** | §15.2 |
| 8 | Selected first detector | **✓ PASS** | §11.2 — `WAF_REQUEST_RATE_SPIKE` |
| 9 | Defined evidence dimensions | **✓ PASS** | §13 |
| 10 | Defined query budget | **✓ PASS** | §17, §20 |
| 11 | Defined test fixtures | **✓ PASS** | §21 |
| 12 | Defined live-validation path | **⚠ CONDITIONAL** | §22 — fully specified, blocked on rows 1–2 |
| 13 | No unresolved blocker that would change the schema or entity model | **✗ FAIL** | OPEN-1 would add a key column (§7.1) and OPEN-3 changes the evidence collector |

### 24.3 Recommendation

> ## NO-GO for Phase B1.

Three of thirteen criteria fail, and they are the three that matter: there is no usable WAF source,
the required fields do not exist, and OPEN-1 could still change the entity key.

The design work is complete. What is missing is **data**, and it is missing for a specific,
fixable reason: seven WAF log sources are parsed by **F5 FirePass**, a VPN DSM, which discards the
HTTP request fields the generator is already emitting.

**To convert this to GO, one operator action is required:**

Configure a WAF log source whose DSM parses HTTP request fields — either re-map to F5 BIG-IP ASM or
ModSecurity/CEF, or configure `LAB Phase A WAF` (`lab-waf-volume-01`) as Universal LEEF with custom
properties for `url` (mandatory), `httpMethod` and `responseCode` (optional; they unlock §7.1 and
§11.3 respectively). This is a console change, made by an operator; this application must never
create or modify a log source.

Then re-run the §17.3 and §17.4 templates against the new source. If endpoint coverage reaches
`WAF_MIN_ENDPOINT_COVERAGE` and distinct endpoints are in the tens-to-hundreds rather than three,
rows 1, 2 and 13 clear and Phase B1 may begin against this document unchanged.

Nothing in this design should be revised to work around the missing data. Building a per-endpoint
baseline on a field that is absent two-thirds of the time would produce exactly the false assurance
that `EvidenceStatus.UNAVAILABLE`, `BucketCompleteness.PARTIAL` and `AnomalyState.INSUFFICIENT_DATA`
were all introduced to prevent.
