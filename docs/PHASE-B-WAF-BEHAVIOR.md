# Phase B — WAF Behavioral Analytics

**Status (2026-09-16):** the B1/B2 backend vertical slice — normalizer, persistence, collector,
baseline, `WAF_REQUEST_RATE_SPIKE`, lifecycle — is **implemented and validated against live lab
telemetry** (§26). **Phase B.1 made it schedulable (§28):** per-source profiles and validation,
bounded admission, recorded maintenance decisions, metric-semantics identities, retention and
per-source scheduled runs. It is off by default and has **no analyst API or UI**. Production WAF
onboarding is **NO-GO**.

**Phase B.1.5 (§29)** made collection resilient and bounded before detector expansion: path floods
above the group cap now yield explicit lower bounds instead of blinding the source, endpoint storage
is sparse, a scheduled run costs a constant number of SQL statements, and WAF Ariel concurrency is
globally capped.

**§29 records Phase B.1.5 and supersedes §28.12 and §28.14 items 1–3 and 6; §28 records what
Phase B.1 built; §27 is the design for everything beyond it.** §1–§26 are the discovery record, written in order: statements there such as "not
implemented" describe the workstream that section belonged to, and later sections supersede
earlier ones where marked.

Evidence in this document is labelled:

| Label | Meaning |
|---|---|
| **[REPO]** | Verified by reading the current working tree. |
| **[LIVE-1]** | Measured 2026-08-03 against the **misparsed** F5 FirePass WAF sources. Superseded where §25 says so; retained because it is why the dedicated source exists. |
| **[LIVE-2]** | Measured 2026-08-03 against the **corrected** Universal LEEF source `lab-waf-behavior-01` (QRadar log source 312), before its custom properties existed. |
| **[LIVE-3]** | Measured 2026-08-03 against source 312 **after** the operator authored the custom event properties. Authoritative — see §26. |
| **[PROJECTED]** | Derived from the generator's deterministic output, not measured on the appliance. Says what a corrected run *will* produce; never presented as observed. |
| **[DECISION]** | A design choice made here, with its rationale. |
| **[ASSUMPTION]** | Not verified. Must be confirmed before it is relied on. |
| **[OPEN]** | Unresolved question or blocker. |

> **Reading order.** §4–§6 record the first measurement round against the misparsed sources; **§25
> supersedes them** with the corrected-source results. **§26 is the current state**: it records the
> verified custom properties, the live-measured cardinality, the implemented vertical slice and the
> gate outcome. Sections 7–23 are the design proper.

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

### 4.2 Live field availability — first round, misparsed sources **[LIVE-1]**

> **Superseded by §25.2.** These numbers describe seven log sources parsed by the F5 FirePass VPN
> DSM. They are the evidence that motivated the dedicated Universal LEEF source, not a description
> of what WAF telemetry looks like when it is parsed correctly.

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

### 4.3 Cross-source consistency — first round **[LIVE-1]**

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

### 4.4 Live discovery method and budget — first round **[LIVE-1]**

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

## 5. First-round discovery findings **[LIVE-1 — superseded by §25]**

> The telemetry **as parsed by the F5 FirePass DSM** supports neither the roadmap entity model nor
> any of the roadmap's ratio detectors.

**§25 revises this.** The limitation was the DSM, not the telemetry: the generator was emitting every
field all along and the VPN parser discarded them. What survives from this section is the reason a
dedicated, correctly-typed source was needed.

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

## 6. Cardinality and density — first round **[LIVE-1 — superseded by §25.3]**

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

## 7. Entity model — first selection **[DECISION — revised in §25.4]**

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
metric AQL still used `COUNT(*)` and that correcting it was deliberately deferred.

**OPEN-4 is resolved.** The defect was measured, confirmed and fixed in `ae9932a`: 19 of 50 live log
sources reported `SUM(eventcount) > COUNT(*)`, four of them at ~38x (~197 records for ~7,570 events).
Both the log-source metric query and the contributor evidence query now sum `eventcount`;
`distinct_count` and `truncated` deliberately stay row-based. Phase B inherits correct semantics
rather than a documented defect.

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

**OPEN-3 is resolved — Approach B.** Binding an endpoint back to a raw path in the `WHERE` clause is
unsound once §8's normalizer is many-to-one (`/users/1` and `/users/2` both normalize to
`/users/{id}`). Three approaches were compared:

| | **A — stored raw-path membership** | **B — query broad, normalize in memory** | **C — generated AQL predicate** |
|---|---|---|---|
| Correctness | Exact | Exact for rows returned | Exact only for endpoints with no templated segment |
| Query cost | 1 query + a write path | 1 query, same shape as today | 1 query |
| Cardinality | **Unbounded** — a scanner mints a new raw path per request | Bounded by `LIMIT` | Bounded |
| Privacy | **Bad** — persists raw paths (or hashes that are trivially reversible for common paths), reintroducing exactly what §8's templating removes | Raw paths exist only in memory and are normalized before any column | Raw paths appear in query text |
| Query strings | Would have to be stored or stripped before hashing, and a stripped hash no longer identifies the row | Stripped at normalization; never persisted | Would have to be matched on |
| Truncation | Cap applies post-normalization | **Cap applies pre-normalization** — a rare endpoint whose raw variants are individually small can be cut off entirely | Cap applies per predicate |
| Late arrivals | Membership set goes stale | Self-healing — re-running re-normalizes | Self-healing |
| Normalizer change | Stored membership becomes wrong and needs a backfill | Self-healing — old rows keep their version, new queries use the new rules | Self-healing |
| Auditability | Two artefacts to reconcile | One fixed AQL template | Query text varies per endpoint |

**Selected: Approach B.** It is correct for every endpoint whether templated or not, it persists
nothing raw, and it reuses the existing bounded-aggregate shape and its truncation semantics
unchanged. Approach A is rejected on privacy and unbounded cardinality — persisting attacker-settable
raw paths is the failure mode §8 and §20 exist to prevent. Approach C is rejected as a general
mechanism because it only works for the untemplated subset, and building predicates from
attacker-controlled raw paths is forbidden outright by §20.

Approach B's one weakness is honest and already representable: the row cap applies to *raw* paths, so
a heavily-templated endpoint can be truncated before normalization collapses it. That is reported
with the existing `DimensionAvailability.TRUNCATED`, which already withholds "new" and "disappeared"
claims (§13.3). No new evidence state, and no new table.

**Persistence impact: none.** This is what removes the schema ambiguity from the §24 gate — Approach A
would have required a raw-path membership table and Approach C a per-endpoint predicate cache;
Approach B requires neither.

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
| ~~Phase A `COUNT(*)` → `SUM(eventcount)` correction~~ | **Done** in `ae9932a`; no longer deferred. |

---

## 24. First-round gate **[superseded by §25.5]**

> Retained for the record. The criteria are unchanged; §25.5 re-evaluates all thirteen against the
> corrected source.



### 24.1 Open questions

| # | Question |
|---|---|
| ~~OPEN-1~~ | **Answered.** A dedicated Universal LEEF source was created (§25.1); Universal LEEF custom properties must be authored (§25.6). |
| **OPEN-2** | *Still open.* Every WAF measurement is synthetic lab telemetry; see §25.8. |
| ~~OPEN-3~~ | **Resolved** — Approach B, no new table (§17.5). |
| ~~OPEN-4~~ | **Resolved and fixed** in `ae9932a` (§9). |
| **OPEN-5** | *Still open, but B1-internal.* Polymorphic evidence FK versus parallel WAF evidence tables (§15.2). Both satisfy the contract; neither changes the entity key. |
| ~~OPEN-6~~ | **Moot.** `WAF_URL` is bound to other log source types and extracts nothing from Universal LEEF (§25.2); Phase B will use its own property. |

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

---

## 25. Revision — the corrected WAF source (2026-08-03)

This section supersedes §4.2, §4.3, §5, §6 and §24. It records what changed after the operator
created a dedicated, correctly-typed WAF log source, and re-evaluates the gate against it.

### 25.1 What the operator did, and what arrived **[LIVE-2]**

| | Value |
|---|---|
| QRadar log source ID | **312** |
| Name / identifier | `lab-waf-behavior-01` |
| Type | **212 — Universal LEEF** (confirmed by REST, not assumed) |
| Enabled | yes |
| Protocol | Syslog UDP/514, identifier matching the syslog hostname |

A dedicated source, not a remap: `lab-waf-volume-01` keeps its Phase A history, and none of the
seven production-shaped F5 sources was touched. The generator gained a matching additive identity in
`0fa7b3c` (`lab-waf-behavior-01`, device 10.20.0.22), with regressions proving no default scenario's
output moved.

**Bounded generator run.**

| | Value |
|---|---|
| Run ID | `labrun-phaseb-20260803T0000Z` |
| Scenario | `source-volume-baseline`, `--format leef` |
| Seed | `20260803` (deterministic) |
| Rate / duration | 2 EPS for 60 s |
| Expected / attempted / sent / errors | 120 / 120 / **120** / **0** |
| Window | `2026-08-03T19:27:58Z – 19:28:58Z` |

Variation in the run, verified before sending: 2 HTTP methods (GET 83, POST 37); 5 raw URLs of which
one carries a query string; 1 virtual host; 4 response codes (200, 304, 403, 404); both `ALLOW` (78)
and `DENY` (42); 3 client addresses; one stable `runId`.

**Arrival, measured read-only:** log source 312 received exactly **120 records / 120 events**
(`COUNT(*)` and `SUM(eventcount)` agree — this synthetic traffic does not coalesce). Ingestion is
therefore proven end to end: generator → syslog → Universal LEEF source → Ariel.

### 25.2 Corrected field capability matrix **[LIVE-2]**

Log source 312, run window, 120 events.

| Field | QRadar property | Kind | Type | Populated | Distinct | Phase B | Missing-value semantics |
|---|---|---|---|---:|---:|---|---|
| Client IP | `sourceip` | normalized | ip | **100.0%** | 3 | required | bucket unusable |
| Server IP | `destinationip` | normalized | ip | **100.0%** | 2 | optional | evidence-only |
| Direction | `eventdirection` | normalized | enum | 100.0% | 1 (`R2L`) | not used | — |
| Category | `CATEGORYNAME(category)` | normalized | string | 100.0% | 1 (`Unknown`) | not used | — |
| Event name | `QIDNAME(qid)` | normalized | string | 100.0% | 1 (`unknown`) | not used | QIDs unmapped |
| Username | `username` | normalized | string | **0.0%** | 0 | not used | generator emits no `usrName` on the WAF profile — correct, not a parser fault |
| **URL / path** | `"URL"`, `"URL Path"`, `"URL Host"`, `"WAF_URL"` | custom | string | **0.0%** | 0 | **required** | **BLOCKER** |
| **HTTP method** | *no property exists* | — | — | **n/a** | — | **required** | **BLOCKER** — query rejected |
| **Virtual host** | `"Hostname"`; `"Virtual Host"` absent | custom | string | **0.0%** | 0 | **required** | **BLOCKER** |
| **Response code** | *no property exists* | — | — | **n/a** | — | optional (ratios) | **BLOCKER** for ratio detectors |
| **Action** | *no property exists* | — | — | **n/a** | — | optional | **BLOCKER** for ratio detectors |
| Rule / signature ID | `"Rule ID"`, `"Rule Name"` | custom | string | **0.0%** | 0 | evidence-only | `UNAVAILABLE` |
| Application | `"Application"` | custom | string | **0.0%** | 0 | optional | `UNAVAILABLE` |
| Run ID | *no property exists* | — | — | **n/a** | — | lab isolation | falls back to the time window |
| Scenario / phase | *no property exists* | — | — | **n/a** | — | lab only | — |

Searchability/indexing was not probed: it is not safely observable read-only without touching index
configuration, so it is left explicitly unmeasured rather than guessed.

**The finding.** Ingestion is fixed; **parsing is not**. The Universal LEEF DSM auto-maps only its
predefined keys — which is exactly why `src` and `dst` normalized perfectly to `sourceip` and
`destinationip` at 100%. The generator's `url`, `httpMethod`, `virtualHost`, `responseCode`,
`action`, `runId`, `scenario` and `phase` are LEEF *custom* keys, and Universal LEEF does not create
properties for them automatically.

This was verified two ways rather than inferred. The custom-property inventory is **unchanged at 115
regex properties**, so no new property was authored. And probing eleven plausible names — `HTTP
Method`, `Request Method`, `Response Code`, `Status Code`, `HTTP Status`, `Virtual Host`, `Request
URL`, `Run ID`, `Scenario`, `Phase`, `Action` — had **every one rejected by QRadar as invalid**,
which is proof of absence rather than a query that returns nulls. The pre-existing `URL`, `URL Path`,
`URL Host` and `WAF_URL` properties do exist but are bound to other log source types, so they extract
nothing from a Universal LEEF payload.

**No compensation in application code.** Per §6 of the workstream brief, a missing required field is
reported as a parser blocker, not worked around.

### 25.3 Recomputed cardinality **[PROJECTED]**

The endpoint dimension cannot be measured on the appliance while the properties are unmapped. The
figures below are derived from the generator's deterministic output for one simulated hour at the
sent rate — a **repository** fact describing what the corrected run will produce, never presented as
an appliance measurement. They must be re-measured live once §25.6 is complete.

Basis: 7,200 events/hour at 2 EPS, one log source.

| | Value |
|---|---:|
| Distinct client IPs | 3 |
| Distinct raw URLs | 5 |
| Distinct paths after query-string removal | **5** |
| Distinct HTTP methods | **2** (GET, POST) |
| Distinct virtual hosts | 1 (`portal.lab.test`) |

Normalized endpoints: `/`, `/api/v1/report`, `/api/v1/status`, `/portal/login`, `/static/app.css`.
Note `/api/v1/report?id=1%27+OR+%271%27%3D%271` normalizing to `/api/v1/report` — §8 rule 3 removing
a SQL-injection payload from the entity key, exactly as designed.

| Candidate | Entities | Events/entity/h | Singletons | Rows/day | 28-day rows | Baseline cells |
|---|---:|---:|---:|---:|---:|---:|
| **C1** `(inst, ls, endpoint)` | 5 | 1,440 | 0.0% | 1,440 | 40,320 | 840 |
| **C2** `(inst, ls, endpoint, method)` | 10 | 720 | 0.0% | 2,880 | 80,640 | 1,680 |
| **C3** `(inst, ls, vhost, endpoint, method)` | 10 | 720 | 0.0% | 2,880 | 80,640 | 1,680 |
| *A (roadmap)* `(inst, ls, client, endpoint, method)` | 30 | 240 | 0.0% | 8,640 | 241,920 | 5,040 |

Every candidate is now comfortably baselineable — **0% singletons** and hundreds of observations per
entity per hour, against the 2,958 tuples averaging 3.9 events/day that the misparsed source
produced. The first round's entity problem was a symptom of the parsing problem.

Candidate A is still rejected: its entity count scales with the client population, which in the lab
is 3 and in production is the internet. The lab's flattering 30 is an artefact of a 3-address
generator profile, not evidence that the roadmap model bounds.

### 25.4 Revised entity selection **[DECISION — supersedes §7]**

```text
(qradar_instance_id, waf_log_source_id, normalized_virtual_host, normalized_endpoint, http_method)
```

**Candidate C3.** Two changes from the first selection, both forced by data that now exists:

**`http_method` is restored to the key.** §7 dropped it only because the field did not exist. It does
exist in the payload, and GET and POST on the same path are different behaviors that must not share a
baseline: a POST spike on `/portal/login` is credential stuffing, a GET spike on the same path is
scraping. Merging them hides both. The cost is exactly 2× the entities — 10 instead of 5 — which is
nothing. This is the §7.1 path being taken, and the normalizer version is bumped accordingly.

**`normalized_virtual_host` is added.** The workstream brief requires that unrelated applications
sharing `/`, `/login` or `/api/status` must not be collapsed, and `waf_log_source_id` alone does not
achieve that: one WAF appliance routinely fronts many virtual hosts and logs them all to a single
log source. In the lab this changes nothing (one vhost, so C3 ≡ C2), which is precisely why it must
be decided on the requirement rather than on the measurement — the lab cannot exhibit the collision
it prevents.

**Fallback when virtual host is unavailable [DECISION].** `normalized_virtual_host` is nullable. When
a log source's DSM does not populate it, the column is **NULL — never a sentinel string** (§9), the
unique constraint treats NULLs as equal so one coherent entity forms per `(ls, endpoint, method)`,
and the log source carries an explicit `virtual_host_unavailable` flag. Every anomaly on such a source
renders the collision caveat: *"this WAF source does not expose a virtual host, so endpoints with the
same path on different applications are counted together."* The risk is then visible to the analyst
rather than silently folded into the number. That is the same discipline as
`DimensionAvailability.UNAVAILABLE`, applied to an entity key.

Client IP remains a `distinct_client_count` metric and a bounded contributor dimension, unchanged
from §7.

**Normalizer contract (§8) is unaffected** and remains valid: it operates on the path alone, and
none of its 20 rules or 18 worked examples depends on method or host. Host handling was already
specified — rule 1 strips it from the path precisely because it belongs to the entity, which is now
literally true.

### 25.5 Re-evaluated Phase B1 gate

| # | Requirement | First round | Now | Evidence |
|---:|---|---|---|---|
| 1 | At least one usable WAF source | ✗ | **⚠ PARTIAL** | Source 312 ingests 120/120, but parses no HTTP field (§25.2) |
| 2 | Required field availability | ✗ | **✗ FAIL** | URL, method, vhost all 0% or non-existent (§25.2) |
| 3 | Selected and bounded entity key | ✓ | **✓ PASS** | §25.4 — C3 |
| 4 | Measured cardinality | ✓ | **⚠ PROJECTED** | §25.3 — derived from the generator, not yet measured live |
| 5 | Defined normalizer behavior | ✓ | **✓ PASS** | §8, unaffected by §25.4 |
| 6 | Defined completeness semantics | ✓ | **✓ PASS** | §10 |
| 7 | Defined persistence natural key | ✓ | **✓ PASS** | §25.4 + §15.2 |
| 8 | Selected first detector | ✓ | **✓ PASS** | `WAF_REQUEST_RATE_SPIKE` |
| 9 | Defined evidence dimensions | ✓ | **✓ PASS** | §13 |
| 10 | Defined query budget | ✓ | **✓ PASS** | §17, §20 |
| 11 | Defined test fixtures | ✓ | **✓ PASS** | §21; `lab-waf-behavior-01` now exists and is deterministic |
| 12 | Defined live-validation path | ⚠ | **✓ PASS** | §22; ingestion proven, only parsing outstanding |
| 13 | No unresolved blocker that could change the schema | ✗ | **✓ PASS** | OPEN-3 resolved (Approach B, no new table); OPEN-4 resolved and fixed; OPEN-1 answered |
| — | Actual event-volume semantics resolved | — | **✓ PASS** | `ae9932a` |

Eleven of thirteen pass, against ten before, and — more importantly — **every remaining failure is a
QRadar console configuration task, not a design question.** Criterion 13 passing is the substantive
change: the schema is no longer capable of moving.

### 25.6 Remaining operator checklist

One task, in the QRadar console, by an operator. The application must never do this.

On **log source 312 `lab-waf-behavior-01` only** — not `lab-waf-volume-01`, and not any of the seven
production-shaped F5 sources — create LEEF key-based custom event properties, scoped to log source
type **212 (Universal LEEF)**:

| # | LEEF key emitted | Suggested property name | Type | Needed for |
|---:|---|---|---|---|
| 1 | `url` | `WAF Request URL` | string | **Required** — the entity key |
| 2 | `httpMethod` | `WAF HTTP Method` | string | **Required** — the entity key |
| 3 | `virtualHost` | `WAF Virtual Host` | string | **Required** — the entity key |
| 4 | `responseCode` | `WAF Response Code` | string | Ratio detectors (§11.3) |
| 5 | `action` | `WAF Action` | string | Ratio detectors (§11.3) |
| 6 | `ruleId` | `WAF Rule ID` | string | Contributor evidence |
| 7 | `runId` | `Lab Run ID` | string | Lab run isolation |
| 8 | `scenario` | `Lab Scenario` | string | Lab only |
| 9 | `phase` | `Lab Phase` | string | Lab only |

Properties 1–3 are the gate. 4–5 unblock the ratio detectors that §11.2 currently lists as blocked.
6–9 are convenience.

Whatever names are chosen, they must be recorded here before Phase B1 writes a query: §25.2's probe
established that display names do **not** follow the LEEF keys, so the AQL templates in §17 must be
rewritten against the real property names rather than the emitted keys.

Then re-run, against source 312 and the new run window only:

1. §17.3 field availability for each of properties 1–5 — each must be effectively complete.
2. §17.4 cardinality, and re-measure §25.3's C1/C2/C3 table live, replacing **[PROJECTED]** with
   **[LIVE-2]**.
3. Confirm `/api/v1/report?id=…` arrives with its query string intact, so §8 rule 3 is exercised on
   real parsed data rather than on generator output.

### 25.7 Recommendation **[superseded by §26.7]**

> Superseded. The operator authored the properties; §26 records the verification and the revised
> verdicts.

### 25.7 Recommendation (as written before the properties existed)

Two separate verdicts, because they have different evidence.

