# Sourced by quick-check.sh and merge-check.sh: finds the Python tools and exports the test
# environment. The virtualenv comes from $QRADAR_VENV (a venv directory), else backend/.venv
# of this checkout, else the python3 on PATH. Nothing here names a host path.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ -n "${QRADAR_VENV:-}" ]; then
  BIN="$QRADAR_VENV/bin"
elif [ -x "$ROOT/backend/.venv/bin/python" ]; then
  BIN="$ROOT/backend/.venv/bin"
else
  BIN="$(dirname "$(command -v python3)")"
fi
[ -x "$BIN/python" ] || { echo "no python in $BIN: set QRADAR_VENV to a virtualenv" >&2; exit 2; }
export PYTHONPATH=.
# A throwaway key for the test runs (the same one CI uses); an existing key wins.
export ENCRYPTION_KEY="${ENCRYPTION_KEY:-sxvVvbfjEG8mA0m2m6b1cQ2E0N4l7rXqO4uJ6c8zY5A=}"
export QRADAR_PROVIDER="${QRADAR_PROVIDER:-mock}"
