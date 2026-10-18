# Task OPS-17 — The image prefix moves to `ghcr.io/wiembe/behavior-analytics`

Read `AGENTS.md` at the repository root first and follow it exactly, §4 above all (test the
command the operator runs, on every exit path). Then read `docs/OPERATIONS.md` (the runbook, in
Turkish) §install and §upgrade, `docs/OPERATIONS-DESIGN.md` §2 (images) and the T-131 and T-132
reports.

**Why (D-26, the user, 2026-10-18):** the user renamed the GitHub repository from
`wiemBe/QRadar-Dashboard` to `wiemBe/Behavior-Analytics`. `release.yml` names the images
`ghcr.io/${GITHUB_REPOSITORY,,}/<service>` (line 91), so **the next release is pushed to
`ghcr.io/wiembe/behavior-analytics/*`**. Every installed site has
`QRADAR_DASH_IMAGE_PREFIX=ghcr.io/wiembe/qradar-dashboard` in `.env` (written from
`ANS[IMAGE_PREFIX]`, `qradar-dash:206` and `:573`) and in `.secrets/install.answers`, and
`qradar-dash` falls back to that old prefix at `:1901`, `:1918`, `:2012`, as does every image line
of `docker-compose.prod.yml`. Without this task, `upgrade <next>` refuses with "target images are
unavailable". The user chose to **move to the new name** (not to keep publishing under the old
one). The released `0.1.0` and `0.1.1` images stay where they are.

Facts from the code you build on (check each before relying on it):
- `upgrade X` runs from the checkout of X (T-131), so the **next release's** script performs the
  move; the old scripts are never changed.
- Rollback re-tags the recorded image **IDs** under the **current** `.env` prefix
  (`tag_previous_images`, `qradar-dash:2009`) and does not restore `.env` (`perform_rollback`,
  `:2043`).
- An install re-run of the same version keeps `.env` and the answers (OPS-11).

## Build

1. **New default prefix** `ghcr.io/wiembe/behavior-analytics` in `qradar-dash` (`:206`, `:1901`,
   `:1918`, `:2012`, and any other place you find) and in every image line of
   `docker-compose.prod.yml`. Keep the old value as one named constant in `qradar-dash`
   (`OLD_IMAGE_PREFIX`), used only by the move below.
2. **The move, inside `upgrade`** (a small function, e.g. `migrate_image_prefix`), run **after**
   every refusal that needs no registry (the T-131 version checks, the state-file checks) and
   **before** the target's release notes are read or anything is pulled:
   - It acts only when `.env`'s `QRADAR_DASH_IMAGE_PREFIX` is exactly `OLD_IMAGE_PREFIX`, or the
     key is absent (the old compose default applied). Any other prefix is an operator's own
     registry or mirror: left untouched, with one line saying the mirror must carry the target's
     images under that prefix.
   - First it tags, locally, each image of the **installed** version that exists under the old
     prefix (`postgres`, `backend`, `frontend`, and `proxy` / `qradar-mcp` when their profile is
     on) under the new prefix, so the running stack can be recreated offline. A required image
     missing locally fails closed: the upgrade is refused, nothing is changed, and the message
     names the image.
   - Then it rewrites `QRADAR_DASH_IMAGE_PREFIX` in `.env` (temp file + `mv`, mode 600, as
     `write_env` does) and the `IMAGE_PREFIX` entry of `.secrets/install.answers` when that file
     exists (same care; keep its mode).
   - It prints one line: `Image prefix moved: ghcr.io/wiembe/qradar-dashboard -> ghcr.io/wiembe/behavior-analytics (the repository was renamed).`
     (through `say`; the script keeps no upgrade log file).
   - A later failure of the upgrade (pull refused, auto-rollback) leaves the move in place; the
     state stays consistent because of the local tags. Say so in the runbook.
   - `upgrade --offline`: the move runs the same way; the target images must be loaded under the
     new prefix.
