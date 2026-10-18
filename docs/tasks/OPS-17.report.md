STATUS: FINAL

# OPS-17 report

## Files changed
- `qradar-dash`: `DEFAULT_IMAGE_PREFIX` (behavior-analytics) and `OLD_IMAGE_PREFIX` constants; all
  four defaults use the new one; new `rewrite_prefix_file` and `migrate_image_prefix`, called in
  `cmd_upgrade` right before `upgrade_pull_target`.
- `docker-compose.prod.yml`: all 8 image lines default to the new prefix.
- `.github/workflows/mirror-images.yml` (new): manual digest-preserving copy of old images.
- `README.md`: the two clone lines. `docs/OPERATIONS.md`: clone lines, new subsection
  "Imaj ön ekinin taşınması" under Yükseltme. `docs/OPERATIONS-DESIGN.md`: image table, rename
  sentence, clone lines.
- `backend/tests/unit/test_qradar_dash_image_prefix.py` (new, 25 tests).

## Gates (from backend/, PYTHONPATH=.)
- `ruff check app tests`: all checks passed.
- `mypy app`: Success, no issues in 256 source files.
- `pytest -m "not integration"`: 4201 passed, 10 skipped (route auth guard), 0 failed.
- `pytest tests/unit/test_qradar_dash_*.py`: 204 passed.
- shellcheck via stdin on `qradar-dash`: no output, exit 0.

## Guard mutations (each restored; script byte-identical to the good copy)
- exact-match check removed: test_custom_prefix_is_left_alone_with_a_note fails.
- tag after rewrite: test_old_prefix_is_tagged_then_rewritten_then_pulled_under_the_new_one fails.
- move before the T-131 check: checkout, newer, failed_outcome, bad_state tests fail (4).
- answers rewrite removed: the old-prefix test and the refused-pull test fail.
- installed-image check removed: the 4 missing-image tests fail.
- duplicate-key check removed: the duplicate test fails.
- Not mutated: the fresh-install default (test asserts the .env and answers carry the new prefix).

## Notes for the next release (`docs/releases/<next>.md`)
Images are now published under `ghcr.io/wiembe/behavior-analytics/<service>` (the repository was
renamed). `./qradar-dash upgrade <next>` moves an existing site by itself: it tags the installed
images locally under the new prefix, then rewrites `QRADAR_DASH_IMAGE_PREFIX` in `.env` and
`IMAGE_PREFIX` in `.secrets/install.answers`, and prints `Image prefix moved: ...`. A custom
registry or mirror prefix is left alone and must carry the new images. Versions 0.1.0 and 0.1.1
stay at the old name; the orchestrator runs the "Mirror images" workflow to copy them.

## Open points
See the final message.
