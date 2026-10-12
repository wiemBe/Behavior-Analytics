# Phase C — log-source fleet: inventory, classification, new sources (goal G1)

Status: **design, 2026-10-12**. Written by the orchestrator. It rests on the live facts measured
in [`tasks/T-015.report.md`](tasks/T-015.report.md), and serves goal G1 in
[`PLANNER.md`](PLANNER.md) §0.

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

Additive only (AGENTS.md rule 7). `log_source` is the Phase 1 inventory table: it gains nullable
inventory facts. **No Phase A detector, baseline, state or anomaly table, and no Phase A enum,
changes** (rule 6). Classification and review get their own table, so their lifecycle is
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

  **The seed list is a deliverable that gets reviewed.** The implementing agent proposes it
  from the live type names and a known-DSM list, and the orchestrator reviews it.
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

**UI** comes after the T-021 quality pass, as its own task under AGENTS.md §3a.

## C.6 Task split

| Task | Agent | Content |
|---|---|---|
| T-022 | Codex | C.2 model, migration `0011`, provider/DTO enrichment (log-source facts, types with `internal`, groups), bulk inventory sync, `classify()` with seed rules, re-classification after sync, new-source detection and alert |
| T-023 | after T-022 | C.5 APIs, and routing by class |
| T-024 | after T-021 | Fleet UI |
