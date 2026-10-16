# OPS-2 report

## 1. Files changed

- `backend/requirements.txt` (new): production lock, hashes, Python 3.12, pip-tools; regeneration command in the header.
- `backend/requirements-dev.txt` (new): `dev` extra, constrained by the production lock plus `ruff<0.16`, `mypy<2.4`; hashes; command and constraints in the header.
- `backend/Dockerfile`: `pip install --require-hashes -r requirements.txt` in the cached layer, then `COPY . .` and `pip install --no-deps .` (not `-e`). Non-root user and layer order unchanged.
- `.github/workflows/ci.yml`: installs both locks with `--require-hashes`, then `pip install --no-deps -e .`; `mypy app || true` became `mypy app`.
- `README.md` (Development only): install from the locks, the regeneration command, the update policy (own commit, no bot).
- `backend/pyproject.toml`: ONE line, as the orchestrator allowed: `sqlalchemy>=2.0.36` became `sqlalchemy[asyncio]>=2.0.36`. No other range changed.
- `docs/tasks/OPS-2.report.md`: this report.

## 2. What I did

- Inherited Grok locks checked: Python 3.12, hashes, dev constrained by prod, header command, same pyproject. Valid but unusable as they were (no `greenlet` under SQLAlchemy 2.1.4; ruff 0.17 and mypy 2.4 fail the existing gates). I regenerated both in a `python:3.12-slim` container (bind mount of a dedicated scratch directory, `:Z` only; no `:z`, no chcon, no repo root).
- Method: pip-compile in a 3.12 container, because the host venv is 3.14 and a lock resolved there could differ.
- Fix for the image bug: `sqlalchemy[asyncio]` in pyproject; lock has `sqlalchemy[asyncio]==2.1.4` and `greenlet==3.5.6`.
- Linter pins live in a compile-time `constraints.txt` (outside the repo, copied into the lock header and README), not in pyproject: ruff 0.17.1 adds 3 lint errors on current code. Locked: ruff 0.15.22, mypy 2.3.1.
- Environment built from the locks in `python:3.12-slim`: `pip install --require-hashes -r requirements.txt -r requirements-dev.txt`, then `pip install --no-deps -e .`; `pip check` clean. SQLAlchemy 2.1.4 did NOT break any test (no fallback to 2.0.x needed).
- Image built (`qradar-dash-ops2-test:local`): 890MB; runs as uid 10001; SQLAlchemy 2.1.4 and greenlet 3.5.6 present. Image removed. Other containers untouched.

## 3. Gates (Python 3.12 env built from the locks, `--network host`, DB `qradar_obs_ops2`)

```
No broken requirements found.
ruff check app tests: All checks passed!
mypy app: Found 3 errors in 3 files (checked 240 source files)
  app/repositories/anomaly.py:423: error: Unused "type: ignore" comment  [unused-ignore]
  app/custom_rules/loaders.py:224: error: Variadic tuple unpacking requires a star target  [misc]
  app/custom_rules/engine.py:236: error: Incompatible types in assignment (expression has type "None", variable has type "CustomRuleBreach")  [assignment]
pytest -m "not integration":
  FAILED tests/unit/test_tls_and_credentials.py::TestSSLContext::test_strict_rfc5280_is_on_by_default
  FAILED tests/unit/test_tls_and_credentials.py::TestSSLContext::test_allow_missing_aki_clears_only_strict
  2 failed, 3441 passed, 9 skipped, 1286 deselected, 4 warnings in 52.91s
pytest -m integration (postgresql+asyncpg://test:test@localhost:5433/qradar_obs_ops2):
  1286 passed, 3452 deselected, 1862 warnings in 719.29s (0:11:59)
```

Host (Python 3.14 venv): `ruff check app tests` All checks passed!

Note: pytest addopts has `-q`; I passed `-o addopts=--strict-markers -q -rfE` to get the summary line.

## 4. Open points (need the orchestrator)

1. **Python 3.12 exposes problems the 3.14 baseline hid.** Not caused by the locks, in application and test code that I may not touch:
   - mypy reports 3 errors under 3.12 (listed above). With `|| true` removed, **CI will go red on the Type check step** until those three are fixed (the 0-error baseline holds only on 3.14).
   - 2 unit tests fail on 3.12: `ssl.create_default_context()` only sets `VERIFY_X509_STRICT` by default from Python 3.13. The code under test (TLS context) therefore does not enable strict RFC 5280 on 3.12, which is the interpreter in the production image. This looks like a real security-relevant gap, not just test drift. Either set the flag explicitly in the app, or move image and CI to 3.13+.
2. The image is 890MB.
3. Scratch note: `/home/efe/Documents/QRadar-Dash-work/ops2/src` holds root-owned files from the container and cannot be removed by my user (`src2`, `run3`, `lock3` are chowned to uid 1000).
4. pip-tools header in the locks shows `--no-strip-extras` (the lock keeps `sqlalchemy[asyncio]==`). `pip --require-hashes` accepts that.
