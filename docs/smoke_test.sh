#!/usr/bin/env bash
set -u

BASE_URL="${BASE_URL:-http://localhost:8000}"
SMOKE_STOCK_CODE="${SMOKE_STOCK_CODE:-2330}"
PYTHON_BIN="${PYTHON:-python}"

failures=0

pass() {
  printf 'PASS %s\n' "$1"
}

fail() {
  printf 'FAIL %s\n' "$1"
  failures=$((failures + 1))
}

check_py_compile() {
  printf 'Checking py_compile: review_src/app.py\n'
  if "$PYTHON_BIN" -m py_compile review_src/app.py; then
    pass "python -m py_compile review_src/app.py"
  else
    fail "python -m py_compile review_src/app.py"
  fi
}

http_get() {
  local path="$1"
  local expect_json="$2"
  local label="$3"
  local url="${BASE_URL}${path}"
  local body_file
  body_file="$(mktemp)"

  local status
  status="$(curl -sS -L -o "$body_file" -w '%{http_code}' "$url" 2>/tmp/smoke_curl_error.txt)"
  local curl_code=$?

  if [ "$curl_code" -ne 0 ]; then
    fail "$label - server unreachable or curl failed: $(cat /tmp/smoke_curl_error.txt)"
    rm -f "$body_file"
    return
  fi

  case "$status" in
    2??) ;;
    400|404)
      fail "$label - HTTP $status (route or parameter issue)"
      rm -f "$body_file"
      return
      ;;
    5??)
      fail "$label - HTTP $status (server error)"
      rm -f "$body_file"
      return
      ;;
    *)
      fail "$label - HTTP $status"
      rm -f "$body_file"
      return
      ;;
  esac

  if [ "$expect_json" = "json" ]; then
    if "$PYTHON_BIN" - "$body_file" <<'PY'
import json
import sys
with open(sys.argv[1], "r", encoding="utf-8") as f:
    json.load(f)
PY
    then
      pass "$label - HTTP $status JSON parsed"
    else
      fail "$label - HTTP $status but response is not JSON"
    fi
  else
    pass "$label - HTTP $status"
  fi

  rm -f "$body_file"
}

printf 'Smoke test baseline\n'
printf 'BASE_URL=%s\n' "$BASE_URL"
printf 'SMOKE_STOCK_CODE=%s\n' "$SMOKE_STOCK_CODE"
printf '\n'

check_py_compile
http_get "/" "html" "GET /"
http_get "/api/quotes?mode=watchlist" "json" "GET /api/quotes?mode=watchlist"
http_get "/api/quotes?mode=tw50" "json" "GET /api/quotes?mode=tw50"
http_get "/api/stock/${SMOKE_STOCK_CODE}/detail" "json" "GET /api/stock/${SMOKE_STOCK_CODE}/detail"

printf '\n'
if [ "$failures" -eq 0 ]; then
  printf 'PASS smoke test completed\n'
  exit 0
fi

printf 'FAIL smoke test completed with %s failure(s)\n' "$failures"
exit 1

