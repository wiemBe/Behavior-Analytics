# Task PW-2a — Phase G provider: the keyed searches, the AQL generator, the fold, the split driver

Read `AGENTS.md` at the repository root first and follow it exactly: §2 rules 1 (read-only
provider: **no** create, update or delete method), 2 (AQL only on `QRadarRestProvider`, never
through MCP), 3 (raw values live only inside the pure fold), 4, 8 (fail closed) and 9, and the §4
test rules. Then read `docs/PHASE-G-PROPERTY-WATCH.md`; it is binding, with D-21, D-22 and the
lab answers of PW-0 (`docs/tasks/PW-0.report.md`). PW-1 is merged: build on
`backend/app/property_watch/facts.py` and `reference.py` and the `pw_*` settings; do not redefine
what they already define.

## What this task builds (design sections)

- **§G.4, the AQL side only:** the `<scope>` filter from an already-resolved scope input
  (`LOG_SOURCE` → `logsourceid IN (<id>)`; `LOG_SOURCE_TYPE` → `devicetype = <id>`;
  `LOG_SOURCE_GROUP` → `logsourceid IN (<ids>)` per chunk). Resolving a scope from the database
  (the cycle-safe group walk, `SCOPE_EMPTY`/`SCOPE_STALE`/`SCOPE_TOO_LARGE`, the fingerprint) is
  **PW-2b**; this task takes the resolved ids and the chunking as inputs.
- **§G.6, the three searches** (grouped, totals, pinned) exactly as written there: the `cap + 1`
  `LIMIT` on each, `IS NOT NULL` on every property, the pinned `IN` form (never OR-ed equality; a
  combination watch gets `IN … AND IN …` over the distinct components and the fold keeps only the
  pinned tuples), references quoted only after `reference.py` validation, pinned values only after
  `PW_PINNED_VALUE_PATTERN`. Add the keyed read methods to `app/providers/base.py` in the
  `get_ips_keyed` style (`base.py:488`), implement them on `QRadarRestProvider` and on the mock
  provider (the mock must exercise the real fold, design §G.15 last integration bullet; SC.5
  item 6). The MCP provider must not run them (rule 2): follow what it does for `get_ips_keyed`.
- **§G.6, the fold** (pure, the `app/ips/fold.py` layer): bucket-key validation (a key outside the
  span fails the whole search), strict integer counts through PW-1's coercer, value
  sanitization (printable, stripped, ≤ `pw_value_max_length`, never raising on hostile input; two
  raw strings that sanitize to one value are one value), the U+001F tuple key, tracked values kept
  whatever their rank, pinned values always kept, the untracked top `pw_max_values_per_bucket` cut
  with the tail summed into `untracked_events` **only when the search was whole**, NULL and `''`
  as unparsed. The tracked and pinned sets are **inputs**: admission (§G.7.3) is PW-2b.
- **§G.5:** totals, parsed, `unparsed_events` and the coverage/measurement facts per bucket from
  the grouped and totals results (exact versus lower bound as §G.5 states).
- **§G.6 "Truncation and splitting" and §G.14.1:** the pure time-first split driver (a truncated
  multi-bucket search keeps nothing and re-runs on whole-bucket halves, left first; at one bucket
  a truncated result is kept as `LOWER_BOUND`; a failed search is not split and leaves its buckets
  `NOT_COLLECTED`), a `PwSearchBudget(pw_max_searches_per_tick)` in the `IpsSearchBudget` shape
  (`app/ips/keyed_search.py:35`) — a spent budget leaves the rest `NOT_COLLECTED`, never zero —
  and chunk sums that are exact only when every chunk was whole (otherwise `LOWER_BOUND`).
- The measured-zero rule of §G.6 is already in `facts.py`: use it, do not restate it.

Out of scope: scope resolution from the database, admission, persistence, the dispatch task,
retention, statement-count pins (PW-2b); detection (PW-3a/3b); the API and the preview route
(PW-5).

## Tests (unit; design §G.15 "References and pins", "Fold and split", "Measured zero and counts", "Coverage")

- Golden AQL strings for source, type and group scopes (one chunk and several), one and two
  properties, with and without pins; the pin generator emits `IN` and never OR-ed equality.
- A hostile-input property test (Hypothesis, as the repository already uses): operator text
  appears in the generated AQL only inside a validated, quoted position, and a rejected reference
  or pinned value never reaches the generator.
- Fold and split: every bullet of §G.15 "Fold and split" and "Measured zero and counts" that
  belongs to this task (key outside the span, hostile values counted, tuple key, tracked kept at
  any rank, untracked only inside the cut, tail only when whole, multi-bucket truncation keeps
  nothing and splits by time, `LOWER_BOUND` at one bucket, a failed search not split, a spent
  budget, `cap` rows whole and `cap + 1` truncated, the integer-valued float, chunk sums).
- Coverage: the remainder is never a value row and never a zero; a lower-bound `parsed`
  over-states the remainder only.
- The REST methods with the HTTP layer mocked, in the style of `tests/unit/test_ips_keyed_provider.py`;
  the mock provider returns rows the fold accepts.
- Break each guard once and confirm a test fails; restore.

## Gates (from `backend/`, the main checkout's venv, `PYTHONPATH=.`)

`ruff check app tests`, `mypy app` (0), the whole unit suite (`-m "not integration"`; report the
counts). No database is given: run no integration tests.

## Allowed files

- `backend/app/providers/base.py`, `backend/app/providers/qradar_rest.py`,
  `backend/app/providers/mock.py`, `backend/app/providers/dto.py` (if a DTO is needed),
  `backend/app/providers/qradar_mcp.py` (only if `base.py` forces an explicit refusal there)
- new modules under `backend/app/property_watch/` (for example `aql.py`, `fold.py`,
  `keyed_search.py`); `facts.py`/`reference.py` only to extend, never to loosen a guard
- new test files under `backend/tests/unit/`
- `docs/PHASE-G-PROPERTY-WATCH.md`: `[BUILT — PW-2a]` markers and as-built notes on what you built
- `docs/tasks/PW-2a.report.md` (new)

Do not commit. Scratch under `/home/efe/Documents/QRadar-Dash-work/pw2a/`, never `/tmp`.
