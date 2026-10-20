# Phase C — log-source fleet: inventory, classification, new sources (goal G1)

Status: **design, 2026-10-12**. It rests on the live facts measured on the lab console on
2026-10-12 — 51 log sources, of which only 4 delivered events in the last hour — and serves goal
G1, log-source fleet monitoring.

## C.0 What the user asked for

- See every log source.
- Notice when one departs from its normal flow. Phase A already does this: `VOLUME_SPIKE`,
  `VOLUME_DROP`, `NO_EVENTS`.
- Be alerted when **selected** sources stop.
- See which sources consume more EPS than normal.
- Notice **new** sources and **classify** them: WAF, IPS, Linux, Windows and so on.
  - The site mostly writes the class into the source name, so a regex should classify.
  - QRadar's type and group would classify better.
- Track SIM Generic Log.

C.1 builds the inventory, the classification, new-source detection and the fleet API.
C.2 builds per-class outage policy and SIM Generic tracking.

## C.1 Facts that constrain the design (T-015, lab QRadar 7.6)

- **Types** (419 rows) carry `internal` but **no category and no vendor field**. A class must come
  from a platform-owned mapping.
- **Catch-all types** exist: SIM Generic Log DSM (67), Universal DSM (39), Universal LEEF (212),
  Universal CEF (367) and IBM Custom DSM (4000). A catch-all type says nothing about what the
  source is.
- **Groups** exist (`group_ids`, and `/log_source_groups` with `parent_id`). In the lab they carry
  nothing: every source is in group 0. A production site may use them deliberately. The id `0`
  is ambiguous: the root's `parent_id` is 0, and so is the leaf's id.
- **Names follow the host, not the type.** A token regex hit 33 of 51. Eleven IPS-typed rows are
  named after Linux hosts, and seven FirePass (SSL-VPN) rows are named `*-waf-*`. So **types and
  names disagree**, and the disagreement itself is information.
- `log_source_identifiers[0].identifier` holds the sender host for user-created and
  auto-discovered sources, and `127.0.0.1` for internal ones.
- Timestamps are epoch milliseconds. `last_event_time = 0` means never. `status` is an object.
- **`average_eps` is a rounded 60-second figure, and must never be used as volume.** Volume
  comes from the platform's own Ariel `SUM(eventcount)` (`log_source_metric`).

## C.2 Data model [DECISION]

Additive only. `log_source` is the Phase 1 inventory table: it gains nullable
inventory facts. **No Phase A detector, baseline, state or anomaly table, and no Phase A enum,
changes**. Classification and review get their own table, so their lifecycle is
auditable and separate from inventory.

| Object | Change |
|---|---|
| `log_source` | + `qradar_created_at`, `qradar_modified_at`, `auto_discovered` (bool), `group_ids` (JSONB int list), `identifier` (first log-source identifier, sanitized, ≤255), `is_internal` (bool), `first_seen_at` (when this platform first saw the source; NOT NULL, backfilled from `created_at` under the transient-default convention) |
| `log_source_type` (new) | `(instance_id, type_id)` unique: `name`, `internal`, `custom`, `synced_at` |
| `log_source_group` (new) | `(instance_id, qradar_id)` unique: `name`, `description`, `parent_qradar_id`, `assignable`, `synced_at` |
| `classification_rule` (new) | `id`, `instance_id` (NULL = global), `kind`, `pattern`, `target_class`, `priority`, `enabled`, `built_in`, `description`, `created_by`, timestamps. Unique on `(instance_id, kind, pattern)`. |
| `log_source_classification` (new, 1:1, PK = `log_source_id`) | `source_class`, `basis`, `rule_id`, `override_class`, `override_by`, `override_at`, `conflicts` (JSONB), `classified_at`, `review_status` (`NEW` / `REVIEWED`), `reviewed_at`, `reviewed_by` |

`SourceClass` is a new StrEnum, stored as `String(32)`:
`WAF, IPS, FIREWALL, PROXY, VPN, LINUX, WINDOWS, NETWORK, DNS, DATABASE, EMAIL, ENDPOINT, CLOUD,
IDENTITY, APPLICATION, QRADAR_INTERNAL, OTHER, UNCLASSIFIED`.

Rule `kind` is one of:
- `GROUP`: the pattern is a QRadar group id;
- `TYPE`: the pattern is a type id;
- `TYPE_NAME_REGEX`: matched against the type name, so the rule is portable across consoles;
- `NAME_REGEX`;
- `IDENTIFIER_REGEX`.

