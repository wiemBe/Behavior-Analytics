# UI-1.1 report: charcoal tokens and self-hosted Inter

## 1. Files changed

- `frontend/src/app/tokens.css`: every existing token takes its UR.2.1 value with the D-11 overrides; radii 4/6/8 px; `--font` starts with Inter; UR.2.3 tokens added (no `--text-disabled`); header comment states D-11.
- `frontend/src/app/globals.css`: `@import "./fonts.css"`; `.layout::before` uses `var(--sidebar-bg)`; `button.action` and `.filters button` fill is `var(--primary)`, `button.action:hover` is `var(--primary-hover)`; `.main a:not(.plain)` uses `var(--link)`.
- `frontend/src/app/fonts.css` (new): two `@font-face` rules with `unicode-range`, `font-weight: 100 900`, `font-display: swap`.
- `frontend/src/app/fonts/` (new): `inter-latin-wght-normal.woff2`, `inter-latin-ext-wght-normal.woff2`, `OFL.txt`.
- `frontend/src/lib/contrast.test.ts` (new): WCAG contrast and token tests, plus the no-colour-literal scan.
- `frontend/src/app/shellFixes.test.ts`: the one `.layout::before` assertion now expects `var(--sidebar-bg)`.
- `frontend/src/components/ui/controls.css`: not changed (it holds no primary-button fill).

## 2. What I did

- Tokens follow UR.2.1 and UR.2.3. D-11 applied: `--text-3` is `#73757B`, `--border-strong` is `#35363A`, `--chart-label` is `var(--text-3)`, the tooltip border is `var(--border)`, no `--text-disabled`. `--chart-other` is `#73757B` and `--sev-info` is `#8C8E94`, as UR.2.3 lists. `color-scheme: dark` unchanged. No layout, markup or component change.
- Inter: files copied from `@fontsource-variable/inter@5.3.0`, fetched with `npm pack` into the scratchpad. Nothing was added to `package.json`. Byte sizes match UR.2.5:
  - `inter-latin-wght-normal.woff2`: 48,256 B
  - `inter-latin-ext-wght-normal.woff2`: 85,068 B
  - `OFL.txt`: 4,477 B (the package `LICENSE`, SIL OFL 1.1)
- Font family is declared as `"Inter"` in `fonts.css`, matching the first entry of `--font`. No `next/font`, no external URL.
- Primary button: the `button.action` fill and the `.filters button` fill (also a primary-style fill using `--info`). Text stays `--on-accent`. The `.filters button` has no hover rule, so none was added.
- Links: only `.main a:not(.plain)` used `--info-text` as a content link; it now uses `--link`. Other `--info-text` uses are status or selection text and were left alone.
- `--text-3` is below AA on every surface by the user's decision D-11 (the contrast test pins the value and does not assert AA for it).

## 3. Gates

From `frontend/`:
- `npm run lint`: no ESLint warnings or errors.
- `npm run typecheck`: clean.
- `npm test`: 98 test files passed, 1603 tests passed, 0 failed. This includes `contrast.test.ts` (35 tests), `shellFixes.test.ts` and an unchanged `chartPalette.test.ts`.
- `npm run build`: succeeded (all 32 routes listed). It was run twice: default env, and with `NEXT_PUBLIC_API_BASE_URL` pointing at the fixture for the captures.
- Break check: set `--text` to `#444444` in `tokens.css`; `contrast.test.ts` failed (5 failures, 30 passed). Restored; 35 of 35 pass.
- Backend gates not run (no backend change).

## 4. Screenshots

