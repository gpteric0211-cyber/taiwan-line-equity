# GET_SIDE_EFFECTS_AUDIT.md — Phase 2A GET 副作用盤點

> 本文件只是真實原始碼盤點，不代表已完成修正。
> Phase 2A 不修改 `review_src/app.py`、不修改 API 行為、不修改資料庫 schema、不搬移任何 route/function/class。

## Phase 2B-1 status

Phase 2B-1 targets only the highest-risk GET side effects in:

- `GET /api/quotes`
- `GET /api/quotes/watchlist`

Handled items:

- `data_readiness_for_items(..., enqueue_missing=True)` in `api_quotes()` changed to read-only `enqueue_missing=False`.
- `enqueue_data_repair(...)` in `api_quotes()` removed from the GET path.
- `set_status("mis_quote", "loading", ...)` in `api_quotes()` removed from the GET path.
- `api_quotes()` now calls `build_row(..., persist_state=False)` so the quote-list GET path does not write `stock_state_history` through `save_stock_state()`.

`mis_quote` status is still updated outside this GET path by the MIS background flow:

- `fetch_mis_quotes_batch()` sets `mis_quote` to `fresh` or `stale`.
- `mis_quote_daemon()` sets `mis_quote` to `stale` when idle or on failure.

No API response format, endpoint path, database schema, scoring formula, practical status logic, RSI, support/resistance, chip cost, price-volume, or next-day outlook logic was intentionally changed in Phase 2B-1.

## Phase 2B-2 status

Phase 2B-2 targets only confirmed GET side effects in:

- `GET /api/stock/{code}/detail`

Handled items:

- `data_readiness_for_items(..., enqueue_missing=True)` in `api_stock_detail()` changed to read-only `enqueue_missing=False`.
- `api_stock_detail()` now calls `build_row(..., persist_state=False)` so the detail GET path does not write `stock_state_history` through `save_stock_state()`.
- `synthesize_next_day_outlook()` now has optional `persist: bool = True`.
- `api_stock_detail()` calls `synthesize_next_day_outlook(..., persist=False)` so the detail GET path can compute the same payload without writing `next_day_outlook_daily`.

Other call sites keep the default `persist=True`. At the time of this update, the only call site found in current source is `api_stock_detail()`.

No API response format, endpoint path, database schema, scoring formula, practical status logic, RSI, support/resistance, chip cost, price-volume, futures-night signal, or next-day outlook formula/weighting was intentionally changed in Phase 2B-2.

## Phase 2B-3 status

Phase 2B-3 targets only confirmed GET side effects in:

- `GET /api/debug/price-volume/{code}`

Handled items:

- `api_debug_price_volume()` no longer calls `compute_price_volume_score_for_code(code)`.
- The debug GET path no longer directly triggers `ensure_ohlcv_reconstructed_price_volume(...)`.
- The debug GET path no longer writes `price_volume_profile_daily`.
- The debug GET path no longer writes `price_volume_score_daily`.
- The debug GET path no longer triggers `cleanup_price_volume_history(...)`.
- The debug GET path no longer triggers `set_status("price_volume", ...)`.

The endpoint is now read-only:

- It reads diagnostics from existing `price_volume_profile_daily` rows through `price_volume_source_diagnostics(code)`.
- It reads the latest persisted `price_volume_score_daily` row, if one exists.
- If no persisted score exists, it returns `score_status.available=false`, `status=not_computed`, `quality=missing`, `needs_update=true`, and `read_only=true`.
- It points callers to the existing write/update flow: `POST /api/update/price-volume`.

Existing write path found:

- `POST /api/update/price-volume`
- Handler: `api_update_price_volume()`
- Location: `review_src/app.py`

No endpoint path, database schema, price-volume formula, price-volume weighting, coverage-days rule, scoring formula, practical status logic, RSI, support/resistance, chip cost, or next-day outlook logic was intentionally changed in Phase 2B-3.

## Phase 2B-4 status

Phase 2B-4 targets only remaining debug GET side effects in:

