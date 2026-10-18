# Task PW-7c — A threshold rule names its pinned value; the Definition tab states the cadence

Read `AGENTS.md` at the repository root first and follow it exactly, §3a (the frontend quality bar;
screenshots are part of acceptance) and the §4 test rules. Then read
`docs/PHASE-G-PROPERTY-WATCH.md` §G.10.2 and §G.13.3, and `docs/tasks/PW-7b.report.md` open
point 1.

**Why (orchestrator review of PW-7b, 2026-10-18):** the watch page's Threshold rules tab finds a
rule's pinned value text by searching the values the page read (plus one wider read: 168 h,
limit 200). A rule whose value is outside those reads shows "not among the tracked values read;
it may no longer be stored" (`frontend/src/components/propertywatch/ThresholdRules.tsx:94`). That
sentence is never true: `custom_rule.pw_value_id` is `ondelete="RESTRICT"`
(`backend/app/models/custom_rule.py:119`), so a rule's value always exists while the rule does.
The backend already joins the value to find the rules (`pinning_rules_by_watch` in
`backend/app/services/property_watches.py`), so it can return the text at no extra statement.

## Build

1. **Backend:** `PinRuleOut` (`backend/app/schemas/property_watch.py:196`) gains `value_key: str`
   and `components: list[str]` (the value split per watched property, as `PwValueRefOut` gives
   it), read in the same statement as today (`pinning_rules_by_watch` selects them from the
   joined `pw_value` row). Both `threshold_rules` (single-watch read) and `PinsOut.rules` carry
   them. The statement counts the PW-6b tests pin do not change.
2. **Frontend:** `ThresholdRules.tsx` shows the rule's value from `value_key`/`components`
   (rendered as text; a two-property value as its components, as the values table shows a pair)
   and drops the wider values read and the "may no longer be stored" branch entirely; the watch
   page (`frontend/src/app/behavior/watches/[id]/page.tsx`) stops issuing that extra read. Keep
   every other state of the tab (loading, error, forbidden, empty, reader without
   `custom_rule:write`).
3. **The Definition tab's cadence** (`WatchDefinition.tsx:125`): state the value, e.g. "Every 1 h
   (the PROPERTY_WATCH family's setting in Search cadence); buckets are five minutes wide.", from
   `cadence_seconds` (formatted as minutes or hours), keeping the link.
4. Types in `frontend/src/lib/api.ts` follow the schema.

## Tests

- Backend: the new fields on both responses, a two-property value's components, the unchanged
  statement counts (reuse PW-6b's pins), a hostile value text returned as stored.
- Frontend: the tab shows the value from the rule itself, including a rule whose value is not in
  the page's values read; no extra read is issued (assert the fetch calls); a hostile value as
  text; the cadence sentence at 5 min, 1 h and 8 h. Break each once; report which test failed.

## Screenshots

The Threshold rules tab (four rules, one of them on a value outside the values page; a reader)
and the Definition tab's collection section, at 1440×900, 1280×800 and 1024×768, headless Chrome
against the fixture API serving the real shapes. Reuse `/home/efe/Documents/QRadar-Dash-work/pw7b/`
(`fixture_api.py`, `capture.mjs`); write the new ones under
`/home/efe/Documents/QRadar-Dash-work/pw7c/`, never `/tmp`. Open the PNGs and say what you saw.

## Gates

Backend from `backend/` (the main checkout's venv, `PYTHONPATH=.`): `ruff check app tests`,
`mypy app` (0), the whole unit suite, and the PW integration files
(`tests/integration/test_pw6b_api.py`, `test_property_watches_api.py`, `test_pw_api_gaps.py`) on
`TEST_DATABASE_URL=postgresql+asyncpg://test:test@localhost:5433/qradar_obs_pw7c`. Frontend from
`frontend/` with `~/.local/node/bin` on `PATH`: `npm ci`, `npm run lint`, `npm run typecheck`,
`npx vitest run --no-cache`, `npm run build`. Copy each summary line verbatim.

## Allowed files

`backend/app/schemas/property_watch.py` (`PinRuleOut`), `backend/app/services/property_watches.py`
(`pinning_rules_by_watch` and `PinningRule`), `frontend/src/components/propertywatch/ThresholdRules.tsx`,
`WatchDefinition.tsx`, `frontend/src/app/behavior/watches/[id]/page.tsx`, `frontend/src/lib/api.ts`
(the rule type), new and existing tests of these files (additive where an exact shape must learn
the new fields), `docs/tasks/PW-7c.report.md` (new).

Do not commit. Stop every process you start by PID. Ports 3116/8116/9116.
