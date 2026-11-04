#!/usr/bin/env bash
# A one-minute check for the integration branch, run after a merge and before its push.
#
#   scripts/merge-check.sh
#
# CI is the whole gate; this only catches what would waste a CI run: a conflict marker, a
# broken migration chain or message catalog, lint or type errors, an entry point that does not
# import. For a full local run (CI down) use scripts/quick-check.sh. Exit code: 0 clean, 1 if
# any check failed (every check still runs), 2 when the tools are missing.
# Environment: QRADAR_VENV = a virtualenv directory (default backend/.venv, else PATH).
set -u
source "$(dirname "${BASH_SOURCE[0]}")/_venv.sh"
cd "$ROOT/backend" || exit 2
failed=0
step() { # step <name> <command...>
  local name="$1"; shift
  local started=$SECONDS
  if "$@" >"${TMPDIR:-/tmp}/merge-check.$$" 2>&1; then
    printf 'ok    %-34s %3ss\n' "$name" "$((SECONDS - started))"
  else
    printf 'FAIL  %-34s %3ss\n' "$name" "$((SECONDS - started))"
    sed 's/^/      /' "${TMPDIR:-/tmp}/merge-check.$$" | tail -40
    failed=1
  fi
  rm -f "${TMPDIR:-/tmp}/merge-check.$$"
}

no_conflict_markers() (
  cd "$ROOT" || return 1
  if [ -n "$(git ls-files -u)" ]; then echo "unmerged paths:"; git ls-files -u | cut -f2 | sort -u; return 1; fi
  # Markers at the start of a line; the exact seven characters git writes.
  if git grep -n -I --untracked -E '^(<{7} |>{7} |<{7}$|>{7}$)' -- . ':(exclude)*.md' ':(exclude)docs'; then return 1; fi
  return 0
)

migration_chain() {
  "$BIN/python" - <<'PY'
import re, sys
from pathlib import Path
rows = {}
for path in sorted(Path("alembic/versions").glob("*.py")):
    text = path.read_text(encoding="utf-8")
    rev = re.search(r'^revision\s*(?::[^=]+)?=\s*["\']([^"\']+)["\']', text, re.M)
    down = re.search(r'^down_revision\s*(?::[^=]+)?=\s*(.+)$', text, re.M)
    if not rev or not down:
        sys.exit(f"{path.name}: no revision / down_revision")
    rows[rev.group(1)] = re.findall(r'["\']([^"\']+)["\']', down.group(1))
downs = {d for ds in rows.values() for d in ds}
heads = sorted(set(rows) - downs)
bad = [f"{r} <- {d} does not rise" for r, ds in rows.items() for d in ds
       if d.isdigit() and r.isdigit() and int(d) >= int(r)]
missing = [f"{r} points at unknown {d}" for r, ds in rows.items() for d in ds if d not in rows]
if len(heads) != 1 or bad or missing:
    sys.exit(f"heads={heads} {bad} {missing}")
print("one head:", heads[0])
PY
}

catalogs_parse() {
  "$BIN/python" - <<'PY'
import json, sys
from pathlib import Path
bad = []
for path in sorted(Path("../frontend/messages/en").glob("*.json")):
    try:
        json.loads(path.read_text(encoding="utf-8"))
    except ValueError as error:
        bad.append(f"{path.name}: {error}")
if bad:
    sys.exit("\n".join(bad))
PY
}

cold_imports() {
  local module
  for module in app.main app.workers.celery_app app.workers.tasks app.cli.doctor app.cli.ariel_load; do
    "$BIN/python" -c "import $module" 2>&1 || { echo "import $module failed"; return 1; }
  done
}

step "no conflict markers"      no_conflict_markers
step "migration chain"           migration_chain
step "frontend/messages/en parse" catalogs_parse
step "ruff check"                "$BIN/ruff" check app tests scripts
step "mypy app"                  "$BIN/mypy" app
step "cold imports (5 modules)"  cold_imports
if [ "$failed" -eq 0 ]; then echo "merge-check: clean (${SECONDS}s)"; else echo "merge-check: FAILED (${SECONDS}s)"; fi
exit "$failed"
