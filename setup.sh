#!/usr/bin/env sh
set -eu
PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$PROJECT_DIR"
command -v uv >/dev/null 2>&1 || { echo "Install uv or create a Python 3.11-3.13 virtual environment first." >&2; exit 1; }
uv sync --frozen
exec .venv/bin/python -m equity init
