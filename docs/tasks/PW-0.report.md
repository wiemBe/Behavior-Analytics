# PW-0 report: Phase G lab probe (read-only)

Counts: **26 Ariel searches** (limit 40, strictly sequential, 0 timeouts/cancels), **7 other GETs** (limit 20; paginated lists count per page: groups x2, sources x2, custom properties+types x3). 1-hour windows (ends 10 min before now, 5-min aligned), LIMIT <= 1000. Provider: `QRadarRestProvider` via `build_provider(get_settings())`, MCP never used, `dependents` endpoint never called. Script, logs: /home/efe/Documents/QRadar-Dash-work/pw0/ (lib.py, s*.py, probe.log with every AQL and status). No repository file changed.

Lab data note: in the probed hour only internal types had events (368 Health Metrics 227011, 147 System Notification 9510, 105 SIM Audit 1391, 267 Asset Profiler 13). WAF/IPS types were silent (a first L3 attempt on type 232 + WAF_URL returned 0 rows, search 1). So the custom-property tests used type 368's string property "Metric ID" (non-personal values).

## L1 Native AQL filter by log-source group: **YES (by group NAME only)**
- `WHERE LOGSOURCEGROUPNAME(logsourceid) = 'Other'` accepted, COMPLETED, 237925 events = exactly the inventory membership's total (all 54 sources are in group 0 'Other').
- Controls: `= 'NoSuchGroupZZ'` accepted, 0 rows (so it really filters); `= 'sensor device'` (the group id 1) 0 rows. `GROUP BY LOGSOURCEGROUPNAME(logsourceid)` accepted, one row (`Other`).
- Rejected (HTTP 4xx "invalid"): `logsourcegroupid = 0`, `INLOGSOURCEGROUP('Other', logsourceid)`, `logsourcegroup = 'Other'`, `LOGSOURCEGROUP(logsourceid) = 'Other'`, `LOGSOURCEGROUPID(logsourceid) = 0`.
- Caveats: it matches by name (not unique, not an id; a rename breaks it), and with only one populated group the lab cannot show multi-group or nested semantics. Design: keep the inventory-resolved `logsourceid IN (...)` as the default/fallback (also needed for parent/descendant handling and chunking); `LOGSOURCEGROUPNAME` may be a later provider-internal optimisation only.

## L2 Does a child-group source list the parent group id in `group_ids`: **not testable on this lab (indicative: no)**
- Lab has 2 groups: id 1 'sensor device' (parent_id 0) and id 0 'Other' (parent_id 1), i.e. each names the other as parent (a cycle, or 0 is a root sentinel). All 54 sources have `group_ids == [0]`; none lists 1. So a member of 'Other' does not list its declared parent 1.
- Consequence: keep the explicit descendant walk (as designed), and it must be cycle-safe: this lab's real data contains a parent cycle (and a `parent_id` of 0 that is also a real group id). A visited-set is mandatory.

## L3 `devicetype = <id>` together with a quoted custom property: **YES**
AQL: `SELECT "Metric ID" AS "v", starttime / 300000 AS "k", SUM(eventcount) AS "events", COUNT(*) AS "records" FROM events WHERE devicetype = 368 AND starttime >= S AND starttime < E GROUP BY "Metric ID", starttime / 300000 ORDER BY SUM(eventcount) DESC LIMIT 1000 START .. STOP ..` (321 chars). Create ok, COMPLETED, 900 rows, 1.1 s, sum of events 227011 = the type's hourly total (nothing lost). No change to the design (no fallback needed).