- Setup: production build (`next start`) on 127.0.0.1:3811, fixture API (Node, deterministic, pinned clock 2026-10-14T09:00:00Z) on 127.0.0.1:4811, headless `/usr/bin/google-chrome` driven over CDP. Script, fixture, profile and PNGs live under `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/` (outside the repository). Both servers and Chrome were stopped afterwards.
- Routes: all 30 routes of UI-R0 "Every route that exists today" (dynamic ones with fixture ids) plus `/admin/outage-policy` and `/fleet/catchall`: 32 routes x 3 viewports = 96 captures. Full-page captures.
- Overflow: `document.documentElement.scrollWidth === clientWidth` held in all 96 captures (0 failures). Per-capture values are listed below.
- Network log: across all 96 page loads, every request went to `http://127.0.0.1:3811` (app) or `http://127.0.0.1:4811` (fixture). The only other URL is a `data:image/svg+xml` URL, which is Chrome's built-in date-picker icon for `datetime-local` inputs, not a network request. The two font requests were `http://127.0.0.1:3811/_next/static/media/inter-latin-ext-wght-normal.3835a68e.woff2` and `.../inter-latin-wght-normal.6c596dfc.woff2`. No external host was contacted. The fixture logged no unknown route (no 404).
- Turkish: the fixture source `Güvenlik Duvarı İstanbul` appears on `/behavior/sources/[id]` (heading), `/fleet` and others. In the 1440x900 capture, `ğ ı İ` render in Inter (latin-ext subset).
- I viewed `/`, `/behavior/sources/[id]`, `/fleet` and `/admin/waf-sources/[id]`: the same layout as before, now charcoal, with Inter. I did not open every one of the 96 PNGs.

### PNG paths by route

- `/`
  - 1440x900: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/root/1440x900.png` (scrollWidth 1440 = clientWidth 1440: yes)
  - 1280x800: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/root/1280x800.png` (scrollWidth 1280 = clientWidth 1280: yes)
  - 1024x768: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/root/1024x768.png` (scrollWidth 1024 = clientWidth 1024: yes)
- `/admin`
  - 1440x900: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/admin/1440x900.png` (scrollWidth 1440 = clientWidth 1440: yes)
  - 1280x800: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/admin/1280x800.png` (scrollWidth 1280 = clientWidth 1280: yes)
  - 1024x768: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/admin/1024x768.png` (scrollWidth 1024 = clientWidth 1024: yes)
- `/admin/classification-rules`
  - 1440x900: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/admin_classification-rules/1440x900.png` (scrollWidth 1440 = clientWidth 1440: yes)
  - 1280x800: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/admin_classification-rules/1280x800.png` (scrollWidth 1280 = clientWidth 1280: yes)
  - 1024x768: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/admin_classification-rules/1024x768.png` (scrollWidth 1024 = clientWidth 1024: yes)
- `/admin/ips-sources`
  - 1440x900: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/admin_ips-sources/1440x900.png` (scrollWidth 1440 = clientWidth 1440: yes)
  - 1280x800: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/admin_ips-sources/1280x800.png` (scrollWidth 1280 = clientWidth 1280: yes)
  - 1024x768: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/admin_ips-sources/1024x768.png` (scrollWidth 1024 = clientWidth 1024: yes)
- `/admin/ips-sources/11111111-1111-1111-1111-111111111111`
  - 1440x900: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/admin_ips-sources_11111111-1111-1111-1111-111111111111/1440x900.png` (scrollWidth 1440 = clientWidth 1440: yes)
  - 1280x800: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/admin_ips-sources_11111111-1111-1111-1111-111111111111/1280x800.png` (scrollWidth 1280 = clientWidth 1280: yes)
  - 1024x768: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/admin_ips-sources_11111111-1111-1111-1111-111111111111/1024x768.png` (scrollWidth 1024 = clientWidth 1024: yes)
- `/admin/search-cadence`
  - 1440x900: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/admin_search-cadence/1440x900.png` (scrollWidth 1440 = clientWidth 1440: yes)
  - 1280x800: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/admin_search-cadence/1280x800.png` (scrollWidth 1280 = clientWidth 1280: yes)
  - 1024x768: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/admin_search-cadence/1024x768.png` (scrollWidth 1024 = clientWidth 1024: yes)
