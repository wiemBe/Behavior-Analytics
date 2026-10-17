# PW-1 report — Phase G foundations

## Files changed

- `backend/app/core/config.py` — added every §G.14.3 `pw_*` setting, field bounds, default-off
  collection/alerting switches, and enabled-family consistency checks.
- `backend/app/models/enums.py` — added `PROPERTY_WATCH` and the separate PW anomaly, scope,
  reference, bucket/pinned measurement, and watch-status vocabularies; Phase A/WAF/IPS enum
  members are unchanged.
- `backend/app/models/property_watch.py` — added the eight aggregate-only PW tables, their keys,
  constraints, lifecycle fields, admission ledger, tracking windows, and sparse metric models.
- `backend/app/models/__init__.py` — registered the PW models and the two Timescale hypertables.
- `backend/app/property_watch/__init__.py` — created the pure PW domain package.
- `backend/app/property_watch/reference.py` — added the `username`-only normalized allowlist,
  one-or-two-property validation, full-tuple pin validation, and the reject-not-escape pinned-value
  pattern.
- `backend/app/property_watch/facts.py` — added the no-I/O search/count, bucket, value/tuple,
  coverage, remainder, measurement and reading contract, including measured-zero handling.
- `backend/alembic/versions/0026_property_watch.py` — added the PW-1 migration: eight tables, two
  hypertables and the idempotent 3,600-second `PROPERTY_WATCH` cadence seed. It does not touch
  `custom_rule`; that remains PW-6.
- `backend/tests/unit/test_pw_enums.py` — pinned enum separation, unchanged Phase A taxonomy,
  additive family membership, model registration and hypertable metadata.
- `backend/tests/unit/test_pw_reference.py` — pinned reference shapes, allowlist exclusions,
  one/two-property bounds and every pinned-value refusal.
- `backend/tests/unit/test_pw_facts.py` — pinned count coercion, NULL/empty remainders,
  truncation, coverage, exact/lower-bound/unknown readings and measured zeros.
- `backend/tests/unit/test_pw_settings.py` — pinned every setting default, every field bound,
  both default-off switches and all cross-setting checks.
- `backend/tests/integration/test_migration_0026.py` — added a no-ini Alembic round trip with
  data carried, seed/hypertable/drift assertions, plus separate zero/two-scope and zero/three-
  property database `CHECK` cases.
- `docs/PHASE-G-PROPERTY-WATCH.md` — marked the PW-1 portions built and recorded precise as-built
  boundaries for later PW tasks.
- `docs/tasks/PW-1.report.md` — this report.

## What I did

I audited rather than accepted the interrupted agent's work. The inherited schema covered the
intended eight tables and correctly kept the PW-6 `custom_rule` change out, but I found and fixed
these concrete problems:

1. `SearchFamily.PROPERTY_WATCH` had been appended after `GEO_COLLECTION`, breaking the existing
   persisted-family ordering guard. I inserted the additive value before the existing final
   `CATCHALL_SENDERS`, `GEO_COLLECTION` pair; every pre-PW member and value remains unchanged.
2. `count_from_search` trusted a present mapping value as an integer. A fractional, boolean,
   string or other invalid count could therefore escape the PW-0 L6 guard. It now calls the common
   strict coercer; integer-valued floats become integers and every other invalid form fails the
   search fact.
3. Six inherited migration/model server defaults violated the repository-wide fresh-schema
   invariant. I removed them from `property_watch` and `pw_value`; SQLAlchemy still supplies the
   designed creation defaults, while the database has no lingering non-timestamp defaults.
4. The inherited new unit files had six Ruff line-length failures. They are corrected without
   formatting unrelated code.
5. Integration coverage, design `[BUILT]` markers/as-built notes, gates and this report were
   missing. They are now present.

The resulting contract is fail-closed: only a completed, untruncated search can prove an absent
key is exact zero; failed, cancelled, timed-out, running or truncated input stays unmeasured.
NULL and empty property values are unparsed, not values. No raw-event table exists, and only
admitted/pinned bounded value text can reach persistence. Both PW switches remain false by
default. `AnomalyType`, `WafAnomalyType` and `IpsAnomalyType` learned no PW member.

## Guard mutations

I broke each PW-1 guard category, ran the focused test, observed failure, then restored the code
with a patch:

- added `category` to the normalized allowlist: 3 failures;
- bypassed pinned-value pattern/printability validation: 14 failures;
- returned exact zero for non-completed searches: 4 failures;
- accepted fractional floats: 2 failures;
- classified NULL as parsed: 1 failure;
- relaxed `pw_min_admission_events` to zero: 1 failure;
- changed `pw_collection_enabled` to default true: 1 failure;
- changed the persisted `PROPERTY_WATCH` value: 1 failure;
- weakened the migration scope `CHECK` to admit zero scope columns: 1 failure;
- widened the migration property `CHECK` to admit three properties: 1 failure.

The restored focused PW unit suite is 187 passed, and the restored migration suite is 5 passed.

## Gates

