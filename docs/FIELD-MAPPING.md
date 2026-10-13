# Field mappings editable in the UI — WAF now, IPS with Phase D (D-8)

Status: **design, 2026-10-12.** The orchestrator owns this file. Tasks T-041 to T-045 build it.

## FM.0 What the user asked

On 2026-10-12 the user wrote: the names of custom properties such as the WAF URI change from
environment to environment, so offer a way to change them from the UI; do the same for some IPS.

Restated as requirements:
1. An administrator can see and change, **in the UI**, which QRadar property each logical WAF field
   is read from (URL, method, virtual host, action, response code, rule id, user agent), per WAF log
   source, without the CLI and without a restart.
2. The same capability exists for IPS sources when IPS behavior (Phase D) is built. Phase D is not
   built yet, so for IPS this design fixes the **contract** Phase D must follow (§FM.9).

## FM.1 What exists

- **Per-source WAF profile, built in Phase B.1** (`waf_source_profile`, design
  [§27.2–27.3](PHASE-B-WAF-BEHAVIOR.md)). Each WAF log source has its own mapping of logical field →
  QRadar property name, plus the action value map (`BLOCKED` / `ALLOWED` values). A mapping change
  bumps `profile_version` and resets validation; collection runs only on a version that validated
  `VALID` (`collection_ineligibility`). Validation is read-only and bounded
  (`WafProfileService.validate`, `app/waf/validation.py`). Every change is audited.
- **It is administered only through the CLI** (`python -m app.cli.waf set|validate|enable|disable`).
  §27.2 rule 6 said an admin API behind `admin:waf` "may follow after its own review". **This
  document is that review** (§FM.10).
- The API has three read-only routes under `/api/v1/waf` (router guarded by `admin:waf`).
- The frontend has no WAF administration screen at all.
- `WAF_URL_PROPERTY`, `WAF_METHOD_PROPERTY`, `WAF_VIRTUAL_HOST_PROPERTY` are settings that collection
  no longer reads; §27.2 rule 7 made them the defaults a new profile is seeded from.
- IPS: no collection, no profile. T-027 found the candidate fields; T-029/T-030 bring vendor-native
  Cisco FTD and Palo Alto THREAT telemetry to the lab.

So "per environment" is already modelled: every log source has its own mapping, and an environment
is a set of log sources. What is missing is the API and the UI, and a way to carry one mapping
across many sources of the same environment without retyping it (§FM.7).

## FM.2 Guarantees the UI must not weaken

1. **Fail closed.** A mapping change pauses that source's collection until the new version
   validates `VALID`. The UI says so *before* the operator saves.
2. **Nothing reaches AQL unvalidated.** A property name reaches query text only after it matches
   `PROPERTY_NAME_PATTERN` (server-side; the client check is a convenience). A name is an
   identifier, not AQL; the platform still never executes frontend-supplied AQL (AGENTS.md rule 4).
3. **Read-only against QRadar.** Saving a mapping writes only the application's database.
   Validation and the property catalog are GETs and bounded Ariel searches.
4. **Audited.** Every create, change, validation request, validation result, enable and disable is
   an `AuditLog` row with the authenticated principal as actor.
5. **No raw event values leave validation** beyond what it already discloses: counts, and the
   bounded value lists of METHOD, ACTION and RESPONSE_CODE (≤ 20 each).
6. **Optimistic concurrency.** Two administrators cannot silently overwrite each other: a change
   names the version it was made against.

## FM.3 The mapping contract (shared by WAF and IPS)

A domain (WAF, later IPS) declares in code a **schema** of logical fields. The API serves it, and the
frontend renders the editor from it, so the IPS editor is the same components with another schema.

```text
MappingSchemaOut
  domain                 "WAF" | "IPS"
  property_name_pattern  "^[A-Za-z0-9_][A-Za-z0-9_ -]{0,63}$"
  fields: [
    { key                "URL"
      label              "URL path"
      description        one sentence: what the platform does with it
      required           true            -- collection cannot run without it
      used_by_collection true            -- coverage and plausibility thresholds apply
      kinds              ["CUSTOM_PROPERTY"]          -- IPS adds "NORMALIZED"
      normalized_options []                           -- IPS: the allowlisted Ariel fields
      default            "URL" | null                 -- prefill for a new profile (§FM.7)
    }, ...]
  value_maps: [ { field "ACTION", outcomes ["BLOCKED", "ALLOWED"] } ]
```

