# AGENTS.md — rules for every coding agent in this repository

You are a coding agent executing **one task** from [`docs/PLANNER.md`](docs/PLANNER.md), handed to
you by the orchestrator. These rules apply to every agent: Codex, opencode models, and Claude
sub-agents. If a rule here conflicts with your task prompt, stop and report the conflict. Do not
pick one yourself.

The project is an on-premises IBM QRadar behavioral-analytics platform. It is strictly
**read-only** against QRadar.
- Backend: FastAPI + SQLAlchemy 2 + Celery, on PostgreSQL + TimescaleDB, in `backend/`.
- Frontend: Next.js, in `frontend/`.
- Start from [`README.md`](README.md). The WAF work is specified in
  [`docs/PHASE-B-WAF-BEHAVIOR.md`](docs/PHASE-B-WAF-BEHAVIOR.md).

## 1. Scope

- Do only your task. Touch only the files your task names. If the task needs another file, say
  which one and why in your report. Do not touch it.
- **Never** commit, push, switch branches, rebase, reset, stash, or run `git checkout -- <path>` /
  `git restore`. The orchestrator owns git.
- Never edit `docs/PLANNER.md` or this file.
- Never delete a file you did not create in this task.

## 2. Architecture rules — never break these

1. **Read-only against QRadar.** The provider interface (`app/providers/base.py`) has no create,
   update or delete methods. Do not add any.
   - One exception, approved by the user on 2026-10-15: on the **lab** console only, a task that
     names it may **add** custom event properties and property expressions through the QRadar
     API, with a throwaway script kept outside the repository. Never through the application's
     code, never on a production console, and never update or delete an existing QRadar object.
2. **AQL runs only on the direct REST/Ariel provider** (`QRadarRestProvider`), never through MCP.
   Never enable MCP `POST`/`DELETE`. Never modify the pinned `qradar-mcp/` gitlink.
3. **Never persist raw events; persist aggregates only.** Raw WAF values (paths, action values,
   response codes, source IPs) live only inside the pure fold functions. Only the bounded
   top-contributor lists defined by the design may leave them.
4. **Never execute frontend-supplied AQL.** Only stored, validated, versioned queries run.
5. **Secrets never go into source, logs, API responses or the browser.**
6. **Phase A tables and enums stay as they are.** `AnomalyType` is persisted in Phase A rows and in
   alert fingerprints. WAF types belong in `WafAnomalyType`.
7. **Migrations are additive.** Never edit a migration that is on `main`. A new migration must
   upgrade, downgrade and upgrade again cleanly, and `alembic check` must report no drift.
8. **Fail closed.** Missing, failed or truncated input yields `INSUFFICIENT_DATA`. It never yields
   a zero, and never `NORMAL`/healthy. A detector never grants itself lower-bound reasoning; its
   verdict scope comes from its declared requirements in `app/anomaly/capabilities.py`.
9. **WAF per-run SQL cost stays constant.** Per-bucket inputs go through the bulk loaders
   (design §29.5, §29.11). Never add a query per endpoint.

## 3. Code style

- Match the surrounding code: its docstring density, naming, idioms and comment voice.
- `ruff check app tests` must be clean. The line length is 100.
- **Never run `ruff format`.** It is not part of this toolchain and would restyle unrelated files.
- `mypy app` must stay clean: the baseline is 0 errors since T-104 (2026-10-15). Do not add any.

## 3a. Frontend quality bar

The user's standing requirement (2026-10-12) is a high-quality frontend, **not AI slop**. This
is a SOC analyst's investigation tool. Judge it against professional observability tools, not
against a template dashboard.

**Read before any UI change:** [`docs/PRODUCT-UX-GUIDELINES.md`](docs/PRODUCT-UX-GUIDELINES.md)
(it is normative) and `frontend/src/app/tokens.css`.

**Build from what exists:**
- Use the existing components (guidelines §8) and the tokens.
- No new colour, spacing or type literal outside `tokens.css`.
- No new UI, icon or CSS framework (Tailwind, MUI, shadcn and the like), unless the task says so.
- No inline styles.

**Never:**
- gradients, glows, glassmorphism, decorative shadows, emoji or decorative icons;
- a row of identical KPI cards holding vanity numbers;
- hero or marketing copy, or text that restates the heading;
- cards nested in cards;
- placeholder, random or invented data;
- a zero drawn where the measurement is missing;
- a chart with no unit on its axis, a library-default chart style (a marker on every point,
  rainbow series), or a pie chart with more than five slices;