- `GET /api/debug/data-readiness`
- `GET /api/debug/ui-completeness`

Handled items:

- `api_debug_data_readiness()` now always calls `data_readiness_for_items(..., enqueue_missing=False)`.
- `auto_repair=1` is acknowledged but never executed by the GET route.
- The response now includes `read_only=true`, `auto_repair_requested`, `auto_repair_executed=false`, and replacement endpoint metadata.
- Existing generic repair/update endpoint found for manual repair:
  - `POST /api/update/ensure-complete`
  - Handler: `api_update_ensure_complete()`
  - Location: `review_src/app.py`
- `api_debug_ui_completeness()` now calls `build_row(..., persist_state=False)` so this debug GET path does not write `stock_state_history` through `save_stock_state()`.

Known residual item intentionally not handled in Phase 2B-4:

- `api_debug_ui_completeness()` still uses existing in-memory memoization through `build_row()` / scoring caches.
- This is retained as a low-to-medium risk residual item because this phase only removes DB write / background repair / repair queue side effects.

No endpoint path, database schema, scoring formula, practical status logic, RSI, support/resistance, chip cost, price-volume, next-day outlook, or general frontend API response was intentionally changed in Phase 2B-4.

# Phase 2C Final GET Side Effect Review

Phase 2C is a documentation-only final review of the Phase 2 target GET endpoints. It does not change Python code, API behavior, database schema, or business logic.

## Fixed items

- `GET /api/quotes` no longer calls `data_readiness_for_items(..., enqueue_missing=True)`.
- `GET /api/quotes` no longer calls `enqueue_data_repair(...)`.
- `GET /api/quotes` no longer calls `set_status("mis_quote", ...)`.
- `GET /api/quotes` uses `build_row(..., persist_state=False)`.
- `GET /api/quotes/watchlist` delegates to `api_quotes()` and inherits the same read-only behavior.
- `GET /api/stock/{code}/detail` no longer calls `data_readiness_for_items(..., enqueue_missing=True)`.
- `GET /api/stock/{code}/detail` uses `build_row(..., persist_state=False)`.
- Detail `next_day_outlook` is still computed, but the GET path no longer persists to `next_day_outlook_daily`.
- `GET /api/debug/price-volume/{code}` no longer computes or writes price-volume tables.
- `GET /api/debug/data-readiness?auto_repair=1` no longer triggers repair; it returns read-only metadata and points to `POST /api/update/ensure-complete`.
- `GET /api/debug/ui-completeness` uses `build_row(..., persist_state=False)`.

## Remaining accepted low / medium risks

The following remaining items are in-memory memoization or cache reads/writes, not confirmed DB writes, repair queues, or background repair scheduling from the Phase 2 target GET endpoints:

- `_row_cache`
- `_score_cache`
- `_practical_cache`
- `yfinance_quote()` in-memory cache
- TAIFEX / futures in-memory cache
- Other pure in-memory memoization

These are accepted as low-to-medium residual risks outside the high-risk Phase 2 scope. They should be reassessed during Phase 5 / Phase 6 when analysis and service orchestration are separated.

## Remaining items

No remaining confirmed high-risk GET DB write / repair queue / background repair side effects found in the Phase 2 target endpoints.

## Phase 2 completion decision

Phase 2 can be considered complete for high-risk GET side effects.

## 搜尋指令

本次至少使用以下搜尋與周邊行號檢查：

```powershell
rg -n "^@app\.get|^@router\.get|enqueue_missing|background_tasks|asyncio.create_task|threading.Thread|BackgroundTasks|create_task|Thread\(" review_src
rg -n "set_status|enqueue_data_repair|warm_row_cache|clear_compute_caches|persist_mis_quote" review_src/app.py
rg -n "data_readiness_for_items|record_next_day_outlook|enqueue_missing=True|warm_row_cache|set_status" review_src/app.py
rg -n "@app\.get|@router\.get" review_src
rg -n "save_stock_state\(|should_write_state_history\(" review_src/app.py
rg -n "INSERT OR REPLACE INTO price_volume_score_daily|price_volume_score_daily|conn.commit\(" review_src/app.py
```

