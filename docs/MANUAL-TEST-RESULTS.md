# QRadar-Dash manual test results page

This page is the results page for the **0.1.10** section of the guide `docs/MANUAL-TEST.md`. It can be printed or a copy can be filled in. The result cells are left empty: write `Pass`, `Fail` or `Not run`; write a Note for `Fail` and `Not run`. **Do not write** passwords, tokens, `.env` values or customer data.

| Field | Value |
|---|---|
| Site | |
| Date (UTC) | |
| Operator | |
| Version before | |
| Version after | |
| Start (UTC) | |
| End (UTC) | |
| Upgrade path (online / offline bundle) | |
| Migration head after (`0047`) | |
| Rollback done (no / yes, time) | |

**Core (production path)**, 37 checks: MT-01, MT-02, MT-03, MT-04, MT-05, MT-06, MT-07, MT-08, MT-09, MT-10, MT-11, MT-126, MT-12, MT-13, MT-14, MT-15, MT-21, MT-22, MT-24, MT-26, MT-27, MT-28, MT-29, MT-30, MT-38, MT-39, MT-44, MT-50, MT-51, MT-52, MT-55, MT-128, MT-95, MT-119, MT-120, MT-121, MT-125. In production only the rows whose `Core` column says "yes" are filled in; the others are for the lab run. MT-126, although its number is high, is done right after MT-11. MT-128 is also done after a `GEO_COLLECTION` cadence has passed.

In 0.1.10 the interface is English only (D-41). The checks of section 4 that tested the Turkish interface or the language switch were removed (MT-20, MT-58, MT-59, MT-124: the result cell says `Removed`; nothing to run) or rewritten as English checks with the same ids. A raw message key or Turkish text anywhere is a Fail of the check where it was seen. New checks: MT-129, MT-130, MT-131, MT-132.

Version before: `0.1.9`, migration head `0044`. Version after: `0.1.10`, migration head `0047`.


## 1. Preconditions

| Id | Core | Check (short) | Result (Pass / Fail / Not run) | Note |
|---|---|---|---|---|
| MT-01 | yes | Version and health before the upgrade | | |
| MT-02 | yes | `doctor` is green before the upgrade | | |
| MT-03 | yes | Verified backup — (writes) | | |
| MT-04 | yes | Where the logs and the support bundle are — (writes) | | |
| MT-05 | yes | The window and the people to inform | | |

## 2. Upgrade 0.1.9 → 0.1.10

| Id | Core | Check (short) | Result (Pass / Fail / Not run) | Note |
|---|---|---|---|---|
| MT-06 | yes | Upgrade, online path — (writes) | | |
| MT-07 | yes | Upgrade, offline bundle path — (writes) | | |
| MT-08 | yes | Version and status after the upgrade | | |
| MT-09 | yes | `doctor` right after the upgrade | | |
| MT-10 | yes | Outage duration and the interface's return | | |
| MT-11 | yes | Collection runs again after the first cadence | | |
| MT-126 | yes | The rate limit does not cut the page in production | | |
| MT-127 |  | The lowered limit gives 429 and the retry sentence — (writes) lab only | | |

## 3. Sign-in

| Id | Core | Check (short) | Result (Pass / Fail / Not run) | Note |
|---|---|---|---|---|
| MT-12 | yes | Sign-in of an existing user | | |
| MT-13 | yes | Account menu and API version | | |
| MT-14 | yes | Account page | | |
| MT-15 | yes | Signing out and in again | | |
| MT-16 |  | Creating a new user — (writes) lab only | | |
| MT-17 |  | Forced password change — (writes) lab only | | |
| MT-18 |  | The viewer role's limit — (writes) lab only | | |
| MT-19 |  | Disabling the test account — (writes) lab only | | |

## 4. Language

