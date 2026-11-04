#!/usr/bin/env bash
# The full local gate, for when CI is down or before a risky push: lint, types, the i18n
# artifact, cold imports, the whole unit suite on every core, the integration files you name
# (or the ones your branch changed), the frontend type check and tests.
#
#   scripts/quick-check.sh [-d TEST_DATABASE_URL] [-b BASE] [--no-frontend] [integration files...]
#
#   -d URL   the database for the integration files (default: $TEST_DATABASE_URL). It must be
#            one that only you use: the integration schema fixture drops and recreates tables.
#            Without a URL the integration step is skipped and the run says so.
#   -b BASE  with no files named, run the integration files that changed in BASE..HEAD
#            (default: the merge base with origin/main, else HEAD~1). A changed shared file
#            (factories, fakes, conftest) selects nothing by itself: name the files you need.
#   files    integration test files (tests/integration/test_x.py), relative to backend/ or the
#            repository root.
#
# Environment: QRADAR_VENV = a virtualenv directory (default backend/.venv, else PATH);
#              NODE_BIN = a directory with node/npm (default: the PATH). The faster, shorter
#              check for a merge into the integration branch is scripts/merge-check.sh.
# Exit code: 0 when every step passed or was skipped on purpose, 1 otherwise.
set -u
source "$(dirname "${BASH_SOURCE[0]}")/_venv.sh"
db="${TEST_DATABASE_URL:-}"; base=""; frontend=1; files=()
while [ $# -gt 0 ]; do
  case "$1" in
    -d) db="$2"; shift 2 ;;
    -b) base="$2"; shift 2 ;;
    --no-frontend) frontend=0; shift ;;
    -h|--help) sed -n '2,19p' "${BASH_SOURCE[0]}"; exit 0 ;;
    -*) echo "unknown option $1" >&2; exit 2 ;;
    *) files+=("${1#backend/}"); shift ;;
  esac
done
cd "$ROOT/backend" || exit 2
failed=0; started=$SECONDS
step() { # step <name> <command...>
  local name="$1" t=$SECONDS; shift
  if "$@"; then echo "ok    $name ($((SECONDS - t))s)"; else echo "FAIL  $name ($((SECONDS - t))s)"; failed=1; fi
}

if [ ${#files[@]} -eq 0 ]; then
  if [ -z "$base" ]; then
    base="$(git merge-base HEAD origin/main 2>/dev/null || git rev-parse HEAD~1)"
  fi
  while IFS= read -r path; do
    case "$path" in backend/tests/integration/test_*.py) [ -f "$ROOT/$path" ] && files+=("${path#backend/}") ;; esac
  done < <(git diff --name-only "$base"..HEAD)
fi

step "ruff check" "$BIN/ruff" check app tests scripts
step "mypy app" "$BIN/mypy" app
step "i18n artifact" "$BIN/python" -m app.i18n.artifact --check
for module in app.main app.workers.celery_app app.workers.tasks app.cli.doctor app.cli.ariel_load; do
  step "import $module" "$BIN/python" -c "import $module"
done
# xdist: one worker per core. The unit suite never touches a database.
step "unit tests (-n auto)" "$BIN/pytest" -n auto -m "not integration" -p no:cacheprovider

if [ ${#files[@]} -eq 0 ]; then
  echo "skip  integration: no integration file named or changed in $base..HEAD"
elif [ -z "$db" ]; then
  echo "skip  integration: no database (-d URL or TEST_DATABASE_URL); would run: ${files[*]}"
else
  # Serial on purpose: the schema fixture drops tables.
  step "integration (${#files[@]} files)" env TEST_DATABASE_URL="$db" \
    "$BIN/pytest" -m integration -p no:cacheprovider "${files[@]}"
fi

if [ "$frontend" -eq 1 ]; then
  [ -n "${NODE_BIN:-}" ] && export PATH="$NODE_BIN:$PATH"
  if [ -d "$ROOT/frontend/node_modules" ] && command -v npm >/dev/null; then
    cd "$ROOT/frontend" || exit 2
    step "frontend typecheck" npm run typecheck
    step "frontend tests" npm test
  else
    echo "skip  frontend: no node_modules or no npm on PATH (set NODE_BIN)"
  fi
fi
echo "quick-check: $([ "$failed" -eq 0 ] && echo clean || echo FAILED) ($((SECONDS - started))s)"
exit "$failed"