## L4 String literals against a custom property
- Equality `"Metric ID" = '<existing value>'`: accepted, matched (4750 events as expected from the grouped search).
- `IN ('<v1>','<v2>')`: accepted, 2 rows, 9500 events = 4750 + 4750 (exact).
- Quote escape by doubling (`'a''b'`): **accepted** (COMPLETED, 0 rows); also `LIKE '%''%'` accepted. Backslash (`'a\'b'`, `LIKE '%\'%'`): **rejected** (invalid).
- Whether the doubled quote *matches* a real value containing a quote: **not testable here**: none of the 125 Metric ID values (nor any QIDNAME in the hour; search 12) contained a quote, so every escaped literal legitimately returned 0. Accepted-by-parser is shown; semantics unproven.
- Design: doubling is the only escape the parser accepts; backslash must never be used. Because semantics are unproven, keep the design fallback "pins containing a quote refused" (or verify on a source that has such a value) rather than relying on escaping. OR-ed equality is unnecessary: `IN` works.

## L5 Unparsed property in a GROUP BY: **NULL** (JSON `null`; the key `v` is present)
Scope devicetype IN (368,147,105,267) grouped by "Metric ID": 126 rows = 1 NULL row (10914 events = 9510 + 1391 + 13, exactly the three types that do not carry the property) + 125 non-empty values (227011 events). No empty-string rows were seen. Design: the fold should treat NULL as unparsed; keep treating `''` as unparsed too (harmless).

## L6 `GROUP BY username`, `QIDNAME(qid)` with the bucket key: **YES to both**
- `SELECT logsourceid AS "log_source_id", username AS "u", starttime / 300000 AS "k", SUM(eventcount), COUNT(*) ... GROUP BY logsourceid, username, starttime / 300000 ORDER BY SUM(eventcount) DESC LIMIT 1000`: accepted, 37 rows (cols events,k,log_source_id,records,u), 0.1 s. Shapes: u NULL 22 rows / 237258 events; non-empty 15 rows / 667 events. Sum 237925 = total. Unset username is NULL.
- `SELECT logsourceid, qid, QIDNAME(qid) AS "qname", starttime/300000 AS "k", ... GROUP BY logsourceid, qid, starttime/300000` (the IPS keyed form, qname selected not grouped): accepted, 69 rows, 1.1 s, qname always non-empty, sum 237925. Row shape: events, k, log_source_id, qid, qname, records (numbers returned as floats).
- Design: v1 may include username and signature-name dimensions, subject to personal-data handling (rule 3).

## L7 50-id `logsourceid IN (...)`: **YES**
50 real ids: accepted, COMPLETED 1.1 s, AQL 437 chars, 237912 events. All 54 inventory ids: accepted, 453 chars, 237925 events. (Inventory has 54 sources, so no id was repeated.) Larger lists not tried; 50 is comfortably within limits, `pw_max_sources_per_search` stays a setting.

## L8 Real Ariel time and row counts (1-hour window)
- L3 (type 368 + property + 5-min bucket): 1.1 s, 900 rows, LIMIT 1000 not hit, 227011 events.
- L6 username: 0.1 s, 37 rows. L6 qid/QIDNAME: 1.1 s, 69 rows, 237925 events.
- Truncation check: same qid search with `LIMIT 20` returned exactly 20 rows (of 69), status COMPLETED, `record_count` 20, no truncation flag from the console: truncation is detectable only as rows == LIMIT, which is why the provider's LIMIT cap+1 construction is required (confirmed). Search times are ~1 s at this volume (about 240k events/hour, tiny); a busy production scope will be slower, not measured here (no generator allowed).
- Polling resolution: status polled every 1 s, so times are 1 s-quantised.

## Surprises
1. `LOGSOURCEGROUPNAME(logsourceid)` exists and works (L1), name-based only.
2. The lab's two groups name each other as parent (cycle), and group id 0 is both a real group and a parent_id value.
3. A no-match ungrouped aggregate returns zero rows (not one row with 0/NULL): the collector must not read an empty result as a zero count without another coverage signal.
4. WAF/IPS types had no events in the probed hour, so L3-L6 ran on internal types; WAF-property-specific behaviour (e.g. long URLs) is untested.

## Gates (AGENTS.md section 6)
Files changed in repository: none. Commands: only the probe scripts above (26 searches, 7 GETs); no ruff/mypy/pytest run (no code changed). Open points: L2 child/parent listing and L4 escape semantics could not be proven on this lab (need a nested group with members and a value containing a quote).