3. **`.github/workflows/mirror-images.yml`** (new, `workflow_dispatch` only): copies
   `ghcr.io/wiembe/qradar-dashboard/<service>:<tag>` to `ghcr.io/${GITHUB_REPOSITORY,,}/<service>:<tag>`
   for the five services and the tags given as an input (default `0.1.0 0.1.1`), so an old version
   can still be pulled under the new name (a same-version install re-run after a rollback pulls the
   old version under the **new** prefix). Requirements: `permissions: contents: read, packages:
   write`; every action pinned by commit SHA as `release.yml` does (reuse its SHAs); the copy keeps
   the digest (verify with `docker buildx imagetools inspect` on source and target and fail the job
   when they differ); refuse when source and target prefix are equal. It pushes nothing else.
   It cannot run before it is on `main` (GitHub lists `workflow_dispatch` workflows from the
   default branch): the orchestrator runs it at the next release.
4. **Docs:**
   - `README.md` §Installation clone lines (`:239-240`): the new URL and directory name. Nothing
     else in the README (T-133 edits §License).
   - `docs/OPERATIONS.md` (Turkish, the runbook): clone URLs (`:42-52`), image names, and in
     §upgrade a short paragraph on the move (when it happens, the one line it prints, a custom
     registry is untouched and must carry the new images, a rollback keeps the new prefix).
   - `docs/OPERATIONS-DESIGN.md`: the image table (`:191-200`) and the clone lines (`:351-352`);
     one sentence at `:31` that the repository was renamed on 2026-10-18.
   - Leave the historical task specs and reports (`docs/tasks/OPS-*.md`) as they are.
   - Write in your report the paragraph the next release's notes (`docs/releases/<next>.md`) must
     carry; do not create that file.

## Tests (new file `backend/tests/unit/test_qradar_dash_image_prefix.py`, the harness of
`test_qradar_dash_upgrade.py`)

Run the **command** (`./qradar-dash upgrade …` against the stubbed Docker), not only the function:
- old prefix in `.env` → local tags made for the installed version's images, `.env` and
  `install.answers` rewritten (modes kept), the line printed, the pull uses the new
  prefix;
- key absent → the same, the key written;
- a custom prefix (`registry.example/qradar`) → nothing tagged or rewritten, the note printed;
- a T-131 refusal (checkout version ≠ target) → `.env` and answers byte-identical;
- an installed image missing locally → refused, nothing rewritten, the image named;
- the move followed by a refused pull, and by an auto-rollback → the prefix stays new and the
  rollback tags the old IDs under the new prefix;
- `--offline` with the target loaded under the new prefix → succeeds; under the old prefix only →
  refused before anything is stopped;
- the `proxy` and `mcp` profiles on and off;
- a fresh `install` writes the new prefix.

Break each guard once (the exact-match check, the tag-before-rewrite order, the refusal order, the
answers rewrite) and confirm a test fails; restore. Run `shellcheck` through stdin:
`docker run --rm -i koalaman/shellcheck:stable - < qradar-dash` (no bind mount).

## Gates (from `backend/`, the main checkout's venv, `PYTHONPATH=.`)

`ruff check app tests`, `mypy app` (0), the whole unit suite (`-m "not integration"`), and every
`test_qradar_dash_*.py` file. No integration database is needed.

## Allowed files

`qradar-dash`, `docker-compose.prod.yml`, `.github/workflows/mirror-images.yml` (new),
`README.md` (the two clone lines), `docs/OPERATIONS.md`, `docs/OPERATIONS-DESIGN.md`,
`backend/tests/unit/test_qradar_dash_image_prefix.py` (new), `docs/tasks/OPS-17.report.md` (new).

Do not commit. Scratch under `/home/efe/Documents/QRadar-Dash-work/ops17/`, never `/tmp`.

## Later, not in this task

A fresh-VM check at the next release (OPS-11 style): v0.1.1 installed from the old prefix →
checkout of the next tag → `upgrade` → the move → `rollback` → same-version install re-run.
