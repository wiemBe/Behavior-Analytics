# Task PW-13p — History bootstrap, lab probe: how far back the lab Ariel holds data, and what wide searches cost

Read `AGENTS.md` at the repository root first and follow it exactly. Then read
`docs/PHASE-G-HISTORY-BOOTSTRAP.md`: the decisions block at the top, §H.3 (above all §H.3.2 and
the probe table §H.3.5), §H.4.5 and §H.12.1. Then read the method of the earlier probes,
`docs/tasks/PW-0.md` and its report.

This is a **read-only probe of the lab console.** Write no application code and change no
repository file except your report. Throwaway scripts go only in your scratch directory.

## What to establish

These are the console facts **[C-1] to [C-6]** of §H.14.2. Run probes **P-1 to P-8** of §H.3.5 as
written, on source 212 `ips-gw-01@IPS` and on type 368. Then answer each question, citing the
probe:
1. **[C-1]** The first day with events for source 212 and for type 368, over the last 35 days.
   From that, **which demonstration case of §H.12.1 (A, B or C) the lab supports.**
2. **[C-2] / A1:** whether `GET /api/help/endpoints` exposes retention buckets, and, if it does,
   what they are (one GET). Then state whether assumption A1 holds on the lab, is contradicted,
   or cannot be established.
3. **[C-3]** The per-day share of events with `username` and with `IPS_Action` not null on 212.
4. **[C-4]** The ramp at the floor, only if P-1 or P-2 finds a floor inside 35 days. Otherwise
   record "not observable".
5. **[C-5]** Seconds and rows per grouped search at windows of 1, 6, 24, 72 and 168 h, plus P-6 (the
   repeats) and P-8 (whether the widest search reaches the 120 s cut in
   `_run_bounded_aggregate`, `backend/app/providers/qradar_rest.py` ~L2110).
6. **[C-6]** Whether the `STOP` pad changes a count on an old window.
7. **The cost table of §H.4.5, recomputed with the measured times.** Say where the design's
   figures were too high or too low.

## Hard rules

- **Read-only against QRadar.** Searches only through `QRadarRestProvider`'s own search path, as
  PW-0 did. Never MCP. Never create, update or delete a QRadar object.
- Sequential: one search at a time. Abort on the first timeout and report it.
- **Budget:** at most **40 Ariel searches and 6 other GETs.** Status and result polling do not
  count. Count them in the report.
- **No generator.** This probe reads only what is already stored.
- **Secrets:** never print or write the token, an auth header, `.env` contents or the console
  address. Override in the process environment only:
  `QRADAR_API_TOKEN_FILE=/home/efe/Documents/QRadar-Dash/.secrets/qradar.sec`,
  `QRADAR_CA_BUNDLE=/home/efe/Documents/QRadar-Dash/.secrets/qradar-ca.pem`.
- No value text in the report: counts, seconds, rows and dates only. Record each probe's AQL in
  the log.
- Never touch SELinux. Nothing under `/tmp`.

## Environment and report

- Code: the main checkout's `backend/`, read only, with
  `/home/efe/Documents/QRadar-Dash/backend/.venv/bin/python -I`. Put the backend path on
  `sys.path` in your script.
- You need no database. If the provider needs a session for the instance row, use the PW-10
  validation DB `qradar_obs_pw10`, **read only**. It holds the synced lab instance.
- Scratch, logs and the report: `/home/efe/Documents/QRadar-Dash-work/pw13p/`, report
  `PW-13p.report.md` there. Write it as you go.
- The report follows AGENTS.md §6, plus:
  - the probe table with each request, its seconds, rows and answer;
  - the answers 1 to 7 above;
  - the search and GET counts;
  - every fact the design assumed that the lab contradicts.