## 1. 所有 GET endpoint 清單

| Endpoint | Handler | 位置 | 初步分類 |
|---|---|---:|---|
| `GET /` | `index()` | `review_src/app.py:4652` | No side effect found |
| `GET /stock/{code}` | `stock_page()` | `review_src/app.py:4657` | No side effect found |
| `GET /detail/{code}` | `detail_page()` | `review_src/app.py:4662` | No side effect found |
| `GET /api/config` | `api_config()` | `review_src/app.py:4667` | No side effect found |
| `GET /api/status` | `api_status()` | `review_src/app.py:4705` | No side effect found |
| `GET /api/watchlist` | `api_watchlist()` | `review_src/app.py:4710` | No side effect found |
| `GET /api/debug/price-volume/{code}` | `api_debug_price_volume()` | `review_src/app.py:4806` | Confirmed side effect |
| `GET /api/debug/volume_units` | `api_debug_volume_units()` | `review_src/app.py:4829` | No side effect found |
| `GET /api/debug/rsi_check/{code}` | `api_debug_rsi_check()` | `review_src/app.py:4837` | No side effect found |
| `GET /api/debug/cost/{code}` | `api_debug_cost()` | `review_src/app.py:4871` | No side effect found |
| `GET /api/stock/{code}/detail` | `api_stock_detail()` | `review_src/app.py:5811` | Confirmed side effect |
| `GET /api/debug/data-readiness` | `api_debug_data_readiness()` | `review_src/app.py:6311` | Confirmed side effect when `auto_repair=1` |
| `GET /api/debug/us-relations/coverage` | `api_us_relations_coverage()` | `review_src/app.py:6322` | No side effect found |
| `GET /api/debug/ui-completeness` | `api_debug_ui_completeness()` | `review_src/app.py:6328` | Confirmed side effect through `build_row()` |
| `GET /api/quotes/watchlist` | `api_quotes_watchlist()` | `review_src/app.py:6413` | Confirmed side effect through delegated `api_quotes()` |
| `GET /api/quotes` | `api_quotes()` | `review_src/app.py:6418` | Confirmed side effect |
| `GET /auth/security-check` | `auth_security_check()` | `review_src/auth/router.py:67` | No side effect found |
| `GET /auth/me` | `me()` | `review_src/auth/router.py:135` | No side effect found |
| `GET /me/watchlist` | `my_watchlist()` | `review_src/auth/router.py:160` | No side effect found |

`review_src/app.py` includes the auth router at `review_src/app.py:141`, so the auth router GET endpoints are part of the running app.

## 2. Confirmed side effect

### 2.1 `GET /api/quotes?mode=watchlist|tw50` auto-enqueues data repair

- Endpoint: `GET /api/quotes`
- Handler: `api_quotes()`
- Route location: `review_src/app.py:6418`
- Trigger location: `review_src/app.py:6434`
- Call: `data_readiness_for_items(items, mode, enqueue_missing=True)`
- Side effect implementation:
  - `data_readiness_for_items()` accepts `enqueue_missing` at `review_src/app.py:6063`.
  - When `enqueue_missing` is true, it starts `threading.Thread(target=background_ensure_complete_data, ...)` at `review_src/app.py:6276-6280`.
- Side effect type:
  - Starts background task.
  - May write DB through `background_ensure_complete_data()`.
  - May call external data sources.
  - May update status/cache after repair.
- Risk: High
- Certainty: Confirmed
- Why it matters: ordinary list reads can schedule multi-source repair work. This violates the “No Side Effects On GET” rule.

### 2.2 `GET /api/quotes?mode=watchlist|tw50` queues FinMind repair after building rows

- Endpoint: `GET /api/quotes`
- Handler: `api_quotes()`
- Route location: `review_src/app.py:6418`
- Trigger location: `review_src/app.py:6464-6466`
- Call: `enqueue_data_repair(repair_codes, ...)`
- Side effect implementation:
  - `enqueue_data_repair()` is defined at `review_src/app.py:468`.
  - It updates `_repair_pending` and starts `threading.Thread(target=_data_repair_worker, ...)` at `review_src/app.py:476-484`.