- `/admin/waf-sources`
  - 1440x900: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/admin_waf-sources/1440x900.png` (scrollWidth 1440 = clientWidth 1440: yes)
  - 1280x800: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/admin_waf-sources/1280x800.png` (scrollWidth 1280 = clientWidth 1280: yes)
  - 1024x768: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/admin_waf-sources/1024x768.png` (scrollWidth 1024 = clientWidth 1024: yes)
- `/admin/waf-sources/11111111-1111-1111-1111-111111111111`
  - 1440x900: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/admin_waf-sources_11111111-1111-1111-1111-111111111111/1440x900.png` (scrollWidth 1440 = clientWidth 1440: yes)
  - 1280x800: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/admin_waf-sources_11111111-1111-1111-1111-111111111111/1280x800.png` (scrollWidth 1280 = clientWidth 1280: yes)
  - 1024x768: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/admin_waf-sources_11111111-1111-1111-1111-111111111111/1024x768.png` (scrollWidth 1024 = clientWidth 1024: yes)
- `/alerts`
  - 1440x900: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/alerts/1440x900.png` (scrollWidth 1440 = clientWidth 1440: yes)
  - 1280x800: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/alerts/1280x800.png` (scrollWidth 1280 = clientWidth 1280: yes)
  - 1024x768: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/alerts/1024x768.png` (scrollWidth 1024 = clientWidth 1024: yes)
- `/alerts/bbbbbbbb-0000-0000-0000-000000000000`
  - 1440x900: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/alerts_bbbbbbbb-0000-0000-0000-000000000000/1440x900.png` (scrollWidth 1440 = clientWidth 1440: yes)
  - 1280x800: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/alerts_bbbbbbbb-0000-0000-0000-000000000000/1280x800.png` (scrollWidth 1280 = clientWidth 1280: yes)
  - 1024x768: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/alerts_bbbbbbbb-0000-0000-0000-000000000000/1024x768.png` (scrollWidth 1024 = clientWidth 1024: yes)
- `/anomalies`
  - 1440x900: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/anomalies/1440x900.png` (scrollWidth 1440 = clientWidth 1440: yes)
  - 1280x800: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/anomalies/1280x800.png` (scrollWidth 1280 = clientWidth 1280: yes)
  - 1024x768: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/anomalies/1024x768.png` (scrollWidth 1024 = clientWidth 1024: yes)
- `/anomalies/aaaaaaaa-0000-0000-0000-000000000000`
  - 1440x900: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/anomalies_aaaaaaaa-0000-0000-0000-000000000000/1440x900.png` (scrollWidth 1440 = clientWidth 1440: yes)
  - 1280x800: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/anomalies_aaaaaaaa-0000-0000-0000-000000000000/1280x800.png` (scrollWidth 1280 = clientWidth 1280: yes)
  - 1024x768: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/anomalies_aaaaaaaa-0000-0000-0000-000000000000/1024x768.png` (scrollWidth 1024 = clientWidth 1024: yes)
- `/behavior`
  - 1440x900: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/behavior/1440x900.png` (scrollWidth 1440 = clientWidth 1440: yes)
  - 1280x800: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/behavior/1280x800.png` (scrollWidth 1280 = clientWidth 1280: yes)
  - 1024x768: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/behavior/1024x768.png` (scrollWidth 1024 = clientWidth 1024: yes)
- `/behavior/sources`
  - 1440x900: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/behavior_sources/1440x900.png` (scrollWidth 1440 = clientWidth 1440: yes)
  - 1280x800: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/behavior_sources/1280x800.png` (scrollWidth 1280 = clientWidth 1280: yes)
  - 1024x768: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/behavior_sources/1024x768.png` (scrollWidth 1024 = clientWidth 1024: yes)