## C.3 Classification [DECISION]

`classify()` is a **pure function** over a source's facts, the rules, the type table and the group
tree. It is deterministic, and it runs after every inventory sync and after every rule change.
That costs O(sources × rules) in memory. SQL stays O(1): load everything in bulk and write the
classifications in bulk.

**Precedence**, highest first:

1. `OVERRIDE`: the operator's per-source choice.
2. `INTERNAL`: the type is `internal`, or the identifier is `127.0.0.1`. The class is
   `QRADAR_INTERNAL`.
3. `GROUP`: a rule matching any of the source's groups or their ancestors. The tree walk treats
   the root explicitly, because of the id-0 ambiguity.
4. `TYPE` / `TYPE_NAME_REGEX`.
5. `NAME_REGEX` / `IDENTIFIER_REGEX`.
6. `UNCLASSIFIED`.

**Within one level,** the highest `priority` wins. If two rules of equal priority name different
classes, the source is `UNCLASSIFIED` at that level, the tie is recorded as a conflict, and the
walk continues to the next level.

**Why the type outranks the name.** A specific type (Snort, BIG-IP ASM) is authoritative about
the product. The name reflects the host. Catch-all types have **no** built-in type rule, so for
Universal DSM, LEEF or CEF sources the name decides. That is how the site's naming convention
keeps working where it matters.

**Conflicts.** Every candidate from a lower level whose class differs from the winner is
recorded in `conflicts` as `[{basis, rule_id, class}]`. A source with conflicts is flagged for
review. Example: "type says IPS, name says LINUX". The UI shows it, and the operator either
overrides or accepts.

**Built-in seed rules** (`built_in = true`). They can be disabled but not deleted; operators add
their own.
- `TYPE_NAME_REGEX` per class, for well-known DSM names. Examples:
  - WAF: BIG-IP ASM, Imperva, FortiWeb, Barracuda WAF, ModSecurity.
  - IPS: Snort, Suricata, McAfee Network Security Platform, TippingPoint, Firepower IPS.
  - FIREWALL: iptables, pfSense, Cisco ASA.
  - **No type rule for multi-role NGFW DSMs** (FortiGate, Palo Alto PA Series, Check Point, Cisco
    Firepower Threat Defense, Juniper SRX; T-022c). One DSM carries firewall, IPS and threat logs,
    and the user runs FTD and Palo Alto as IPS, so the site's naming convention decides.
  - PROXY: Squid, Blue Coat, Zscaler.
  - VPN: FirePass, Pulse Secure, GlobalProtect.
  - LINUX: Linux OS, plus Red Hat- and Ubuntu-named types.
  - WINDOWS: Microsoft Windows Security Event Log.
  - DNS: types with "DNS" in the name.
  - DATABASE, EMAIL, IDENTITY, CLOUD, ENDPOINT: their common DSMs.

  **The seed list is a deliverable that gets reviewed.** It is proposed from the live type names
  and a known-DSM list, and reviewed against them.
- `NAME_REGEX`, reflecting the site convention (case-insensitive, with word boundaries, so
  `dmz-ips-01` matches):
  - `waf`→WAF;
  - `ips|ids`→IPS;
  - `fw|firewall`→FIREWALL;
  - `linux`→LINUX;
  - `windows`→WINDOWS;
  - `proxy`→PROXY;
  - `vpn`→VPN;
  - `dns`→DNS.

**Regex safety.** Operator-supplied patterns are admin-only and audited (`AuditLog`). Each is at
most 256 characters, compiled at save time, and rejected if invalid. Python `re` has no timeout,
so this residual ReDoS risk is accepted on the basis of admin-only authorship, and recorded.

## C.4 New sources [DECISION]

- `first_seen_at` is set when the platform first syncs a source.
- **Bootstrap without a flood:** every source seen in an instance's **first** successful inventory
  sync is `REVIEWED`. After that, a source the platform has not seen before is `NEW`.
- A `NEW` source that is not internal raises an **inventory alert**:
  - fingerprint: condition `inventory`, subtype `NEW_LOG_SOURCE`, keyed on the instance and the
    QRadar id;
  - severity `LOW`;
  - context: class, basis, type, `auto_discovered`, identifier, plus the routing keys
    (`owner`, `instance`);
  - alerts go through `AlertService.apply_bulk` (built in T-016);
  - the switch is `inventory_new_source_alerts_enabled` (default true).
- **Marking a source reviewed** (API) resolves its alert. Classifying it does not, because
  reviewing is the deliberate act.
