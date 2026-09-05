#!/usr/bin/env sh
set -eu

BASE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

if [ -d "$BASE_DIR/app/review_src" ]; then
  if [ -x "$BASE_DIR/python/bin/python" ]; then
    PY="$BASE_DIR/python/bin/python"
  elif [ -x "$BASE_DIR/.venv/bin/python" ]; then
    PY="$BASE_DIR/.venv/bin/python"
  else
    echo "Portable Python environment not found or incomplete." >&2
    echo "Please rebuild the portable package." >&2
    exit 1
  fi
else
  if [ -x "$BASE_DIR/.venv/bin/python" ]; then
    PY="$BASE_DIR/.venv/bin/python"
  elif [ -x "$BASE_DIR/review_src/.venv/bin/python" ]; then
    PY="$BASE_DIR/review_src/.venv/bin/python"
  elif command -v python3 >/dev/null 2>&1; then
    PY="python3"
  elif command -v python >/dev/null 2>&1; then
    PY="python"
  else
    echo "Python not found." >&2
    exit 1
  fi
fi

"$PY" "$BASE_DIR/start_dashboard.py" --host 127.0.0.1 --port 8000
