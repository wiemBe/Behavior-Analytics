# UI-5.7 report — administration pages

## Files changed

- `frontend/src/app/admin/page.tsx` — removed the stale Phase 4 promise from the hub description.
- `frontend/src/app/admin/layout.tsx` — added the administration-only styling scope without
  changing the child pages' order.
- `frontend/src/app/admin/admin.css` — added the divided hub index, readable form/notice measure,
  and semantic section boundaries, using tokens only.
- `frontend/src/app/tokens.css` — added `--admin-form-max` and `--admin-hub-max`; these are layout
  measures used by several administration routes, so their two deliberate widths live in the
  token source rather than as route CSS literals.
- `docs/tasks/UI-5.7.report.md` — recorded the review, defects, captures, measurements and gates.

## What I did

### Review method

- Built the production frontend against a deterministic stdlib fixture API outside the repository.
- Captured and visually inspected all 42 before PNGs: 14 route/state scenarios at 1440×900,
  1280×800 and 1024×768.
- Measured the document and body at every scenario/viewport. All 42 had
  `scrollWidth === clientWidth`; the only local overflow was the intended Search cadence table at
  1024×768 (`799px` scroll width inside a `765px` client width).
- Made the CSS/minimal-markup fixes, rebuilt, captured the same 42 scenarios again and inspected
  every after PNG. All 42 after documents also had `scrollWidth === clientWidth`; the cadence
  table remained the only local overflow at the same measured widths.

### Defect list

| Route | Viewport | Defect | Rule | Fix |
|---|---|---|---|---|
| `/admin` | all three | The header says more sections arrive in Phase 4 although all five sections are present. | UI-5.7 binding spec; UR.1.3 | Remove the stale roadmap sentence and describe the settings that are present. |
| `/admin` | all three | The five destinations read as a loose cluster of small links and secondary text, leaving the page without a strong scan path. | Guidelines §2 and §3; §3a “first screen answers it”; UR.2 typography | Turn the existing list into one bounded, divided index with a stronger destination/description hierarchy; keep the same links and order. |
| WAF and IPS detail routes | 1440×900, 1280×800 | Field mapping controls stretch across the full content column, separating labels, controls and their supporting text and making the form harder to scan. | Guidelines §9 (intrinsic controls and shrinkable layout); §10 associations remain intact | Bound the mapping/editor measure with an administration layout token while retaining the one-column responsive fold at 1024. |
| WAF and IPS detail routes | all three | Consecutive Mapping, Validation, Values, Collection and History sections rely on large blank gaps alone; on long forms the section boundary is weak. | Guidelines §3 (clear separation) and §8 (`PageHeader`/section hierarchy) | Add a token-based hairline and controlled spacing between adjacent semantic sections, without changing heading or DOM order. |
| `/admin/outage-policy`, WAF and IPS detail routes | 1440×900, 1280×800 | Operational notices and supporting prose run much wider than a readable setting/form measure. | Guidelines §2 (information hierarchy) and §9 (responsive containment) | Bound notices and form-supporting content to the same administration measure; tables keep their own full-width local containers. |

No page-level overflow, missing timezone, unaligned numeric table column, inline style, unshared page
header, nested-card pattern, decorative shadow, vanity KPI row, or indistinguishable failed/unavailable
state was found in the before review. The fixture showed failed checks as “a check could not run” and
unavailable checks as “no events in the window”; the distinction is preserved.

### What changed by route

| Route | Outcome |
|---|---|
| `/admin` | Replaced the stale Phase 4 sentence with current orientation. The same five links, in the same order, now form a bounded divided index with stronger destination names and readable descriptions. |
| `/admin/search-cadence` | Verified the existing shared header, UTC labels, never-run/not-counted distinctions and numeric alignment. The seven-column table still scrolls only inside `TableScroll` at 1024×768. No page-specific fix was needed. |
| `/admin/classification-rules` | Verified the shared header, fixed decision order, empty group state, labelled forms and existing internal table containment. No page-specific fix was needed. |
| `/admin/outage-policy` | Bounded the operational explanation to the administration reading measure. Class/source tables, null default grace, governed-source counts and action order are unchanged. |
| `/admin/waf-sources` | Verified valid, failed-query and unavailable/no-events rows remain explicitly distinct, with UTC last-validation times and internal table containment. No list-specific fix was needed. |
| `/admin/waf-sources/[logSourceId]` | Bounded the mapping form and supporting notices to `848px` at 1440/1280 captures (from `1129px`/`969px`) while retaining the available `765px` at 1024. Adjacent long-form sections now have a hairline boundary. Valid, failed, unavailable and no-profile states remain distinct. |
| `/admin/ips-sources` | Verified valid, failed-query and unavailable/no-events rows remain explicitly distinct, with UTC last-validation times and internal table containment. No list-specific fix was needed. |
| `/admin/ips-sources/[logSourceId]` | Applied the same bounded form measure and section hierarchy as WAF without changing preset, mapping, validation or collection behavior. Valid, failed, unavailable and no-profile states remain distinct. |

No tests were added: the task permits tests only for new observable markup, while this change adds
an inert route styling scope and CSS, plus the binding stale-copy correction. The existing tests
already pin the page hierarchy, state distinctions and asserted classes; all passed without edits.

### Before PNGs

Root: `/home/efe/Documents/QRadar-Dash-work/ui57/png/before/`

Each row lists `1440x900`, `1280x800`, then `1024x768`.

