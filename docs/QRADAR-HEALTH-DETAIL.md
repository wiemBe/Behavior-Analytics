# QRadar health in detail (D-42)

Status: design, 2026-11-03. Decision D-42 (the user): the SIEM load page's QRadar section gains
(a) a per-host table, (b) disk and capacity, (c) the event pipeline and QRadar's own system
notifications, (d) trends over time — and it must understand **HA pairs**.

Everything here is read-only against QRadar (AGENTS.md §2.1). REST reads go through the existing
health service; Ariel reads run only on the direct REST/Ariel provider, through the Ariel permit
pool (`docs/ARIEL-PERMIT.md`), as a search family with a cadence (`docs/SEARCH-CADENCE.md`).
Missing, failed or truncated input is NOT_COLLECTED / FAILED / PARTIAL, never a zero and never a
healthy look (§2.8).

## 1. What QRadar gives (probe of the lab console, 7.6.0 FP1, API 11.0)

| Need | Source | Fields |
|---|---|---|
| Per host | `GET /config/deployment/hosts` | `id, hostname, private_ip, public_ip, appliance{id, description}, version, status, eps_allocation, eps_rate_hardware_limit, average_eps, peak_eps` (previous month), `fpm_allocation, fpm_rate_hardware_limit, average_fpm, peak_fpm, primary_server_id, secondary_server_id, license_serial_number, components, cpus, total_memory, app_memory` |
| Server status, HA members | `GET /system/servers` | `server_id, managed_host_id, hostname, private_ip, status` |
| Disk, CPU, memory, pipeline | **Ariel**, log source type `Health Metrics` (one log source per host, named `Health Metrics-<n> :: <hostname>`), QID `Health Metric` | shipped custom properties `Metric ID`, `Value` (numeric), `Element` (e.g. a mount point), `Component Type`, `Component Name` (IBM default content, created 2014; present on every QRadar since 7.2) |
| System notifications | **Ariel**, log source type `System Notification` | `QIDNAME(qid)` (e.g. `Warning Message`, `Error Message`, `License Expired`, `SAR Sentinel: threshold crossed`, `Process monitor status mismatch`), the QID's severity, `LOGSOURCENAME` (host) |

No REST endpoint gives disk, pipeline or notification values: `/health/metrics/system_metrics`
and `/health/metrics/qradar_metrics` list metric *definitions* only (`metric_id`,
`time_resolution`, `enabled`); they are read once per run to know which metrics are enabled.

