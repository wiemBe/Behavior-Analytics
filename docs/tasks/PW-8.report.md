STATUS: IN PROGRESS

# PW-8 report — Phase G property-watch editor UI

## Scope and source

- Work started from the accepted PW-7 read side plus PW-6 and the PW-5b snapshot described in
  the handoff.
- `docs/tasks/PW-8.md` is absent from this branch. Its authoritative contents were read from
  repository commit `d169aad`, the commit that added the PW-8 specification; the handoff matches
  that specification. The missing task file is not being added because it is outside PW-8's
  allowed-file list.

## Files changed

- `frontend/src/app/behavior/watches/**` — added the new-watch route and tests; exposed writer
  actions on the inherited list/detail routes; added the collapsed detail editor and state tests.
- `frontend/src/components/propertywatch/**` — added typed authoring helpers, scope/property
  pickers, the five-section editor, confirmations/refusals, honest preview states, fixtures and
  tests; extended the existing token-based styles.
- `frontend/src/components/customrules/**` — added the PW_VALUE rule kind, pinned watch-value
  picker, guidance, fixtures and tests.
- `frontend/src/lib/api.ts` — added the property-watch write/preview/scope contracts and methods,
  plus the PW_VALUE rule fields.
- `docs/tasks/PW-8.report.md` — this live task report.

## What I did

- Implemented the five-section watch editor, deterministic summary, backend-authored preview,
  scope/property comboboxes, safe pin validation, and every specified refusal/empty/honesty state.
- Implemented writer-only new/edit/action surfaces and explicit enable/delete confirmations.
- Added PW_VALUE custom-rule authoring from pinned values only, with fresh-pin latency guidance.
- Preserved the backend boundary: the browser submits structured fields and never accepts AQL.

## Gates

- `npm run typecheck` before dependency installation — **failed**, `tsc: command not found`.
- `npm ci` — **passed**: 470 packages added, 471 audited; npm reported 1 moderate and 5 high
  dependency vulnerabilities.
- `npm run typecheck` after installation — **passed**, zero TypeScript errors.
- Focused Vitest run — **failed**: 1 `WatchActions` test failed because the confirmation and
  refusal were simultaneously exposed as alerts. The component now closes the confirmation when
  a refusal arrives and models confirmations as named groups.
- Focused Vitest rerun — **passed**: 8 files, 67 tests.
- `npm run lint` — **passed**: `✔ No ESLint warnings or errors` (Next also printed its lint-command
  deprecation notice).
- `npm run typecheck` — **passed**, zero TypeScript errors.
- `npm run test -- --no-cache` — **failed**: 306 files and 3,394 tests passed; 1 test failed because
  inherited `frontend/src/widgets/permissions.ts` omits the two Phase G backend permissions that
  inherited `backend/app/security/rbac.py` defines. That file is outside PW-8's allowed scope.
- `npm run build` — **passed**: compiled successfully, 35/35 static pages generated, including the
  new dynamic `/behavior/watches/new` route.

## Break-it checks

- Not run yet.

## Screenshots

- Not captured yet.

## Open points

- None identified beyond the missing checked-out task file noted above; repository history
  provides the complete task contract.
