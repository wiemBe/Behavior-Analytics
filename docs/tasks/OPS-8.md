# Task OPS-8 — `./qradar-dash support-bundle`: a redacted diagnostic archive

Read `AGENTS.md` at the repository root first and follow it exactly. §2 rule 5 (secrets never
leave) matters most. Then read:
- `docs/OPERATIONS-DESIGN.md` **§7, all of it (binding)**;
- §6 (`doctor`).

Then read the operator script `qradar-dash`: `cmd_doctor`, the `dc` helper, `env_get`,
`need_installed` and `admin_exists`'s read-only `psql` pattern. Read its tests
`backend/tests/unit/test_qradar_dash_doctor.py` (stub executables on PATH) and
`backend/app/cli/doctor.py`.

## Decisions already taken by the orchestrator

1. **The scrubber is Python, in the backend image.** A host may have no Python, and a scrubber
   written in sed or awk is too fragile for this job.
   - Write it as pure functions in `backend/app/cli/support_bundle.py`, run as
     `python -m app.cli.support_bundle`.
   - The script runs it through `dc run --rm --no-deps -T backend python -m app.cli.support_bundle …`,
     so it works while the backend service is down (the usual moment for a bundle).
   - The secret values reach it **only on stdin**: never as an argument, never in an
     environment variable, never in a file outside the staging directory.
   - You choose the stdin framing. One tar stream with a reserved member that holds the exact
     secret values is fine. That member is never written to the output.
2. **Fail closed.** No `.tar.gz` is written when:
   - the scrubber cannot run (no backend image, for example), or
   - the scrubber fails.

   Print what failed and what to do. An **unscrubbed archive is never written**, not even for a
   moment, outside the 0700 staging directory.
3. **Any other part that fails** (the database is down, the API is unreachable, a service has
   no logs) does **not** stop the bundle.
   - That part becomes a one-line `<part>.error.txt` in the archive.
   - The bundle is still produced. A support bundle exists for broken systems.
4. **Staging.**
   - A directory with mode 0700 under the backup root, removed by a `trap` on every exit path,
     including a failure or Ctrl-C.
   - The final file is `<backup-root>/support-<UTC yyyymmddThhmmssZ>.tar.gz`, mode **0600**.
   - Print its path.
5. **§7.2's ambiguity, resolved.** The planted SEC token is a UUID. The exact-value pass removes
   that UUID **everywhere**, including the watermark row where it also appears as a
   `correlation_id` value.
   - What must survive is the row itself and the field name `correlation_id`.
   - A **different** UUID used as a `correlation_id` elsewhere in the fixture must also survive.
     This proves UUIDs are not removed wholesale.

## Build

1. **Contents.** Assemble exactly what design §7 lists:
   - **`doctor.txt`:** the output of this script's own `doctor`. Run it in a subshell and
     capture its output; its exit code does not fail the bundle.
   - **`version.txt`:**
     - the state file `.qradar-dash/state.json` if it exists (OPS-6 creates it);
     - `GET /api/v1/version` and the frontend `/api/health` over the loopback ports in `.env`;
     - the image digest of each compose service;
     - `alembic_version`, read with read-only `psql`.
   - **`compose-ps.txt`.**
   - **`watermarks.txt` and `cadence.txt`:** the `collection_watermark` and `search_cadence`
     rows, through read-only `psql`. Select columns by name; never `SELECT *` on a table that
     could gain a secret column.
   - **`config.env`:** `.env` with the §7.1 secret lines **removed**, not masked. The match is
     on the key, case-insensitive:
     - the exact keys `SECRET_KEY`, `ENCRYPTION_KEY`, `ENCRYPTION_KEYS_JSON`,
       `POSTGRES_PASSWORD`, `QRADAR_SEC_TOKEN`, `OIDC_CLIENT_SECRET`, `LLM_API_KEY` and
       `SMTP_PASSWORD`;
     - any key containing `WEBHOOK`, `PASSWORD` or `TOKEN`;
     - the one exception: `QRADAR_API_TOKEN_FILE` is kept, because it is a path.
   - **`logs/<service>.log`:** `docker compose logs --tail=2000 --no-color` for each service.
   - **Never in the bundle:**
     - `.secrets/` and either secrets volume;
     - any dump, `env.bak` or `secrets.tar`;
     - the GHCR token, session cookies, password hashes.