- `/behavior/sources/11111111-1111-1111-1111-111111111111`
  - 1440x900: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/behavior_sources_11111111-1111-1111-1111-111111111111/1440x900.png` (scrollWidth 1440 = clientWidth 1440: yes)
  - 1280x800: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/behavior_sources_11111111-1111-1111-1111-111111111111/1280x800.png` (scrollWidth 1280 = clientWidth 1280: yes)
  - 1024x768: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/behavior_sources_11111111-1111-1111-1111-111111111111/1024x768.png` (scrollWidth 1024 = clientWidth 1024: yes)
- `/behavior/waf`
  - 1440x900: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/behavior_waf/1440x900.png` (scrollWidth 1440 = clientWidth 1440: yes)
  - 1280x800: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/behavior_waf/1280x800.png` (scrollWidth 1280 = clientWidth 1280: yes)
  - 1024x768: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/behavior_waf/1024x768.png` (scrollWidth 1024 = clientWidth 1024: yes)
- `/behavior/waf/sources/11111112-1111-1111-1111-111111111111`
  - 1440x900: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/behavior_waf_sources_11111112-1111-1111-1111-111111111111/1440x900.png` (scrollWidth 1440 = clientWidth 1440: yes)
  - 1280x800: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/behavior_waf_sources_11111112-1111-1111-1111-111111111111/1280x800.png` (scrollWidth 1280 = clientWidth 1280: yes)
  - 1024x768: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/behavior_waf_sources_11111112-1111-1111-1111-111111111111/1024x768.png` (scrollWidth 1024 = clientWidth 1024: yes)
- `/config-changes`
  - 1440x900: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/config-changes/1440x900.png` (scrollWidth 1440 = clientWidth 1440: yes)
  - 1280x800: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/config-changes/1280x800.png` (scrollWidth 1280 = clientWidth 1280: yes)
  - 1024x768: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/config-changes/1024x768.png` (scrollWidth 1024 = clientWidth 1024: yes)
- `/coverage`
  - 1440x900: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/coverage/1440x900.png` (scrollWidth 1440 = clientWidth 1440: yes)
  - 1280x800: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/coverage/1280x800.png` (scrollWidth 1280 = clientWidth 1280: yes)
  - 1024x768: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/coverage/1024x768.png` (scrollWidth 1024 = clientWidth 1024: yes)
- `/fleet`
  - 1440x900: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/fleet/1440x900.png` (scrollWidth 1440 = clientWidth 1440: yes)
  - 1280x800: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/fleet/1280x800.png` (scrollWidth 1280 = clientWidth 1280: yes)
  - 1024x768: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/fleet/1024x768.png` (scrollWidth 1024 = clientWidth 1024: yes)
- `/fleet/sources`
  - 1440x900: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/fleet_sources/1440x900.png` (scrollWidth 1440 = clientWidth 1440: yes)
  - 1280x800: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/fleet_sources/1280x800.png` (scrollWidth 1280 = clientWidth 1280: yes)
  - 1024x768: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/fleet_sources/1024x768.png` (scrollWidth 1024 = clientWidth 1024: yes)
- `/log-sources`
  - 1440x900: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/log-sources/1440x900.png` (scrollWidth 1440 = clientWidth 1440: yes)
  - 1280x800: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/log-sources/1280x800.png` (scrollWidth 1280 = clientWidth 1280: yes)
  - 1024x768: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/log-sources/1024x768.png` (scrollWidth 1024 = clientWidth 1024: yes)
- `/log-sources/11111111-1111-1111-1111-111111111111`
  - 1440x900: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/log-sources_11111111-1111-1111-1111-111111111111/1440x900.png` (scrollWidth 1440 = clientWidth 1440: yes)
  - 1280x800: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/log-sources_11111111-1111-1111-1111-111111111111/1280x800.png` (scrollWidth 1280 = clientWidth 1280: yes)
  - 1024x768: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/log-sources_11111111-1111-1111-1111-111111111111/1024x768.png` (scrollWidth 1024 = clientWidth 1024: yes)
- `/offenses`
  - 1440x900: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/offenses/1440x900.png` (scrollWidth 1440 = clientWidth 1440: yes)
  - 1280x800: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/offenses/1280x800.png` (scrollWidth 1280 = clientWidth 1280: yes)
  - 1024x768: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/offenses/1024x768.png` (scrollWidth 1024 = clientWidth 1024: yes)