WAF fields, labels and descriptions (the copy is part of the contract):

| key | label | required | description |
|---|---|---|---|
| `URL` | URL path | yes | The request path. Normalized into the endpoint every WAF metric is counted for. |
| `METHOD` | HTTP method | yes | Part of the endpoint key, e.g. `GET /login`. |
| `VIRTUAL_HOST` | Virtual host | no | Separates applications that share a path. Unmapped, endpoints combine their traffic. |
| `ACTION` | Action | no | What the WAF did. Feeds the block ratio, through the action value map. |
| `RESPONSE_CODE` | HTTP status | no | Feeds the 4xx and 5xx ratios. |
| `RULE_ID` | WAF rule id | no | Counted for evidence only. |
| `USER_AGENT` | User agent | no | Counted for cardinality only; values are never stored. |

## FM.4 WAF administration API

All routes sit on the existing `/api/v1/waf` router (guarded by `admin:waf`, which `read:*` does
not satisfy). Writes take the principal (`PrincipalDep`) for the audit actor. Profile writes are
keyed by **log source id** (the profile's natural key; one profile per log source).

```text
GET  /waf/mapping-schema                                  MappingSchemaOut (domain WAF)
GET  /waf/candidates?instance_id=&q=&limit=               log sources with no WAF profile
GET  /waf/log-sources/{log_source_id}/profile             WafProfileOut            404 if none
PUT  /waf/log-sources/{log_source_id}/profile             WafProfileIn → WafProfileOut
                                                          201 created, 200 changed or unchanged
POST /waf/log-sources/{log_source_id}/profile/validate    202 WafProfileOut (pending set)
POST /waf/log-sources/{log_source_id}/profile/enable      200 WafProfileOut
POST /waf/log-sources/{log_source_id}/profile/disable     200 WafProfileOut
GET  /waf/log-sources/{log_source_id}/profile/history     list[WafProfileHistoryEntryOut], newest first, ≤ 50
GET  /waf/log-sources/{log_source_id}/property-catalog    PropertyCatalogOut       (T-043, §FM.6)
```

The existing `GET /waf/sources`, `GET /waf/sources/{profile_id}` and `GET /waf/ariel-budget` stay
unchanged.

```text
WafProfileIn
  expected_profile_version  int | null    -- null only to create; otherwise the version edited
  product_label             str | null    -- ≤ 128, operator note, never parsed
  mapping                   {URL: str, METHOD: str, VIRTUAL_HOST: str|null, ACTION: str|null,
                             RESPONSE_CODE: str|null, RULE_ID: str|null, USER_AGENT: str|null}
  action_value_map          {BLOCKED: [str], ALLOWED: [str]}     -- may be empty lists

WafProfileOut
  profile_id, instance_id, log_source_id, qradar_log_source_id, log_source_name,
  log_source_type_name | null
  product_label | null
  profile_version, enabled
  mapping                   as in WafProfileIn
  action_value_map          as in WafProfileIn
  validation_status         UNVALIDATED | VALID | INVALID | INCONCLUSIVE
  validated_profile_version | null
  validated_at | null
  validation                null, or the stored report: {status, reasons[], total_events | null,
                            window_start, window_end, profile_version,
                            fields: [{field, property_name, status, used_by_collection,
                                      populated_events, populated_ratio, distinct_values,
                                      plausible_ratio, sampled_events, values_truncated,
                                      discovered_values: [{value, events}], detail}]}
                            -- the report's `queries` are not served
  validation_pending        bool
  validation_requested_at | null, validation_requested_by | null
  collection_eligible       bool
  ineligible_reason | null
  updated_by | null, updated_at

WafProfileHistoryEntryOut
  at, actor, action          -- waf.profile.created | mapping_changed | relabelled |
                                validation_requested | validated | enabled | disabled
  profile_version | null
  mapping_before | null, mapping_after | null      -- from the audit detail
  outcome, reasons | null                          -- for `validated`
```

**Contract additions (reconciled 2026-10-13 from T-044's questions; binding on both sides):**
- `MappingSchemaOut` also carries `min_coverage_ratio` (the `waf_validation_min_ratio` setting,
  e.g. 0.9), so the UI never hard-codes the threshold.
- `GET /waf/sources` items (`WafSourceStatusOut`) also carry `validation_pending`,
  `validation_requested_at` and `validation_requested_by`, so the list can show "Validating v5…".
- Each `/waf/candidates` item also carries `instance_id`, which the §FM.7 prefill needs.
- **Every 409 has a structured `detail`:** `{"message": str, "current_version": int | null,
  "updated_by": str | null, "pending_since": datetime | null}`. `message` is safe to show; the
  other keys are filled where they apply (stale version; validation already pending).
- A 422 keeps FastAPI's shape for body validation; a `WafProfileError` 422 has
  `detail = {"message": str}`.

**PUT semantics.**
- No profile and `expected_profile_version` is null → create (disabled, `UNVALIDATED`), 201.
- Profile exists and `expected_profile_version` ≠ its version (or is null) → **409**, body says the
  current version. Nothing is written.
- Invalid property name, an action value in both lists, or an action map without an action property
  → **422** with `WafProfileError`'s message (it is safe to show).
- A mapping change bumps the version and resets validation exactly as `WafProfileService.upsert`
  does today, **and requests validation** (§FM.5), because the design validates on every mapping
  change (§27.3). A label-only change does neither.
- Unchanged → 200, same version, nothing audited.
- Unknown log source → 404. The log source must belong to a registered instance.

**Enable** is refused with **409** and `set_enabled`'s message unless the *current* version is
`VALID`. **Disable** is always allowed. Both are idempotent (200, unchanged).

**Candidates** lists log sources without a profile, at most `limit` (default 50, max 200), filtered
by a case-insensitive name substring `q`. Sort: Phase C class `WAF` first, then name. Each item:
`log_source_id, qradar_id, name, type_name | null, source_class | null`.

## FM.5 Validation from the UI is asynchronous

Validation runs several bounded Ariel searches, up to 24, which can take a minute on a busy
console. An HTTP request must not wait for it.

- **Migration 0015 (additive):** `waf_source_profile.validation_requested_at timestamptz NULL` and
  `validation_requested_by varchar(255) NULL`. Upgrade → downgrade → upgrade clean; `alembic check`
  clean.
- **Request** (`POST …/validate`, or a mapping change through PUT): set both columns, audit
  `waf.profile.validation_requested`, enqueue the Celery task `validate_waf_profile(profile_id)`.
  - Already pending and younger than `waf_validation_pending_timeout_seconds` (new setting, default
    900) → **409**, "validation of version N is already running since …". Older → treated as dead;
    the request proceeds.
  - The CLI `validate` keeps running synchronously and is unchanged.
- **Worker:** loads the profile, notes the version `v` it validates, runs
  `WafProfileService.validate`. Before writing the result it re-reads the row `FOR UPDATE`:
  - version still `v` → store the result as today, clear the pending columns, commit;
  - version changed meanwhile → **discard the result**, keep the pending columns, re-enqueue once.
    A result is never stamped onto a version it did not check.
  - Any exception → clear pending, store nothing as `VALID`, audit the failure with its class; the
    profile keeps its previous validation state (which, after a mapping change, is `UNVALIDATED`).
- **One validation per profile at a time** follows from the pending marker; the worker also takes
  the profile's existing source lock if collection holds one, or skips and re-enqueues.

## FM.6 The property catalog (T-041 → T-043)

The editor should offer the names that exist on the console instead of a bare text box.

**Facts (T-041, lab 7.6, [report](tasks/T-041.report.md)):**
- `GET /config/event_sources/custom_properties/regex_properties` (119 rows) holds the names;
  `.../property_expressions` (474 rows) ties each property to a log-source type through
  `regex_property_identifier` → `identifier` and `log_source_type_id`. No single endpoint does the
  join; it is done in memory. Only enabled expressions count; every lab expression has
  `log_source_id = -1` (the whole type).
- `property_aql_expressions` holds only internal "facade" properties: ignore it. The calculated,
  JSON, LEEF, CEF, NVP, XML and generic-list collections exist but are empty in the lab.
- `fields=`, `filter=log_source_type_id=<n>` and `Range` work. Slim (`fields=`), the whole catalog
  is two requests and about 80 KB; with bodies it is 710 KB, so `fields=` is mandatory.
- **The type join catches real mistakes:** on the lab WAF source (type 212) five properties apply —
  `HTTP_Method`, `URL`, `WAF_Action`, `WAF_ResponseCode`, `WAF_USERAGENT` — while the
  plausible-looking `WAF_URL` has its only expression on another type.
- All 119 lab names match `PROPERTY_NAME_PATTERN`.
- Double-quoted **normalized** fields (`"destinationport"`, `"sourceip"`) and `QIDNAME(qid)` next to a
  quoted custom property in one `GROUP BY` are accepted (syntax only; the source had no events).

Contract:

```text
PropertyCatalogOut
  status          AVAILABLE | UNAVAILABLE          -- UNAVAILABLE: QRadar refused or failed
  fetched_at | null
  filtered_by_source_type  bool                    -- false: the whole console's list
  properties: [{name, property_type | null, applies_to_source_type: bool | null,
                name_allowed: bool}]
                  -- sorted: applies_to_source_type true first, then name
  detail | null                                    -- why UNAVAILABLE, safe to show
```

- A new read-only provider method `list_custom_properties()` (base, REST, mock) returning
  `CustomPropertyDTO {qradar_id, identifier, name, property_type, use_for_rule_engine,
  auto_discovered, log_source_type_ids, name_allowed}`. GET only, always with `fields=`. Never MCP:
  the MCP provider reports the capability as unsupported, and the catalog is then `UNAVAILABLE`.
- Cached per instance (default 10 min) as the slim joined list; filtered per source type in memory.
  A `refresh=true` query parameter bypasses the cache (the editor's "Refresh list" action).
- `applies_to_source_type` is true when the property has an enabled expression for the source's
  type, false when it has none, null when the source's type is unknown.
- A property whose name fails the pattern is served with `name_allowed: false` and shown as "not
  selectable: name not allowed", not dropped silently.
- Only names and types are served — never an expression or regex body.
- **The catalog is a suggestion, never a gate.** A name absent from the catalog may still be typed
  (the token may not see every property); validation is what decides. When the catalog is
  `UNAVAILABLE` the editor degrades to free text and says why.

## FM.7 Carrying a mapping across an environment

An environment's WAFs of one product usually share one mapping. Two aids, both prefill only:

1. **New-profile defaults.** A new profile's form is prefilled from the most recently validated
   `VALID` profile **on the same instance**, if any; otherwise from the schema defaults (the
   `WAF_*_PROPERTY` settings). The form says where the prefill came from.
2. **Copy from another source.** The editor offers "Copy mapping from…" listing the instance's other
   profiles; choosing one fills the form (mapping and action map, not the label). Nothing is saved
   until the operator saves, and the copy validates like any change.

A bulk "apply to many sources" is deferred: each source must validate on its own anyway.

## FM.8 UI

Read `AGENTS.md` §3a, `docs/PRODUCT-UX-GUIDELINES.md` and `frontend/src/app/tokens.css` first. No
new framework, no new colour or spacing literal, every state designed.

**Navigation.** The sidebar's admin group gains **WAF sources** beside **Search cadence**.

**`/admin/waf-sources` — "Which WAF sources are collected, and which need my attention?"**
- One table. Columns: Source (name, QRadar id), Product, Mapping (version), Validation, Collection,
  Last validated (UTC). Sorted: needs attention first (not eligible, `INVALID`, `INCONCLUSIVE`,
  `UNVALIDATED`, pending), then name.
- Validation reads as text with the existing tone tokens, never colour alone: "Valid (v4)",
  "Version 5 not validated", "Validating v5…", "Invalid: URL property does not exist".
- Collection: "Collecting" or "Paused — <ineligible_reason>".
- **Add WAF source** opens a picker over `/waf/candidates` (search by name; class `WAF` first).
- Empty: "No log source is configured as a WAF source. A log source becomes one when its fields are
  mapped." with the Add action. Error: what failed and that the data is not shown.

**`/admin/waf-sources/[logSourceId]` — "Does this source's mapping work, and what will collection
read?"**
1. **Header:** source name, QRadar id, DSM type, product label, version, validation state,
   collection state.
2. **Field mapping** (generic `FieldMappingForm`): one row per schema field — label, description,
   required marker, a property input with catalog suggestions (properties that apply to this
   source's DSM type first), and "Not mapped" for optional fields. Two warnings, as text beside the
   input, neither blocking:
   - a name the catalog does not list: "Not in this console's property list. Validation will
     check it.";
   - a name the catalog lists but not for this type: "This property has no parsing expression for
     this source's log-source type (DSM name)." This is how `WAF_URL` on the lab WAF would be caught. Client-side pattern check with the server's message on 422. "Copy mapping
   from…" (§FM.7).
3. **Save** shows a confirmation that lists each changed field (`URL: URL → request_uri`) and says:
   "Collection from this source pauses until version N+1 validates. Validation starts now and runs
   a few bounded searches against QRadar." 409 on a concurrent change: "This mapping changed to
   version N+1 while you were editing (by X). Your edits are kept below; reload to see theirs."
4. **Validation** (generic `ValidationReport`): the window (UTC), total events, the reasons, and per
   field: status in words, populated share (%), distinct values, plausible share (%). Statuses
   explained in the analyst's terms:
   - `PROPERTY_REJECTED` → "QRadar does not know a property with this name."
   - `UNPOPULATED` → "The property exists but no event in the window carried it."
   - `INSUFFICIENT_COVERAGE` → "Only 62 % of events carry it; collection needs 90 %."
   - `NO_EVENTS` → "The source sent no events in the window. This says nothing about the field."
   - `QUERY_FAILED` → "The check could not run. This says nothing about the field."
   Pending: "Validating version 5 since 23:52 UTC" and the page polls every 5 s until it ends.
   A version mismatch says "This report is for version 4; version 5 has not been validated."
5. **Action values** (generic `ValueMapEditor`): the discovered ACTION values with their event
   counts, each assigned **Blocked / Allowed / Not mapped**; values already in the map but not
   discovered are listed too, marked "not seen in the last validation". A truncated list says so,
   and that unlisted values count as unmapped — never as allowed. Saving the map is a mapping change
   (version bump, validation).
6. **Collection:** Enable / Disable. Enable is disabled, with the reason as text, unless the current
   version is `VALID`.
7. **History** (generic `MappingHistory`): newest first — time (UTC), actor, what changed.

Components live under `frontend/src/components/fieldmapping/` and take the schema as a prop, so the
IPS screens are the same components with the IPS schema.

## FM.9 IPS (Phase D) — the contract it inherits

**Facts (T-030, lab 7.6, 2026-10-13, [report](tasks/T-030.report.md)).** QRadar auto-discovered both
vendor-native lab sources within seconds: `PaSeries @ pa-fw-01` (id 412, type 206 "Palo Alto PA
Series", from raw PAN-OS THREAT CSV) and `CiscoFirepowerThreatDefense @ ftd-01` (id 413, type 448).

| Logical field | Cisco FTD | Palo Alto THREAT | Lab population |
|---|---|---|---|
| `SOURCE_IP` | normalized `sourceip` | normalized `sourceip` | 100 % |
| `DESTINATION_IP` | normalized `destinationip` | normalized `destinationip` | 100 % |
| `DESTINATION_PORT` | normalized `destinationport` (> 0) | normalized `destinationport` (> 0) | 100 % |
| `PROTOCOL` | normalized `protocolid` (> 0) | normalized `protocolid` (> 0) | 100 % |
| `SIGNATURE` | `QIDNAME(qid)`; one generic "Unknown Snort Event" | `QIDNAME(qid)`; several threats collapse onto "URL Filtering" | 100 % |
| `ACTION` | **absent** | **absent** (no property, QID or severity separates block from allow) | 0 % |
| `SEVERITY` | normalized `severity`, constant | normalized `severity`, varies by QID | 100 % |

Consequences:
- **The normalized defaults are right for both products**, so a new IPS profile starts valid for
  everything except the action.
- **`ACTION` is exactly the site-specific mapping this design exists for.** Neither DSM exposes it;
  an operator authors a custom property in QRadar (e.g. extracting the PAN-OS action column or the
  FTD `AccessControlRuleAction` key) and maps it in the editor. Until then the block-ratio detector
  is `INSUFFICIENT_DATA`, never "no blocks".
- `SIGNATURE` by QID is coarse on PAN (several threats per QID). A threat-id custom property, where
  a site has one, is the finer choice; the editor offers it as a `CUSTOM_PROPERTY` alternative.
- The PAN type has nine custom properties (zones, application, bytes, URL), all unpopulated on the
  raw CSV; FTD's type has none.
- The lab events are deterministic: these numbers show what parses, not how a real fleet behaves.

Phase D is not built (§0 roadmap item 4). When it is, IPS sources get the same administration, with
these differences (T-030 and T-041 confirmed the syntax and the population):

- **Field references have two kinds.** IPS fields are mostly QRadar **normalized** fields, which
  DSMs fill for every product: `sourceip`, `destinationip`, `destinationport`, `protocolid`, `qid`.
  A field reference is either `NORMALIZED` (one of a fixed allowlist, never free text) or
  `CUSTOM_PROPERTY` (pattern-checked, then validated). The schema's `kinds` and
  `normalized_options` carry this.
- **Draft logical fields:** `SOURCE_IP` (default `sourceip`), `DESTINATION_IP` (`destinationip`),
  `DESTINATION_PORT` (`destinationport`), `PROTOCOL` (`protocolid`), `SIGNATURE` (default `qid`,
  shown through `QIDNAME`; may be a custom property such as a threat id), `ACTION` (custom property
  with a value map; no normalized field carries an action, T-027 §6), `SEVERITY` (optional custom
  property).
- **Product presets** for Cisco FTD and Palo Alto THREAT prefill a new IPS profile. A preset is a
  prefill, never trusted: the profile still validates.
- **Validation semantics differ** (T-027): an absent port is `0`, not NULL, so population is a
  `> 0` share; `UNIQUECOUNT` counts the 0; internal events carry loopback IPs.
- `ips_source_profile` is its own table (Phase A and WAF tables stay as they are, AGENTS.md rule 6),
  with the same version, validation, pending and audit columns as the WAF profile.
- The UI route is `/admin/ips-sources`, built from the §FM.8 components.

## FM.10 Security review of the admin API (§27.2 rule 6)

| Risk | Control |
|---|---|
| A viewer changes what is collected | Router guard `admin:waf`; `read:*` does not grant it. Guard test covers every new route (401 / 403). |
| Injection through a property name | Server-side `PROPERTY_NAME_PATTERN` before storage and again before AQL; rejected, never escaped. Validated names only reach AQL. |
| Silent collection on an unchecked mapping | Version bump + validation reset + eligibility check (unchanged); the async result is never stamped onto a newer version (§FM.5). |
| Ariel load from repeated validation | One pending validation per profile; 409 while pending; bounded probe (≤ 24 searches). |
| Lost update between administrators | `expected_profile_version`, 409 on mismatch. |
| Disclosure through the report | `queries` not served; only counts and the bounded METHOD/ACTION/RESPONSE_CODE values, as today. |
| Disclosure through the catalog | Names and types only; no expression bodies; behind `admin:waf`. |
| Repudiation | Every write and validation audited with the principal. |

## FM.11 Tasks

| ID | Task | Owner |
|---|---|---|
| T-041 | Live research: custom-property catalog endpoints, type join, quoted normalized fields | Sonnet |
| T-042 | Backend: §FM.4 API (without the catalog), §FM.5 async validation and migration 0015, tests | Codex |
| T-043 | Backend: §FM.6 provider catalog method, cache and route (after T-041) | Sonnet |
| T-044 | Frontend: §FM.8 routes and the generic components, against a fixture API; §3a screenshots | Sonnet |
| T-045 | Docs: README admin section; WAF design §27.2 rule 6 and §27.14 point here | opencode free model |