> ### Phase B1 development: NO-GO — pending one operator task
>
> Not a design blocker. Every design question that could move the schema is now closed: the entity
> key is selected and justified (§25.4), evidence filtering needs no new table (§17.5), and the
> event-volume defect is fixed and tested. What is missing is three custom event properties on one
> lab log source. When §25.6's properties 1–3 exist and its three re-runs confirm them, this becomes
> a **GO** with no further design work — the document does not need revising, only its
> **[PROJECTED]** cardinality table re-measured and its §17 templates bound to the real property
> names.

> ### Production WAF onboarding: NO-GO — and not yet assessable
>
> Every WAF field measurement in this document comes from synthetic lab telemetry. The seven
> production-shaped F5 sources remain parsed by a VPN DSM and were deliberately left untouched. No
> claim is made about what a real F5 BIG-IP ASM, ModSecurity or cloud WAF deployment exposes.
>
> The schema does not depend on that answer, which is what allows the two verdicts to diverge:
> §25.4's key uses only vendor-neutral concepts — log source, virtual host, path, method — and §25.4
> already specifies the fallback for a source that cannot supply a virtual host. Production
> onboarding therefore needs its own field-availability pass per WAF product, and that pass can
> happen after B1 rather than blocking it.

### 25.8 Remaining production-estate assumptions **[ASSUMPTION]**

1. A production WAF DSM exposes request path, method and virtual host as queryable properties. Not
   verified for any real product.
2. Production endpoint cardinality is in the tens-to-hundreds per virtual host after §8
   normalization. The lab's 5 proves nothing about this; §8.4's circuit breaker is the guard.
3. Production traffic exhibits a diurnal pattern that `weekday × hour` can baseline. The lab's
   bursty synthetic profile cannot demonstrate this.
4. Coalescing behaves on WAF sources as measured elsewhere on this appliance. The synthetic run did
   not coalesce at all (120 records / 120 events), so the fix in `ae9932a` is proven against
   production-shaped sources but not yet against a WAF one.

---

## 26. Wave 1 — verified properties and the implemented vertical slice (2026-08-03)

This section supersedes §25.2, §25.3 and §25.7. The operator authored the WAF custom event
properties; everything below is measured against them, and the Phase B1/B2 vertical slice is
implemented.

### 26.1 Confirmed property names **[LIVE-3]**

The operator's names, which are authoritative and are what every Ariel query now uses:

| Purpose | QRadar property | Declared type | Superseded guesses |
|---|---|---|---|
| Request URL | `"URL"` | string | `URL Path`, `WAF_URL`, `Request URL`, `url` |
| HTTP method | `"HTTP_Method"` | string | `HTTP Method`, `Request Method`, `httpMethod` |
| WAF action | `"WAF_Action"` | string | `WAF Action`, `Action`, `action` |
| Response code | `"WAF_ResponseCode"` | **string** | `Response Code`, `Status Code`, `responseCode` |
| User agent | `"WAF_USERAGENT"` | string | — |

The guessing round is closed: §25.2 recorded eleven speculative names, all rejected by QRadar, and
this table replaces them. The general lesson is recorded in the code — property names are
**configuration** (`waf_url_property`, `waf_method_property`), never derived from the LEEF keys a
device emits, because QRadar display names do not follow them.

`WAF_ResponseCode` is declared **string**, not numeric. Application code therefore validates a strict
three-digit status before converting, and an invalid or absent value stays missing rather than
becoming 0 — `normalize_response_code` and its tests enforce exactly that.

### 26.2 Population, measured on the controlled run **[LIVE-3]**

Log source 312, run `labrun-phaseb-20260803T0000Z`, window `19:25Z–19:35Z`, 120 events
(120 records / 120 events — this synthetic traffic does not coalesce).

| Field | Property | Populated | Distinct | Values observed | Phase B role |
|---|---|---:|---:|---|---|
| Client IP | `sourceip` (normalized) | **100.0%** | 3 | — | metric + contributor |
| Request URL | `"URL"` | **100.0%** | 5 | 4 clean paths + 1 carrying a query string | **entity key** |
| HTTP method | `"HTTP_Method"` | **100.0%** | 2 | `GET` (83), `POST` (37) | **entity key** |
| WAF action | `"WAF_Action"` | **100.0%** | 2 | `ALLOW` (78), `DENY` (42) | deferred (§11.3) |
| Response code | `"WAF_ResponseCode"` | **100.0%** | 4 | `200` (42), `403` (42), `304` (22), `404` (14) | deferred (§11.3) |
| User agent | `"WAF_USERAGENT"` | **100.0%** | 4 | — | contributor evidence only |
| Virtual host | *no property* | — | — | — | **NULL**, see §25.4 |

Every required field is effectively complete. The distributions match the generator's deterministic
output exactly, which is what makes this run a usable fixture rather than merely a smoke test.

`WAF_Action`'s bounded set was **discovered, not assumed**: the query enumerated distinct values and
returned exactly `ALLOW` and `DENY` for this source. That is a fact about this generator profile, not
a claim about WAF products in general, and a ratio detector built on it must re-discover the set per
source.

`WAF_USERAGENT` is confirmed and deliberately excluded from the entity key. It is bounded contributor
evidence only; no user-agent baseline exists and none is planned for the MVP.

### 26.3 Run isolation **[LIVE-3]**

Run ID is not a QRadar property, so isolation is by dedicated source plus exact window. It is
reliable here, so no `LAB_RunID` property is required:

| Check | Result |
|---|---|
| Dedicated log source | 312, receiving only generated lab traffic |
| Expected events | 120 |
| Observed in the exact window | **120** |
| Unrelated events in the window | **0** |
| Generator errors | **0** |
| Seed | `20260803`, deterministic |

Because the observed count equals the expected count exactly, no unrelated traffic is present and the
window uniquely identifies the run.

### 26.4 Live cardinality — measured, replacing the projection **[LIVE-3]**

§25.3's figures were derived from the generator and marked `[PROJECTED]`. They are now measured, and
they match:

| | Projected (§25.3) | **Measured** |
|---|---:|---:|
| Distinct client IPs | 3 | **3** |
| Distinct raw URLs | 5 | **5** |
| Distinct normalized endpoints | 5 | **5** |
| Distinct HTTP methods | 2 | **2** |
| C1 `(ls, endpoint)` entities | 5 | **5** |
| **C2/C3 `(ls, [vhost,] endpoint, method)` entities** | 10 | **10** |
| Singleton entities | 0% | **0%** |

Normalized endpoints: `/`, `/api/v1/report`, `/api/v1/status`, `/portal/login`, `/static/app.css`.

The SQL-injection payload on `/api/v1/report` normalizes to `/api/v1/report` — §8 rule 3 removing an
attacker-controlled query string from the entity key, now demonstrated on live parsed data rather
than on generator output. A live assertion confirms **zero** stored endpoints contain `?`.

Storage at this shape: 10 entities × 288 five-minute buckets = 2,880 rows/day, 80,640 over a 28-day
retention, 1,680 baseline cells.

### 26.5 What was implemented

| Concern | Commit | Contents |
|---|---|---|
| Coalescing correctness | `ae9932a` | `SUM(eventcount)` for volume; cardinality stays row-based |
| Dedicated lab source | `0fa7b3c` | `lab-waf-behavior-01`, additive, with defaults-unchanged regressions |
| Normalizer | `f2208c3` | §8's 20 rules, versioned, 99 tests |
| Persistence | `b51cbcf` | Five tables, one additive migration, `NULLS NOT DISTINCT` |
| Collector | `0142b61` | Two bounded aggregates, measured coverage, bounded zero-fill, retirement |
| Detector + lifecycle | `82eed39` | `WAF_REQUEST_RATE_SPIKE`, baseline, engine |

Deliberately **not** implemented: any API route, any frontend component, any Celery schedule
registration, and any drop, ratio or novelty detector. WAF collection is off by default
(`waf_collection_enabled=False`).

### 26.6 Reused unchanged from Phase A

`app.anomaly.statistics` (median, MAD, `effective_scale`, `robust_z_with_status`, `ratio`),
`app.anomaly.evidence` (`AnomalyEvidence`, `healthy`, `insufficient`), and
`app.anomaly.lifecycle.next_state`. Reusing the lifecycle unchanged is what gives Phase B the two
rules Phase A learned in live validation — an `UNKNOWN` bucket advances nothing, and an active
incident is not downgraded because one bucket was unreadable — without a second state machine to
keep in sync.

Phase A's `BaselineBuilder`, `MetricPoint`, detectors and `AnomalyEngine` were **not** reused: each
is statically typed on the Phase A ORM classes. No Phase A file's behavior was changed by this
workstream except the two AQL aggregate expressions in `ae9932a`.

### 26.7 Bounded live lab validation **[LIVE-3]**

The full vertical slice — generator → syslog → QRadar → REST/Ariel → normalization → PostgreSQL →
baseline → detector → lifecycle — exercised against live telemetry with nothing stubbed but the
clock.

**The run.** `labrun-phaseb-spike`, seed `777`, log source 312 only, three phases at 60-second
buckets:

| Phase | Window (UTC) | Rate | Sent |
|---|---|---:|---:|
| baseline | 20:27:35 – 20:32:35 | 3 EPS | 900 |
| anomaly | 20:32:35 – 20:34:35 | 20 EPS | 2,400 |
| recovery | 20:34:35 – 20:37:35 | 3 EPS | 540 |
| **total** | | peak 20 EPS | **3,840 attempted / 3,840 sent / 0 errors** |

**Collection.** 11 buckets collected through the real REST provider; 10 entities; 110 metric rows
(10 × 11, so every entity has an explicit row in every bucket including its measured zeros);
**3,832 of 3,840 requests** accounted for — the eight-event gap is the run's tail crossing the
window boundary, not a parsing loss. **110/110 buckets COMPLETE**, and **zero** stored endpoints
contain a query string.

**Detection**, on `GET /api/v1/report`:

```
20:27  20 requests   INSUFFICIENT_DATA
20:28  43            INSUFFICIENT_DATA
20:29  38            INSUFFICIENT_DATA
20:30  37            INSUFFICIENT_DATA     <- baseline still below 4 samples
20:31  43            NORMAL                <- baseline adequate, verdict healthy
20:32  313           OPEN
20:33  714           OPEN
20:34  450           OPEN
20:35  31            RECOVERING
20:36  34            RESOLVED
20:37  26            NORMAL
```