- Side effect type:
  - Mutates in-memory repair queue.
  - Starts background task.
  - Background task can write DB and update statuses.
- Risk: High
- Certainty: Confirmed
- Why it matters: a read-only quote request can trigger data repair.

### 2.3 `GET /api/quotes?mode=watchlist` writes status when MIS quote is not ready

- Endpoint: `GET /api/quotes`
- Handler: `api_quotes()`
- Route location: `review_src/app.py:6418`
- Trigger location: `review_src/app.py:6467-6472`
- Call: `set_status("mis_quote", "loading", ...)`
- Side effect implementation:
  - `set_status()` writes `fetch_status` and commits in `review_src/core/status.py:11-18`.
- Side effect type:
  - DB write.
- Risk: Medium
- Certainty: Confirmed
- Why it matters: even without repair work, a GET route can write status.

### 2.4 `GET /api/quotes/watchlist` inherits all `GET /api/quotes` side effects

- Endpoint: `GET /api/quotes/watchlist`
- Handler: `api_quotes_watchlist()`
- Route location: `review_src/app.py:6413`
- Delegation: `return api_quotes(mode="watchlist", q=q, force=force)` at `review_src/app.py:6414-6416`
- Side effect type:
  - Same as `GET /api/quotes?mode=watchlist`.
- Risk: High
- Certainty: Confirmed

### 2.5 `GET /api/stock/{code}/detail` auto-enqueues data repair

- Endpoint: `GET /api/stock/{code}/detail`
- Handler: `api_stock_detail()`
- Route location: `review_src/app.py:5811`
- Trigger location: `review_src/app.py:5824`
- Call: `data_readiness_for_items(..., enqueue_missing=True)`
- Side effect implementation:
  - `threading.Thread(target=background_ensure_complete_data, ...)` at `review_src/app.py:6276-6280`.
- Side effect type:
  - Starts background task.
  - May write DB and fetch external data.
- Risk: High
- Certainty: Confirmed
- Why it matters: opening a stock detail page can enqueue background data completion.

### 2.6 `GET /api/stock/{code}/detail` writes next-day outlook history

- Endpoint: `GET /api/stock/{code}/detail`
- Handler: `api_stock_detail()`
- Route location: `review_src/app.py:5811`
- Trigger location:
  - `synthesize_next_day_outlook(...)` is called at `review_src/app.py:5885-5897` when `next_day_outlook_gate().show` is true.
  - `synthesize_next_day_outlook()` calls `record_next_day_outlook(code, out)` at `review_src/app.py:5656`.
- DB write implementation:
  - `record_next_day_outlook()` is defined at `review_src/app.py:5692`.
  - It executes `INSERT OR REPLACE INTO next_day_outlook_daily...` at `review_src/app.py:5696-5711`.
- Side effect type:
  - DB write.
- Risk: High
- Certainty: Confirmed when the display gate allows outlook calculation.
- Why it matters: a detail GET can write a historical prediction/outlook record.

### 2.7 `GET /api/stock/{code}/detail` can write row/practical caches and stock state history through `build_row()`

- Endpoint: `GET /api/stock/{code}/detail`
- Handler: `api_stock_detail()`
- Route location: `review_src/app.py:5811`
- Trigger location: `base_row = build_row(...)` at `review_src/app.py:5832`.
- Cache writes:
  - `build_row()` writes `_row_cache` at `review_src/app.py:4505-4517`.
  - `score_stock_cached()` writes `_score_cache` at `review_src/app.py:3307-3327`.
  - `classify_practical_status_cached()` writes `_practical_cache` at `review_src/app.py:3945-3965`.
- DB write:
  - `_build_row_uncached()` calls `save_stock_state(...)` at `review_src/app.py:4388-4391`.
  - `save_stock_state()` writes `stock_state_history` after 15:00 at `review_src/app.py:3968-3983`.
- Side effect type:
  - Writes in-memory caches.
  - Conditional DB write after 15:00.
