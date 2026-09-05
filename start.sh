#!/usr/bin/env sh
set -eu
PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$PROJECT_DIR"
if [ -x python/bin/python3 ]; then
  exec python/bin/python3 -m equity run "$@"
elif [ -x .venv/bin/python ]; then
  exec .venv/bin/python -m equity run "$@"
fi
echo "Run setup.sh once, or use a platform-specific package with a bundled Python runtime." >&2
exit 1
