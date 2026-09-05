#!/usr/bin/env sh
set -eu

CLEAN=0
INCLUDE_DB=0
INCLUDE_ENV=0
SKIP_INSTALL=0
PYTHON_BIN=${PYTHON_BIN:-python3}
PYTHON_RUNTIME_DIR=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --clean) CLEAN=1 ;;
    --include-db) INCLUDE_DB=1 ;;
    --include-env) INCLUDE_ENV=1 ;;
    --skip-install) SKIP_INSTALL=1 ;;
    --python) shift; PYTHON_BIN="$1" ;;
    --python-runtime-dir) shift; PYTHON_RUNTIME_DIR="$1" ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
OUTPUT_DIR="$REPO_ROOT/dist/Taiwan50Dashboard_Portable_Mac"
APP_DIR="$OUTPUT_DIR/app"
VENV_DIR="$OUTPUT_DIR/.venv"
VENV_PY="$VENV_DIR/bin/python"
PYTHON_RUNTIME_TARGET="$OUTPUT_DIR/python"
PYTHON_RUNTIME_PY="$PYTHON_RUNTIME_TARGET/bin/python"

if [ "$CLEAN" -eq 1 ] && [ -d "$OUTPUT_DIR" ]; then
  rm -rf "$OUTPUT_DIR"
fi

mkdir -p "$APP_DIR"

copy_tree() {
  src="$1"
  dst="$2"
  rm -rf "$dst"
  mkdir -p "$(dirname "$dst")"
  cp -R "$src" "$dst"
}

copy_tree "$REPO_ROOT/review_src" "$APP_DIR/review_src"
copy_tree "$REPO_ROOT/docs" "$APP_DIR/docs"
copy_tree "$REPO_ROOT/scripts" "$APP_DIR/scripts"

cp "$REPO_ROOT/AGENTS.md" "$APP_DIR/AGENTS.md"
cp "$REPO_ROOT/start_dashboard.py" "$OUTPUT_DIR/start_dashboard.py"
LAUNCHER_SH=$(find "$REPO_ROOT" -maxdepth 1 -type f -name "*.sh" | head -n 1)
cp "$LAUNCHER_SH" "$OUTPUT_DIR/$(basename "$LAUNCHER_SH")"
cp "$REPO_ROOT/review_src/requirements.txt" "$OUTPUT_DIR/requirements.txt"
chmod +x "$OUTPUT_DIR/$(basename "$LAUNCHER_SH")"

find "$APP_DIR" -type d \( -name .git -o -name .venv -o -name venv -o -name env -o -name dist -o -name node_modules -o -name __pycache__ \) -prune -exec rm -rf {} +
find "$APP_DIR" -type f \( -name '*.pyc' -o -name '*.pyo' -o -name '*.log' -o -name '*.zip' -o -name '*.7z' -o -name '*.tar.gz' \) -delete

if [ "$INCLUDE_ENV" -ne 1 ]; then
  find "$APP_DIR" -type f \( -name '.env' -o -name '.env.*' \) -delete
fi
if [ "$INCLUDE_DB" -ne 1 ]; then
  find "$APP_DIR" -type f \( -name '*.db' -o -name '*.sqlite' -o -name '*.sqlite3' -o -name '*.db-shm' -o -name '*.db-wal' \) -delete
fi

INSTALL_STATUS="skipped"
RUNTIME_MODE="venv-based"
if [ -n "$PYTHON_RUNTIME_DIR" ]; then
  rm -rf "$PYTHON_RUNTIME_TARGET"
  cp -R "$PYTHON_RUNTIME_DIR" "$PYTHON_RUNTIME_TARGET"
fi
if [ -x "$PYTHON_RUNTIME_PY" ]; then
  RUNTIME_MODE="true-self-contained"
fi

if [ "$RUNTIME_MODE" = "true-self-contained" ]; then
  if [ "$SKIP_INSTALL" -ne 1 ]; then
    INSTALL_STATUS="ok"
    "$PYTHON_RUNTIME_PY" -m pip install --upgrade pip || INSTALL_STATUS="pip upgrade failed"
    if [ "$INSTALL_STATUS" = "ok" ]; then
      "$PYTHON_RUNTIME_PY" -m pip install -r "$OUTPUT_DIR/requirements.txt" || INSTALL_STATUS="requirements install failed"
    fi
    if [ "$INSTALL_STATUS" != "ok" ]; then
      echo "Package install did not complete: $INSTALL_STATUS" >&2
      exit 1
    fi
  fi
  "$PYTHON_RUNTIME_PY" -c "import sys; print(sys.executable); import uvicorn; import fastapi; print('PORTABLE_PYTHON_OK')"
  (cd "$APP_DIR/review_src" && "$PYTHON_RUNTIME_PY" -c "import sys; print(sys.executable); import app; print('PORTABLE_APP_IMPORT_OK')")
elif [ "$SKIP_INSTALL" -eq 1 ]; then
  if [ ! -x "$VENV_PY" ]; then
    echo "skip-install requested but $VENV_PY does not exist" >&2
    exit 1
  fi
  "$VENV_PY" -c "import sys; print(sys.executable); import uvicorn; import fastapi; print('PORTABLE_VENV_OK')"
  (cd "$APP_DIR/review_src" && "$VENV_PY" -c "import sys; print(sys.executable); import app; print('PORTABLE_APP_IMPORT_OK')")
else
  "$PYTHON_BIN" -m venv "$VENV_DIR"
  INSTALL_STATUS="ok"
  "$VENV_PY" -m pip install --upgrade pip || INSTALL_STATUS="pip upgrade failed"
  if [ "$INSTALL_STATUS" = "ok" ]; then
    "$VENV_PY" -m pip install -r "$OUTPUT_DIR/requirements.txt" || INSTALL_STATUS="requirements install failed"
  fi
  if [ "$INSTALL_STATUS" != "ok" ]; then
    echo "Package install did not complete: $INSTALL_STATUS" >&2
    exit 1
  fi
  "$VENV_PY" -c "import sys; print(sys.executable); import uvicorn; import fastapi; print('PORTABLE_VENV_OK')"
  (cd "$APP_DIR/review_src" && "$VENV_PY" -c "import sys; print(sys.executable); import app; print('PORTABLE_APP_IMPORT_OK')")
fi

cat > "$OUTPUT_DIR/README_使用方式.txt" <<'EOF'
Taiwan50 Dashboard Portable - macOS / Linux

How to run:
1. Open Terminal in this folder.
2. Run the dashboard .sh launcher in this folder.
3. Open http://127.0.0.1:8000 if the browser does not open automatically.

Build this package on the target platform. Apple Silicon and Intel virtual
environments are not guaranteed to be interchangeable.
EOF

GIT_HASH=$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)
cat > "$OUTPUT_DIR/BUILD_INFO.txt" <<EOF
Name: Taiwan50Dashboard_Portable_Mac
BuiltAt: $(date)
GitHash: $GIT_HASH
PythonExe: $PYTHON_BIN
RuntimeMode: $RUNTIME_MODE
InstallStatus: $INSTALL_STATUS
IncludeDb: $INCLUDE_DB
IncludeEnv: $INCLUDE_ENV
EOF

if [ "$RUNTIME_MODE" = "true-self-contained" ]; then
  "$PYTHON_RUNTIME_PY" "$OUTPUT_DIR/start_dashboard.py" --dry-run
elif [ -x "$VENV_PY" ]; then
  "$VENV_PY" "$OUTPUT_DIR/start_dashboard.py" --dry-run
fi

echo "Portable package created:"
echo "  $OUTPUT_DIR"
