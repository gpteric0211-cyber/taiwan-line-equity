#!/bin/sh
set -eu
cd -- "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
if [ -x python/bin/python3 ]; then
  exec python/bin/python3 -m equity.membership_admin create-owner
fi
exec .venv/bin/python -m equity.membership_admin create-owner