- vague copy such as "Something went wrong", or exclamation marks.

**Always:**
- Each view answers one question, and its first screen answers it.
- Numbers carry their unit, use tabular figures, and are right-aligned in tables.
- Times show their timezone.
- Every state is designed, not left to the default:
  - loading;
  - empty, which says what is empty and why;
  - error, which says what failed and what the analyst can do;
  - the honesty states INSUFFICIENT_DATA, PARTIAL, TRUNCATED and NOT_COLLECTED (guidelines §4
    and §6).
- Expected ranges render as a band, not as three lines.
- Pages pass guidelines §9 (no page overflow at 1440×900, 1280×800 and 1024×768) and §10
  (accessibility).
- Vitest tests cover each state.

**Acceptance of a UI task needs screenshots.**
- Capture the changed routes at all three viewports with headless Chrome
  (`/usr/bin/google-chrome`), against a deterministic fixture API, as
  `docs/UI-WORKSTREAM-COMPLETION.md` §4 describes.
- Keep any capture script outside the repository.
- List the PNG paths in your report.

The orchestrator reviews the screenshots before accepting the task. "The tests pass" is not
enough.

## 4. Tests

- **Never weaken, skip, `xfail` or delete an existing test.** If a test seems wrong, say so in your
  report. Do not change it unless your task says to.
- New behavior needs tests. Pin every guard and exit path, not just the happy path.
- After you write a test, break the code on purpose and confirm the test fails. Then restore the
  code.
- Tests must run on any machine and against any test database. Never hard-code an absolute path
  (`/home/...`), your task's database name, or a venv path in a test. Use `sys.executable`, the
  `TEST_DATABASE_URL` the suite gives you (parse it; do not copy its name), and pytest's
  `tmp_path` for files a test writes. (Added 2026-10-16 after an AUTH-1 test passed only on its
  own agent database.)

## 5. Running things

Use the backend virtualenv. Do not install system packages. Always set `PYTHONPATH=.` from
`backend/`: the virtualenv holds an editable install of the main checkout, and without it a
worktree silently imports and tests the main checkout's `app`, not yours.

```bash
cd backend
.venv/bin/ruff check app tests
PYTHONPATH=. ENCRYPTION_KEY=sxvVvbfjEG8mA0m2m6b1cQ2E0N4l7rXqO4uJ6c8zY5A= .venv/bin/pytest -q -m "not integration"
```

**Integration tests need a database of your own.** The schema fixture drops and recreates tables,
so two pytest processes on one database break each other. Use only the `TEST_DATABASE_URL` your
task gives you. If your task gives none, do not run integration tests.

```bash
ENCRYPTION_KEY=sxvVvbfjEG8mA0m2m6b1cQ2E0N4l7rXqO4uJ6c8zY5A= \
PYTHONPATH=. TEST_DATABASE_URL=<the URL from your task> .venv/bin/pytest -q -m integration <paths>
```

For the frontend, Node is at `~/.local/node/bin`. Put it on `PATH` first.

**SELinux is enforcing on this machine and stays enforcing.** That means:
- never `setenforce`, permissive mode, or disabling SELinux;
- never `label:disable` / `label=disable`;
- never the shared-label `:z` volume flag. It opens the directory to every container, and on the
  repository root it would expose `.secrets/`;
- never `chcon`, `restorecon` or `semanage` on anything.

There is one exception, approved by the user on 2026-10-12. The private-label `:Z` flag is
allowed on a bind mount, but only when **all** of these hold:
- the directory is dedicated to that one container;
- it contains no secrets;
- it is **not** the repository root, `.secrets/`, a home directory, or a system path.

Prefer named volumes or tmpfs, which need no label at all. If something else fails because of
SELinux, stop and report it.

## 6. Your final report

End with a short report containing:
1. **Files changed:** each file, with one line on what changed.
2. **What you did:** how it meets the task's acceptance criteria.
3. **Gates:** each command you ran, with its actual result (counts of passed and failed, lint
   output). If you did not run a command, say so.
4. **Open points:** anything not done, uncertain, or outside your scope that you noticed.

Report honestly. A reported failure is useful; a hidden one costs a review cycle.