- A source that disappears from QRadar is out of scope for C.1 and is recorded as a gap.

## C.5 Fleet API [DECISION]

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/fleet/summary?window=1h\|24h\|7d` | Per class: source count (enabled / disabled / internal), events and mean EPS in the window from `log_source_metric`, the expected EPS from reliable baselines (with the share of sources that have one), open Phase A incidents by type, and the top 5 sources by events. Plus NEW, conflict and UNCLASSIFIED counts. Window completeness is reported, never hidden (MISSING buckets are not zeros). |
| `GET /api/v1/fleet/sources` | Filters: `class`, `review_status`, `has_conflict`, `basis`. Returns the classification fields. |
| `PATCH /api/v1/log-sources/{id}/classification` | Set or clear the override. Operator role; audited. Re-classifies that source. |
| `POST /api/v1/log-sources/{id}/review` | Mark reviewed. Resolves the new-source alert. Audited. |
| `GET/POST/PATCH/DELETE /api/v1/classification-rules` | Admin; audited. A built-in rule can only be disabled. Any change re-classifies its instance. |

**Routing.** `RoutingRule` gains a `source_classes` match. Phase A anomaly alerts and inventory
alerts carry `source_class` in their context. This is the hook C.2's per-class outage policy
builds on.

**UI** comes after the T-021 quality pass, as its own task under the frontend quality bar
([`PRODUCT-UX-GUIDELINES.md`](PRODUCT-UX-GUIDELINES.md)).

## C.6 Task split

| Task | Depends on | Content |
|---|---|---|
| T-022 | — | C.2 model, migration `0011`, provider/DTO enrichment (log-source facts, types with `internal`, groups), bulk inventory sync, `classify()` with seed rules, re-classification after sync, new-source detection and alert |
| T-023 | after T-022 | C.5 APIs, and routing by class |
| T-024 | after T-021 | Fleet UI |

---

# Part 2 (Phase C.2): outage policy and catch-all senders

Status: **design, 2026-10-13 (sixth session).** Live facts from the lab: a sender on a catch-all
source is told apart by `logsourceidentifier`, not `sourceip`, and the SIM Generic marker is
`qid = 18750001`. It covers the two G1 gaps C.1 left: "be alerted
when **selected** sources stop" and "track SIM Generic Log".

## C.7 Outage alert policy [DECISION, 2026-10-13]

**Today.** Every monitored source (`monitoring_enabled`) that goes silent opens a `NO_EVENTS`
anomaly, and the engine opens an alert for it. Maintenance windows are the only exception. Which
channel is notified is decided by routing (severity, owner, instance, class). There is no way to
say "record outages of these sources, but only alert on those". On a large fleet, every silent
test box pages.

**Policy.** A new table, `outage_alert_policy`:

| Column | Meaning |
|---|---|
| `id`, `instance_id` (nullable: global) | |
| `scope_kind` | `CLASS` or `SOURCE` |
| `scope_value` | a `SourceClass` value, or a `log_source.id` |
| `mode` | `ALERT` (open an alert, as today) or `RECORD_ONLY` (the anomaly is recorded and visible; no alert) |
| `silence_grace_buckets` | nullable; overrides `anomaly_silence_grace_buckets` for this scope (bounded 0–48) |
| `updated_by`, timestamps | |

The pair `(instance_id, scope_kind, scope_value)` is unique.

**Precedence:** source row > class row > `outage_alert_default` (a new setting: `ALERT`, which keeps
today's behaviour on upgrade). The class is the source's effective class from C.3.

**Effect, and only this effect:**
- When the policy resolves to `RECORD_ONLY`, the Phase A engine does not open, refresh or notify
  an alert for that source's `NO_EVENTS` anomaly. Detection, the anomaly row, its lifecycle and
  the UI are unchanged.
- If an alert is already open when a source switches to `RECORD_ONLY`, it resolves normally when
  the anomaly does. The policy never mass-resolves alerts.
- `silence_grace_buckets` changes when `NO_EVENTS` fires for that scope.
- `VOLUME_DROP` and `VOLUME_SPIKE` are not touched. "Outage" means `NO_EVENTS`.
- Fingerprints and Phase A tables do not change. The engine loads the policy
  once per evaluation run, in one statement.

**API:** `GET/PUT/DELETE /api/v1/outage-policy` (class rows and source rows), under a new
permission `admin:outage`. Every change is audited. `GET /fleet/summary` gains each class's
resolved mode.

**UI:** `/admin/outage-policy`. Per class, one row with the mode, the grace, and the number of
monitored sources it covers. A source-override list, with search over the fleet. Each row shows
what it changes: "12 monitored sources will no longer raise outage alerts".

## C.8 Catch-all senders (SIM Generic and the Universal DSMs)

**Facts (T-063, lab 7.6).**
- Unparseable syslog from unknown hosts lands in the existing SIM Generic source (type 67). No
  source is auto-created, and nothing is dropped.
- `sourceip` is the transport address. In production it is often a relay, with many hosts behind
  one IP.
- **The Ariel field `logsourceidentifier` carries the sender's syslog hostname**, with no payload
  read. (`"Log Source Identifier"` is rejected by Ariel.)
- The unknown-event marker is `qid = 18750001` (`category = 10008`), independent of language.
- One keyed search over the catch-all types takes about 4 s:
  `devicetype IN (67, 39, 212, 367, 4000)`, grouped by `logsourceid, logsourceidentifier,
  starttime / bucket_ms`, with `SUM(eventcount)` and `LIMIT n+1`.
- Ingest latency is 35–90 s, which the existing settle delay and `STOP` pad absorb.

**What is tracked.** Phase A already judges the catch-all sources' total volume: they are log
sources like any other. C.2b adds **who is feeding them**:
- **`catchall_sender`**: `(instance_id, catchall_log_source_id, identifier)` unique.
  - `identifier` is sanitized: printable characters only, at most 255, stored only for senders
    that pass the admission rule below.
  - `first_seen_at`, `last_seen_at`, `events_24h` (rolling, recomputed per run),
    `review_status` (`NEW` / `REVIEWED` / `DISMISSED`), `reviewed_by`, `reviewed_at`.
- **`catchall_bucket`** (a hypertable, PK `(catchall_log_source_id, bucket_start)`):
  - total events and records;
  - `distinct_senders`, a lower bound when truncated;
  - the measurement (`EXACT` / `LOWER_BOUND` / `FAILED` / `NOT_COLLECTED`);
  - the top senders, at most 20 `(identifier, events)` (a bounded contributor list);
  - `untracked_senders` and `untracked_events`;
  - `query_provenance`.
- **Admission** bounds what an attacker-controlled hostname can do. It reuses the WAF/IPS
  controls: a per-bucket new-sender quota (default 20), an active ceiling per catch-all source
  (default 2,000), and a minimum of 3 events in the bucket. Refused senders are counted, never
  stored by name.
- **A new sender** raises an inventory-class alert of subtype `NEW_CATCHALL_SENDER`, severity
  `LOW`. It is off by default (`catchall_new_sender_alerts_enabled = false`), and the first run
  only bootstraps `REVIEWED`, exactly as C.4 does. Reviewing or dismissing a sender is audited.

**Collection.**
- The `CATCHALL_SENDERS` search family defaults to 5 min with the §SC.4 gate; it defaulted to
  1 h until 2026-10-18.
- One keyed search runs per tick per instance, under the §SC.5 contract (split on truncation,
  time first; a bucket is never written from a truncated wide result; one bucket truncated gives
  `LOWER_BOUND` with the top kept).
- The type ids are a setting (`catchall_type_ids`, default `[67, 39, 212, 367, 4000]`), because
  sites have their own catch-alls. They are ints only, validated before quoting.

**Out of C.2:** unknown events on *parsed* sources. T-063 found no single QID marker (each DSM has
its own "Unknown …" QIDs), so it needs a per-DSM QID set from `qid_records`. Recorded as a gap.

**UI.** `/fleet/catchall` answers "Which unconfigured devices are sending to the catch-all sources,
and how much?" It shows the senders queue (NEW first) with first seen, last seen and 24 h events,
the per-bucket honesty states, and review and dismiss actions.

## C.9 Part 2 task split

| Task | Content |
|---|---|
| T-068 | C.7 backend: `outage_alert_policy`, migration, the engine hook (one load per run), the API, `admin:outage`, audit, the `fleet/summary` field |
| T-069 | C.8 backend: the provider keyed search and fold, tables, admission, collector, the `CATCHALL_SENDERS` family and gate, alerts, the review API |
| T-070 | UI: `/admin/outage-policy` and `/fleet/catchall`, with screenshots at three viewports |
| T-071 | Live validation: a T-063-style sender at ≤ 2 EPS shows up as a `NEW` sender; a `RECORD_ONLY` class silences its outage alert while the anomaly is still recorded |