- `/offenses/4001`
  - 1440x900: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/offenses_4001/1440x900.png` (scrollWidth 1440 = clientWidth 1440: yes)
  - 1280x800: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/offenses_4001/1280x800.png` (scrollWidth 1280 = clientWidth 1280: yes)
  - 1024x768: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/offenses_4001/1024x768.png` (scrollWidth 1024 = clientWidth 1024: yes)
- `/rules`
  - 1440x900: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/rules/1440x900.png` (scrollWidth 1440 = clientWidth 1440: yes)
  - 1280x800: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/rules/1280x800.png` (scrollWidth 1280 = clientWidth 1280: yes)
  - 1024x768: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/rules/1024x768.png` (scrollWidth 1024 = clientWidth 1024: yes)
- `/rules/cccccccc-0000-0000-0000-000000000000`
  - 1440x900: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/rules_cccccccc-0000-0000-0000-000000000000/1440x900.png` (scrollWidth 1440 = clientWidth 1440: yes)
  - 1280x800: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/rules_cccccccc-0000-0000-0000-000000000000/1280x800.png` (scrollWidth 1280 = clientWidth 1280: yes)
  - 1024x768: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/rules_cccccccc-0000-0000-0000-000000000000/1024x768.png` (scrollWidth 1024 = clientWidth 1024: yes)
- `/rules/statistics`
  - 1440x900: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/rules_statistics/1440x900.png` (scrollWidth 1440 = clientWidth 1440: yes)
  - 1280x800: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/rules_statistics/1280x800.png` (scrollWidth 1280 = clientWidth 1280: yes)
  - 1024x768: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/rules_statistics/1024x768.png` (scrollWidth 1024 = clientWidth 1024: yes)
- `/searches`
  - 1440x900: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/searches/1440x900.png` (scrollWidth 1440 = clientWidth 1440: yes)
  - 1280x800: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/searches/1280x800.png` (scrollWidth 1280 = clientWidth 1280: yes)
  - 1024x768: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/searches/1024x768.png` (scrollWidth 1024 = clientWidth 1024: yes)
- `/searches/eeeeeeee-0000-0000-0000-000000000000`
  - 1440x900: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/searches_eeeeeeee-0000-0000-0000-000000000000/1440x900.png` (scrollWidth 1440 = clientWidth 1440: yes)
  - 1280x800: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/searches_eeeeeeee-0000-0000-0000-000000000000/1280x800.png` (scrollWidth 1280 = clientWidth 1280: yes)
  - 1024x768: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/searches_eeeeeeee-0000-0000-0000-000000000000/1024x768.png` (scrollWidth 1024 = clientWidth 1024: yes)
- `/admin/outage-policy`
  - 1440x900: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/admin_outage-policy/1440x900.png` (scrollWidth 1440 = clientWidth 1440: yes)
  - 1280x800: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/admin_outage-policy/1280x800.png` (scrollWidth 1280 = clientWidth 1280: yes)
  - 1024x768: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/admin_outage-policy/1024x768.png` (scrollWidth 1024 = clientWidth 1024: yes)
- `/fleet/catchall`
  - 1440x900: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/fleet_catchall/1440x900.png` (scrollWidth 1440 = clientWidth 1440: yes)
  - 1280x800: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/fleet_catchall/1280x800.png` (scrollWidth 1280 = clientWidth 1280: yes)
  - 1024x768: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/png/fleet_catchall/1024x768.png` (scrollWidth 1024 = clientWidth 1024: yes)

## 5. Open points

- Fixture realism: the fixture serves plausible data for every endpoint the routes call, written by hand from `lib/api.ts` types; it is not backend output. Route pages that depend on rarely seen shapes may not show every state.
- D-11 consequence: `--text-3` text (table headers, captions) is below WCAG AA by decision. Not a defect to fix here.
- `--info` remains the accent for chips, chart observed series and selection; only fills of the primary button and content links moved to `--primary` and `--link`, as the task says.
- `.next` was rebuilt with the fixture's API base URL; it is gitignored build output. Rebuild before running against a real backend.
- No git state-changing command was run.
