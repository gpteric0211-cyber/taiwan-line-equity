#!/usr/bin/env sh
set -eu
PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec sh "$PROJECT_DIR/start.sh" --open-browser "$@"
