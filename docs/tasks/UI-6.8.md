# Task UI-6.8 — two widget defects seen in the UI-6.6 review

Read `AGENTS.md` at the repository root first and follow it exactly, §3a above all. Then read
`docs/PRODUCT-UX-GUIDELINES.md` (normative; §4 honesty states, §8 components) and
`frontend/src/app/tokens.css`. Both defects are on the Dashboard, seen by the orchestrator in
`/home/efe/Documents/QRadar-Dash-work/ui66/png/after/after-p3-template-desc-line-1024x768.png`
("Morning triage" at 1024×768).

1. **"Anomalies by severity" draws an empty chart with no sentence**
   (`frontend/src/components/home/SeverityChart.tsx`). When the window holds no anomaly, the
   widget shows an x-axis and an empty plot, and says nothing. AGENTS.md §3a: the empty state says
   what is empty and why. When there is no anomaly in the window, say so in one sentence with the
   window ("No anomaly opened in the last 24 h." — derive the window from the widget's range), and
   draw no empty plot. Distinguish this from "not measured" if the data can say that (an
   unavailable or failed read is the error state, never "no anomaly"). Check what the backend
   returns for an empty window first, and pin both cases.
2. **The "Other classes" line of "EPS by source class" clips at the right edge**
   (`frontend/src/components/home/EpsByClass.tsx`): at 1024×768 the list of folded classes under
   "Other classes" ("Proxy, VPN, Network, Other, IPS, WAF, DNS…") is cut off by the widget's edge.
   It must wrap inside the widget (or be truncated with the full list reachable, for example in a
   `title`), never clipped mid-word by overflow. Check 1440, 1280 and 1024 and the narrowest
   widget width the grid allows.

## Tests, screenshots, gates

- Vitest for both: the empty window sentence (and no plot), the error state not reading as
  "no anomaly", and the folded-class line wrapping (class or computed style contract). Break-it
  each.
- Before/after PNGs of the Dashboard holding both widgets at 1440×900, 1280×800 and 1024×768, from
  the fixture in `/home/efe/Documents/QRadar-Dash-work/ui66/` (copy it to `…/ui68/`; it serves an
  empty severity window if you set its mode, or extend it). Ports 3168/8168/9168. Open them and
  say what you saw.
- From `frontend/`: `npm run lint`, `npm run typecheck`, `npx vitest run --no-cache`,
  `npm run build`, summary lines verbatim.

## Allowed files

`frontend/src/components/home/SeverityChart.tsx`, `EpsByClass.tsx`, their CSS and tests;
`docs/tasks/UI-6.8.report.md` (new). Do not commit. Stop every process you start by PID. Nothing
under `/tmp`.