The incident: observed **714**, expected **38**, ratio **18.79x**, robust z **91.19**, opened and
resolved, with three persisted transitions carrying their reasons —
`NORMAL → OPEN` ("1 consecutive abnormal bucket(s)"), `OPEN → RECOVERING` ("normal bucket
observed"), `RECOVERING → RESOLVED` ("2 consecutive normal bucket(s) reached the recovery threshold
of 2").

Four behaviours worth calling out, because each is a design decision being honoured rather than a
happy path:

1. **`INSUFFICIENT_DATA` came first, and was correct.** The first four buckets had fewer than
   `baseline_min_samples` observations, so the detector refused a verdict instead of reporting
   NORMAL. An unbaselined endpoint never masquerades as healthy.
2. **The incident retained its worst reading, not its latest.** `observed_value` is 714 (the peak),
   not 450 (the last abnormal bucket). An operator asking "how bad did this get?" is not shown the
   tail of a recovery.
3. **Recovery needed two consecutive normal buckets.** The single normal bucket at 20:35 moved the
   incident to `RECOVERING`, not `RESOLVED`.
4. **The explanation states what changed and no more** — observed, expected, ratio, delta, and the
   basis that fired. No causal claim.

**Query budget.** 22 bounded Ariel searches (2 per bucket), all aggregated, all `LIMIT`-capped, all
`START`/`STOP`-scoped, read-only. No raw event was retrieved and no QRadar object was created,
modified or deleted at any point in this workstream.

### 26.8 Re-evaluated gate

| # | Requirement | §25.5 | **Now** | Evidence |
|---:|---|---|---|---|
| 1 | At least one usable WAF source | ⚠ | **✓ PASS** | Source 312, all fields 100% (§26.2) |
| 2 | Required field availability | ✗ | **✓ PASS** | URL and method 100%; action and status also present (§26.2) |
| 3 | Selected and bounded entity key | ✓ | **✓ PASS** | §25.4, implemented in `b51cbcf` |
| 4 | Measured cardinality | ⚠ projected | **✓ PASS** | Measured live; matches the projection exactly (§26.4) |
| 5 | Defined normalizer behavior | ✓ | **✓ PASS** | Implemented and tested, `f2208c3` |
| 6 | Defined completeness semantics | ✓ | **✓ PASS** | Implemented and tested, `0142b61` |
| 7 | Defined persistence natural key | ✓ | **✓ PASS** | Migration `0005`, `NULLS NOT DISTINCT` asserted |
| 8 | Selected first detector | ✓ | **✓ PASS** | `WAF_REQUEST_RATE_SPIKE`, `82eed39` |
| 9 | Defined evidence dimensions | ✓ | **✓ PASS** | §13 |
| 10 | Defined query budget | ✓ | **✓ PASS** | 2 bounded searches per source per bucket (§26.7) |
| 11 | Defined test fixtures | ✓ | **✓ PASS** | Mock WAF surface + deterministic generator source |
| 12 | Defined live-validation path | ✓ | **✓ PASS** | Executed (§26.7) |
| 13 | No blocker that could change the schema | ✓ | **✓ PASS** | Schema built and exercised end to end |
| — | Event-volume semantics | ✓ | **✓ PASS** | `SUM(eventcount)`, `ae9932a` |
| — | Nullable virtual-host identity | — | **✓ PASS** | `NULLS NOT DISTINCT` + collector `IS NULL` lookup, both tested |
| — | Exact-window isolation | — | **✓ PASS** | 120/120 and 3,840/3,840, no unrelated events |

**Thirteen of thirteen.**

### 26.9 Recommendation

> ## Phase B1/B2 development: GO — and delivered
>
> Every development condition is met and the vertical slice is implemented, tested and validated
> against live QRadar telemetry. `INSUFFICIENT_DATA → NORMAL → OPEN → RECOVERING → RESOLVED` was
> observed end to end on real data (§26.7).
>
> What remains for a *complete* Phase B2 is deliberately not built here: the additive API fields of
> §18.2, the UI rendering of a `WAF_ENDPOINT` subject, and Celery schedule registration. Those are
> presentation and scheduling, not schema, and none of them can invalidate what is above.

> ## Production WAF onboarding: NO-GO — unchanged, and still not assessable
>
> Every WAF measurement in this document is synthetic lab telemetry from a generator whose payload
> the operator mapped by hand. The seven production-shaped F5 sources remain parsed by a VPN DSM and
> were deliberately left untouched. Nothing here demonstrates what a real F5 BIG-IP ASM, ModSecurity
> or cloud WAF deployment exposes, what its endpoint cardinality is after normalization, or whether
> its traffic has a shape `weekday × hour` can baseline.
>
> The schema does not depend on those answers, which is what lets the two verdicts diverge: the
> entity key uses only vendor-neutral concepts, property names are configuration rather than
> constants, and the fallback for a source with no virtual host is specified and tested.
> Onboarding needs its own field-availability pass per WAF product.

### 26.10 Remaining production-estate assumptions **[ASSUMPTION]**

Superseding §25.8. Still unverified, and none of them is a lab question:

1. A production WAF DSM exposes request path and method as queryable properties. The lab proves only
   that *a* Universal LEEF source with hand-authored properties does.
2. Production endpoint cardinality stays in the tens-to-hundreds per application after §8
   normalization. The lab's 10 entities prove the mechanism, not the scale; §8.4's circuit breaker
   is the guard, and it has never been tripped by real traffic.
3. Production traffic has a diurnal shape `weekday × hour` can baseline. The lab's profile is flat
   by construction.
4. Coalescing on a WAF source behaves as measured elsewhere on this appliance. Both lab runs were
   1:1 (records = events), so `ae9932a` is proven against production-shaped sources and merely
   *not contradicted* on a WAF one.
5. A production WAF exposes a virtual host. None verified so far does, so the nullable fallback —
   and its documented collision risk between applications sharing a path on one log source — is the
   live path, not the exception.

---

## 27. Consolidated Phase B design (preflight audit, 2026-09-13)

This section is authoritative for all Phase B work not yet built. It is grounded in the working
tree as of this audit and in the live measurements of §26. Where it restates an earlier section it
says so; where it changes one, it supersedes it. Every item is marked **[BUILT]** (exists and is
tested), **[DESIGN]** (specified here, not built) or **[BLOCKED]** (cannot be built until a named
prerequisite exists).

### 27.1 Guarantees every Phase B increment must keep

| Guarantee | How Phase B keeps it |
|---|---|
| QRadar strictly read-only | Ariel aggregates and GET-only reference reads through `QRadarRestProvider`, gated on `AQL_EXECUTION`. Authoring a custom property stays an operator console action. |
| No raw payload persistence | A raw URL exists only inside one collection call and is normalized before it reaches a column. Query strings are never persisted (§8.3). User-agent and forwarded-for values are request headers: never stored, only counted (§27.11). |
| Bounded Ariel queries | §27.12 |
| Incomplete observations never become zero | A failed query writes no row and holds the watermark; a truncated or under-covered bucket is `PARTIAL`; an unmeasured count is NULL **[BUILT]** |
| Incomplete observations never poison a baseline | Only `COMPLETE`, adequately covered buckets of the current normalizer version, outside incident spans **[BUILT]**; profile version and maintenance windows **[DESIGN]** (§27.8) |
| No misleading findings from truncated evidence | A `TRUNCATED` dimension withholds cardinality and new/disappeared claims (`404bc39`); a truncated endpoint attribution is `TRUNCATED`, not complete (§27.11) |
| Deterministic decisions, no LLM | Pure detectors over stored rows; versioned normalizer, profile and policy |
| Idempotent collection | Upsert on `(waf_endpoint_id, bucket_start)`; watermark per source **[BUILT — fixed in this audit]** |
| Auditable provenance | Grouped AQL, coverage AQL, aggregation, window, truncation and coverage on every row **[BUILT]** |
| UTC storage | `timestamptz` throughout; seasonal key computed in UTC |

### 27.2 WAF source configuration [DESIGN]

**Current state [BUILT].** Three global settings — `WAF_URL_PROPERTY`, `WAF_METHOD_PROPERTY`,
`WAF_VIRTUAL_HOST_PROPERTY` — apply one property mapping to every WAF log source, and nothing
declares which log sources are WAF sources: `WafEndpointCollector.collect(instance, log_source=…)`
has no production caller. That was enough for one lab source. It is wrong for an estate, because
two WAF products parsed by two DSMs expose different property names. On a source whose DSM lacks a
mapped property, QRadar rejects every query and the source is never collected; on one where the
property exists but is unpopulated, coverage collapses and every bucket is `PARTIAL`. Both fail
safely — no false zero, no poisoned baseline — but the source is never monitored.

**Design.** A per-log-source profile is the unit of WAF configuration.

```text
waf_source_profile
  id                      uuid  PK
  log_source_id           uuid  FK log_source(id) ON DELETE CASCADE   UNIQUE NOT NULL
  enabled                 bool          DEFAULT false                 NOT NULL
  profile_version         int           DEFAULT 1                     NOT NULL
  product_label           varchar(128)                                -- operator note, never parsed
  url_property            varchar(64)                                 NOT NULL
  method_property         varchar(64)                                 NOT NULL
  virtual_host_property   varchar(64)                                 -- NULL: source cannot supply one
  action_property         varchar(64)
  action_value_map        jsonb         DEFAULT '{}'                  NOT NULL
  response_code_property  varchar(64)
  user_agent_property     varchar(64)                                 -- cardinality only
  client_address_field    varchar(64)   DEFAULT 'sourceip'            NOT NULL
  forwarded_for_property  varchar(64)
  trust_forwarded_for     bool          DEFAULT false                 NOT NULL
  path_case_insensitive   bool          DEFAULT false                 NOT NULL
  validation_status       varchar(24)   DEFAULT 'UNVALIDATED'         NOT NULL
  validated_at            timestamptz
  validation_report       jsonb         DEFAULT '{}'                  NOT NULL
  created_at, updated_at
```

Rules:

1. **Collection requires all three:** global `WAF_COLLECTION_ENABLED`, the profile's `enabled`, and
   `validation_status = VALID`. A log source without a profile is not a WAF source.
2. **Any mapping change bumps `profile_version` and resets `validation_status` to `UNVALIDATED`,**
   pausing that source until it is revalidated. Property names decide what an endpoint *is*, so a
   mapping change is an entity-semantics change exactly like a normalizer change (§8.6) and is
   scoped the same way (§27.8).
3. **`method_property` is required.** The key includes the method (§25.4) and
   `waf_endpoint.http_method` is NOT NULL. A product with no method fails validation as
   `UNSUPPORTED_FIELDS`. Supporting one needs a nullable method under the same NULLS NOT DISTINCT
   discipline as `virtual_host` — a migration, not an assumption.
4. **`action_value_map` maps site values to outcomes**, e.g.
   `{"BLOCKED": ["DENY", "BLOCK"], "ALLOWED": ["ALLOW", "PASS"]}`, authored from values discovered
   by validation (§27.3). A value in neither list is counted as `UNMAPPED`, never as allowed.
   §26.2's `ALLOW`/`DENY` is a fact about the lab generator, not about WAF products.
5. **Forwarded-for is untrusted by default** (§11.4). `trust_forwarded_for` may be set only for a
   source documented as sitting behind a trusted proxy. A trusted value is parsed to one IP address
   or discarded; the header value itself is never stored.
6. **Profiles are application configuration, not QRadar configuration.** Writes go through a CLI
   first (`python -m app.cli.waf profile set|validate|enable|disable`), each recorded in `AuditLog`.
   An admin API behind a dedicated `admin:waf` permission may follow after its own review. No
   profile operation writes to QRadar.
   **[BUILT 2026-10-13]** That review is [`FIELD-MAPPING.md`](FIELD-MAPPING.md) §FM.10; the admin
   API is §FM.4–§FM.5 (T-042) and the UI is `/admin/waf-sources` (T-044, with the searchable
   property picker of T-058).
7. The three global settings become the defaults a new profile is seeded from. The collector stops
   reading them.

### 27.3 QRadar custom-property mapping [DESIGN; allowlist BUILT]

Property names are display names an operator authored in the QRadar console. They follow neither
the LEEF keys a device emits (§25.2: eleven plausible names, all rejected) nor any cross-vendor
convention. Nothing in Phase B may assume one.

**Safety [BUILT].** Every name is matched against `^[A-Za-z0-9_][A-Za-z0-9_ -]{0,63}$` before it
reaches AQL, and is rejected rather than escaped on failure.

**Validation [DESIGN].** Bounded and read-only, run on demand and on every mapping change, scoped to
one log source over an operator-chosen window (default the last hour, maximum 24 h), every query
`START`/`STOP`-bounded:

| Step | AQL shape | Establishes | Failure |
|---|---|---|---|
| Existence | `SELECT "<p>" FROM events WHERE logsourceid = <ls> LIMIT 1` | QRadar accepts the name | query rejected → `INVALID`, "property does not exist" — proof of absence, not an empty result |
| Population | `SELECT SUM(eventcount) … AND "<p>" IS NOT NULL`, against the source total | share of events carrying it | URL or method below `WAF_MIN_ENDPOINT_COVERAGE` → `INVALID`, with the measured share |
| Cardinality | `SELECT UNIQUECOUNT("<p>") …` | distinct raw values | recorded; URL cardinality feeds §27.5 |
| Value discovery | `GROUP BY "<p>" ORDER BY SUM(eventcount) DESC LIMIT 50` | bounded value set for action and response code | a truncated set is recorded as truncated, and the action map must then be authored explicitly — never inferred from a prefix |
| Normalized projection | URL groups `LIMIT 500`, normalized in memory | endpoint cardinality and singleton share after normalization | recorded; sizes the admission controls (§27.5) |

Budget: at most 4 queries per mapped property and 24 per validation, one validation per source at a
time. The report stores each query's AQL, window, row count and truncation flag and the measured
numbers. It never stores a raw URL, query string or header value — only counts, discovered action
and response-code values, and normalized endpoints.

**Declared types.** The response-code property measured as a *string* on the lab appliance (§26.1),
so values are validated in application code (`normalize_response_code`, strictly three digits) and
never cast in AQL. Reading declared types from QRadar's custom-property reference API is
**[ASSUMPTION]**: its availability depends on version and token capability. Where it is unavailable
the type is inferred from values, and the report says so.

**Drift.** A daily light check (existence and population over the last hour, two queries per
property) marks a profile `STALE` when a required property's population drops below threshold — the
signature of a DSM update or a parser edit. `STALE` does not stop collection, because per-bucket
coverage already downgrades affected buckets to `PARTIAL`. It raises an operator-visible banner and
must be cleared by revalidation.

### 27.4 Endpoint normalization [BUILT]

§8 is implemented as `ENDPOINT_NORMALIZER_VERSION = 1` (`f2208c3`), with two recorded refinements:
a schemeless `//host/path` is a path, not an authority, so `//evil.example/admin` cannot be counted
against `/admin`; and a long hex-shaped segment is `{hex}` before it is `{overlong}`. Absence is
never `/`, an unknown method is never `"UNKNOWN"`, and a query string never survives.

Changes from this audit:

- **Version scoping now reaches the entity key.** The natural key omitted `normalizer_version` while
  the collector looked endpoints up by it, so the first version bump would have raised an
  IntegrityError on every unchanged endpoint and stopped collection. Migration `0007` adds the
  version to the key, keeping NULLS NOT DISTINCT. A bump now creates fresh entities whose cells start
  at `INSUFFICIENT_DATA`; old-version entities stop being zero-filled at once and retire on the
  ordinary `WAF_ENDPOINT_ACTIVE_DAYS` schedule.
- **Rule 10, case-insensitive paths,** becomes the profile's `path_case_insensitive` [DESIGN].
  Because it changes what an endpoint is, applying it bumps `profile_version`.
- **Route templates** (§8.5) remain out of scope.

### 27.5 Entity cardinality strategy [BUILT in B.1, with changes — see §28.5]

> **Superseded in part by §28.5.** Two rules below were changed when built. Admission truncation
> does **not** mark a bucket `PARTIAL`: that would let anyone spraying new paths suppress verdicts
> on every endpoint already tracked. And admission uses the per-bucket volume threshold only; the
> "seen in two buckets within 24 h" alternative would have required storing refused endpoints.

The entity is `(instance, log source, virtual host | NULL, normalized endpoint, method, normalizer
version)` — §25.4 plus the version. Client address is never part of it.

The normalizer bounds *structure*, not *intent*. A scanner requesting `/aaaa`, `/aaab`, … —
alphabetic segments with too little entropy for rule 14 — mints a new entity per path. Today the only
bound is `WAF_MAX_GROUPS_PER_BUCKET` (500), and it bounds the *query*, not the *entities*: a
truncated bucket is marked `PARTIAL`, but every returned group still creates an endpoint. That is up
to 500 new entities per source per bucket, each zero-filled for seven days. §8.4's circuit breaker
was specified and never built. **It must exist before any WAF collection is scheduled (§27.18).**

| Control | Rule |
|---|---|
| **Admission cap** | at most `WAF_MAX_NEW_ENDPOINTS_PER_BUCKET` (default 20) new entities per source per bucket, admitted in descending request volume. Overflow groups create no entity and no row; their count and summed volume are recorded in provenance as `admission_overflow`, and the bucket is `PARTIAL`. |
| **Active ceiling** | at most `WAF_MAX_ACTIVE_ENDPOINTS_PER_SOURCE` (default 2,000). At the ceiling nothing new is admitted until retirement frees room, and the source reports it. |
| **Activation threshold** | admitted only with ≥ `WAF_MIN_ADMISSION_REQUESTS` (default 3) in the bucket, or when seen in ≥ 2 distinct buckets within 24 h. One-off probes never become entities. |
| **Retirement** | unchanged: no observation for `WAF_ENDPOINT_ACTIVE_DAYS` stops zero-filling **[BUILT]** |
| **Measured before enabling** | validation reports normalized cardinality and singleton share (§27.3) |

With these controls, zero-fill rows per source per day are at most the active ceiling × buckets per
day, and new entities per day at most the admission cap × buckets per day. The lab's 10 entities
give 2,880 rows/day; a source at the default ceiling gives 576,000. That is why the ceiling is a
per-source setting sized from the validation report, not a constant.

### 27.6 Endpoint behavior metrics

Per endpoint, per bucket:

| Metric | Source | Status | Notes |
|---|---|---|---|
| `request_count` | `SUM(eventcount)` | **[BUILT]**, baselined | the MVP metric |
| `distinct_client_count` | `UNIQUECOUNT(<client>)` | **[BUILT]**, collected, not baselined | a lower bound after folding raw groups (maximum, not sum) — rendered "≥ n" |
| `blocked_count` | `SUM(eventcount)` where action ∈ BLOCKED | [BLOCKED] on a validated action map | NULL when unmapped, never 0 |
| `unmapped_action_count` | action in neither list | [DESIGN] | makes a stale map visible instead of silently "allowed" |
| `client_error_count`, `server_error_count` | 4xx / 5xx from a validated response code | [BLOCKED] on a validated response-code property | NULL when unavailable |
| `block_ratio`, `error_ratio` | derived at read time | [DESIGN] | §11.3 unchanged: minimum denominator, NULL on a zero denominator, an absolute floor as well as the seasonal guard |

The method is part of the key, not a metric. Action and response class are added as `GROUP BY`
columns on the existing grouped query rather than as new queries, then folded per endpoint in
memory. That multiplies grouped cardinality by (actions × response classes), so the group cap is
re-sized from the validation report before either is enabled.

### 27.7 Client behavior metrics [DESIGN — wording revised by §28.11]

> **Revised by §28.11.** The sentence below rules out *unbounded* per-client entities. It is not a
> permanent prohibition on client-level rows: future detectors need to know which client address
> dominates a source's traffic, what share it holds, and whether one fanned out across endpoints.
> §28.11 defines the bounded client aggregate those detectors may add.

Clients are unbounded and attacker-settable: there are **no unbounded per-client entities, rows or
baselines** (§6.1, §25.4). Client behavior is measured in three bounded ways.

1. **Per endpoint, per bucket:** `distinct_client_count` **[BUILT]**. Top-client share — the
   fraction of an endpoint's requests from its largest client — is computed from the §27.11 evidence
   query, and only for an endpoint already in an `OPEN` incident, never for every endpoint every
   bucket.
2. **Per source, per bucket:** `source_distinct_clients` and `source_top_client_share`, from one
   more bounded query per source per bucket (`GROUP BY <client> ORDER BY SUM(eventcount) DESC LIMIT
   20` with a `UNIQUECOUNT`), stored in a new `waf_source_metric` hypertable. This catches one client
   spraying many endpoints, which no single endpoint's spike reveals. Share is scale-free and
   baselines well.
3. **At investigation time:** client-address contributors (§27.11), flagged *"client address not
   observed in the baseline window"* only over the window actually queried and retained.

Never claimed: identity ("user"), intent ("attacker"), or novelty beyond the retention actually held
(§11.4). Behind NAT or a CDN a client address is an intermediary, and the wording says so.

### 27.8 Baseline scopes

| Scope | Key | Metric | Seasonality | Status |
|---|---|---|---|---|
| Endpoint | `waf_endpoint_id`, `normalizer_version`, `profile_version` | `request_count` | weekday × hour, UTC | **[BUILT]** except `profile_version` [DESIGN] |
| Endpoint ratio | same | `block_ratio`, `error_ratio` | weekday × hour | [BLOCKED] on §27.6 fields |
| Source client concentration | `log_source_id`, `profile_version` | `source_top_client_share`, `source_distinct_clients` | weekday × hour | [DESIGN] |
| Peer group | — | — | — | **not designed** (§27.9) |

Every scope uses `app.anomaly.seasonal` **[BUILT]**: median, MAD and p05/p95; the minimum sample
count; incident-span exclusion; completeness as the retained fraction. Every scope excludes buckets
that are not `COMPLETE`, fall below coverage, or lie in an incident span — and, new, buckets inside
a **maintenance window on the WAF log source**. `WafBaselineBuilder` and `WafAnomalyEngine` ignore
`LogSource.maintenance_mode` today, which Phase A honors; that gap is closed before scheduling
(§27.18).

A scope's key contains every version that changes what its observations mean. A baseline never
mixes rows from two normalizer or profile versions; a version change starts new cells at
`INSUFFICIENT_DATA`.

### 27.9 Cold start and new clients

**New source.** No collection until its profile is `VALID`. Before any cell is reliable, every
verdict is `INSUFFICIENT_DATA`, never `NORMAL` **[BUILT; observed live, §26.7]**.

**New endpoint.** `INSUFFICIENT_DATA` per seasonal cell until the cell holds `BASELINE_MIN_SAMPLES`
complete samples. At 5-minute buckets a cell gains 12 samples per week, so a steady endpoint's cells
become reliable in its first week; a sparse endpoint's may never, which is the honest outcome
(§12.3). The endpoint's *appearance* is reported separately as `WAF_NEW_ENDPOINT` (§27.10), which is
how a genuinely new surface is surfaced while it has no baseline.

**New client.** Never baselined, so there is nothing to be cold. A new client counts towards its
endpoint's volume and the source's concentration metrics, and at investigation time is flagged as
not observed in the baseline window. If Ariel retention is shorter than the requested lookback, the
flag is withheld and the dimension says why.

**Peer-group baselines for new clients**, the roadmap's line item, are **not designed for
implementation**. A peer group needs a peer dimension the telemetry lacks: no verified WAF source
exposes application, tenant, ASN or geography, and a peer group over client addresses alone is the
per-client entity §6.1 rejected. The item returns when a validated profile exposes such a dimension.
Until then it would be an invented model.

### 27.10 Anomaly types

| Type | Verdict basis | Prerequisite | Status |
|---|---|---|---|
| `WAF_REQUEST_RATE_SPIKE` | endpoint `request_count` against its seasonal baseline; conjunctive guards; degenerate-MAD fallback | — | **[BUILT]**, not scheduled |
| `WAF_NEW_ENDPOINT` | first admitted appearance of an entity, after the source has been `VALID` for one full lookback | admission controls (§27.5) | [DESIGN] — without the cap it amplifies scanners |
| `WAF_BLOCK_RATIO_SPIKE` | `block_ratio` seasonal deviation **and** absolute floor **and** minimum denominator | validated action map | [BLOCKED] |
| `WAF_ERROR_RATIO_SPIKE` | same, on server (5xx) and separately client (4xx) error ratios | validated response-code property | [BLOCKED] |
| `WAF_CLIENT_CONCENTRATION_SPIKE` | `source_top_client_share` against its seasonal baseline, above a minimum source volume | `waf_source_metric` | [DESIGN] |
| `WAF_REQUEST_RATE_DROP` | — | — | **excluded** (§10.4): endpoint silence is normal, source silence is Phase A `NO_EVENTS` |
| `WAF_NEW_CLIENT_ENDPOINT` | — | — | **evidence only** (§11.4), never an incident |
| Peer-group deviation | — | a peer dimension | **not designed** (§27.9) |

Each type is a `WafAnomalyType` member with a `WAF_DETECTORS` entry in
`app/anomaly/capabilities.py`, whose status `GET /api/v1/behavior/detectors` publishes, and each has
its own detector-state row, so coexisting incidents on one endpoint recover independently (§14).

### 27.11 Evidence collection [DESIGN]

Built when an endpoint incident reaches `OPEN`, asynchronously, as in Phase A: detection never waits
on Ariel.

- **Windows.** The anomaly window is the incident's abnormal buckets, clamped to
  `EXPLANATION_MAX_WINDOW_SECONDS`. The comparison window ends where the anomaly begins, is
  `EXPLANATION_BASELINE_WINDOW_MULTIPLE` times as long, and is compared on a rate basis.
- **Filtering (Approach B, §25).** AQL cannot apply the normalizer, so each dimension query filters
  on log source and window only, groups by `(url, method, dimension)`, and the application keeps the
  groups that normalize into the incident's endpoint.
- **Attribution completeness.** If that grouped result is truncated, groups belonging to the
  endpoint may be missing: the dimension is `TRUNCATED`, shares are stated as "among returned
  groups", and cardinality, new-value and disappeared-value claims are withheld (`404bc39`). A
  dimension where no returned group folds into the endpoint is `UNAVAILABLE` with that reason, never
  an empty success.
- **Dimensions.** Client address; action via the profile map; response class; QID and event name;
  destination IP. A dimension whose profile property is unmapped is recorded `UNAVAILABLE` and
  rendered, never omitted.
- **Request headers are counted, never stored.** User agent and forwarded-for are request headers,
  which the architecture forbids persisting (§7.5 there). They contribute only a distinct-value count
  per window. The provenance AQL lets an analyst pivot to the values in QRadar itself. This
  supersedes §26.2's "contributor evidence" role for `WAF_USERAGENT`.
- **Storage.** Parallel typed tables `waf_anomaly_explanation`, `…_dimension` and `…_contributor`,
  column for column the Phase A shape, reusing the pure comparison in `app/anomaly/explanation.py`.
  §15.2's polymorphic foreign keys on the Phase A evidence tables are **rejected**: they alter
  live-validated tables for no capability the parallel tables lack. This closes OPEN-5.
- **Budget.** At most 2 queries per dimension per incident (anomaly and comparison window), 6
  dimensions, and `EXPLANATION_MAX_PER_RUN` incidents per run, under a per-instance advisory lock.

### 27.12 Query bounding

| Query | Per | Bound | Status |
|---|---|---|---|
| Grouped observation | source × bucket | `LIMIT WAF_MAX_GROUPS_PER_BUCKET`; truncation → `PARTIAL` | **[BUILT]** |
| Coverage total | source × bucket | single row | **[BUILT]** |
| Source client concentration | source × bucket | `LIMIT 20` plus one `UNIQUECOUNT` | [DESIGN] |
| Evidence | incident | §27.11 | [DESIGN] |
| Validation | source, on demand and daily | §27.3 | [DESIGN] |

Every query is read-only, aggregated and scoped to one log source; interpolates only integer epochs
and allowlisted property names; carries `START`/`STOP` plus a half-open `starttime` predicate
(`dc9aa34`); stays within the 168-hour window ceiling; polls for at most 120 s and cancels on timeout;
and is capped by `LIMIT` and provider pagination. Steady-state collection costs 2 searches per enabled
source per bucket, 3 with concentration — 576 to 864 per source per day at 5-minute buckets. The
scheduled task processes sources sequentially under per-source locks and caps sources per run with
`WAF_MAX_SOURCES_PER_RUN`, so a large estate falls visibly behind (watermark lag) rather than
flooding Ariel.

### 27.13 Retention [DESIGN]

`waf_endpoint_metric` is a hypertable, but it is **absent from `Settings.retention_days()`**, so
nothing ever drops it.

| Table | Retention | Why |
|---|---|---|
| `waf_endpoint_metric`, `waf_source_metric` | `RETENTION_WAF_METRIC_DAYS`, default **35** | 28-day lookback plus a week, so a rebuild never runs off its own data |
| `waf_endpoint_baseline` | lifetime of the endpoint | 168 cells per metric per endpoint, bounded by the active ceiling |
| `waf_endpoint` (retired) | deleted `RETENTION_WAF_METRIC_DAYS` after `retired_at` when no incident references it | bounds retired-entity growth |
| `waf_anomaly`, transitions, evidence | the Phase A incident policy | investigations must outlive the metrics that raised them |
| `waf_source_profile` validation | latest report on the profile; history in `AuditLog` | |

Applied through the existing non-destructive `app/services/timescale.py` path and never inside a
migration, as Phase A does.

### 27.14 API shape [DESIGN]

Every route is `GET`, behind `read:anomalies`, paginated and range-bounded by `API_MAX_RANGE_DAYS`,
and joins the existing GET-only route test.

**Unified anomaly list and detail** — §18.2 in intent, made fully additive:

```text
AnomalyListItem / AnomalyDetailOut
  + subject_kind      "LOG_SOURCE" | "WAF_ENDPOINT"
  + waf_endpoint      {id, normalized_endpoint, http_method, virtual_host | null,
                       virtual_host_available, normalizer_version} | null
  ~ log_source_id     unchanged and still required: for a WAF subject, the WAF log source
  ~ anomaly_type      AnomalyType | WafAnomalyType
GET /api/v1/anomalies?subject_kind=&waf_endpoint_id=&log_source_id=
```

Keeping `log_source_id` required — the WAF log source, for a WAF subject — removes the one
non-additive change §18.2 proposed. `detection` gains a WAF projection (observed and expected
requests, coverage, normalizer version) through the same explicit whitelist.

**WAF behavior:**

```text
GET /api/v1/behavior/waf/sources                                profile, validation status, coverage, endpoint counts
GET /api/v1/behavior/waf/sources/{log_source_id}                one source, last validation report (counts only)
GET /api/v1/behavior/waf/sources/{log_source_id}/endpoints      paged inventory: adequacy, last seen, state
GET /api/v1/behavior/waf/endpoints/{id}/metrics                 buckets, completeness, coverage; NULL stays NULL
GET /api/v1/behavior/waf/endpoints/{id}/baselines               cells, reliability, versions
GET /api/v1/behavior/detectors                                  [BUILT] detector status, both domains
```

No route writes to QRadar. Profile writes go through the CLI or the `admin:waf` API of
[`FIELD-MAPPING.md`](FIELD-MAPPING.md) §FM.4 (built 2026-10-13); the routes above stay read-only.

### 27.15 UI shape [DESIGN]

Reuses the investigation components that are generic over evidence (§3.4). No new visual system.

- **Anomaly list.** A subject column rendering `GET /portal/login · lab-waf-behavior-01`, and a
  subject-kind filter.
- **Investigation detail.** The header names method, endpoint and source. When the source exposes no
  virtual host it says: *"This WAF source does not expose a virtual host, so this endpoint may
  combine traffic for different applications that share the path."* The timeline plots requests
  against the seasonal band, `PARTIAL` buckets as gaps, and coverage as a secondary series. Evidence
  renders all six evidence states and `UNAVAILABLE` sections exactly as Phase A does.
- **`/behavior/waf`.** Per-source profile and validation status, coverage over time, active
  endpoints against the ceiling, admission overflow.
- **`/behavior/waf/sources/[id]`.** Endpoint inventory: baseline adequacy, last seen, retired, state.
- **Detector coverage.** The `/behavior` overview lists every detector's status from
  `/behavior/detectors`, so an operator sees that parsing or cardinality detection is not active
  instead of inferring it from an empty list.
- Honesty rules unchanged: unmeasured renders as an em dash, a measured zero as `0`, and
  `distinct_client_count` as "≥ n".

### 27.16 Live-lab validation strategy

Against the dedicated source `lab-waf-behavior-01` (312): bounded, ≤ 20 EPS, deterministic seed,
each scenario sized to one UTC hour, each recorded in a sanitized `LAB-*` document with no raw
events.

| # | Scenario | Proves |
|---|---|---|
| 1 | Validate a correct profile | existence, population and cardinality measured; `VALID` |
| 2 | Validate a profile with one wrong property name | `INVALID`, "property does not exist"; nothing collected |
| 3 | Baseline, spike on one endpoint, recovery — through the scheduled task | `INSUFFICIENT_DATA → NORMAL → OPEN → RECOVERING → RESOLVED`; sibling endpoints stay `NORMAL` |
| 4 | Two WAF sources in one collection cycle | both collect every bucket (per-source watermark) |
| 5 | 30% of events without a URL | buckets `PARTIAL`, excluded from baselines; detector `INSUFFICIENT_DATA` |
| 6 | 1,000 distinct low-entropy paths in 10 minutes | admission cap holds; overflow recorded; entity count bounded |
| 7 | Burst of byte-identical events | Ariel coalesces (`SUM(eventcount) > COUNT(*)`) and `request_count` equals events sent — the first WAF-source proof of `ae9932a` (§26.10 item 4) |
| 8 | `DENY` burst on one endpoint | `WAF_BLOCK_RATIO_SPIKE` through the validated action map |
| 9 | Normalizer or profile version bump mid-run | new entities at `INSUFFICIENT_DATA`, no IntegrityError, old entities retire |
| 10 | Maintenance window on the WAF source | no incident opens; buckets excluded from baselines |
| 11 | Evidence on an `OPEN` incident with a forced group cap | contributors present; the truncated dimension withholds cardinality claims |

Each scenario states the QRadar event count it expects and checks the observed count by
exact-window isolation (§26.3). Cleanup stops the generator and disables the profile. Nothing is
written to QRadar beyond the generator's syslog traffic to the operator-configured lab source.

### 27.17 Migration strategy

**Done in this audit — additive, reversible, no Phase A table touched:**

| Revision | Change |
|---|---|
| `0006` | Data only. `waf_anomaly.details.anomaly_type` corrected from the borrowed `VOLUME_SPIKE` to the row's `detector_type`. No-op downgrade. |
| `0007` | `uq_waf_endpoint_natural_key` gains `normalizer_version`, keeping NULLS NOT DISTINCT. Downgrade fails loudly if endpoints exist under two versions. |

The WAF collector's watermark moved from one `waf_endpoint_metric` row per instance to
`waf_endpoint_metric:<QRadar log source id>` per source. The old row is left in place and no longer
read. A source's first run under the new key collects its most recent interval, as every first run
does.

**Next Phase B revisions, in order, each additive:**

1. `waf_source_profile` (§27.2), seeded for log sources that already have endpoints from the global
   settings with `validation_status = UNVALIDATED` — so upgrading pauses WAF collection until each
   source is validated instead of continuing on an unverified mapping.
2. `profile_version` on `waf_endpoint`, `waf_endpoint_metric` and `waf_endpoint_baseline`: added
   nullable, backfilled to 1, set NOT NULL and added to the natural key and baseline uniqueness in
   the same revision, with the backfill default dropped so none is left behind.
3. `waf_source_metric` hypertable, and WAF retention wired into `Settings.retention_days()`.
4. `unmapped_action_count`, `client_error_count`, `server_error_count` added nullable. NULL is the
   correct value for every historical row; `blocked_count` and `error_count` already exist as NULL.
5. The parallel `waf_anomaly_explanation*` tables.

Admission-overflow provenance needs no schema change (JSONB).

**Operator procedure for the mixed-history hazard** (architecture §12.4), for a deployment whose
28-day lookback spans `ae9932a`:

1. Find coalescing sources with one bounded read-only aggregate:
   `SELECT logsourceid, SUM(eventcount) AS events, COUNT(*) AS records FROM events GROUP BY logsourceid LAST 24 HOURS`.
   A source whose `events / records` is close to 1.0 is unaffected.
2. For each affected source choose one of:
   - **wait** — `VOLUME_SPIKE` may open on unchanged traffic until pre-fix rows leave the lookback,
     at most 28 days;
   - **maintenance** — set `maintenance_mode` with `maintenance_until` 28 days after the deployment,
     which suppresses incidents and excludes those buckets from baselines, at the cost of no
     detection on that source for the period;
   - **relearn** — delete that source's `log_source_metric` rows collected before the deployment
     and rebuild its baselines, accepting `INSUFFICIENT_DATA` until they re-accumulate.
3. Never reset a watermark while collection is running (Phase A §11.1).

The platform does not do this automatically. Rows collected before this audit do not record which
aggregate produced them, and deleting history on an inference is exactly the kind of silent
destructive action the platform exists to avoid.

### 27.18 Blockers

**Before scheduling WAF collection at all — all cleared by Phase B.1 (§28):**

1. ~~Per-source profiles and validation (§27.2, §27.3)~~ — built, §28.2–§28.3.
2. ~~Admission controls (§27.5)~~ — built, §28.5.
3. ~~Maintenance honoring in `WafBaselineBuilder` and `WafAnomalyEngine` (§27.8)~~ — built, §28.4.
4. ~~Retention for `waf_endpoint_metric` (§27.13)~~ — built, §28.9.
5. ~~A Celery task with per-source locks~~ — built as per-source fan-out, §28.6. A sources-per-run
   cap is unnecessary once each source is its own task; Ariel load is instead bounded by worker
   concurrency, see §28.14.

The remaining open items are listed in §28.14.

**Before exposing WAF incidents to analysts:** the §27.14 API fields, §27.11 evidence, §27.15
rendering, and alert routing with a WAF fingerprint.

**Before production WAF onboarding:** §26.10's five production-estate assumptions, answered per
product by §27.3 validation against a real, non-lab source — including a coalescing measurement on
WAF traffic.

**Carried from Phase A, not specific to WAF:** watermark last-write-wins (Phase A §11.1),
intermittent `MissingGreenlet` in collection (Phase A §11), local-development auth asymmetry
(Phase A §11.3), and the mixed-history hazard (§27.17).

---

## 28. Phase B.1 — WAF collection productionization (2026-09-13)

What was built to make WAF collection safe to schedule across several real WAF sources, the exact
semantics it enforces, what it measurably costs, and what is still open. Built on the §27 design;
where it departs from §27 it says so. No new detector, no analyst UI and no alert routing were
added.

### 28.1 Components

| Concern | Where |
|---|---|
| Profile model and migration | `app/models/waf.py` (`WafSourceProfile`), `alembic/versions/0008_*` |
| Logical field mapping, eligibility | `app/waf/profile.py` |
| Validation judgement (pure) | `app/waf/validation.py` |
| Validation queries (read-only) | `QRadarProvider.probe_waf_properties`, REST and mock |
| Profile administration, audit | `app/services/waf_profiles.py`, `python -m app.cli.waf` |
| Admission (pure) | `app/waf/admission.py` |
| Per-source bucket record | `WafSourceBucket` hypertable, written by `WafEndpointCollector` |
| Metric-semantics identity | `app/waf/semantics.py` |
| Shared maintenance rule | `app/anomaly/seasonal.in_maintenance` (also used by Phase A, unchanged in effect) |
| Scheduled pipeline | `app/services/waf_pipeline.py`; tasks `dispatch_waf_collection`, `collect_waf_source` |
| Retention | `app/services/waf_retention.py`; task `apply_waf_retention` |
| Operational status | `app/services/waf_status.py`, `GET /api/v1/waf/sources` (`admin:waf`) |

### 28.2 Profile and configuration model

One `waf_source_profile` per log source, belonging to that source's QRadar instance. It maps seven
logical fields — `URL`, `METHOD`, `VIRTUAL_HOST`, `ACTION`, `RESPONSE_CODE`, `RULE_ID`,
`USER_AGENT` — to site-authored QRadar property names. `URL` and `METHOD` are required; the rest
are nullable, and **NULL means unavailable by configuration**, which validation reports as
`NOT_MAPPED`: distinct from zero, from an empty string, from a parse failure and from an empty
window.

- **No property name is a default anywhere in collection.** The three `WAF_*_PROPERTY` settings
  are no longer read by scheduled collection; the collector requires a mapping.
- **Names only, never expressions.** Every name must match
  `^[A-Za-z0-9_][A-Za-z0-9_ -]{0,63}$` at write time and again before it reaches AQL. A quote,
  comma or parenthesis is refused, not escaped.
- **Versioned.** Any mapping change increments `profile_version`, clears the validation report and
  resets `validation_status` to `UNVALIDATED`.
- **Audited.** Create, mapping change (with the full before and after mapping), enable, disable
  and every validation are written to the append-only `audit_log` with the acting operator.
- **SOC-admin controlled.** Writes go through the CLI only. The API is read-only and requires
  `admin:waf`, which `read:*` does not grant.
- **Eligibility fails closed.** A source is collected only when WAF collection is enabled globally,
  the profile is enabled, the log source is monitored, and `validation_status = VALID` **for the
  current `profile_version`**. Enabling a profile whose current version has not validated is
  refused.

`action_value_map`, `path_case_insensitive` and forwarded-for trust (§27.2) are **not** built: no
code reads action, response code or forwarded-for yet, so storing their configuration now would be
configuration without a reader.

### 28.3 QRadar profile validation

Read-only, bounded to `WAF_VALIDATION_WINDOW_SECONDS` (default 900 s, hard maximum 3,600 s), scoped
to one log source, `START`/`STOP`-bounded and `LIMIT`-capped:

1. **Log source lookup** — REST `GET` of the log source. Not found → `INVALID`, and **no Ariel
   query is spent**. A failed lookup → `INCONCLUSIVE`.
2. **Total volume** — one `SUM(eventcount)` over the window.
3. **Per mapped property**, at most two aggregates: population (`SUM(eventcount)` and
   `UNIQUECOUNT` over `IS NOT NULL` events) and the top `WAF_VALIDATION_MAX_VALUES` values by
   `SUM(eventcount)`.

Budget: at most 1 + 2 × 7 = 15 searches per validation.

Per field the outcome is exactly one of:

| Status | Established by | Proves |
|---|---|---|
| `NOT_MAPPED` | profile | unavailable by configuration |
| `PROPERTY_REJECTED` | QRadar answered HTTP 422 to a query naming it | the property does not exist as named |
| `QUERY_FAILED` | any other failure | nothing |
| `NO_EVENTS` | the source's total was 0 | nothing |
| `UNPOPULATED` | no event carried a value | the property exists and is empty |
| `INSUFFICIENT_COVERAGE` | a collection field populated on < `WAF_VALIDATION_MIN_RATIO` of events | unusable for collection |
| `IMPLAUSIBLE` | < `WAF_VALIDATION_MIN_RATIO` of sampled events pass the field's check | mapped to the wrong property |
| `AVAILABLE` | everything above passed | usable |

**A query succeeding is never taken as proof a field exists.** Existence also needs populated
values, and usability needs plausible ones: a URL must normalize, a method must be a supported
verb, a response code must be three digits, an action or rule id a bounded printable token.

The profile is `VALID` only when every field collection reads — `URL`, `METHOD`, and
`VIRTUAL_HOST` when mapped — is `AVAILABLE`. Any invalidating status on one of those makes it
`INVALID`; `NO_EVENTS` or `QUERY_FAILED` on one makes it `INCONCLUSIVE`. Optional fields are
reported but never decide the outcome.

The stored report holds statuses, counts, ratios and query text. Only low-cardinality enumerations
— method, action, response code — are recorded as discovered values. URLs, virtual hosts, rule
ids and user agents are recorded as counts; their values are discarded after judgement.

**[ASSUMPTION]** that QRadar answers an Ariel query naming a nonexistent property with HTTP 422. It
is consistent with §25.2, where eleven guessed names were each "rejected as invalid" — the
sanitized text this provider emits for 422 — but it has not been re-verified against the appliance
in this workstream. If QRadar instead accepted such a query and returned no rows, validation would
report `UNPOPULATED`, which is still `INVALID` for a collection field.

### 28.4 Maintenance semantics

**The rule** is Phase A's, now one shared function, `app.anomaly.seasonal.in_maintenance`, over the
two columns a log source carries:

| Configuration | Bucket `[start, start + width)` is in maintenance when |
|---|---|
| `maintenance_mode` off | never |
| on, `maintenance_until` NULL | always |
| on, `maintenance_until` set | `start <= maintenance_until` |

So a bucket fully inside the window is in maintenance; a bucket the window ends inside (partial
overlap) is in maintenance; the bucket starting exactly at `maintenance_until` is in maintenance;
and **the first bucket not in maintenance is the first to start strictly after
`maintenance_until`**.

**When the rule is asked is the WAF difference.** The window has no start time, so re-asking the
rule against the current flag rewrites history: turning maintenance on would put every earlier
bucket in the lookback "in maintenance", and turning it off would take every maintenance bucket
out. The WAF collector therefore asks once per bucket, at collection, and records the answer on
`waf_source_bucket.in_maintenance`. Both the WAF engine and the WAF baseline builder read that
record. Only a bucket with no record falls back to the rule evaluated now.

This was found by the integration test, not by review: with the rule re-evaluated at rebuild time,
entering maintenance excluded all eight buckets of the lookback, the cell was retrained on a single
spike, and the next genuine spike was judged `INSUFFICIENT_DATA`.

**Effects of a bucket in maintenance:**

- It is collected: its metrics, admission record and provenance are stored.
- It **never trains** a baseline (`exclusion_counts.maintenance`).
- An anomalous verdict on it is demoted to `UNKNOWN`: it **advances no confirmation counter and
  opens no incident**.
- A detector in `NORMAL`, `INSUFFICIENT_DATA` or `RESOLVED` moves to `SUPPRESSED`. An incident
  already `CANDIDATE`, `OPEN` or `RECOVERING` is not suppressed, and healthy buckets still count
  towards its recovery — exactly as Phase A.

**The first complete bucket after maintenance** moves `SUPPRESSED → NORMAL` ("suppression
lifted") whatever its verdict, and that verdict does advance the counters. Detection resumes from
the next bucket: with `ANOMALY_OPEN_AFTER_INTERVALS = 1`, a spike that starts in maintenance and
continues is `SUPPRESSED, SUPPRESSED, NORMAL, OPEN` (tested). **Limitation:** an anomalous
lift bucket opens no incident, so no incident span excludes it from training. It contributes one
sample to a median/MAD cell. This is Phase A's lifecycle behavior, kept for consistency rather than
changed in one domain.

**Backfill.** A bucket collected late is judged against the flag at the time it is collected. A
maintenance window configured before a backlog is collected therefore covers the backlog's buckets
that start at or before `maintenance_until` — conservative: they are excluded rather than judged.

**Phase A consequence — reported, not changed.** Phase A re-asks the rule against the current flag
on every use. Its baseline builder therefore excludes every bucket in the 28-day lookback that
starts at or before `maintenance_until` for as long as maintenance is on — including buckets from
before the maintenance was configured — and readmits the maintenance buckets to training once the
flag is cleared. (A Phase A cell whose samples are all excluded keeps its previous values, because
the builder only rewrites cells that have samples.) Fixing that means recording the decision on
`log_source_metric`, a schema change and a behavior change to the live-validated pipeline, so it is
left for an explicit decision (§28.14).

### 28.5 Endpoint admission and cardinality

Implemented as §27.5 describes, with the two changes noted there.

For each collected bucket, after folding raw groups into normalized `(virtual host, endpoint,
method)` keys:

1. **Tracked** — keys with an *active* entity of the current normalizer version — are always
   collected. The new-endpoint quota never applies to them.
2. **Unseen** — keys with no entity, or only a retired one — are candidates.
3. A candidate below `WAF_MIN_ADMISSION_REQUESTS` (default 3) in the bucket is not admitted.
4. The rest are ordered by request count descending, then virtual host (NULL first), endpoint and
   method — a total order, so arrival order from QRadar cannot change the result.
5. The first `min(WAF_MAX_NEW_ENDPOINTS_PER_BUCKET, WAF_MAX_ACTIVE_ENDPOINTS_PER_SOURCE − active)`
   are admitted (defaults 20 and 2,000). A retired entity that is admitted is reactivated; its
   `first_seen_at` is kept.
6. Everything else creates no entity and no row, and is **never stored by name**.

Every bucket's `WafSourceBucket` records `observed_endpoints`, `tracked_endpoints_observed`,
`admitted_endpoints`, `below_admission_threshold`, `rejected_over_quota`, `untracked_endpoints`,
`untracked_requests` and `admission_truncated` (eligible endpoints refused for want of quota). The
record is written even when no endpoint row is.

**Admission truncation does not make a bucket `PARTIAL`.** Every tracked endpoint's count in that
bucket is still whole. **Provider group truncation still does**: when the grouped query hits
`WAF_MAX_GROUPS_PER_BUCKET`, any tracked endpoint's raw groups may be missing, so the bucket cannot
be a whole observation of anything. See the corresponding limitation in §28.14.

The normalizer is the first bound. Numeric enumeration (`/users/1` … `/users/5000`) folds into one
`/users/{id}` entity before admission runs (tested); admission is what bounds enumeration the
normalizer cannot template, such as alphabetic segments.

### 28.6 Scheduled task topology

```
beat: dispatch-waf-collection   every COLLECTION_INTERVAL_SECONDS
  └─ dispatch_waf_collection    no-op while WAF_COLLECTION_ENABLED=false
       └─ collect_waf_source(profile_id)   one Celery task per eligible profile
            └─ run_waf_source   own session; commit on success
                 └─ lock waf_pipeline:<qradar id>
                      1. collect   watermark waf_endpoint_metric:<qradar id>
                      2. evaluate  watermark waf_endpoint_evaluation:<qradar id>
                      3. train     metadata on the evaluation watermark
beat: apply-waf-retention       every WAF_RETENTION_INTERVAL_SECONDS (daily)
  └─ apply_waf_retention
```

- **Independent sources.** Each source is its own task, session, lock, watermarks and failure
  record, so a source whose Ariel searches hang for their full timeout delays no other source.
- **Idempotent.** Collection resumes from its watermark and upserts on
  `(waf_endpoint_id, bucket_start)` and `(log_source_id, bucket_start)`. Evaluation resumes from
  its own watermark, and each detector state's `last_interval_start` makes a replayed bucket a
  no-op.
- **Duplicate delivery.** Two runs for one source: the second fails to take
  `waf_pipeline:<qradar id>` and returns `locked`. A forced re-collection of an already-collected
  bucket rewrites the same rows (tested).
- **Failure is not traffic.** A failed run rolls back everything it wrote — no bucket, no metric,
  no zero — leaves both watermarks where they were, and records `consecutive_failures`,
  `last_error` and `last_failure_at` on the collection watermark in its own commit. A provider
  error is recorded with its sanitized text; any other exception by class name only, because a
  database error can echo request values.
- **Observable lag.** Collection lag is the distance from the end of the last collected bucket to
  now; evaluation lag and pending buckets are reported separately (§28.10).

### 28.7 Collect → evaluate → train ordering

Inside one run, in this order:

1. **Collect** completed buckets; the collection watermark advances only past buckets written.
2. **Evaluate** buckets with a `WafSourceBucket` newer than the evaluation watermark and no newer
   than the collection watermark, oldest first, at most `WAF_MAX_EVALUATION_BUCKETS_PER_RUN`
   (default 24). Every endpoint row in a bucket is judged against the baseline **as trained before
   this run's step 3**.
3. **Train** at most `WAF_BASELINE_ENDPOINTS_PER_RUN` (default 50) active endpoints, least recently
   rebuilt first (`waf_endpoint.baseline_rebuilt_at`, NULL first, so an endpoint whose rebuild
   writes no cell cannot starve the others), over buckets up to the **end of the last evaluated
   bucket** only.

So a bucket is never trained on before it is judged, and training can never run ahead of
evaluation even when collection does (tested: two buckets collected, one evaluated, training bound
to the one). Once judged, an anomalous bucket is excluded from training by its incident span.
`PARTIAL` buckets are judged — as `INSUFFICIENT_DATA`, which advances nothing — and never train;
a failed collection leaves no bucket to judge or train on (tested).

### 28.8 Metric-semantics identity

`app/waf/semantics.py` defines the identity of `request_count` as
`request_count/v<definition>;agg=<aggregation>;normalizer/v<N>;profile/v<P>`, where `aggregation`
comes from the provider (`SUM(eventcount)` for REST). Every `WafEndpointMetric`, `WafSourceBucket`
and `WafEndpointBaseline` records the identity it was produced under.

- The baseline builder trains only on rows whose identity equals the one being rebuilt for; others
  are counted as `incompatible_semantics`.
- The engine refuses a cell whose identity differs from the bucket's: the verdict is
  `INSUFFICIENT_DATA` with the reason "rebuild required".
- **NULL matches nothing.** Rows collected before identities existed can neither train nor be
  judged. Nothing guesses what an old row counted.

A change of event aggregation, normalizer version or profile mapping therefore fails closed until
the next rebuild of each endpoint (tested: a remapped profile's spike is not judged against the old
cell, and the rebuild excludes all six old rows).

**Not applied to Phase A.** Enforcing an identity on `log_source_metric` would turn every existing
row into an incompatible one and every Phase A source into `INSUFFICIENT_DATA` until relearned.
That is a Phase A behavior change, so Phase A keeps the recorded-but-unenforced provenance of the
preflight and the documented operator procedure (§27.17).

### 28.9 Retention policy

Run daily by `apply_waf_retention` while `WAF_RETENTION_ENABLED` (default **true** — unlike the
generic Timescale policies, because WAF collection is itself opt-in and its growth is influenced by
whoever sends requests). Each table has its own rule:

| Data | Deleted when | Default |
|---|---|---|
| `waf_endpoint_metric`, `waf_source_bucket` (incl. their provenance) | bucket started before `WAF_METRIC_RETENTION_DAYS` | 35 days |
| `waf_endpoint_baseline` | cell not recomputed within `WAF_METRIC_RETENTION_DAYS` — every sample behind it is already gone | 35 days |
| `waf_anomaly` + transitions | state `RESOLVED`, or a closed candidate (`NORMAL`, `INSUFFICIENT_DATA`), ended before `WAF_ANOMALY_RETENTION_DAYS`, and referenced by no detector state | 180 days |
| `waf_endpoint` + its detector state and remaining cells | retired before `WAF_METRIC_RETENTION_DAYS`, with no incident and no metric row | 35 days |
| `waf_source_profile` | never; removed only with its log source | — |

- **Never deleted:** an incident in `CANDIDATE`, `OPEN`, `RECOVERING` or `SUPPRESSED`, whatever its
  age, nor any endpoint that still has an incident (tested with a 400-day-old `OPEN` incident on a
  retired endpoint).
- **The baseline window is protected.** With WAF collection and retention enabled, startup refuses
  `WAF_METRIC_RETENTION_DAYS <= BASELINE_LOOKBACK_DAYS`; and the retention task itself deletes
  nothing while that holds, so retention can never remove a sample a rebuild relies on. A
  deployment without WAF collection is never refused startup over the WAF setting (tested).
- **Bounded.** Deletes run as `DELETE … WHERE key IN (SELECT … LIMIT n)` in batches of
  `WAF_RETENTION_BATCH_SIZE` (5,000), at most `WAF_RETENTION_MAX_BATCHES` (20) per table per run,
  each committed separately. A table not cleared within the limit is reported `incomplete` and
  continues next run (tested).
- Evidence packages for WAF incidents do not exist yet; when built they inherit the incident rule.

### 28.10 Observability

`GET /api/v1/waf/sources` and `GET /api/v1/waf/sources/{profile_id}` (`admin:waf`), and
`python -m app.cli.waf status [--json]`, report per source: profile enabled state, version,
validation status, validated version and time, per-field validation status; collection eligibility
with the reason when not eligible; collection watermark, last run, last success, lag, consecutive
failures, last error and last failure time; the last bucket's completeness, coverage, observed,
tracked, admitted and untracked endpoint counts, untracked requests, admission truncation,
maintenance flag, profile version and metric identity; admission-truncated buckets in the last 24
h; active endpoints against the ceiling; whether baseline training and detection are active,
last training time and bound, endpoints never rebuilt; evaluation watermark and pending buckets.

No field carries a URL, user agent, client address or other request value (tested).

### 28.11 Client aggregates — kept possible, not built

§27.7 is revised: there must be no **unbounded** per-client entity. Detectors planned for B.2 need
to answer which client address contributes most WAF traffic, what share it holds, whether one
fanned out across an abnormal number of endpoints, and whether one became dominant without total
volume spiking. A structure conceptually like

```text
waf_client_bucket (qradar_instance, waf_log_source, bucket_start, client_address)
  request_count, endpoint_count, share_of_source, …
```

is compatible with what B.1 built, **provided it is admitted the same way endpoints are**: only
bounded, material clients — the top-N by volume, clients above a minimum share, or clients named by
an open incident — with the refused remainder counted on `WafSourceBucket`, never stored by
address. `WafSourceBucket` is the per-source, per-bucket anchor such a table joins to; the
admission rules of §28.5 are pure and reusable for it; and retention (§28.9) would take it with the
bucket it belongs to. Nothing in B.1 has been built in a way that prevents it. It is not built
here.

### 28.12 Measured cost

> **Superseded by §29.7–§29.8**, which re-measures these scenarios before and after Phase B.1.5.

Measured 2026-09-13 against the `timescale/timescaledb:2.17.2-pg16` test database on a developer
workstation, through `run_waf_source` with a stub provider — so the figures below are **database
cost only**, excluding Ariel round-trips. Plain tables (not hypertables) were used; treat them as
orders of magnitude, not capacity guarantees.

| Scenario | Wall time | SQL statements | Outcome |
|---|---:|---:|---|
| Attack bucket: 5,000 distinct normalized endpoints, one source | 0.19 s | 205 | 20 entities admitted, 4,980 untracked, 20 metric rows, 1 bucket record |
| Steady bucket at the default ceiling: 2,000 active endpoints, 200 observed, 1,800 zero-filled; evaluation of 2,000 endpoints; 50 baseline rebuilds | 2.52 s | 6,273 | 2,000 metric rows |
| One endpoint's full rebuild over the 28-day lookback (8,064 buckets) | 0.22 s | 341 | 168 cells |

What follows from them:

- **An attack is cheap to absorb.** Refused endpoints cost one bounded lookup and a count; nothing
  is written per refused path.
- **Steady-state cost is linear in active endpoints**, about three statements per endpoint
  evaluation, dominated by per-endpoint baseline and detector-state reads. At the default ceiling
  that is ~6,300 statements and ~2.5 s per source per five-minute bucket, ~1.8 M statements per
  source per day. At the lab's 10 endpoints it is negligible.
- **Storage.** Measured at ~430 bytes per `waf_endpoint_metric` row including indexes (a small table
  overstates this). At the ceiling: 576,000 rows/day, roughly 250 MB/day and ~8.7 GB retained over
  35 days per source; at the lab's 10 endpoints, 2,880 rows/day.
- **Ariel cost is unchanged by B.1:** two bounded searches per source per bucket (576 per source per
  day), plus at most 15 per validation. Admission, maintenance, identity and retention add no
  Ariel query.
- **Baseline training** at 50 endpoints per run costs ~50 × 0.22 s of database time in the worst
  case (full lookback), and refreshes a 2,000-endpoint source's baselines about every 3.3 hours.

**Not measured:** live Ariel latency under B.1 scheduling, hypertable chunk behavior at retention
scale, and cardinality of any real WAF product. The first two belong to the §27.16 live-lab plan;
the third to production onboarding.

### 28.13 Migration impact

- **`0008`** is additive: `waf_source_profile`, `waf_source_bucket` (hypertable when Timescale is
  present), nullable `metric_semantics` on `waf_endpoint_metric` and `waf_endpoint_baseline`, and
  `waf_endpoint.baseline_rebuilt_at`. Downgrade drops them, losing profiles and bucket records.
  Upgrade → downgrade to `0007` → upgrade → `alembic check` was run on a freshly reset schema.
- **Existing WAF data** — the lab rows of §26.7 — has no identity, so it neither trains nor is
  judged; a source restarts at `INSUFFICIENT_DATA` and relearns.
- **Existing deployments must create and validate a profile** before any WAF source is collected.
  Without one nothing is collected, which is the safe default.
- **New endpoint rows carry slim provenance** (`{"collector", "source_bucket", "zero_filled"}`); the
  full query provenance is on the bucket record, written once instead of once per endpoint.
- **Startup validation:** a deployment that enables WAF collection with `BASELINE_LOOKBACK_DAYS` of
  35 or more must raise `WAF_METRIC_RETENTION_DAYS` too, or the backend refuses to start. The check
  was first written unconditionally, which the full suite caught: it would have refused to boot a
  Phase A-only deployment with a 60-day lookback. It now applies only with WAF collection on.
- **New permission** `admin:waf`, granted by `admin:*`.
- **Phase A:** no table touched. Its engine and baseline builder now call the shared
  `in_maintenance`, which is the same rule they inlined, so their behavior is unchanged; the full
  Phase A suite passes unmodified.

### 28.14 Known limitations and open blockers

> **Items 1, 2, 3 and 6 are addressed by Phase B.1.5 — see §29.1–§29.9.**

1. **Group-cap blinding.** Admission cannot stop a source's grouped query from hitting
   `WAF_MAX_GROUPS_PER_BUCKET` (500): grouping happens on raw URLs in AQL, before normalization, so
   even `/users/1` … `/users/5000` produces 5,000 groups. A truncated bucket is correctly `PARTIAL`,
   which means **anyone able to send more than ~500 distinct raw paths per bucket can suppress every
   verdict on that source** for that bucket. Mitigations for B.2 — a second bounded query scoped to
   tracked endpoints, or raising the cap with measured cost — need measurement against real traffic.
2. **Evaluation cost is linear in active endpoints** — see §28.12 — because the engine judges each
   endpoint with its own queries. Acceptable at the default ceiling at five-minute buckets; a B.2
   detector that adds per-endpoint work must batch it.
3. **No global Ariel concurrency cap for WAF.** Fan-out bounds WAF Ariel load by Celery worker
   concurrency only; the existing in-process limiter serves scheduled searches. Size worker
   concurrency, or add a shared limiter, before enabling many sources.
4. **Phase A maintenance semantics** (§28.4) — historical buckets are excluded while maintenance is
   on and readmitted when it is cleared. Needs a decision; not changed here.
5. **Phase A metric identity** is recorded but not enforced (§28.8); the mixed-history procedure
   (§27.17) still applies.
6. **The HTTP 422 assumption** of §28.3 should be re-verified against the appliance.
7. **Advisory-lock release after a database error.** A run that fails with a database error aborts
   its transaction before the lock's release statement runs. In Celery the task disposes its engine,
   closing the connection and releasing the lock; a long-lived caller reusing the connection would
   hold it. The same pattern exists in every Phase A collector.
8. **Carried from Phase A, inspected and left:** watermark last-write-wins (Phase A §11.1 — the WAF
   watermarks share the pattern and the same manual procedure applies); the intermittent
   `MissingGreenlet` (cause not established; the B.1 tests hit a *different* one — an ORM object
   expired by a rollback and touched outside the async context — which the scheduled task avoids by
   using a fresh session per run); the local-development auth asymmetry (a deliberate dev
   convenience with test dependents; an isolated fix could not be proven not to break them).
9. **Production WAF onboarding** remains NO-GO: §26.10's assumptions are unanswered for any real
   WAF product.

### 28.15 Gates

2026-09-13, after B.1: Ruff clean · Mypy 31 errors in 18 files (unchanged baseline) · **1489
backend tests passed, 0 failed** (1112 unit + 377 integration), including 66 new B.1 unit test cases
and 22 new B.1 integration tests, with no existing Phase A or Phase B test weakened · frontend lint,
typecheck, 629 tests and production build clean (frontend unchanged by B.1) · `alembic upgrade
head` → `downgrade 0007` → `upgrade head` → `alembic check` clean on a freshly reset schema.

### 28.16 Readiness for Phase B.2

**Yes, for detector expansion on validated sources in the lab**, with §28.14 items 1–3 as explicit
design inputs rather than surprises: a new detector reads the recorded bucket and identity, runs in
the evaluate step, trains in the train step, inherits maintenance, retention and failure isolation,
and must keep per-endpoint work batched. **Not yet** for production onboarding or analyst exposure.

---

## 29. Phase B.1.5 — collection resilience and scale hardening (2026-09-16)

Phase B.1 was correct but had three design limits and one missing control that Phase B.2 would
have built on: a raw-path flood above the group cap blinded a whole source (§28.14 item 1), a
scheduled run cost about three SQL statements per active endpoint (§28.14 item 2), dense
zero rows put the 35-day metric footprint of a 2,000-endpoint source near 8 GB, and nothing bounded
how many WAF sources searched Ariel at once (§28.14 item 3). This section records how each was
resolved, the exact guarantees now given, what it measurably costs, and what is still open. **No
detector, analyst UI or alert routing was added.** Where this section changes an earlier one, it
supersedes it.

### 29.1 Root cause of the group-cap blind spot

The grouped observation query groups by the **raw** `(URL, method[, virtual host])` in AQL,
orders by `SUM(eventcount)` and stops at `WAF_MAX_GROUPS_PER_BUCKET` (500). Normalization happens
afterwards, in the application. In B.1:

1. `truncated` was set whenever the cap was reached (`len(groups) >= limit`), whatever the volumes.
2. Coverage was computed as *returned* parsed events over the source total, so under truncation it
   dropped as well — a second, independent route to the same verdict.
3. Either condition set the **whole bucket** `PARTIAL`, and every row written for it — observed
   endpoints and zero-filled ones alike — carried that completeness.
4. The detector's first guard returned `INSUFFICIENT_DATA` for anything not `COMPLETE`.

So 500 distinct raw paths in five minutes — at one request each — made every endpoint of the
source unjudgeable for that bucket. The counts themselves were not wrong: every returned group's
`SUM(eventcount)` was a real count of real events. They were *discarded* because a single flag
could not say "incomplete, but only in one direction". Measured on the B.1 code: a tracked endpoint
at 9× its baseline during a 5,000-path spray opened no incident (§29.7).

### 29.2 Completeness dimensions and measurement semantics

A bucket record now states each dimension separately:

| Dimension | Where | Exact when | Otherwise |
|---|---|---|---|
| Source event volume | `waf_source_bucket.total_events` | always — it is its own ungrouped aggregate | — (a failed query writes no bucket) |
| Endpoint grouping / new-endpoint discovery | `groups_truncated`, `discovery_complete` | the grouped result did not reach the cap | discovery saw only the returned groups; `observed_endpoints`, `untracked_endpoints` and `untracked_requests` are lower bounds |
| Endpoint request counts | `request_count_measurement` | not truncated **and** coverage ≥ `WAF_MIN_ENDPOINT_COVERAGE` → `EXACT` (`COMPLETE`) | `LOWER_BOUND` (`PARTIAL`) |
| Endpoint coverage ratio | `coverage_measurement` (derived) | not truncated | a lower bound on true coverage |
| Composition | `distinct_client_count` | never — it is the maximum over folded raw groups | always a lower bound (unchanged from B.1) |

`EXACT` keeps B.1's tolerance: a bucket with coverage at or above the floor counts as whole even
though up to `1 − floor` of its events carried no endpoint. That tolerance is unchanged.

**Lower-bound guarantee.** For a bucket whose counts are `LOWER_BOUND`, for every stored endpoint
count *c* and the endpoint's true count *T*: **c ≤ T**. This rests on:

- **G1** — each returned group's `SUM(eventcount)` counts only events matching its
  `(URL, method[, host])`, the log source and the half-open window (`starttime` predicate plus
  `START/STOP`), and every `eventcount ≥ 1`;
- **G2** — an endpoint's count is the sum of the returned raw groups that normalize into it, and
  omitted groups (truncation) or unattributed events (low coverage) can only add to *T*;
- **[ASSUMPTION A1]** Ariel does not double-count an event inside one aggregate. G1 does *not*
  assume the top-N is exact: if the appliance merged per-processor top-N lists, a returned group's
  sum could only be an undercount, which keeps c ≤ T.

**Which verdicts a lower bound can support.** Every guard on the way to a
`WAF_REQUEST_RATE_SPIKE` verdict — minimum volume, ratio, absolute delta, robust *z*, and the
degenerate-MAD band — is monotonically non-decreasing in the observed count. If *c* clears all of
them, *T ≥ c* clears all of them. A normal verdict is never concluded from *c*: the hidden events
may be exactly what makes the bucket abnormal. A randomized property test (4,000 cases, including
MAD = 0 and zero-median baselines) asserts both properties
(`tests/unit/test_waf_resilience.py::TestLowerBoundSpike::test_monotonicity_property`).

**Capability model, not detector-specific checks.** `DetectorSpec.measurement`
(`app/anomaly/capabilities.py`) declares how a detector may use a lower bound:

| Requirement | Exact count | Lower bound | Unknown semantics |
|---|---|---|---|
| `EXACT` | any verdict | **no verdict** | no verdict |
| `LOWER_BOUND_PROVES_INCREASE` | any verdict | anomalous verdict only; every other exit is `INSUFFICIENT_DATA` | no verdict |

`WAF_REQUEST_RATE_SPIKE` declares `LOWER_BOUND_PROVES_INCREASE`. A detector with no declared
requirement is treated as `EXACT`. The engine computes the scope from the spec and passes it to the
pure detector; the detector never grants itself lower-bound reasoning — a `PARTIAL` point without a
granted scope, or with no recorded semantics, gets no verdict (a row with no bucket record is exact
only when `COMPLETE`, never assumed a lower bound). The scope is published by
`GET /api/v1/behavior/detectors` as `measurement_requirement`.

**Never displayed as exact.** A lower-bound verdict's reason reads "at least *n* requests (a lower
bound: …)"; its evidence carries `request_count_measurement: LOWER_BOUND`; and
`waf_anomaly.observed_measurement` and `waf_anomaly_transition.observed_measurement` record whether
the stored reading is exact. An incident keeps its worst reading; if that reading is a lower bound,
the incident says so.

**Considered and not built:** upper-bound reasoning (true count ≤ visible + residual unattributed
volume) to prove *normal* behaviour under truncation. It would depend on two separately timed
searches being consistent, and under exactly the floods that matter the residual is large enough to
prove nothing.

**Behaviour change — low coverage.** The same semantics apply when coverage, not truncation, is the
cause: unattributed events can only belong to some endpoint. A low-coverage bucket therefore now
proves a sufficiently large spike, where B.1 gave no verdict. Every non-spike outcome is still
`INSUFFICIENT_DATA`, and such buckets still never train (§27.16 scenario 5 holds).

### 29.3 Adversarial flood behaviour

Driven through `run_waf_source` with the REST provider's exact truncation shape
(`tests/integration/test_waf_resilience.py::TestPathFloods`); cap 500:

| Scenario | Bucket | Tracked endpoints | Incidents | Baseline |
|---|---|---|---|---|
| 5,000 unique raw paths, tracked endpoints at normal volume | `PARTIAL`, `LOWER_BOUND`, discovery incomplete, `total_events` exact; ≤ 20 admitted | `INSUFFICIENT_DATA` — **not** `NORMAL` | none | bucket excluded (`incomplete`); next exact bucket returns them to `NORMAL` |
| 5,000 raw `/users/<n>` paths (3 requests each) folding into tracked `/users/{id}` (baseline ≈ 100) | as above | 500 visible groups → lower bound 1,500 | **OPEN**, `observed_measurement = LOWER_BOUND`, "at least 1500 requests" | excluded |
| Tracked `/portal/login` at 900 (baseline ≈ 100) plus 5,000 one-off paths | as above | login proven; sibling at normal volume `INSUFFICIENT_DATA` | **OPEN** on login only | excluded |
| An `OPEN` incident; login back to normal during a 3,000-path spray of 10 each; `/rare` (3 requests) pushed below the cap | as above | login unprovable; `/rare` has no row and its absence is **not** zero | stays `OPEN` — unprovable normality is not recovery | excluded |
| … the spray stops | `COMPLETE` | exact again | `RECOVERING → RESOLVED` | trains |

The group query stays bounded throughout (`max_groups` is the configured cap on every call).

**What an attacker can still do.** Suppress *normal* verdicts and baseline training for as long as
they sustain more than *cap* distinct raw groups per bucket — at a cost of one request per group per
bucket — and so keep existing incidents from resolving and cells from refreshing. That is fail-closed
and visible (`lower_bound_buckets_24h`). To *hide a spike* whose raw group carries *V* requests they
must place at least *cap* groups each at or above *V* — at least `500 × V` requests in the same
bucket, which is itself an exact source-volume anomaly Phase A's `VOLUME_SPIKE` can see. A spike
spread thinly over more raw variants than the cap (each below the cut) is visible only up to the
returned share.

### 29.4 Sparse storage

**Why the rows existed.** B.1 wrote an explicit zero row for every active tracked endpoint absent
from a bucket: at the 2,000-endpoint ceiling, 576,000 rows per source per day whatever the traffic,
each ~380–430 bytes with indexes.

**Now.** A row is written only for an endpoint the grouped result mentioned. Absence is read through
the bucket record (`app/waf/observations.py`, shared by the engine and the builder):

| Row | Bucket record | Endpoint tracked at the bucket | Meaning |
|---|---|---|---|
| yes | yes | — | the row, under the bucket's measurement semantics |
| yes | no (pre-0008 row) | — | the row; exact only when `COMPLETE` |
| no | sparse, `EXACT` | yes | **a measured zero** |
| no | sparse, `LOWER_BOUND` | yes | unknown — lower bound 0; judged `INSUFFICIENT_DATA`, trains as `incomplete` |
| no | sparse | no | not an observation |
| no | dense (pre-0009) | — | not tracked (0008 wrote its zeros explicitly) |
| no | none — collection failed or never ran | — | nothing; never zero |

**Why the zero is sound.** A tracked endpoint is written whenever the grouped result mentions it,
whatever its volume, so its absence from an exact result is zero. An untracked endpoint may have
been refused admission without a row (below the threshold or over quota), so its absence says
nothing — which is why the *tracking window* matters:

- `waf_endpoint.tracked_since` — start of the bucket that created or last reactivated the entity;
- `waf_endpoint.tracked_until` — set at retirement to the last bucket the run collected while the
  endpoint was still tracked, so a lagging backfill collected afterwards does not read its absence
  as zero.

**Evaluation** judges every endpoint with a row plus, in a sparse bucket, every endpoint tracked at
it — so an incident on an endpoint that goes completely silent still recovers and resolves on
measured zeros (tested). **Training** reconstructs the same series; a unit test asserts that the same
randomized traffic stored densely (B.1) and sparsely (B.1.5), with exact, lower-bound, maintenance
and failed buckets mixed, trains identical samples, exclusions and candidate counts.

**Slim rows.** Collection time, provenance, watermark and the metric-semantics identity are the same
for every row of a bucket; they now live only on the bucket record (the row fields stay nullable and
are left NULL or `{}`). A row's identity is its own when set (pre-0009 rows) and otherwise its
bucket's. **Re-collecting a bucket replaces its rows** (delete, then insert, in the run's
transaction), so a row can never outlive the bucket record it inherits from; B.1 merged instead,
which could leave a stale row for an endpoint the fresh result no longer mentioned.