- Risk: Medium
- Certainty: Confirmed

### 2.8 `GET /api/debug/price-volume/{code}` writes price-volume profile and score tables

- Endpoint: `GET /api/debug/price-volume/{code}`
- Handler: `api_debug_price_volume()`
- Route location: `review_src/app.py:4806`
- Trigger location:
  - Calls `compute_price_volume_score_for_code(code)` at `review_src/app.py:4809-4810`.
  - `compute_price_volume_score_for_code()` is defined at `review_src/app.py:1385`.
- DB write implementation:
  - `compute_price_volume_score_for_code()` calls `ensure_ohlcv_reconstructed_price_volume(conn, code, limit=240)` at `review_src/app.py:1397`.
  - `ensure_ohlcv_reconstructed_price_volume()` can `INSERT OR REPLACE INTO price_volume_profile_daily...` at `review_src/app.py:1360-1374`.
  - `compute_price_volume_score_for_code()` can `INSERT OR REPLACE INTO price_volume_score_daily...` at `review_src/app.py:1450-1471`.
  - `cleanup_price_volume_history()` can `DELETE FROM price_volume_profile_daily` / `price_volume_score_daily` at `review_src/app.py:1155-1175`.
- Side effect type:
  - DB insert/update/delete.
  - Status write through `set_status("price_volume", ...)` inside reconstruction at `review_src/app.py:1375`.
- Risk: High
- Certainty: Confirmed
- Why it matters: a debug GET can materialize computed price-volume data and clean old rows.

### 2.9 `GET /api/debug/data-readiness?auto_repair=1` can enqueue background repair

- Endpoint: `GET /api/debug/data-readiness`
- Handler: `api_debug_data_readiness()`
- Route location: `review_src/app.py:6311`
- Trigger location: `data_readiness_for_items(items, mode, enqueue_missing=bool(auto_repair))` at `review_src/app.py:6319`.
- Side effect implementation:
  - Same `threading.Thread(target=background_ensure_complete_data, ...)` path at `review_src/app.py:6276-6280`.
- Side effect type:
  - Starts background task when query parameter `auto_repair=1` is used.
  - May write DB and fetch external data.
- Risk: High
- Certainty: Confirmed when `auto_repair=1`; no side effect found for default `auto_repair=0`.

### 2.10 `GET /api/debug/ui-completeness` can write caches and stock state history through `build_row()`

- Endpoint: `GET /api/debug/ui-completeness`
- Handler: `api_debug_ui_completeness()`
- Route location: `review_src/app.py:6328`
- Trigger location: `row = build_row(item, mode)` at `review_src/app.py:6371-6374`.
- Side effect type:
  - Writes `_row_cache`, `_score_cache`, `_practical_cache`.
  - May write `stock_state_history` after 15:00 through `save_stock_state()` as described above.
- Risk: Medium
- Certainty: Confirmed

## 3. Possible side effect

### 3.1 `GET /api/stock/{code}/detail` writes external quote caches

- Endpoint: `GET /api/stock/{code}/detail`
- Handler: `api_stock_detail()`
- Route location: `review_src/app.py:5811`
- Trigger location:
  - `yfinance_quote()` is called in a loop at `review_src/app.py:5872-5878`.
  - `futures_night_signal_for_stock()` is called at `review_src/app.py:5885-5888` when the outlook gate allows display.
- Cache writes:
  - `yfinance_quote()` writes `_us_market_cache` at `review_src/app.py:4939-5007`.
  - `market/futures.py` writes `_taifex_cache` in `fetch_taifex_openapi()` at `review_src/market/futures.py:25-35`.
- Side effect type:
  - In-memory cache write.
  - External network calls.
- Risk: Medium
- Certainty: Confirmed cache write if the data is not already cached; no DB write found in these functions.

### 3.2 GET list/detail build path writes analysis caches

- Endpoints:
  - `GET /api/quotes`
  - `GET /api/quotes/watchlist`
  - `GET /api/stock/{code}/detail`
  - `GET /api/debug/ui-completeness`