| Scenario | PNGs |
|---|---|
| Administration hub | `admin-1440x900.png`, `admin-1280x800.png`, `admin-1024x768.png` |
| Search cadence | `search-cadence-1440x900.png`, `search-cadence-1280x800.png`, `search-cadence-1024x768.png` |
| Classification rules | `classification-rules-1440x900.png`, `classification-rules-1280x800.png`, `classification-rules-1024x768.png` |
| Outage policy | `outage-policy-1440x900.png`, `outage-policy-1280x800.png`, `outage-policy-1024x768.png` |
| WAF sources | `waf-sources-1440x900.png`, `waf-sources-1280x800.png`, `waf-sources-1024x768.png` |
| WAF detail — valid | `waf-detail-valid-1440x900.png`, `waf-detail-valid-1280x800.png`, `waf-detail-valid-1024x768.png` |
| WAF detail — failed check | `waf-detail-failed-1440x900.png`, `waf-detail-failed-1280x800.png`, `waf-detail-failed-1024x768.png` |
| WAF detail — unavailable/no events | `waf-detail-unavailable-1440x900.png`, `waf-detail-unavailable-1280x800.png`, `waf-detail-unavailable-1024x768.png` |
| WAF detail — no profile | `waf-detail-none-1440x900.png`, `waf-detail-none-1280x800.png`, `waf-detail-none-1024x768.png` |
| IPS sources | `ips-sources-1440x900.png`, `ips-sources-1280x800.png`, `ips-sources-1024x768.png` |
| IPS detail — valid | `ips-detail-valid-1440x900.png`, `ips-detail-valid-1280x800.png`, `ips-detail-valid-1024x768.png` |
| IPS detail — failed check | `ips-detail-failed-1440x900.png`, `ips-detail-failed-1280x800.png`, `ips-detail-failed-1024x768.png` |
| IPS detail — unavailable/no events | `ips-detail-unavailable-1440x900.png`, `ips-detail-unavailable-1280x800.png`, `ips-detail-unavailable-1024x768.png` |
| IPS detail — no profile | `ips-detail-none-1440x900.png`, `ips-detail-none-1280x800.png`, `ips-detail-none-1024x768.png` |

### After PNGs

Root: `/home/efe/Documents/QRadar-Dash-work/ui57/png/after/`

Each row lists `1440x900`, `1280x800`, then `1024x768`.

| Scenario | PNGs |
|---|---|
| Administration hub | `admin-1440x900.png`, `admin-1280x800.png`, `admin-1024x768.png` |
| Search cadence | `search-cadence-1440x900.png`, `search-cadence-1280x800.png`, `search-cadence-1024x768.png` |
| Classification rules | `classification-rules-1440x900.png`, `classification-rules-1280x800.png`, `classification-rules-1024x768.png` |
| Outage policy | `outage-policy-1440x900.png`, `outage-policy-1280x800.png`, `outage-policy-1024x768.png` |
| WAF sources | `waf-sources-1440x900.png`, `waf-sources-1280x800.png`, `waf-sources-1024x768.png` |
| WAF detail — valid | `waf-detail-valid-1440x900.png`, `waf-detail-valid-1280x800.png`, `waf-detail-valid-1024x768.png` |
| WAF detail — failed check | `waf-detail-failed-1440x900.png`, `waf-detail-failed-1280x800.png`, `waf-detail-failed-1024x768.png` |
| WAF detail — unavailable/no events | `waf-detail-unavailable-1440x900.png`, `waf-detail-unavailable-1280x800.png`, `waf-detail-unavailable-1024x768.png` |
| WAF detail — no profile | `waf-detail-none-1440x900.png`, `waf-detail-none-1280x800.png`, `waf-detail-none-1024x768.png` |
| IPS sources | `ips-sources-1440x900.png`, `ips-sources-1280x800.png`, `ips-sources-1024x768.png` |
| IPS detail — valid | `ips-detail-valid-1440x900.png`, `ips-detail-valid-1280x800.png`, `ips-detail-valid-1024x768.png` |
| IPS detail — failed check | `ips-detail-failed-1440x900.png`, `ips-detail-failed-1280x800.png`, `ips-detail-failed-1024x768.png` |
| IPS detail — unavailable/no events | `ips-detail-unavailable-1440x900.png`, `ips-detail-unavailable-1280x800.png`, `ips-detail-unavailable-1024x768.png` |
| IPS detail — no profile | `ips-detail-none-1440x900.png`, `ips-detail-none-1280x800.png`, `ips-detail-none-1024x768.png` |

## Gates

- Targeted characterization suite, before the after capture:
  `npx vitest run --no-cache src/app/admin src/components/cadence src/components/fieldmapping`
  — `Test Files  22 passed (22)`; `Tests  374 passed (374)`; 0 failed.
- `npm run lint` — `✔ No ESLint warnings or errors` (exit 0).
- `npm run typecheck` — `> tsc --noEmit` completed with exit 0 and no diagnostics.
- `npx vitest run --no-cache` — `Test Files  160 passed (160)`;
  `Tests  2312 passed (2312)`; 0 failed.
- `npm run build` — `✓ Compiled successfully in 3.8s`;
  `✓ Generating static pages (25/25)`; exit 0.
- Browser capture/measurement — 42 before and 42 after PNGs; 42/42 before and 42/42 after had
  zero document/body overflow. Every PNG was visually inspected.
- `git diff --check` — exit 0, no output.
- Backend gates and integration tests were not run because the task forbids touching `backend/`
  and supplies no integration database.

## Open points

- `next lint` prints its existing Next.js 16 deprecation notice before the clean result.
- Vitest prints existing React `act(...)` warnings in `AutoRefresh`, `AddSourcePicker` and
  `AddIpsSourcePicker` tests; all 2,312 tests pass and no test file was edited.
- No process remains listening on ports 3144, 8144 or 9144. Each fixture/frontend/Chrome capture
  stack was stopped using its recorded PIDs (`24`, `25`, `26`) in its owning shell session.