**Behaviour change — reactivation.** A retired endpoint that returns starts a new tracking period.
Rows from before retirement no longer train: they lost the zeros that belonged with them, and
training on the non-zero half of a period would bias the baseline upward. Its rebuild is queued first
(`baseline_rebuilt_at = NULL`). `first_seen_at` still records the first sighting.

**Retention** is unchanged: buckets and rows age out together by bucket start, so no bucket record
survives to vouch for an absence its rows no longer back (tested).

### 29.5 SQL batching

Profiled with a statement counter on the driver (`before_cursor_execute`). B.1's collection was
already bulk (11 statements); evaluation was the amplification.

| Path | B.1 | B.1.5 |
|---|---|---|
| Evaluation | per endpoint: baseline cell lookup, detector state lookup (+ insert), anomaly lookups, flush — ~3 statements per endpoint | per bucket: 1 query for rows + tracked endpoints (outer join), 1 for cells, 1 for states, 1 for live incidents, 1 flush — **6 statements**, any N |
| Training | per endpoint: rows, spans, and a SELECT + INSERT/UPDATE **per cell** (341 statements for one endpoint's full lookback) | bucket records once; per chunk of 25 endpoints: rows, spans, one bulk `INSERT … ON CONFLICT` for all cells, one batched rebuild-time update |
| Collection | 11 statements; zero rows written | 13 statements; rows replaced per bucket; no zero rows |
| Retirement | SELECT + one UPDATE per retired endpoint | one `UPDATE … RETURNING` |

One non-obvious fix was found by the regression test rather than by review: SQLAlchemy batches
UPDATEs into one `executemany` only across *consecutive* rows that change the same columns.
Detector states that transitioned interleaved with states that only advanced a counter, and the
flush fragmented into one UPDATE per row (5 statements for 60 endpoints, 299 for 1,200). The engine
now marks the full column set on every modified state and incident, so each table is one batch.

**Regression guards** (`tests/integration/test_waf_scale.py`): a scheduled run issues the same
number of statements for 60 and 1,200 endpoints (≤ 40); evaluation is equal for 40 and 800
endpoints with one detector and with a second registered detector (≤ one extra statement, never
per endpoint); a rebuild of 100 endpoints costs exactly 3 × 4 statements more than of 25; a
2,000-endpoint bucket with 2,000 observed endpoints is ≤ 40 statements. Reverting the uniform-update
fix fails four of the five.

### 29.6 Global WAF Ariel budget

`app/services/waf_ariel_budget.py`. A counting semaphore of `WAF_ARIEL_MAX_CONCURRENT_SOURCES`
(default **2**) slots built from the primitive the collectors already use: PostgreSQL session
advisory locks. Slot *i* is `pg_try_advisory_lock(WAF_ARIEL_PERMIT_LOCK_NAMESPACE, i)` (namespace
4712, refused at startup if equal to the collector namespace), held on a **dedicated
autocommit connection** for the duration of one bucket's searches.

- **Per-source independence and the per-source lock are unchanged.** A run takes its source lock
  first; only then does collection take one permit per bucket. A locked source spends no permit.
- **Cross-worker** — every worker sees the same lock table. **Crash recovery / no leaked permits** —
  the lock belongs to its connection; if the worker dies, the server ends the backend and the slot is
  free, with no TTL to tune (tested with `pg_terminate_backend` and with a connection dropped without
  unlocking). The permit is outside the run's transaction, so a rollback or aborted transaction
  cannot strand it.
- **Bounded wait** — polls every `WAF_ARIEL_PERMIT_POLL_SECONDS` (1 s) for at most
  `WAF_ARIEL_PERMIT_WAIT_SECONDS` (30 s); never queues behind a lock.
- **On no permit:** the collection loop stops before issuing that bucket's searches. Nothing is
  written for it — no bucket, no row, no zero. The watermark stays on the last bucket actually
  written (a squeezed backfill keeps the two buckets it got; tested), buckets already collected are
  still evaluated and trained, the failure counter is untouched, and the run reports
  `capacity_unavailable`. The next dispatch resumes exactly there.
- **Tested across workers:** four sources in four concurrent sessions on four engines with a limit of
  2 — peak concurrent searches exactly 2, all four collected.

**Why not Redis.** `RedisConcurrencyLimiter` exists but no production path uses it, and its
INCR/EXPIRE design refreshes a shared TTL on every acquire, so a slot leaked by a dead worker is not
reclaimed while other workers keep acquiring. Advisory locks give exact ownership and release on
connection loss.

**Limits of the mechanism.** A worker that is alive but stuck holds its slot until its Ariel poll
bound (120 s per search, cancelled on timeout) or its task time limit ends it. A host that vanishes
without closing TCP holds its slot until the database server's TCP keepalive notices. One permit per
source covers that bucket's two sequential searches — it bounds concurrent *sources*, i.e. at most
`2 × limit` concurrent WAF searches counting both queries of a bucket as one unit of work. Profile
validation (operator CLI, ≤ 15 searches) is not budgeted.

### 29.7 Measured cost — database only

Stub provider, `timescale/timescaledb:2.17.2-pg16` test database on a developer workstation, plain
tables created from metadata. **No Ariel time is included and nothing here was measured on a QRadar
appliance.** "Before" is the B.1 working tree snapshotted before this workstream, run through the
same harness. One steady-state scheduled run (collect → evaluate → train 50 endpoints) with detector
states and reliable cells already present.

**Per bucket, 10% of tracked endpoints requested:**

| Endpoints | Before: statements | Before: seconds | Before: rows written | After: statements | After: seconds | After: rows written |
|---:|---:|---:|---:|---:|---:|---:|
| 100 | 573 | 0.23 | 100 | **36** | 0.034 | 10 |
| 500 | 1,773 | 0.74 | 500 | **36** | 0.073 | 50 |
| 1,000 | 3,273 | 1.38 | 1,000 | **36** | 0.108 | 100 |
| 2,000 | 6,273 | 2.60 | 2,000 | **36** | 0.249 | 200 |

**Per bucket, every tracked endpoint requested (worst case for sparse storage):**

| Endpoints | Before: statements | Before: seconds | After: statements | After: seconds | Rows written (both) |
|---:|---:|---:|---:|---:|---:|
| 100 | 573 | 0.25 | **36** | 0.056 | 100 |
| 500 | 1,773 | 0.74 | **36** | 0.156 | 500 |
| 1,000 | 3,273 | 1.49 | **36** | 0.208 | 1,000 |
| 2,000 | 6,273 | 2.76 | **36** | 0.410 | 2,000 |

**Baseline evaluation cost** (evaluate phase alone, 10% requested): before 303 / 1,503 / 3,003 /
6,003 statements and 0.11 / 0.55 / 1.08 / 2.14 s; after **6** statements at every size and 0.011 /
0.041 / 0.075 / 0.197 s (the remaining time is pure-Python detector and lifecycle work).

**Training** — 50 endpoints, each with a row in every bucket of a full 28-day lookback (403,200
rows, 8,400 cells): before **16,951 statements, 11.5 s**; after **9 statements, 4.7 s**.

**Group-cap flood bucket** (tracked endpoint at 9× baseline, 5,000 one-off paths, cap 500): before
95 statements, bucket `PARTIAL`, spike `INSUFFICIENT_DATA`, **0 incidents**; after 35 statements,
bucket `PARTIAL`/`LOWER_BOUND`, **incident OPEN** with a lower-bound reading.

**Storage per row** (`waf_endpoint_metric`, 200,000 rows, table + indexes): before 376 bytes (PK plus
the duplicate `(endpoint, bucket_start)` index); after **164 bytes** (slim row, PK only). With a
`bucket_start` time index as a Timescale hypertable adds, a separate side-by-side measurement gave
387 → **173 bytes**; the projections below use those. A fully populated baseline cell (48
observations) is 1,219 bytes; a bucket record ~600 bytes.

### 29.8 Capacity estimates and the endpoint ceiling

Per WAF source, 5-minute buckets, 35-day metric retention. *Activity* is the share of tracked
endpoints requested in a given bucket; B.1 wrote a row for every tracked endpoint regardless.

| Tracked endpoints | Metric rows/day (B.1) | Metric rows/day (B.1.5, 100% / 10% active) | 35-day metrics (B.1) | 35-day metrics (B.1.5, 100% / 10%) | Baseline cells | **Rough total incl. PostgreSQL overhead ×1.3** (B.1.5, 100% / 10%) |
|---:|---:|---:|---:|---:|---:|---:|
| 100 | 28,800 | 28,800 / 2,880 | 390 MB | 174 MB / 17 MB | 20 MB | 0.26 GB / 0.05 GB |
| 500 | 144,000 | 144,000 / 14,400 | 1.95 GB | 0.87 GB / 87 MB | 102 MB | 1.3 GB / 0.25 GB |
| 1,000 | 288,000 | 288,000 / 28,800 | 3.90 GB | 1.74 GB / 174 MB | 205 MB | 2.5 GB / 0.5 GB |
| 2,000 | 576,000 | 576,000 / 57,600 | 7.80 GB | 3.49 GB / 349 MB | 410 MB | 5.1 GB / 1.0 GB |

The ×1.3 factor is a **rough allowance**, not a measurement, for chunk metadata, page fill, dead
tuples from detector-state updates between vacuums, and statistics; WAL and backups are excluded.
§28.12's 8.7 GB used 430 bytes per row from a smaller table; the larger measurement here gives 387.

Database time per bucket stays under half a second at 2,000 endpoints; training at full history and
100% activity is the largest cost (~4.7 s per run per source, rebuilding each endpoint about every
3.3 hours at 50 per run).

**Ariel side [PROJECTED, not measured].** Two searches per source per bucket, unchanged. With
`WAF_ARIEL_MAX_CONCURRENT_SOURCES = L` and *S* seconds per bucket's searches, the budget sustains
about `L × 300 / S` sources per 5-minute interval before lag grows: with L = 2, ≈ 300 sources at
S = 2 s (the order of §4.4's lab searches), ≈ 20 at S = 30 s. Live Ariel latency under load is not
known; size L from observed `last_collection_duration_ms` and lag.

**Default ceiling: kept at 2,000.** The database no longer limits it (36 statements and ≤ 0.41 s per
bucket; worst-case ~5 GB per source per 35 days). The binding limit is different and is now
surfaced rather than hidden: **the grouped query cannot return more than `WAF_MAX_GROUPS_PER_BUCKET`
(500) raw groups**, so a bucket in which more than ~500 distinct raw `(URL, method)` combinations are
requested yields only lower bounds — it can prove spikes but never trains or proves normality.
Tracking more endpoints than the cap is useful only while fewer than the cap are active in any one
bucket. `GET /api/v1/waf/sources` reports `active_endpoints_exceed_group_cap`, and
`lower_bound_buckets_24h` shows whether it is biting. A source whose normal traffic truncates needs
a larger cap (measured against its Ariel cost) or a narrower profile, not a larger ceiling.

### 29.9 QRadar rejection semantics (the HTTP 422 assumption)

Still **[ASSUMPTION]** — not verified in this workstream. What changed is that correctness no longer
leans on it and there is a procedure to verify it:

- **Fail-closed regardless.** `PROPERTY_REJECTED` (HTTP 422) makes a required field `INVALID`; any
  other failure is `QUERY_FAILED` → `INCONCLUSIVE`; a search that QRadar accepts and completes with
  no values is `UNPOPULATED` → `INVALID`. None of them enables collection, so a different real
  behaviour costs diagnostic precision, never safety.
- **Operator procedure:**
  `python -m app.cli.waf probe-rejection --instance <name> --log-source <id> [--json]` issues two
  bounded, read-only searches over the last 15 minutes of one log source — one naming a randomly
  generated property that cannot exist (`QRDASH_NONEXISTENT_<hex>`), one naming the profile's URL
  property as a control — and records for each the create's HTTP status, whether a search was
  created, and how it ended. **No result row is fetched.** It prints a conclusion
  (`CONFIRMED_422`, `REJECTED_WITH_OTHER_STATUS`, `REJECTED_ASYNCHRONOUSLY`, `ACCEPTED_SILENTLY`,
  `INCONCLUSIVE`, or `INCONCLUSIVE_CONTROL_FAILED`) and writes the observation to the audit log as
  `waf.qradar.rejection_semantics_observed`. Run it once per appliance version and record the result
  here.

### 29.10 Query budget observability

Admin-only (`admin:waf`), counts and timestamps only — no query text in status:

- `GET /api/v1/waf/ariel-budget` — `enforced`, `max_concurrent_sources`, `active_permits` (held now,
  by any worker, from `pg_locks`), `permit_wait_seconds`.
- Per source, `GET /api/v1/waf/sources[/{id}]` adds: `ariel_searches_last_run`,
  `last_collection_duration_ms`, `last_run_intervals_collected` / `_due`, `last_permit_wait_ms`,
  `last_skip_reason` (`source_locked` | `global_capacity`), `last_skipped_at`,
  `previous_run_skipped`, `lower_bound_buckets_24h`, `active_endpoints_exceed_group_cap`, and on the
  last bucket `request_count_measurement`, `groups_truncated`, `discovery_complete`,
  `coverage_measurement`, `sparse_endpoint_rows`. Collection lag was already reported.
- `python -m app.cli.waf status` prints the permit line and per-source searches, duration and skip.

A run skipped because another worker holds the source is recorded on a separate
`waf_scheduling:<qradar id>` row with one atomic upsert, so the skipped worker neither waits on nor
overwrites the watermark row the running worker is updating.

### 29.11 Detector batch preparation for Phase B.2

The engine (`app/waf/engine.py`) is batch-first: for one bucket it loads every endpoint the bucket
observed (rows plus reconstructed zeros and lower bounds), every registered detector's baseline
cells, every detector state and every live incident in a fixed number of queries, runs the pure
detectors and lifecycle in memory, and flushes once. A Phase B.2 detector is added as

1. a pure function in `WAF_DETECTOR_FUNCTIONS`;
2. a `DetectorSpec` in `WAF_DETECTORS` with its baseline metrics and a **declared measurement
   requirement** — ratio detectors (`WAF_BLOCK_RATIO_SPIKE`, `WAF_ERROR_RATIO_SPIKE`) must declare
   `EXACT`: a ratio of two lower bounds bounds nothing;
3. if it needs per-bucket inputs beyond request counts, an extension of the bulk loaders and
   `app/waf/observations.py` — never a query inside the detector loop.

Novelty detectors need a further rule the model already exposes: "new endpoint" may be claimed from
a visible group, but "not seen before" or "disappeared" requires `discovery_complete` across the
comparison window. `tests/integration/test_waf_scale.py` registers a second detector and proves the
statement count does not grow with endpoints × detectors.

*Superseded for ratio numerators by §30.4.*

### 29.12 Migration 0009

Additive for every existing row's meaning; no Phase A table touched.

- `waf_source_bucket.request_count_measurement` (backfilled from completeness: `PARTIAL` buckets were
  PARTIAL only through truncation or low coverage, so `LOWER_BOUND`) and `sparse_endpoint_rows`
  (false for existing buckets).
- `waf_endpoint.tracked_since` (= `first_seen_at`) and `tracked_until` (= `last_seen_at` for already
  retired endpoints). Existing buckets are dense and never consult them; the values only need to be
  right for buckets collected afterwards, which they are.
- `observed_measurement` on `waf_anomaly` and `waf_anomaly_transition` (existing readings `EXACT`).
- Drops `ix_waf_endpoint_metric_entity_time`, which duplicated the primary key.
- **Downgrade restores 0008 semantics, not just its shape:** it copies each bucket's identity onto
  slim rows and re-materializes the zero rows sparse buckets implied (with the bucket's
  completeness, for endpoints tracked at the bucket). Its choice of normalizer version uses the
  highest stored, an approximation of the code constant 0008 zero-filled for.
- Verified: `upgrade head` → `alembic check` → `downgrade 0008` → `upgrade head` → `alembic check`
  on a fresh database, and a data-carrying round trip test
  (`test_0009_backfills_measurement_and_its_downgrade_restores_dense_zeros`).

Existing dense history stays as it is and ages out with retention; nothing is rewritten on upgrade.

### 29.13 Behaviour changes, stated explicitly

1. A `PARTIAL` WAF bucket (truncated or low coverage) can now open or sustain a
   `WAF_REQUEST_RATE_SPIKE` incident from a lower bound (§29.2). Every other outcome on such a
   bucket is unchanged: `INSUFFICIENT_DATA`, no recovery, no training.
2. Explicit zero rows are no longer written; the zero is reconstructed (§29.4). The B.1 test
   `test_a_previously_seen_endpoint_gets_an_explicit_zero` asserted the row itself; it now asserts
   the reconstructed exact zero from the bucket record and tracking window
   (`…_gets_a_measured_zero`).
3. A reactivated endpoint's baseline relearns from its reactivation (§29.4).
4. Re-collecting a bucket replaces its endpoint rows rather than merging (§29.4).
5. Collection stops early without a global permit and reports `capacity_unavailable` (§29.6).
6. The B.1 test `test_a_mapping_change_fails_closed_until_rebuilt` spied on the per-endpoint rebuild
   method the pipeline no longer calls; it now spies on `rebuild_endpoints`. Its assertions are
   unchanged.

No other existing test was modified, and no Phase A behaviour changed: the only shared-code edits
are an optional `status_code` on `ProviderError`, a new optional provider method, and one additive
field on the detector capability API.

### 29.14 Gates

2026-09-16, after B.1.5: Ruff clean · Mypy 31 errors in 18 files — the identical pre-existing set
(it caught one real defect during this workstream: an insertion had moved `@abstractmethod` from
`list_rules` onto the new provider method) · **1,559 backend tests passed, 0 failed** (1,153 unit +
406 integration; B.1 recorded 1,489), including 38 new unit tests and 27 new integration tests
(13 flood and sparse-storage, 5 statement-count, 8 Ariel-budget, 1 migration round trip), with two
B.1 tests adapted as §29.13 states · frontend lint, typecheck, 629 tests and production build clean
(frontend unchanged) · `alembic upgrade head` → `alembic check` → `downgrade 0008` → `upgrade head`
→ `alembic check` clean on a freshly created database.

New tests were checked against deliberate regressions: declaring the spike detector `EXACT` fails
the two flood-spike tests; removing the tracking-window check fails two unit tests and the retirement
integration test; removing the uniform-update fix fails four of the five statement-count tests.

### 29.15 Remaining risks

1. **[ASSUMPTION A1]** — no double counting inside one Ariel aggregate — underpins every lower
   bound; not verified on an appliance.
2. **Sustained floods suppress normal verdicts.** For as long as a source's buckets truncate, no
   incident on it can resolve and no cell refreshes (fail-closed, visible, §29.3). Spikes hidden
   below the cut by an attacker willing to spend `cap × V` requests are not detected per endpoint.
3. **Group cap versus active endpoints** (§29.8): a source that normally exceeds ~500 raw groups per
   bucket never trains. Surfaced, not solved; raising the cap has unmeasured Ariel cost.
4. **Ariel performance under the budget is unmeasured.** The default of 2 concurrent sources is
   conservative by intent, not by measurement.
5. **A stuck-but-alive worker or a half-open connection holds a permit** until its search bound,
   task time limit or TCP keepalive ends it (§29.6).
6. **The HTTP 422 assumption** remains unverified until the §29.9 procedure is run.
7. **Training CPU at full history** (~4.7 s per run per source, all endpoints active) scales with the
   number of sources; not measured beyond one source.
8. **Detector-state row churn** — every tracked endpoint's state is updated every bucket (as in
   B.1); HOT updates keep this cheap, but autovacuum behaviour at many sources was not measured.
9. **Carried from §28.14, unchanged:** the Phase A maintenance-history defect (item 4, a separate
   corrective workstream), Phase A metric identity not enforced (item 5), advisory-lock release after
   a database error for the per-source lock (item 7 — the Ariel permit is not affected), Phase A
   carried items (item 8), and production WAF onboarding NO-GO (item 9).

### 29.16 Readiness for Phase B.2

> ## Phase B.2 detector development: **GO** — on validated lab sources
>
> The three limits that would have made every new detector inherit a blind spot, a per-endpoint query
> cost or an unbounded storage and Ariel footprint are resolved and pinned by tests: floods degrade to
> honest lower bounds instead of silence, a run costs a constant 36 statements, storage follows
> traffic, and WAF Ariel concurrency is globally bounded and observable. Conditions: every new
> detector declares a measurement requirement (§29.11; ratios `EXACT`), novelty claims require
> discovery completeness, and per-bucket inputs are added through the bulk loaders.
>
> *Superseded for ratio numerators by §30.4.*

> ## Production WAF onboarding: **NO-GO** — unchanged
>
> §26.10's assumptions, A1, the 422 assumption and live Ariel cost under the budget are all
> unmeasured on a real WAF product and appliance.

What Phase B.2 built under these conditions, and its readiness, is recorded in §30.

## 30. Phase B.2 — behavioral detectors (2026-10-12)

Phase B.2 adds five detectors on top of the B.1.5 base, plus the per-endpoint inputs they read
(action, response-code and source-IP breakdowns), pooled ratio baselines, a verified-history
novelty claim and, as a separate opt-in, alerting from WAF incidents. This section records what the
code at this commit does, the exact claim each detector makes, what it costs, and what is still
open. It is written from the code, not from the plan: **[BUILT]** means present and tested at this
commit. Where it supersedes §27–§29 it says so. **No analyst API or UI for WAF incidents was
added**, and production WAF onboarding is still unvalidated.

Every detector follows the §29 rules it inherits. A missing, failed or truncated input is
`INSUFFICIENT_DATA` and never a zero or a normal verdict. A detector never grants itself
lower-bound reasoning: its verdict scope is computed by the engine from the per-input requirements
declared in `app/anomaly/capabilities.py`. No detector asserts intent: a high block ratio is not a
successful attack, a 5xx rise is not malicious traffic, a dominant source IP is a composition, and
a new endpoint may be a deployment.

### 30.1 Components **[BUILT]**

| Concern | Where |
|---|---|
| Breakdown folds (pure): action, response code, source IP | `app/waf/breakdown.py` |
| Breakdown searches, per-breakdown completeness, row and bucket columns | `WafEndpointCollector` in `app/collectors/waf_endpoint_collector.py` |
| Which breakdowns a source may collect | `BreakdownPlan`, `breakdown_plan()` in `app/waf/profile.py` |
| Breakdown search (AQL, read-only, bounded) | `get_waf_breakdown` in `app/providers/base.py`, `qradar_rest.py`, `mock.py` |
| Per-input measurement and scope inputs | `app/waf/inputs.py`; requirements in `app/anomaly/capabilities.py` |
| Ratio, concentration and novelty detectors (pure) | `app/waf/behavior.py` |
| Novelty history verification (pure) | `app/waf/novelty.py` |
| Novelty facts, bulk loaders, lifecycle, alert work | `app/waf/engine.py` |
| Pooled ratio baselines | `app/waf/baseline.py` (`RATIO_METRICS`, `clean_ratio_samples`) |
| Scheduled wiring (plan to collector and to training) | `app/services/waf_pipeline.py` |
| WAF alert fingerprint | `waf_anomaly_fingerprint` in `app/alerts/fingerprint.py` |
| Schema | `alembic/versions/0010_waf_behavior_breakdowns.py` |

The detector functions are registered in `WAF_DETECTOR_FUNCTIONS` (`app/waf/engine.py`) next to
`WAF_REQUEST_RATE_SPIKE`, and each has a `DetectorSpec` in `WAF_DETECTORS`. The engine reads them
through the bulk loaders of §29.11; no detector issues a query.

### 30.2 Breakdowns **[BUILT]**

A breakdown groups one source's requests by the raw endpoint key plus one more field, over events
that carry a value for it (`<field> IS NOT NULL`), ordered by `SUM(eventcount)` and capped at
`WAF_MAX_BREAKDOWN_GROUPS_PER_BUCKET` (default 2,000; the REST provider also bounds it by what it
can page). Numerator and denominator of a ratio come from the **same** search, so they describe the
same population of events. Normalization is many-to-one and happens in the application: raw groups
are summed into normalized endpoints exactly as `fold_groups` does for volume.

| Breakdown | Field | Per endpoint it yields | Stored |
|---|---|---|---|
| `ACTION` | the profile's action property | events carrying an action; of those the ones the map names `BLOCKED`; the ones it names in neither list (`unmapped`) | `action_events`, `blocked_count`, `unmapped_action_count` |
| `RESPONSE_CODE` | the profile's response-code property | events carrying a value; 4xx; 5xx; values that are not a three-digit status (`unclassified`) | `response_code_events`, `client_error_count`, `server_error_count`, `unclassified_response_count` |
| `SOURCE_IP` | QRadar's normalized `sourceip` (never a configured property, never quoted) | attributed events; the largest source IP's requests; the five largest's; distinct source IPs; the bounded contributor list | `client_events`, `top1_client_requests`, `top5_client_requests`, `client_sources`, `top_clients` |

**One bounded search each, under the bucket's single permit.** Each planned breakdown is one more
read-only Ariel search per source per bucket, so a bucket costs two volume searches plus at most
three. They run inside the same global WAF Ariel permit as the volume searches (§29.6; the fetch
happens inside `permit()`), so the concurrency bound is unchanged and a bucket that gets no permit
still writes nothing. AQL runs only through `QRadarRestProvider`.

**Each breakdown has its own completeness.** The bucket's `action_measurement`,
`response_code_measurement` and `client_measurement` each hold a `WafTelemetryStatus`:

| Status | Meaning | Per-endpoint values |
|---|---|---|
| `EXACT` | the grouped result was whole | exact; an endpoint the search did not mention reads zero of that breakdown |
| `LOWER_BOUND` | the result reached its cap | the visible part only; an unmentioned endpoint reads a visible zero |
| `NOT_COLLECTED` | the breakdown was not in the source's plan | NULL |
| `FAILED` | the search raised `ProviderError` or `ValueError` | NULL |

A failed breakdown is recorded with the exception class name only (a message can echo query text),
and it **does not fail the bucket**: the volume searches succeeded and are still judged, so an
unreliable optional field cannot stop request-rate detection. A whole volume search says nothing
about whether the action search was whole, which is why the completeness is per breakdown. Each
breakdown also records its own metric-semantics identity (`*_semantics`, built by
`breakdown_semantics` in `app/waf/semantics.py`) and the sum of its visible groups (`*_events`).

**The settings gate Ariel spend.** `WAF_OUTCOME_COLLECTION_ENABLED` (default false) gates the
`ACTION` and `RESPONSE_CODE` searches; `WAF_CLIENT_COLLECTION_ENABLED` (default false) gates the
`SOURCE_IP` search. Turning one on is an explicit decision to spend Ariel budget, and the engine
runs the detectors that read a breakdown only while its setting is on. A detector that reads only
data already collected needs no such setting (§30.5, decision D-4). `breakdown_plan()` fails closed,
and every condition must hold for the profile's current version:

- `ACTION` needs the setting, an action property mapped and validated `AVAILABLE` for the current
  profile version, an authored map naming at least one `BLOCKED` value, and **every action value
  validation discovered must be mapped**.
- `RESPONSE_CODE` needs the setting and a response-code property validated `AVAILABLE`.
- `SOURCE_IP` needs only its setting, since every event carries a normalized `sourceip`.

A breakdown left out of the plan has its reason in `BreakdownPlan.skipped`, and the buckets record
it `NOT_COLLECTED`.

**The action map, and why an unmapped value is never allowed.** A profile authors
`action_value_map` as `{"BLOCKED": [...], "ALLOWED": [...]}` from values validation discovered.
Values are compared trimmed and case-folded, a value in both lists is refused, and changing the map
bumps the profile version exactly as a property change does, because it changes what a blocked
count means. A value in neither list is `unmapped`, and the fold counts it in `events` but in
neither `blocked` nor an allowed total: nothing is ever treated as allowed. While any unmapped value
exists the blocked count is only a **lower bound** (§30.4), which can prove a block-ratio rise but
never a normal verdict. A 403 is counted as a 4xx response and never as a WAF block; WAF blocking is
read only through the action map.

**T-013 fixes.** Two folds could be steered by hostile values, and both were closed:

- Response-code status digits are **ASCII only** (`normalize_response_code`). `str.isdigit()` also
  accepts `²`, which `int()` rejects with `ValueError`, and `int()` accepts other scripts' decimal
  digits; either let one odd value fail a whole bucket or count as a status. Such a value is now
  `unclassified`, and the function never raises. Pinned by
  `test_a_response_code_that_int_rejects_is_unclassified_not_an_error`.
- A source-IP value that is not a parseable address is **left unattributed**
  (`normalize_source_ip`). It counts in `unattributed` and in neither `events` nor any ranking, so a
  flood of garbage values can never pose as the largest client; it lowers the breakdown's coverage
  instead, and the concentration detector's coverage floor then yields `INSUFFICIENT_DATA`. Pinned
  by `test_garbage_values_never_reach_a_ranking_even_as_the_largest_group`.

**T-014: the scheduled path now passes the plan.** `WafSourcePipeline.run` once built the
collector without a `BreakdownPlan`, so in production every bucket recorded every breakdown
`NOT_COLLECTED` and no B.2 detector could ever reach a verdict; only tests that drove the collector
directly collected breakdowns. The pipeline now computes `breakdown_plan(profile, settings)` **once
per run**, passes it to the collector, and derives the training identities from the same plan
object, so collection and training cannot disagree within a run. The pipeline report carries
`planned_breakdowns` and `skipped_breakdowns` with their reasons. Pinned by
`test_scheduled_run_collects_the_plan_and_reports_it`,
`test_scheduled_run_with_breakdown_settings_off_issues_no_search`,
`test_missing_action_map_skips_only_action_and_reports_the_reason` and the end-to-end
`test_scheduled_cycles_train_and_open_a_block_ratio_incident`.

**What is persisted.** Counts, a semantics identity and a completeness per breakdown, and per
endpoint per bucket at most the five largest source IPs with their request counts, stored only when
the largest source IP reached `WAF_CONCENTRATION_MIN_CLIENT_REQUESTS` (default 20). Action values,
response codes and the other source IPs live only inside the fold functions. The source IP is
QRadar's normalized field: behind a proxy, load balancer or NAT it is the intermediary, and the
evidence says so (`client_identity`).

### 30.3 Ratio baselines **[BUILT]**

`RATIO_METRICS` defines four proportion metrics. Each seasonal (weekday, hour) cell keeps, besides
the usual median, MAD, p05 and p95 of the bucket proportions, its **pooled** `numerator_sum` and
`denominator_sum`.

| Metric | Numerator | Denominator | Minimum denominator setting |
|---|---|---|---|
| `block_ratio` | `blocked` | `action_events` | `WAF_RATIO_MIN_DENOMINATOR` |
| `server_error_ratio` | `server_errors` | `response_code_events` | `WAF_RATIO_MIN_DENOMINATOR` |
| `client_error_ratio` | `client_errors` | `response_code_events` | `WAF_RATIO_MIN_DENOMINATOR` |
| `top1_client_share` | `top1_client_requests` | `client_events` | `WAF_CONCENTRATION_MIN_ENDPOINT_REQUESTS` |

The expected proportion is `numerator_sum / denominator_sum`, not the median of bucket
proportions: a bucket with two requests weighs two requests, so a quiet hour cannot dominate a cell.
The median and MAD still feed the seasonal robust score (§30.4). A ratio metric trains only for a
breakdown that has a semantics identity in the plan, and only on **exact, material** buckets.

`clean_ratio_samples` attributes each excluded bucket to the first rule that applies, and the order
is part of the contract operators read: `unfinished`, `incompatible_semantics`, `maintenance`,
`unavailable`, `incomplete`, `uncertain_values`, `inexact_request_count`, `low_source_coverage`,
`immaterial` (empty population), `low_coverage`, `immaterial` (below the minimum denominator),
`known_anomaly`. Concretely:

| Exclusion | A bucket is excluded when |
|---|---|
| `unfinished` | it ends after the rebuild's end |
| `incompatible_semantics` | its breakdown identity differs from the plan's |
| `maintenance` | it was in maintenance when collected (the recorded decision) |
| `unavailable` | the breakdown was `NOT_COLLECTED` or `FAILED`, or a count is NULL |
| `incomplete` | the breakdown is a `LOWER_BOUND` |
| `uncertain_values` | any unmapped action or unclassified response exists |
| `inexact_request_count` | the endpoint's request count is a lower bound |
| `low_source_coverage` | fewer than `WAF_MIN_ENDPOINT_COVERAGE` of the **source's** events parsed to an endpoint (decision D-3, §30.6) |
| `immaterial` | the population is empty (a sparse exact breakdown reconstructs 0/0 for an absent endpoint, which is no ratio population, not a zero proportion), or below the minimum denominator |
| `low_coverage` | the breakdown covers fewer than `WAF_MIN_ENDPOINT_COVERAGE` of the endpoint's visible requests |
| `known_anomaly` | it lies inside an incident span |

`low_source_coverage` and `low_coverage` are different quantities and are tallied separately.
The cell's `completeness` is the retained fraction of candidates. Training cost scales with chunks
of `WAF_BASELINE_REBUILD_CHUNK_SIZE` endpoints, never with endpoints or metrics (§30.9).

### 30.4 Detectors **[BUILT]**

| Detector | Status | Reads | Enabling setting |
|---|---|---|---|
| `WAF_BLOCK_RATIO_SPIKE` | built | blocked share of requests carrying an action | `WAF_OUTCOME_COLLECTION_ENABLED` |
| `WAF_ERROR_RATIO_SPIKE` | built | 5xx share of requests carrying a response code | `WAF_OUTCOME_COLLECTION_ENABLED` |
| `WAF_CLIENT_ERROR_RATIO_SPIKE` | built | 4xx share of requests carrying a response code | `WAF_OUTCOME_COLLECTION_ENABLED` |
| `WAF_CLIENT_CONCENTRATION_SPIKE` | built | largest source IP's share of attributed requests | `WAF_CLIENT_COLLECTION_ENABLED` |
| `WAF_NEW_ENDPOINT_ACCESS` | built | material traffic to an endpoint absent from a verified history (§30.5) | none (D-4) |
| `WAF_CLIENT_ENDPOINT_FANOUT_SPIKE` | **named only** | not built; `UNSUPPORTED` | — |

All are scheduled by `dispatch_waf_collection` and evaluated in the same engine pass as
`WAF_REQUEST_RATE_SPIKE`. The engine skips a detector whose enabling setting is off entirely; it
does not write a verdict for it.

**Why `WAF_CLIENT_ENDPOINT_FANOUT_SPIKE` is not built.** Distinct normalized endpoints per source
IP need a search grouped by raw URL, method, host and source IP, because normalization cannot run
in AQL. Under its group cap that search drops the lowest-volume groups first, which is one request
per path, exactly the enumeration signature, so the lower bound it leaves is structurally blind to
the behaviour. A per-client baseline would be an unbounded per-client entity, and no source-scoped
incident subject exists. It needs a QRadar-side normalized-endpoint property with verified
`UNIQUECOUNT` exactness and a source-scoped detector subject. Its spec publishes this as its
`limitation`, with `implemented=False`.

**Declared input requirements** (`app/anomaly/capabilities.py`). A verdict is only as strong as the
narrowest input (`narrowest`).

| Detector | Input (role) | Required | Why |
|---|---|---|---|
| block ratio | `BLOCKED_REQUESTS` (numerator) | `LOWER_BOUND_PROVES_INCREASE` | an unmapped action may have been a block, so blocked is a lower bound while any exist |
| | `ACTION_EVENTS` (denominator) | `EXACT` | a lower-bound denominator can make a proportion too large or too small |
| | `REQUEST_COUNT` (population coverage) | `EXACT` | coverage is checked against the exact request count |
| 5xx ratio | `SERVER_ERRORS` (numerator) | `LOWER_BOUND_PROVES_INCREASE` | an unclassified value may have been a 5xx |
| | `RESPONSE_CODE_EVENTS` (denominator), `REQUEST_COUNT` (coverage) | `EXACT` | as above |
| 4xx ratio | `CLIENT_ERRORS` (numerator) | `LOWER_BOUND_PROVES_INCREASE` | an unclassified value may have been a 4xx |
| | `RESPONSE_CODE_EVENTS` (denominator), `REQUEST_COUNT` (coverage) | `EXACT` | as above |
| concentration | `TOP_CLIENT_REQUESTS` (largest contributor), `CLIENT_EVENTS` (total), `REQUEST_COUNT` (coverage) | `EXACT` | in a truncated breakdown the largest source IP may be hidden entirely, and hidden raw groups can fold into a visible one |
| new endpoint | `ENDPOINT_HISTORY` (history) | `EXACT` | "not seen before" is an absence claim |
| | `REQUEST_COUNT` (materiality) | `LOWER_BOUND_PROVES_INCREASE` | minimum requests and buckets are non-decreasing in the counts |

`input_measurement` (`app/waf/inputs.py`) turns each input into `EXACT`, `LOWER_BOUND` or unknown
for one observation. A ratio numerator is a lower bound when its search was, **or** when any
unmapped or unclassified value exists in the denominator's events; a denominator is exact only when
its search was whole. The resulting scope per bucket:

| Situation | Block, 5xx, 4xx | Concentration | New endpoint |
|---|---|---|---|
| everything exact | `FULL` | `FULL` | `FULL` |
| breakdown whole, some unmapped or unclassified values | `INCREASE_ONLY` | not applicable | not applicable |
| breakdown truncated | `NONE` | `NONE` | not applicable |
| request count a lower bound | `NONE` | `NONE` | `INCREASE_ONLY` |
| history incomplete | not applicable | not applicable | `NONE` |

Under `INCREASE_ONLY` every exit that would have been `HEALTHY` becomes `INSUFFICIENT_DATA`,
because every anomalous guard is non-decreasing in the numerator: a lower bound that clears them
proves the true proportion does. **This supersedes the wording of §29.11 and §29.16 that ratio
detectors "must declare `EXACT`".** The numerator may prove an increase; the denominator, the
coverage input and the whole of the concentration detector still need exact inputs. The registry,
not the detector, decides: `behavior.py` takes the scope from `ctx.effective_scope`.

**Materiality thresholds** (settings, defaults; the engine builds `WafBehaviorThresholds` from
them). Ratios must clear all of the following at once.

| Guard | Setting | Default |
|---|---|---|
| minimum denominator | `WAF_RATIO_MIN_DENOMINATOR` | 50 |
| minimum numerator | `WAF_RATIO_MIN_NUMERATOR` | 10 |
| minimum rise, in proportion units | `WAF_RATIO_MIN_INCREASE` | 0.10 |
| minimum multiple of the expected proportion (when it is nonzero) | `WAF_RATIO_MIN_MULTIPLE` | 2.0 |
| seasonal robust score | `ANOMALY_DEVIATION_THRESHOLD` | 3.5 |
| MAD floor ratio | `BASELINE_MAD_FLOOR_RATIO` | 0.1 |
| coverage floor (source level and population level) | `WAF_MIN_ENDPOINT_COVERAGE` | 0.90 |
| concentration: endpoint requests | `WAF_CONCENTRATION_MIN_ENDPOINT_REQUESTS` | 50 |
| concentration: largest source IP's requests | `WAF_CONCENTRATION_MIN_CLIENT_REQUESTS` | 20 |
| concentration: minimum rise in share | `WAF_CONCENTRATION_MIN_INCREASE` | 0.20 |
| novelty: requests since first appearance | `WAF_NEW_ENDPOINT_MIN_REQUESTS` | 20 |
| novelty: buckets with traffic | `WAF_NEW_ENDPOINT_MIN_BUCKETS` | 2 |
| novelty: window after first appearance, in buckets | `WAF_NEW_ENDPOINT_WINDOW_BUCKETS` | 12 |
| novelty: verified history, in days | `WAF_NEW_ENDPOINT_HISTORY_DAYS` | 7 |

When the cell's MAD is zero the robust score is unavailable and the observed proportion must exceed
the cell's p95 instead. Concentration uses the same score against its own cell. Severity is `HIGH`
when the rise is at least twice its minimum (and, for ratios, the multiple at least twice its
minimum), otherwise `MEDIUM`; novelty is always `MEDIUM`.

**`INSUFFICIENT_DATA` exits, in order.**

- *Ratios:* the breakdown was `NOT_COLLECTED` or `FAILED` (the reason names the breakdown and says
  the value is "unavailable, not zero"); the scope is `NONE`; fewer than the coverage floor of the
  **source's** events parsed to an endpoint (D-3); the breakdown covers fewer than the floor of the
  endpoint's requests; the baseline cell is missing, incompatible, below its minimum sample count or
  has no pooled denominator; fewer than the minimum denominator. Below the minimum **numerator**,
  or short of the rise, multiple or robust score, the verdict is `HEALTHY` under `FULL` scope and
  `INSUFFICIENT_DATA` under `INCREASE_ONLY`.
- *Concentration:* the breakdown was not collected or failed; the scope is not `FULL`; the source
  or population coverage is under the floor (unattributed garbage values land here); the baseline
  exits above; fewer than the minimum endpoint requests. Below the largest source IP's minimum,
  the minimum rise or the robust score it is `HEALTHY`. It **never** runs under `INCREASE_ONLY`.
- *Novelty:* see §30.5.

Evidence carries the observed and expected proportions with their numerators and denominators, the
measurement of each, the baseline version and identity, the exit reasoning, a neutral caveat, and
the bounded contributor list. Contributors describe **all** of the endpoint's requests in the
bucket, because the breakdowns are separate searches: a blocked-request or 5xx verdict cannot
attribute its numerator to a source IP, and the evidence says so. Shares are given only when the
source-IP breakdown is exact; a truncated list is marked `TRUNCATED`.

### 30.5 Novelty — `WAF_NEW_ENDPOINT_ACCESS` **[BUILT]**

**The claim, exactly.** In every bucket of the history window before the endpoint's first
appearance, the endpoint was not requested at least `WAF_MIN_ADMISSION_REQUESTS` (default 3) times
in any one bucket, each bucket being recorded, with whole discovery and whole admission, under the
same profile version and normalizer. Admission is how an endpoint becomes an entity, so an endpoint
requested at or above the threshold in such a bucket would already exist. "Never requested" is
**not** claimed: traffic below the threshold is never recorded. The evidence states the claim in
those words (`expected.claim`).

**History completeness** (`verify_endpoint_history`, pure). The history is
`[first_seen_at - WAF_NEW_ENDPOINT_HISTORY_DAYS, first_seen_at)`. The guards run in this order,
which is the order reported to operators: the window is divisible by the bucket width; every
expected bucket was collected; there is no extra or misaligned record; every bucket has the same
width; none had truncated endpoint discovery; none had truncated admission; every bucket carries
the first-appearance profile version; every bucket's metric-semantics identity names the same
normalizer version (read as the component `normalizer/v<N>`, so `v1` is never taken for `v10`).
Separately the engine requires the first-appearance bucket itself to exist, to carry a profile
version and to match the endpoint's normalizer. A reactivated endpoint keeps its original
`first_seen_at`, so it is established, not new.

**Exits, in order** (`detect_new_endpoint_access`):

1. first appearance not established: `INSUFFICIENT_DATA`;
2. the bucket is at or past `first_seen_at + WAF_NEW_ENDPOINT_WINDOW_BUCKETS` buckets: `HEALTHY`,
   "established endpoint" (no history is loaded for it);
3. scope `NONE` (the history is incomplete, or its measurement is unknown): `INSUFFICIENT_DATA`
   naming the missing bucket or the failed condition;
4. **appearance flood** — endpoint discovery or admission was truncated in the first-appearance
   bucket: `INSUFFICIENT_DATA`. A flood of raw paths can make an old endpoint look newly admitted,
   or hide when it first appeared, so its first appearance proves nothing;
5. fewer than `WAF_NEW_ENDPOINT_MIN_REQUESTS` requests, or `WAF_NEW_ENDPOINT_MIN_BUCKETS` buckets
   with traffic, since first appearance: `HEALTHY` with exact counts, `INSUFFICIENT_DATA` with lower
   bounds; otherwise `ANOMALOUS`.

**Lower-bound traffic.** The counts since first appearance are lower bounds when the scope is
`INCREASE_ONLY`, when any source bucket from first appearance through the judged bucket counted
only a lower bound, or when one of those buckets was never collected (an uncollected bucket's
traffic is unknown). A lower bound that clears the materiality floors still opens ("at least N
requests"); one that does not never concludes normal.

**Cost: +2 statements, and only when some endpoint is inside its window.** Novelty facts are built
in bulk: one statement for the source's bucket records over the needed history, and one for the
post-appearance traffic and recorded buckets of every in-window endpoint (the latter is issued once
per chunk of up to 5,000 endpoint ids). An evaluation with no in-window endpoint does no novelty
I/O at all. Pinned by `test_novelty_cost_is_constant_with_and_without_in_window_endpoints`
(§30.9).

**Decision D-4 [DECISION], 2026-10-12: `WAF_NEW_ENDPOINT_ACCESS` has no enabling setting.** The
B.2 enabling settings exist because they gate Ariel spend, and this detector adds no search: it
reads data already collected. It is `OPERATIONAL` whenever WAF collection runs. The decision is
pinned by `test_the_detector_has_no_enabling_setting_and_is_registered`. It is revisited if
operators need a per-detector mute now that alerting exists (§30.7, §30.13). A startup validator
refuses `WAF_NEW_ENDPOINT_HISTORY_DAYS` at or above `WAF_METRIC_RETENTION_DAYS` when WAF collection
is on, because such a history could never be complete.

### 30.6 Decision D-3 — source-level coverage on the ratio paths **[DECISION]**

**Decision (2026-10-12): the ratio and concentration paths require source-level coverage, in both
training and detection** (T-017; fail closed).

The ratio guards already required that the breakdown cover the endpoint's visible requests
(`low_coverage`, and the matching detector exit). That says nothing about the events of the source
that never parsed to an endpoint at all. Those unparseable requests may differ in kind from the
parseable ones: if 50% of a source's events carry no URL, the visible 5% block ratio may describe
a different population from the real one. The visible proportion then **bounds nothing**, however
complete the breakdown is over what is visible. The volume detector already treats low source
coverage as a lower bound; the ratio paths now treat it as no verdict.

- *Detection:* `detect_ratio` and `detect_client_concentration_spike` return `INSUFFICIENT_DATA`
  when `endpoint_coverage_ratio < WAF_MIN_ENDPOINT_COVERAGE`, with the reason "only N% of this
  source's events parsed to an endpoint, below the 90% coverage floor: the unparsed requests may
  differ in kind from the parsed ones, so the visible proportion bounds nothing". A bucket exactly
  at the floor is still judged. The check precedes the population-coverage check.
- *Training:* `clean_ratio_samples` tallies `low_source_coverage` and does not train the bucket. It
  comes after `inexact_request_count` and before the empty-population and `low_coverage` checks.
- *Relaxation:* only with live evidence that unparsed requests do not differ in kind.

Pinned by `TestSourceCoverage` in `tests/unit/test_waf_behavior.py`, by
`test_low_source_coverage_is_excluded_and_not_called_breakdown_coverage` and its two ordering tests
in `tests/unit/test_waf_ratio_baseline.py`, and by
`test_ratio_rise_in_a_low_source_coverage_bucket_opens_no_incident` (engine level).

### 30.7 WAF alerting **[BUILT]**

Alerting from WAF incidents is a separate, default-off switch, `WAF_ALERTING_ENABLED`, so a source
can collect and baseline silently in **shadow mode** before its incidents can page anyone. With the
switch off the lifecycle is unchanged and no alert or notification is written.

**Fingerprint identity** (`waf_anomaly_fingerprint`, `app/alerts/fingerprint.py`). The identity of
one detector condition on one endpoint is the endpoint's whole natural key plus the detector type:
instance, log source, virtual host, normalized endpoint, HTTP method, normalizer version and
detector type, hashed with `compute_fingerprint` (`source_type="waf_endpoint"`). It never uses the
endpoint's row id, which would change with the entity; a new normalizer version is a new identity.
Each detector owns its own fingerprint, so detectors open and resolve independently. The
`condition` family is **`waf_anomaly`**, so the fingerprint begins `waf_anomaly:` and a Phase A
routing rule limited to `alert_types={"anomaly"}` does not match it; a route must name
`waf_anomaly`.
`AnomalyType` is untouched (§2 rule 6).

**Which transitions alert.** Only lifecycle transitions that change the operator's picture:

| Lifecycle event | Alert | Notification |
|---|---|---|
| `CANDIDATE`, `RECOVERING`, `INSUFFICIENT_DATA`, `NORMAL` | none | none |
| to `OPEN` | opens the alert for the fingerprint (or updates an active one) | enqueued when it is newly opened |
| still `OPEN` and the verdict is anomalous | refreshes evidence and occurrence count | none |
| to `RESOLVED` | resolves the alert (`condition recovered`) | enqueued |

A recurrence after `RESOLVED` opens a new alert. Alert severity is the evidence's severity. The
alert carries the title `<DETECTOR> on <source>: <METHOD> <endpoint>`, the evidence, the incident
id, `source_type="waf_endpoint"` with the endpoint id as `source_id`, and the routing context
(log source, owner, criticality, instance, virtual host, endpoint, method, detector type).

**Maintenance.** The bucket's recorded maintenance decision applies as for the incidents
themselves (§28.4): an anomalous verdict in maintenance is demoted to unknown, so it cannot open an
incident, and **no alert is opened and none is refreshed** while the bucket is in maintenance. A
resolution is not suppressed: a recovered incident still closes its alert. Pinned by
`test_maintenance_neither_opens_nor_refreshes`.

**Cost.** The synchronous fold only collects alert work; one async phase applies it, with one
active-alert lookup (a PostgreSQL array query, so bind count does not grow with endpoints) and one
batched update for refreshes. New alerts keep the savepoint protection of the partial unique index.
Measured (T-016, not asserted to the exact figure): with no transitions, 4 statements per
evaluation at both 40 and 800 endpoints with alerting off and with it on; every endpoint holding an
open incident and alert, 8 statements on refresh at both sizes; one transition with one matching
route costs **+7** statements for OPEN (14 against 7 in shadow mode) and **+5** for RESOLVED (12
against 7). Pinned by `<=` bounds (§30.9). The Celery WAF task builds an enqueue-only
`NotificationDispatcher` with the default routing policy and passes it through the pipeline.

**Known limit: no coalescing.** There is no source-level notification coalescing or burst cap. A
broadly correlated event can open one alert per affected endpoint and detector, and each alert
creates one notification per matching route (§30.13 item 3).

### 30.8 Capability truth (T-009) **[BUILT]**

The registry once reported all five B.2 detectors `OPERATIONAL` and `WAF_BASELINE_METRICS` claimed
four ratio baselines, while the engine ran none of the detectors and the builder trained none of
the baselines. That is the failure `app/anomaly/capabilities.py` exists to prevent, so it is now
guarded in both directions.

- `GET /api/v1/behavior/detectors` is derived from the same registry the engine enforces plus this
  deployment's settings, not from stored rows. `detector_status(spec, settings)` reports
  **`NOT_ENABLED`** for a WAF detector whose enabling setting is off, with a limitation text that
  names the setting (`Disabled in this deployment: set WAF_OUTCOME_COLLECTION_ENABLED=true.`) and
  the activation prerequisites. With default settings `WAF_REQUEST_RATE_SPIKE` and
  `WAF_NEW_ENDPOINT_ACCESS` are `OPERATIONAL`, the four opt-in detectors are `NOT_ENABLED`,
  `WAF_CLIENT_ENDPOINT_FANOUT_SPIKE` and `CARDINALITY_DROP` are `UNSUPPORTED`, and
  `TIMESTAMP_DELAY` is `SYNTHETIC_ONLY`. Without settings the status is what the code supports.
- Guard tests make the registry and the engine agree:
  `test_engine_and_registry_agree_on_what_is_executable` (a detector the registry calls
  `OPERATIONAL` has an engine function, and the reverse),
  `test_every_waf_baseline_metric_is_trained` (the registry claims only metrics the builder writes),
  and `test_every_operational_waf_detector_needs_only_trained_baselines`. `TestDeploymentStatus`
  pins the defaults and the both-settings-on case, and
  `test_every_non_operational_detector_states_its_limitation` requires a stated limitation for
  everything that is not operational. The API tests `TestDetectorCapabilities` pin the published
  statuses.

### 30.9 Cost **[BUILT]**

All figures are SQL statements on the test database; **no Ariel time is included and nothing was
measured on a QRadar appliance**. The assertions below are quoted from
`tests/integration/test_waf_scale.py`; measured values that are not asserted exactly are labelled.

| Test | What it pins (quoted assertions) |
|---|---|
| `test_a_scheduled_run_does_not_grow_with_endpoints` | `assert per_run[60] == per_run[1200]`; `assert max(per_run[1200]) <= 40` |
| `test_evaluation_does_not_multiply_by_endpoints_times_detectors` | `assert one[40] == one[800]`; `assert two[800] <= one[800] + 1`; `assert await count(db_session, WafDetectorState) == 3 * (40 + 800)` |
| `test_evaluation_cost_is_constant_with_every_b2_detector_enabled` | `assert first_on[800] <= off[800] + 1`; `assert steady_on[800] <= off[800] + 1`, with equality across 40 and 800 endpoints |
| `test_novelty_cost_is_constant_with_and_without_in_window_endpoints` | `assert in_window[800] - established[800] == 2` |
| `test_alerting_without_transitions_adds_at_most_one_statement` | `assert on[800] <= off[800] + 1`, with equality across sizes |
| `test_refreshing_every_open_alert_is_constant_cost` | `assert costs[40] == costs[800], costs` |
| `test_alert_transition_statement_cost_is_bounded` | `assert 0 < open_cost <= 8`; `assert 0 < resolve_cost <= 6` |
| `test_training_scales_with_chunks_not_endpoints` | `assert one_chunk == 5`; `assert four_chunks - one_chunk == 3 * 4` |
| `test_training_statement_count_is_unchanged_with_breakdowns` | `assert with_breakdowns == without_breakdowns == 5` |
| `test_collection_at_the_default_ceiling_is_bulk` | `assert len(statements) <= 40` at 2,000 endpoints |

Reading them together: an evaluation costs the same number of statements at 40 and at 800
endpoints with all B.2 detectors enabled, and enabling them adds at most one statement; training
cost does not change when the three ratio-bearing breakdowns are present; novelty adds exactly two
statements, only when some endpoint is inside its window. **Measured by T-016 and not asserted
exactly:** an evaluation with no transitions is 4 statements at 40 and 800 endpoints with alerting
off and on; refreshing every open alert is 8 at both sizes; an OPEN transition with one route costs
+7 and a RESOLVED one +5. The cost of breakdown collection is Ariel time, which was not measured
(§30.13 item 1).

### 30.10 Migration 0010 **[BUILT]**

`0010_waf_behavior_breakdowns` follows `0009` and is additive for every existing row's meaning; no
Phase A table is touched.

- `waf_source_profile.action_value_map` (JSONB, NOT NULL): backfilled `{}`, which leaves the action
  breakdown uncollectable until an operator authors a map.
- `waf_source_bucket.{action,response_code,client}_measurement` (String(16), NOT NULL, with
  check constraints `ck_waf_source_bucket_<name>_measurement_known` limiting them to the four
  `WafTelemetryStatus` values), `*_semantics` (String(160)) and `*_events` (BigInteger). Existing
  buckets read `NOT_COLLECTED` with NULL semantics and events: nothing was measured, and NULL
  semantics matches nothing, so no existing bucket can train or be judged by a breakdown metric.
- `waf_endpoint_metric`: ten nullable count columns (`action_events`, `unmapped_action_count`,
  `response_code_events`, `client_error_count`, `server_error_count`, `unclassified_response_count`,
  `client_events`, `top1_client_requests`, `top5_client_requests`, `client_sources`) and the
  `top_clients` JSONB. NULL is the correct value for every existing row.
- `waf_endpoint_baseline.numerator_sum` and `denominator_sum` (Float, nullable): NULL for the
  existing `request_count` cells, which are not ratios.
- Downgrade drops the columns; 0009 code never read them, so it reads the same history it wrote.
  Profiles lose their authored action maps.

**The transient-default convention.** The two NOT NULL backfills are done in three steps: add the
column nullable, `UPDATE` existing rows, then `ALTER ... SET NOT NULL`. No `server_default` is left
behind. This is a deliberate project convention (migration 0004 documents it): backfill
defaults are transient, the models use client-side defaults, and a fresh install and an upgraded
database must be identical, which `alembic check` verifies. T-011 first added server defaults to
0010 and broke `test_upgrade_from_phase2_preserves_data_and_adds_phase3`, which pins the
convention; the defect was in the 0009 test instead. That test
(`test_0009_backfills_measurement_and_its_downgrade_restores_dense_zeros`) inserted 0009-shaped rows
at `head`, where 0010's NOT NULL columns reject them. It is now pinned to revision `0009`
(`command.upgrade(cfg, "0009")` before its inserts), and
`test_0010_backfills_existing_rows_and_leaves_no_server_default` covers 0010 under the convention:
backfilled values, the rejected unknown measurement values, no leftover column default, a
downgrade to `0009`, an upgrade to `head`, and no drift. **Rolling-deploy risk:** a 0009-era writer
running against a 0010 schema fails on the NOT NULL measurement columns. That risk applies to every
migration that adds a NOT NULL column under this convention and is tracked as an operations item
in `docs/PLANNER.md`, not solved here.

### 30.11 Behaviour changes, stated explicitly

1. Five detectors exist where B.1.5 had one. Four are `NOT_ENABLED` by default and collect nothing
   until their setting is on; `WAF_NEW_ENDPOINT_ACCESS` runs whenever WAF collection runs (D-4).
2. A bucket now records one completeness per breakdown. A truncated or failed breakdown never fails
   the bucket; it makes only its own detectors `INSUFFICIENT_DATA`.
3. An unmapped action value or a non-three-digit response code makes the numerator a lower bound. It
   can prove a rise and never a normal verdict (`INCREASE_ONLY`), and it excludes the bucket from
   training (`uncertain_values`).
4. **Supersedes §29.11 and §29.16:** ratio detectors do not require every input `EXACT`. The
   numerator may be a lower bound; the denominator and coverage input must be exact.
5. **Source-level coverage (D-3):** a ratio or concentration verdict, and a ratio training sample,
   now require at least `WAF_MIN_ENDPOINT_COVERAGE` of the source's events to have parsed to an
   endpoint. Below it: `INSUFFICIENT_DATA`, and `low_source_coverage` in the exclusion tally.
6. Response-code digits are ASCII only, and an unparseable source IP is unattributed (T-013). Both
   change what an odd value counts as; neither can fail a bucket.
7. The scheduled path collects the planned breakdowns, and training and collection share one plan
   (T-014).
8. Evaluation gains statements only for in-window novelty (+2, §30.5) and, with alerting on, for
   transitions (§30.7).
9. `GET /behavior/detectors` reports deployment status, including `NOT_ENABLED`, and names the
   setting that enables a detector (T-009).
10. With `WAF_ALERTING_ENABLED` on, an `OPEN` WAF incident raises an alert under the `waf_anomaly`
    condition family. With it off, nothing changes.
11. Migration 0010 adds nullable columns, two backfilled NOT NULL columns and no server default.
12. Notifications for alerts loaded from the database now render (T-019). Before, a matching route
    crashed `NotificationDispatcher._render` on Phase A recovery across runs and on the operator
    acknowledge and resolve API; the dispatcher now normalizes through `Severity`.

The one existing test adapted for B.2 is the 0009 migration test, pinned to its revision (§30.10);
its assertions are unchanged. No Phase A behaviour changed: the shared-code edits are the optional
`waf_anomaly` fingerprint, a generic bulk alert-action helper in `AlertService`, and additive
detector-capability fields.

### 30.12 Gates

Measured by the orchestrator on `feat/phase-b2` immediately before the merge to `main`
(2026-10-12), on fresh databases:

| Gate | Result |
|---|---|
| `ruff check app tests` | clean |
| `pytest` (unit + integration) | **2,088 passed, 0 failed** |
| `mypy app` (advisory) | 31 errors in 18 files, the unchanged baseline |
| `alembic upgrade head` → `check` → `downgrade 0009` → `upgrade head` → `check` | clean; no drift |
| Frontend `lint` / `typecheck` / `test` / `build` | clean / clean / 629 passed / built |

### 30.13 Remaining risks

1. **Breakdown Ariel cost is unmeasured.** Each enabled source adds up to three searches per bucket
   under the existing permit. The default of 2 concurrent sources and the 2,000-group cap are
   conservative by intent, not by measurement; nothing here was run on an appliance.
2. **Field availability is unverified on a real WAF product.** The action map, the response-code
   property and the `sourceip` semantics were checked in the lab and against the mock only. Whether
   a real product exposes an action and response code per event, with values the profile can map, is
   the open question, and a site that cannot map them gets `NOT_COLLECTED`, not a detector.
3. **Alert storms.** Alert identity is one per endpoint per detector, and there is no
   source-level coalescing or burst cap. A source-wide event, such as a scanner sweep, a WAF rule
   change flipping many block ratios, or a deployment introducing many endpoints, can open many
   incidents in one bucket and one notification per alert per matching route. Admission
   (`WAF_MAX_NEW_ENDPOINTS_PER_BUCKET`, default 20, bounds how many endpoints enter per bucket),
   `ANOMALY_OPEN_AFTER_INTERVALS` hysteresis and shadow mode bound or delay this; they do not cap
   it.
   Coalescing changes the fixed fingerprint identity and is separate design work. Stage thresholds
   and routing with `WAF_ALERTING_ENABLED=false` before enabling paging.
4. **Novelty alerts a deployment as readily as a probe.** The evidence says so, but D-4 leaves no
   per-detector mute. Revisit when alerting is live.
5. **Source-IP identity.** Behind a proxy, load balancer or NAT the dominant source IP is the
   intermediary, which can raise or hide concentration. The evidence labels it.
6. **Coverage floors may starve a source.** A source whose events mostly do not parse to an
   endpoint never trains a ratio cell and never gets a ratio verdict (D-3). Visible in the
   exclusion tally; relaxed only with live evidence.
7. **Contributors are not an attribution.** A block or 5xx incident's contributor list covers all
   of the endpoint's requests, not the blocked ones.
8. **Rolling deploy against migration 0010** (§30.10).
9. **Carried from §29.15, unchanged:** assumption A1, the HTTP 422 assumption, sustained floods
   suppressing normal verdicts, the group cap versus active endpoints, stuck permits, training CPU
   at many sources, detector-state row churn.

### 30.14 Readiness

> ## Phase B.2 detectors: **GO** — for lab use, with breakdowns enabled per source
>
> The five detectors are built, exercised through the scheduled path (a block-ratio incident and an
> alert open end to end), and fail closed:
> every missing, failed, truncated or low-coverage input is `INSUFFICIENT_DATA`, every verdict
> scope comes from declared requirements that the registry and the engine are tested to agree on,
> and evaluation, training and alerting cost a bounded number of statements whatever the number of
> endpoints. Enable `WAF_OUTCOME_COLLECTION_ENABLED` and `WAF_CLIENT_COLLECTION_ENABLED` per source
> on validated lab sources, author the action map from discovered values, and run alerting in
> shadow mode first.

> ## Production WAF onboarding: **NO-GO** — unchanged
>
> The field-availability pass against a real WAF product has not been done (item 2), live Ariel cost
> under three extra searches per bucket is unmeasured (item 1), and the alert-storm risk with many
> endpoints opening at once has no cap (item 3). Assumption A1 and the 422 assumption from §29.15
> remain unverified. There is still no analyst API or UI for WAF incidents.