Metric ids used (from the console's catalog; each is per host):

- **Disk:** `DiskUsage` (fraction 0–1, `Element` = mount), `DiskSpaceUsed` (bytes, per mount),
  `DiskSpaceTotal` (bytes, hourly).
- **System:** `SystemCPU` (%), `SystemMemoryUsed` (%), `LoadAvg5`, `SystemSwapUtil`, `IoWait`.
- **Pipeline:** `CREQueueSize` (events waiting for the rule engine),
  `QueuedEventThrottleFilterDiskSize` (events spilled to disk because the host is over its EPS
  allocation — the license throttle), `ECTCPTOEPDiskSize`, `FlowGovernerQueueDiskSize` (bytes of
  pipeline queue on disk), and the busiest of the `*ThreadInfo` stages (% busy, 0–100:
  `EventParserThreadInfo`, `CREPRocessorThreadInfo`, `ArielWriterThreadInfo`, …). A stage's
  % busy is the share of its threads in the RUNNABLE state (the `RunnableRatio` element); the
  other thread states (`WaitingRatio`, `TimedWaitRatio`, `BlockedRatio`, …) are not stored. A
  window collected before query version 2 shows no busiest stage, because its stage row was the
  maximum over all states, not a busy share.
- **Ariel:** `RunningQueries`, `HostRunningQueries`.

Mounts are reported for every filesystem, including container overlays
(`/store/docker-data/...`, `/run/user/...`, `/dev/shm`). Only an allowlist is shown and stored:
`/`, `/store`, `/storetmp`, `/transient`, `/var/log`, `/var/log/audit`, `/opt`, `/home`, `/tmp`,
`/var`, `/boot`, `/recovery` (a configurable setting, `QRADAR_HEALTH_DISK_MOUNTS`). Any other
mount is ignored, never shown.

## 2. HA

A managed host with a `secondary_server_id` is an **HA pair**; its two members are the
`/system/servers` rows with that `managed_host_id`, `primary_server_id` naming the primary.

- Each member shows its **role** (primary / secondary) and its **status** as QRadar reports it.
- A pair is **healthy** when exactly one member is `ACTIVE` and the other is in a standby state
  (`STANDBY`, or another non-failure HA state). The standby member is **expected**, never listed
  as "not ACTIVE", never a WARN. Status strings QRadar documents for HA (to be confirmed against
  IBM's documentation in HLT-1; unknown strings render as themselves and are a WARN, not a
  guess): `ACTIVE`, `STANDBY`, `OFFLINE`, `FAILED`, `SYNCHRONIZING`, `ACTIVE_UPGRADING`,
  `STANDBY_UPGRADING`, `UNKNOWN`.
- A pair is **degraded** (WARN) when the standby is not available (offline, failed,
  synchronizing) — no failover is possible — and **failed** (FAIL) when no member is `ACTIVE`.
- **Failed over** — the secondary is `ACTIVE` and the primary is not — is shown as a fact
  ("running on the secondary since …" when known), a WARN.
- EPS/FPM allocations, averages and peaks are the managed host's, counted **once per pair**, never
  summed over members. Disk and system metrics are per member (each member has its own disks and
  its own `Health Metrics` log source, keyed by hostname).
- The existing "Managed hosts ACTIVE n of N" line counts managed hosts (pairs count once) and
  says how many are HA pairs; `doctor`'s `qradar_health` line uses the same rule.

The lab console is one host without HA: HA is proven with fixtures (every state above) and is a
production verification point (MANUAL-TEST check written with HLT-4).

## 3. Collection

**(a) per host** — REST, inside the existing health service (`app/services/qradar_health.py`,
30 s cache, per console), extended: the host table, HA grouping, the bounded host list (the
current `Range: items=0-99` cap is made explicit: more than 100 managed hosts → PARTIAL with the
count read). No new search, no migration.

**(b)(c) disk, system, pipeline, notifications** — a new search family **`QRADAR_HEALTH`**
(default cadence 15 min, allowed range as other families, on by default:
`QRADAR_HEALTH_COLLECTION_ENABLED=true`), two AQL searches per console per run, each over the
last cadence window, both aggregated in Ariel (`GROUP BY`), never selecting payloads:

1. metrics: `SELECT LOGSOURCENAME(logsourceid), "Metric ID", "Element", MAX("Value"),
   AVG("Value"), COUNT(*) FROM events WHERE LOGSOURCETYPENAME(devicetype) = 'Health Metrics'
   AND "Metric ID" IN (<the fixed list above>) GROUP BY …` — bounded rows (hosts × metrics ×
   allowlisted mounts), the fold drops anything else.
2. notifications: `SELECT LOGSOURCENAME(logsourceid), QIDNAME(qid), MAX(severity), COUNT(*),
   MAX(starttime) FROM events WHERE LOGSOURCETYPENAME(devicetype) = 'System Notification' AND
   QIDNAME(qid) <> 'Information Message' GROUP BY …` — top 50 by count per run; the rest is a
   counted remainder. Never the payload (raw text can carry addresses and our own AQL errors).

Fail closed: a missing custom property (`Metric ID` / `Value` / `Element` absent — checked once
per run against `regex_properties`, as GEO-3 does for its property) → the metric part is
NOT_COLLECTED with a coded reason; an Ariel failure → FAILED; a host without a `Health Metrics`
log source → that host's metrics NOT_COLLECTED. A truncated result → PARTIAL.

**(d) trends** — each run stores one row per (console, host, metric, element) bucket with max /
avg / sample count, and one row per (console, host, notification QID) bucket with count / max
severity / last seen (migration `0050`, hypertables with the same retention as other aggregates).
The REST part stores one row per console per run (reachable, API latency ms, hosts active of
total, HA pairs degraded/failed, license over-allocated) so reachability and latency have a
history too. EPS trend comes from the existing log-source volume data (already per bucket), not
from QRadar's monthly averages.

## 4. Page

`/admin/siem-load`, QRadar section per console:

- **Hosts table**: hostname (+ IP), appliance, version, role (console / managed; HA: primary /
  secondary with status), status, EPS allocated / average / peak (month) with the hardware limit,
  FPM the same, disk: the fullest allowlisted mount (% and free bytes) with the others on expand,
  CPU / memory (last window max), pipeline: CRE queue, throttle spill (bytes on disk — flagged when
  > 0), busiest stage %. HA pairs are one row group with both members. Every cell carries its own
  state (OK / NOT_COLLECTED / FAILED / STALE with the window's time).
- **Notifications**: last 24 h by QID name and host, count, severity, last seen UTC; links to
  nothing in QRadar (no deep link we cannot verify).
- **Trends** (24 h / 7 d): reachability + API latency, the fullest mount per host, CRE queue and
  throttle spill per host; expected ranges as bands only where a baseline exists (none in v1).

Thresholds (settings, defaults): disk WARN ≥ 85 %, FAIL ≥ 95 %; throttle spill > 0 WARN;
CRE queue WARN above a setting (default 10,000). `doctor` gains `qradar_disk` and
`qradar_pipeline` lines from the latest stored run.

## 5. Tasks

- **HLT-1** backend: (a) + HA in the health service, API fields, `doctor` HA-aware, fixtures for
  every HA state; no migration.
- **HLT-2** backend: the `QRADAR_HEALTH` family — collector, fold, migration `0050`, API for the
  latest window and the history, `doctor` `qradar_disk` / `qradar_pipeline`.
- **HLT-3** frontend: the page (hosts table with HA groups, disk, pipeline, notifications,
  trends), after HLT-1 and HLT-2.
- **HLT-4** lab validation on qradarce2 + docs (MANUAL-TEST, OPERATIONS, CONFIGURATION, the HA
  production check).
