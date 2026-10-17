# Task PW-0 — Phase G lab probe: settle the eight console facts the design could not verify

Read `AGENTS.md` at the repository root first and follow it exactly. Then read
`docs/PHASE-G-PROPERTY-WATCH.md` §G.4, §G.5, §G.6 and §G.17 ("What could not be verified from the
repository", L1 to L8). The user approved this probe on 2026-10-17 (D-22, Q14).

This is a **read-only probe of the lab console. Write no application code.** The deliverable is
a report. The probe script is throwaway and lives outside the repository.

## What to settle

For each item, record the exact AQL you sent (with the console address, any token and any
personal value removed), how the console answered (HTTP status of the create, the search's final
status, error text if rejected), and what the result shows.

| # | Question | How |
|---|---|---|
| L1 | Can AQL filter by log-source **group** natively? | Try a small, fixed set of candidate forms (for example a group function or field the AQL reference documents). One search per form. Report which the console accepts and whether the result matches the membership from L2. If none works, say so: the design's fallback (inventory-resolved `logsourceid IN (…)`) stands. |
| L2 | Does a source in a **child** group list the **parent** group's id in `group_ids`? | Read-only GETs through the provider: `list_log_source_groups()` and `list_log_sources()`. Find a source in a nested group, if the lab has one; if it has no nested group, say so. No search needed. |
| L3 | Does `WHERE devicetype = <id>` work together with a **quoted custom property** in the same query? | One grouped search on a log-source type that has a custom property with data. |
| L4 | String literals against a custom property: equality, `IN (…)`, and whether any escape of a quote inside a literal works | Equality and `IN` with values that exist; then a literal containing a quote, escaped by doubling (`''`) and, separately, by a backslash. Report accepted or rejected, and whether the escaped literal matched what it should. |
| L5 | Is an **unparsed** custom property `NULL` or an empty string in a `GROUP BY`? | Group by the property over a scope where some events do not parse it. Report the key shapes seen (`NULL`, `''`, non-empty) and their counts, not the values. |
| L6 | `GROUP BY username`, and `QIDNAME(qid)`, together with the bucket key | Use the same bucket-key expression the IPS keyed search uses (read it in `backend/app/providers/qradar_rest.py`). Report accepted or rejected and the row shape. |
| L7 | A 50-id `logsourceid IN (…)` list | Use real ids from the inventory (repeat ids only if the lab has fewer than 50, and say so). Report accepted or rejected, and the AQL length in characters. |
| L8 | Real Ariel time and row counts | For the L3 and L6 searches over a 1-hour window: the time from create to complete, the record count, and whether a `LIMIT` truncated. |

## Hard rules

- **Read-only against QRadar.** Searches only through the application's provider
  (`QRadarRestProvider`: `create_ariel_search`, `get_ariel_search_status`,
  `get_ariel_search_results`, and `cancel_ariel_search` for your own search on a timeout), plus
  the provider's read-only list methods. Never MCP. Never create, update or delete any QRadar
  object (the 2026-10-15 custom-property exception does **not** apply to this task). **Never
  call `GET /analytics/rules/{id}/dependents`.**
- **Budget:** at most **40 Ariel searches**, sequential (never two at once); status and result
  polling do not count. At most 20 other GET requests. Every search window is **at most 1 hour**
  and every grouped search has a `LIMIT` of at most 1000. Count everything in the report.
- **No generator** and no syslog: use the data the lab already has.
- **Secrets:** never print or write the token, an auth header, `.env` contents or the console
  address. Override in the process environment only:
  `QRADAR_API_TOKEN_FILE=/home/efe/Documents/QRadar-Dash/.secrets/qradar.sec` and
  `QRADAR_CA_BUNDLE=/home/efe/Documents/QRadar-Dash/.secrets/qradar-ca.pem`.
- **Personal data:** the report never contains a username, an IP address or any other event
  value that could identify a person. Report key shapes, lengths and counts. Values that identify
  no one (an action such as `DENY`, a `devicetype` id, a property name, a group name) may appear.
- Run the script with the main checkout's virtualenv
  (`/home/efe/Documents/QRadar-Dash/backend/.venv/bin/python`) and `PYTHONPATH` set to the main
  checkout's `backend/`. Change no file in the repository.
- Never touch SELinux. Nothing under `/tmp`.

## Environment

- Script, logs and the report: `/home/efe/Documents/QRadar-Dash-work/pw0/`, report
  `PW-0.report.md` there. Write the report as you go (the plan, then each item), so a cut-off
  leaves a usable record.

## Report

Per AGENTS.md §6, plus: for L1 to L8, the answer (**yes / no / not testable on this lab**), the
evidence, and what it means for the design's section (keep the fallback, or drop it); the search
and GET counts; anything that surprised you.