All commands ran from `backend/` with `PYTHONPATH=.` and the main checkout virtualenv. Database
commands used the assigned `qradar_obs_pw1` database and the supplied test encryption key.

- `alembic heads`: `0026 (head)`.
- `ruff check app tests`: passed, `All checks passed!`.
- `mypy app`: passed, 0 errors in 248 source files.
- `pytest -m "not integration"`: final run passed: **3,860 passed, 10 skipped, 1,308
  deselected, 0 failed**, 5 dependency warnings, 73.22 s. The first full attempt correctly exposed
  the inherited SearchFamily placement through
  `test_geo_task.py::TestRegistration::test_the_family_is_last_in_the_enum`; after the enum-order
  fix, the final run is green.
- Focused PW unit command over the four new files: **187 passed**, 0 failed, 0.53 s.
- New migration suite `pytest -m integration tests/integration/test_migration_0026.py`: **5
  passed**, 0 failed. It performed 0025 → 0026 → 0025 → head, checked both hypertables,
  checked the cadence seed and ran `alembic check` with no drift.
- Required whole integration command `pytest -m integration tests/integration/ -ra`: **1,306
  passed, 2 failed, 0 skipped, 2 deselected**, 11 warnings, 1,133.80 s. No test was skipped on this
  host. The two deselected tests are the unmarked liveness/security-header tests in
  `tests/integration/test_api.py`, excluded by the required `-m integration` selector.
  - `test_migrations.py::test_upgrade_from_phase2_preserves_data_and_adds_phase3` exposed the six
    inherited PW server defaults. After the fix, that exact test plus all five 0026 tests passed:
    **6 passed**, 0 failed, 4.87 s.
  - `test_migration_0025.py::test_0025_disables_the_seed_admin_and_round_trips` still asserts that
    the repository head is `0025`; PW-1 necessarily makes the additive head `0026`. This existing
    test is outside PW-1's allowed files and was not weakened or edited.
- `git diff --check`: passed with no whitespace errors.

## Open points

1. `backend/tests/integration/test_migration_0025.py:64` must be made future-proof by its owner:
   it should pin revision 0025 and its `down_revision`, as its docstring says, rather than assert
   that 0025 remains the repository head forever. Until that out-of-scope existing test is updated,
   the whole integration/CI gate will report one deterministic failure after PW-1 adds 0026.
2. I did not repeat the 18-minute whole integration run after removing the six server defaults.
   I reran the exact test that those defaults failed together with every new 0026 integration test
   (6/6 passed). The remaining full-suite failure is the unchanged 0025 head assertion above; an
   unobserved cross-module interaction from the default removal is unlikely but cannot be claimed
   impossible without another whole run after that existing assertion is repaired.
3. No real QRadar search was run in PW-1; this task adds no provider or AQL execution path. PW-0's
   lab facts are encoded in the pure contract, but PW-10 must still validate end-to-end Ariel cost
   and behavior. If that later validation differs, collection tuning may be required; the schema
   and fail-closed measurement boundary should not be relaxed.

No commit was created.

## Round 1

The orchestrator accepted PW-1 and resolved the first two open points above. This round changed
only the authorized existing assertion in `backend/tests/integration/test_migration_0025.py`:
the test now requires exactly one Alembic head and walks the sole head's revision lineage to
require revision `0025`. Its existing `down_revision == "0024"` assertion and every other line in
the file remain unchanged. This continues to reject a fork while allowing additive descendants
such as `0026`.

For the required mutation, I temporarily changed `0026.down_revision` from `0025` to `0024`.
The focused 0025 migration test failed as intended at `len(heads) == 1`, reporting the two heads
`['0025', '0026']`: **1 failed**. I restored `down_revision = "0025"`; the same focused test then
passed: **1 passed**. The restored migration chain is the only state left in the worktree.

Round 1 gates, run from `backend/` with `PYTHONPATH=.`, the main-checkout virtualenv, the supplied
encryption key, and (where applicable) the assigned `qradar_obs_pw1` database:

- `ruff check app tests`: passed, `All checks passed!`.
- `mypy app`: passed, 0 errors in 248 source files.
- `pytest -q -m "not integration"`: **3,860 passed, 10 skipped, 1,308 deselected, 0 failed**.
  Pytest's configured double-quiet output omitted the numeric footer; a collection-only check
  confirmed 3,870 selected of 5,178 tests, and the completed run displayed 10 skips.
- `pytest -m integration tests/integration/ -ra`: **1,308 passed, 0 failed, 0 skipped, 2
  deselected**, 11 warnings, 910.26 s. No test skipped on this host, so there is no skipped test or
  skip reason to name. The full log is
  `/home/efe/Documents/QRadar-Dash-work/pw1/integ-r1.log`.
- `git diff --check`: passed with no whitespace errors.

Open points 1 and 2 above are therefore closed. Open point 3 remains unchanged: PW-1 has no real
QRadar execution path, and PW-10 must perform the end-to-end Ariel validation. No commit was
created in Round 1.