| Id | Core | Check (short) | Result (Pass / Fail / Not run) | Note |
|---|---|---|---|---|
| MT-131 |  | English only (LANG-1, D-41) | | |
| MT-20 | | Removed in 0.1.10 (D-41) | Removed | |
| MT-21 | yes | Number, ratio and time format | | |
| MT-22 | yes | Top bar and navigation | | |
| MT-23 |  | English: sign-in page `/login` | | |
| MT-24 | yes | English: Dashboard `/` | | |
| MT-25 |  | English: Behavioral overview `/behavior` | | |
| MT-26 | yes | English: Anomaly list `/anomalies` | | |
| MT-27 | yes | English: Anomaly detail, log source volume `/anomalies/<id>` | | |
| MT-28 | yes | English: Anomaly detail, WAF endpoint `/anomalies/<id>` | | |
| MT-29 | yes | English: Anomaly detail, IPS `/anomalies/<id>` | | |
| MT-30 | yes | English: Anomaly detail, property watch value `/anomalies/<id>` | | |
| MT-31 |  | English: Source behavior `/behavior/sources` and `/behavior/sources/<id>` | | |
| MT-32 |  | English: WAF behavior `/behavior/waf` and `/behavior/waf/sources/<id>` | | |
| MT-33 |  | English: Property watches `/behavior/watches`, `/behavior/watches/new`, `/behavior/watches/<id>` | | |
| MT-34 |  | English: Fleet `/fleet` | | |
| MT-35 |  | English: Fleet sources `/fleet/sources` | | |
| MT-36 |  | English: Catch-all senders `/fleet/catchall` | | |
| MT-37 |  | English: Offenses `/offenses` and `/offenses/<id>` | | |
| MT-38 | yes | English: Alerts `/alerts` and `/alerts/<id>` | | |
| MT-39 | yes | English: Rule health `/rules` and `/rules/<id>` | | |
| MT-40 |  | English: Rule statistics `/rules/statistics` | | |
| MT-41 |  | English: Custom rules `/custom-rules`, `/custom-rules/new`, `/custom-rules/<id>` | | |
| MT-42 |  | English: Detection coverage `/coverage` | | |
| MT-43 |  | English: Log sources `/log-sources` and `/log-sources/<id>` | | |
| MT-44 | yes | English: Searches `/searches`, `/searches/new`, `/searches/<id>` | | |
| MT-45 |  | English: Configuration changes `/config-changes` | | |
| MT-46 |  | English: Administration `/admin` | | |
| MT-47 |  | English: Search cadence `/admin/search-cadence` | | |
| MT-48 |  | English: Classification rules `/admin/classification-rules` | | |
| MT-49 |  | English: Outage alerts `/admin/outage-policy` | | |
| MT-50 | yes | English: WAF sources `/admin/waf-sources` and `/admin/waf-sources/<id>` | | |
| MT-51 | yes | English: IPS sources `/admin/ips-sources` and `/admin/ips-sources/<id>` | | |
| MT-52 | yes | English: History backfill `/admin/history-backfill` | | |
| MT-53 |  | English: Users `/admin/users` | | |
| MT-54 |  | English: Directory `/admin/directory` | | |
| MT-55 | yes | English: Guide `/guide` and `/guide/<section>` | | |
| MT-56 |  | English: Account `/account` and `/account/password` | | |
| MT-57 |  | Failed sign-in sentence — (writes) | | |
| MT-58 | | Removed in 0.1.10 (D-41) | Removed | |
| MT-59 | | Removed in 0.1.10 (D-41) | Removed | |

## 5. Dashboard

| Id | Core | Check (short) | Result (Pass / Fail / Not run) | Note |
|---|---|---|---|---|
| MT-60 |  | Dashboard widget: “Total EPS” | | |
| MT-61 |  | Dashboard widget: “Log sources” | | |
| MT-62 |  | Dashboard widget: “Open offenses” | | |
| MT-63 |  | Dashboard widget: “Active anomalies” | | |
| MT-64 |  | Dashboard widget: “Fleet EPS” | | |
| MT-65 |  | Dashboard widget: “EPS by source class” | | |
| MT-66 |  | Dashboard widget: “Top log sources by EPS” | | |
| MT-67 |  | Dashboard widget: “Anomalies by severity” | | |
| MT-68 |  | Dashboard widget: “Log source health” | | |
| MT-69 |  | Dashboard widget: “Recent anomalies” | | |
| MT-70 |  | Dashboard widget: “Recent offenses” | | |
| MT-71 |  | Dashboard widget: “Scheduled searches” | | |
| MT-72 |  | Dashboard widget: “Detection health” | | |
| MT-73 |  | Dashboard widget: “Evidence collection” | | |
| MT-74 |  | Dashboard widget: “Catch-all senders to review” | | |
| MT-75 |  | Dashboard widget: “Source classes” | | |
| MT-76 |  | Dashboard widget: “Outage policy” | | |
| MT-77 |  | Dashboard widget: “WAF sources” | | |
| MT-78 |  | Dashboard widget: “Recent WAF anomalies” | | |
| MT-79 |  | Dashboard widget: “Recent IPS anomalies” | | |
| MT-128 | yes | World map: dashboard widget and `/geography` | | |
| MT-129 |  | Country source and the `geo_country_source` check (GEO-3) | | |

## 6. Anomalies