- Cache write locations:
  - `_row_cache` at `review_src/app.py:4505-4517`.
  - `_score_cache` at `review_src/app.py:3307-3327`.
  - `_practical_cache` at `review_src/app.py:3945-3965`.
- Side effect type:
  - In-memory cache write.
- Risk: Low to Medium
- Certainty: Confirmed cache write; categorized here because cache writes are intentional memoization, but still violate strict read-only semantics if interpreted literally.

### 3.3 `GET /api/debug/ui-completeness` opens a DB connection without `closing()`

- Endpoint: `GET /api/debug/ui-completeness`
- Handler: `api_debug_ui_completeness()`
- Location: `review_src/app.py:6332`
- Code: `db().execute("SELECT code,name FROM watchlist ORDER BY sort_order, code")`
- Side effect type:
  - No direct DB write found.
  - Possible resource leak due to connection not explicitly closed.
- Risk: Low
- Certainty: Possible operational issue, not a confirmed write-side effect.

## 4. No side effect found

The following GET endpoints were inspected and no DB write, background task, repair task, update task, or file write was found in the visible route body and directly referenced read helpers:

- `GET /` at `review_src/app.py:4652`: returns `static/index.html`.
- `GET /stock/{code}` at `review_src/app.py:4657`: returns `static/detail.html`.
- `GET /detail/{code}` at `review_src/app.py:4662`: returns `static/detail.html`.
- `GET /api/config` at `review_src/app.py:4667`: returns env/config flags.
- `GET /api/status` at `review_src/app.py:4705`: reads `fetch_status` through `get_status()`.
- `GET /api/watchlist` at `review_src/app.py:4710`: reads watchlist.
- `GET /api/debug/volume_units` at `review_src/app.py:4829`: calls `audit_volume_units()`; route comment says audit-only and no migration.
- `GET /api/debug/rsi_check/{code}` at `review_src/app.py:4837`: reads history and compares RSI implementations.
- `GET /api/debug/cost/{code}` at `review_src/app.py:4871`: computes cost diagnostics from existing data.
- `GET /api/debug/us-relations/coverage` at `review_src/app.py:6322`: reads static relation mapping.
- `GET /auth/security-check` at `review_src/auth/router.py:67`: returns security warning metadata.
- `GET /auth/me` at `review_src/auth/router.py:135`: uses `get_current_user()`; dependency reads DB at `review_src/auth/dependencies.py:42-66`.
- `GET /me/watchlist` at `review_src/auth/router.py:160`: calls `list_user_watchlist()`; read-only SQL at `review_src/auth/service.py:229-235`.

## 5. Phase 2B 最小修改建議

> 只寫建議，不代表已實作。

### 5.1 First fix: remove auto repair from normal `GET /api/quotes`

- Modify file: `review_src/app.py`
- Scope:
  - Change `data_readiness_for_items(items, mode, enqueue_missing=True)` at `review_src/app.py:6434` to read-only behavior.
  - Remove or gate `enqueue_data_repair(...)` at `review_src/app.py:6464-6466` from GET path.
  - Remove `set_status("mis_quote", ...)` at `review_src/app.py:6467-6472` from GET path or move it to the MIS daemon / explicit status task.
- Move side effects to:
  - Explicit POST update/repair endpoint, or
  - scheduled background job.
  - `task/`: Unverified / Not found in current source for a dedicated task package.
- Expected API response impact:
  - Should preserve response shape.
  - Missing data should be represented by existing readiness/status fields, without starting repair.
- Data completion impact:
  - Data will no longer auto-repair merely from opening the list.
  - Users may need explicit update/repair POST action or scheduled task.
- Test:
  - `python -m py_compile review_src/app.py`
  - Phase 0 smoke test.
  - Call `GET /api/quotes?mode=watchlist` and confirm no new repair thread/status repair scheduling is triggered.
- Rollback:
  - Restore the original `enqueue_missing=True` and `enqueue_data_repair(...)` lines.
- Risk: Medium

### 5.2 Second fix: make `GET /api/stock/{code}/detail` read-only

