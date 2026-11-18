#!/bin/sh
# ClipForge AI launcher (macOS/Linux): starts the app and opens the dashboard.
ROOT="$(cd "$(dirname "$0")" && pwd)"
PY="$ROOT/backend/.venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "ClipForge AI: the Python environment is missing at backend/.venv"
  exit 1
fi
cd "$ROOT" && exec "$PY" -m clipforge "$@"
