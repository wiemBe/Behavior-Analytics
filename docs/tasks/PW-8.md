# Task PW-8 — Phase G UI, the editor: `/behavior/watches/new`, the edit disclosure, the preview panel, the F.1 subject picker kind

Read `AGENTS.md` at the repository root first and follow it exactly, **§3a above all** (the
frontend quality bar; screenshots are part of acceptance) and §2 rule 4 (**no AQL field anywhere**:
the generated AQL is shown read-only, never accepted). Then read `docs/PRODUCT-UX-GUIDELINES.md`
(normative), `frontend/src/app/tokens.css`, and `docs/PHASE-G-PROPERTY-WATCH.md` §G.3 (the watch
model and its validation), §G.6 "Preview", §G.10.2 (the pin-only rule), §G.11, §G.14 (the enable
caps) and **§G.13.4** (binding).

**The API is built (PW-5, PW-5b, PW-6) and PW-7's read side is merged.** Read
`backend/app/api/routes/property_watches.py`, `backend/app/schemas/property_watch.py`, the
custom-rule schema's `pw_value_id`, and the PW-5, PW-5b, PW-6 and PW-7 reports. Reuse PW-7's
series chart (`frontend/src/components/propertywatch/`) for the preview; extend it only by props.
**No backend change in this task**: if the UI needs one, stop and report.

## Build

1. **`/behavior/watches/new` and the Edit disclosure on `/behavior/watches/[id]`** (§G.13.4):
   one form, five labelled sections (Scope, Properties, Values, Detection, Preview) with the
   preview beside it as the F.1 rule editor does. Scope: kind control + searchable combobox over
   `scope-options` with each option's size; the resolved member count and the sub-groups walked;
   a group over `pw_max_chunks_per_watch` chunks shown disabled with the number and what to do.
   Properties: one or two pickers in the T-058 `PropertyInput` pattern over `property-options`,
   with "defined on n of m types in this scope", `name_allowed = false` entries disabled with the
   reason, normalized `username` beside them, a typed name marked "not in the catalog", and the
   two-property limit stated. Values: pinned values (at most 10; pairs on a two-property watch),
   each validated inline against the pin pattern with the reason; the automatic-tracking sentence
   with the numbers from the catalog response. Detection: the detectors with their one-line claim
   and learning need, the severity, the link sentence for threshold alerts.
2. **Save and preview:** "Save as disabled and preview" creates (or PATCHes with
   `expected_updated_at`) and runs `POST /{id}/preview`; the result shows the values with counts,
   coverage and the unparsed remainder, the per-type reading, the generated AQL in a read-only
   `CodePanel` rendered as text; an empty result says whether the scope sent nothing or the
   property parsed on nothing. The 422 `reasons` render inline, each as sent; the 409 version
   conflict says who changed it and offers to reload; the rate limit says when to retry.
3. **Enable / disable / delete** for writers on the watch page: enabling is a separate explicit
   action that states the family switch, the cap and the search budget, and renders the enable 409
   naming the cap or budget; delete confirms in words, and renders the PW-6 409 naming the rules
   that pin a value; a scope or property change shows the PW-6 409 the same way.
4. **The deterministic summary sentence** above the actions, regenerated from the form, never free
   text; the latency sentence once. The **New watch** action on `/behavior/watches` for writers;
   others are told which permission adds it.
5. **The F.1 subject picker** (`frontend/src/components/customrules/`): the "Property watch value"
   kind: choose a watch, then one of its **pinned** values; an automatic value is not offered, and
   the picker says to pin it first, linking to the watch.

Values and names are attacker-controlled text: rendered as text, and through
`echarts.format.encodeHTML` in any chart tooltip (T-117); pin it with a test.

## States and tests

Every state of §G.13.4 and §G.13.6: loading, error, forbidden for non-writers (the editor hidden,
`property_watch:write` named), collection off, each inline reason, the disabled
`name_allowed = false` entries, the oversize group, the preview's empty / not-applicable /
lower-bound / rate-limited results, the three 409s. A test that **no input of the editor accepts
or displays AQL as an editable field**. Vitest covers each state; break-it each and report which
failed.

## Screenshots

`/behavior/watches/new` (empty form; a group scope with its walk; two properties with one
undefined on some types; a 422 reasons list; a preview result with remainder; an empty preview),
the watch page with the Edit disclosure open and the enable confirmation, `/custom-rules/new`
with the PW value kind, at 1440×900, 1280×800 and 1024×768 with headless Chrome
(`/usr/bin/google-chrome`) against a deterministic fixture API serving the **real** payload
shapes. Fixture, capture script, PNGs and scratch under `/home/efe/Documents/QRadar-Dash-work/pw8/`,
never `/tmp`. Ports 3118/8118/9118. Open the PNGs and say what you saw; list their paths. No
page-level horizontal overflow (guidelines §9).

## Allowed files

- `frontend/src/app/behavior/watches/**`, `frontend/src/components/propertywatch/**`
- `frontend/src/components/customrules/**` (the PW kind only)
- `frontend/src/lib/api.ts` (property-watch calls and types only)
- new tests; `docs/PHASE-G-PROPERTY-WATCH.md` `[BUILT — PW-8]` marker in §G.13.4;
  `docs/tasks/PW-8.report.md` (new)

## Gates

From `frontend/`, with `~/.local/node/bin` on `PATH`: `npm ci`, `npm run lint`,
`npm run typecheck`, `npx vitest run --no-cache`, `npm run build`. Copy each summary line verbatim.
Stop every process you start by PID. Do not commit.