- Modify file: `review_src/app.py`
- Scope:
  - Change readiness call at `review_src/app.py:5824` to not enqueue missing data.
  - Prevent `synthesize_next_day_outlook()` from writing through `record_next_day_outlook()` on GET, or add a no-persist option.
  - Prevent `build_row()` / `save_stock_state()` DB writes from detail GET, or move state history persistence to a scheduled/POST path.
- Move side effects to:
  - Explicit POST endpoint for outlook generation / persistence, or scheduled task after data update.
  - `task/`: Unverified / Not found in current source for a dedicated task package.
- Expected API response impact:
  - Should preserve response shape as much as possible.
  - `next_day_outlook` can still be computed read-only, but persistence must be decoupled.
- Data completion impact:
  - Detail page will no longer auto-fill missing data.
- Test:
  - `python -m py_compile review_src/app.py`
  - Phase 0 smoke test.
  - Call `GET /api/stock/2330/detail` and confirm no insert/update into `next_day_outlook_daily` or `stock_state_history`.
- Rollback:
  - Restore `enqueue_missing=True`, direct outlook persistence, and `save_stock_state()` call behavior.
- Risk: Medium to High

### 5.3 Third fix: split debug GET that materializes price-volume data

- Modify file: `review_src/app.py`
- Scope:
  - Make `GET /api/debug/price-volume/{code}` diagnostic-only.
  - Move `compute_price_volume_score_for_code()` persistence into an explicit POST/update endpoint or scheduled price-volume task.
- Move side effects to:
  - existing POST `/api/update/price-volume` path if suitable, or equivalent explicit update endpoint.
  - `task/`: Unverified / Not found in current source for a dedicated task package.
- Expected API response impact:
  - Debug response may need to report cached/latest score only.
  - If no cached score exists, return explicit unavailable/needs-update metadata instead of computing and writing.
- Data completion impact:
  - Debug GET will stop creating profile/score rows.
- Test:
  - `python -m py_compile review_src/app.py`
  - Call `GET /api/debug/price-volume/2330` and confirm it does not insert/update/delete `price_volume_profile_daily` or `price_volume_score_daily`.
  - Confirm explicit POST/update still writes as expected.
- Rollback:
  - Restore `compute_price_volume_score_for_code()` call in the GET route.
- Risk: Medium

### 5.4 Fourth fix: separate read-only row building from cache/persistence

- Modify file: `review_src/app.py`
- Scope:
  - Introduce a read-only row builder or a flag to avoid `_row_cache`, `_score_cache`, `_practical_cache`, and `save_stock_state()` side effects in GET routes.
  - Preserve existing output initially.
- Move side effects to:
  - scheduled cache warm task, explicit update endpoint, or POST route.
- Expected API response impact:
  - No response shape change expected.
- Data completion impact:
  - Might increase GET latency if cache writes are disabled before a replacement cache-warm path exists.
- Test:
  - `python -m py_compile review_src/app.py`
  - Phase 0 smoke test.
  - Compare JSON top-level keys before/after for quotes/detail.
- Rollback:
  - Restore original `build_row()` cache/persistence path.
- Risk: Medium to High

## 6. Highest-risk finding

Highest risk: `GET /api/quotes` and `GET /api/quotes/watchlist` trigger automatic repair work through `data_readiness_for_items(..., enqueue_missing=True)` and `enqueue_data_repair(...)`.

This is highest risk because it is a normal UI polling/read path. A user opening or refreshing the list can start background repair, external data fetches, DB writes, cache changes, and status changes.

## 7. Phase 2B recommended first scope

Recommended first Phase 2B scope:

1. Make `GET /api/quotes` and delegated `GET /api/quotes/watchlist` stop scheduling repair/update work.
2. Keep response shape intact.
3. Keep missing-data readiness metadata visible.
4. Do not touch formulas, scoring, practical status, support/resistance, chip cost, price-volume scoring, or next-day outlook.

Reason: this is the broadest user-facing GET endpoint and the most likely to be called repeatedly by the frontend.