| Id | Core | Check (short) | Result (Pass / Fail / Not run) | Note |
|---|---|---|---|---|
| MT-80 |  | Anomaly list and filters | | |
| MT-81 |  | Log source volume anomaly detail and the honesty states | | |
| MT-82 |  | Lifecycle tab and reasons | | |
| MT-83 |  | WAF anomaly detail | | |
| MT-84 |  | IPS anomaly detail | | |
| MT-85 |  | Property watch anomaly detail | | |
| MT-86 |  | Honesty states are visible and are not turned into zero | | |

## 7. Alerts

| Id | Core | Check (short) | Result (Pass / Fail / Not run) | Note |
|---|---|---|---|---|
| MT-87 |  | Alert list | | |
| MT-88 |  | Alert detail: Description and resolution reason | | |
| MT-89 |  | Acknowledging and resolving an alert — (writes) lab only | | |

## 8. Custom rules

| Id | Core | Check (short) | Result (Pass / Fail / Not run) | Note |
|---|---|---|---|---|
| MT-90 |  | Custom rule list and states | | |
| MT-91 |  | Custom rule detail | | |
| MT-92 |  | Custom rule draft preview (without saving) | | |
| MT-93 |  | Creating and deleting a custom rule — (writes) | | |

## 9. Searches

| Id | Core | Check (short) | Result (Pass / Fail / Not run) | Note |
|---|---|---|---|---|
| MT-94 |  | Search list | | |
| MT-95 | yes | Search run history and the error row | | |
| MT-96 |  | Schedule floor (cron) and presets | | |
| MT-97 |  | Running a search now — (writes) lab only QRadar search | | |
| MT-98 |  | Storing a new search — (writes) lab only | | |

## 10. History backfill

| Id | Core | Check (short) | Result (Pass / Fail / Not run) | Note |
|---|---|---|---|---|
| MT-99 |  | History backfill page: decision block and table | | |
| MT-100 |  | History backfill: the first-open question | | |
| MT-101 |  | Read history now / Stop reading — (writes) lab only QRadar search | | |

## 11. Rule health, statistics, coverage and fleet

| Id | Core | Check (short) | Result (Pass / Fail / Not run) | Note |
|---|---|---|---|---|
| MT-102 |  | Rule health list | | |
| MT-103 |  | Rule detail and health history | | |
| MT-104 |  | Rule statistics | | |
| MT-105 |  | Coverage | | |
| MT-106 |  | Fleet, fleet sources and catch-all senders | | |
| MT-107 |  | Log sources and detail | | |
| MT-108 |  | Configuration changes | | |
| MT-109 |  | Behavior pages: overview, sources, WAF, property watches, offenses | | |

## 12. Administration

| Id | Core | Check (short) | Result (Pass / Fail / Not run) | Note |
|---|---|---|---|---|
| MT-110 |  | WAF sources: list and profile | | |
| MT-111 |  | IPS sources: list and profile | | |
| MT-112 |  | Re-running a source validation — (writes) lab only QRadar search | | |
| MT-113 |  | Users and directory (view only) | | |
| MT-114 |  | Search cadence (view only) | | |
| MT-115 |  | Changing the search cadence — (writes) lab only | | |
| MT-116 |  | Outage policy and classification rules (view only) | | |
| MT-117 |  | Administration home page | | |
| MT-130 |  | SIEM load page, command and `qradar_health` (ARL-1, ARL-2, ARL-3) | | |

## 13. Guide

| Id | Core | Check (short) | Result (Pass / Fail / Not run) | Note |
|---|---|---|---|---|
| MT-118 |  | The guide opens in English | | |
| MT-132 |  | Header Guide link shows once; fixed Guide entry (GUIDE-3) — (writes) | | |

## 14. Read-only against QRadar

| Id | Core | Check (short) | Result (Pass / Fail / Not run) | Note |
|---|---|---|---|---|
| MT-119 | yes | `doctor` shows that it makes only one kind of call to QRadar | | |
| MT-120 | yes | Console side: confirming that there was no write in the test window — console administrator step | | |

## 15. Rollback criteria and steps

| Id | Core | Check (short) | Result (Pass / Fail / Not run) | Note |
|---|---|---|---|---|
| MT-121 | yes | Evaluating the rollback criteria | | |
| MT-122 |  | Rollback (only if needed) — (writes) | | |
| MT-123 |  | Rollback rehearsal — (writes) lab only | | |

## 16. Delivery of the results

| Id | Core | Check (short) | Result (Pass / Fail / Not run) | Note |
|---|---|---|---|---|
| MT-124 | | Removed in 0.1.10 (D-41) | Removed | |
| MT-125 | yes | Fill in the results page and hand it over | | |

## Summary

| Count | Value |
|---|---|
| Pass | |
| Fail | |
| Not run | |
| Support bundle attached (yes / no) | |
| Delivery time (UTC) | |