2. **The scrub (§7.1), over every text file after assembly, in this order:**
   1. Remove the exact bytes of every secret the script can read:
      - the removed `.env` values;
      - `.secrets/qradar.sec`, `.secrets/ghcr.token` and `.secrets/tls.key` (take the paths
        from the script and `.env`, and skip a file that does not exist);
      - empty values and values shorter than 8 characters are skipped, so the pass cannot
        destroy ordinary text. Say so in the module docstring.
   2. Replace `(?i)authorization:\s*\S+` and `(?i)\bSEC\b\s*[:=]\s*\S+` with the header name
      and `<redacted>`.
   3. Replace Fernet tokens (`gAAAAA` followed by url-safe base64) with `<redacted>`.
   4. Replace every PEM block whose label contains `PRIVATE KEY` with `<redacted>`. Keep
      `CERTIFICATE` blocks.
   - **Do not** run `app.security.redaction.redact()` over the bundle. §7.1 explains why: it
     masks `consecutive_failures`.
3. **`main()`:** replace the `support-bundle) not_built …` stub. Keep `usage` accurate.

## Tests

1. **`backend/tests/unit/test_support_bundle_scrub.py`:** design §7.2's fixture test, against
   the Python module. No database, no QRadar, no Docker.
   - Plant:
     - a UUID SEC token in a log line that does not contain `SEC`;
     - the same UUID as a `correlation_id` in a watermark row;
     - a different UUID as another `correlation_id`;
     - a Fernet string;
     - `ENCRYPTION_KEY=` and `POSTGRES_PASSWORD=` lines;
     - an `Authorization: Bearer …` header;
     - a `BEGIN PRIVATE KEY` block and a `BEGIN CERTIFICATE` block;
     - a `consecutive_failures` field.
   - Assert, on the **archive bytes** (decompress and read every member):
     - every planted secret is absent, including the reserved secrets member;
     - `correlation_id`, the other UUID, `consecutive_failures` and `BEGIN CERTIFICATE` are
       present.
   - Break each of the four scrub steps once, confirm a test fails, then restore it.
2. **`backend/tests/unit/test_qradar_dash_support_bundle.py`:** the script, with stub
   executables on PATH, as the doctor tests do. The `docker` stub serves `compose ps`, `logs`
   and `exec`/`run`, and records its arguments and its stdin. Cover:
   - the archive is created with mode 0600, and the staging directory is gone afterwards;
   - `config.env` lacks every removed key, and keeps `QRADAR_API_TOKEN_FILE`;
   - no file from `.secrets/` is in the archive;
   - no secret value appears in any **argument** passed to `docker` (only on stdin);
   - a failing scrubber (the stub exits non-zero) leaves **no** `.tar.gz` and no staging dir,
     and the exit code is non-zero;
   - a failing part (the `psql` call fails) still yields a bundle with `<part>.error.txt`.

## Gates

- `shellcheck`, from the image, reading stdin:
  `docker run --rm -i koalaman/shellcheck:stable - < qradar-dash`. If the image cannot run in
  your sandbox, say so; the orchestrator runs it.
- From `backend/`, with `PYTHONPATH=.` and the main checkout's venv:
  - `ruff check app tests`;
  - `mypy app` (0 errors);
  - the two new test files;
  - `tests/unit/test_qradar_dash_doctor.py` and `tests/unit/test_qradar_dash_installer.py`,
    still green;
  - the unit suite.
- No integration run is needed.

## Allowed files

- `qradar-dash`
- `backend/app/cli/support_bundle.py` (new)
- the two new test files
- `backend/tests/unit/test_qradar_dash_installer.py`: only remove `"support-bundle"` from the
  unbuilt-stub tuple in `test_unbuilt_subcommands_exit_non_zero`
- `docs/OPERATIONS-DESIGN.md`: §7 only, for as-built notes, including how §7.2's ambiguity was
  resolved
- `docs/tasks/OPS-8.report.md` (new)

Do not commit. Scratch goes under `/home/efe/Documents/QRadar-Dash-work/ops8/`, never `/tmp`
(set `TMPDIR` to a scratch directory for pytest).
