# DB_REPOSITORY_AUDIT.md - Phase 4A DB / Repository Audit

Phase 4A is documentation-only. It audits current database schema, read/write
locations, and repository extraction candidates before any repository code is
created. No Python code, API behavior, database schema, route, function, class,
or business logic is changed by this file.

## Commands Used

Required searches were run:

```bash
rg -n "CREATE TABLE|CREATE INDEX|ALTER TABLE|SELECT |INSERT |UPDATE |DELETE |INSERT OR REPLACE|conn.commit|db\(|sqlite|fetch_status|watchlist|stock_state_history|next_day_outlook_daily|price_volume_score_daily|price_volume_profile_daily" review_src
rg -n "fetch_status|set_status\(|get_status\(" review_src
rg -n "from .*core.status|import .*set_status|import .*get_status|core.status" review_src
rg -n "CREATE TABLE.*fetch_status|CREATE TABLE IF NOT EXISTS.*fetch_status|INSERT.*fetch_status|UPDATE.*fetch_status|DELETE.*fetch_status|SELECT.*fetch_status" review_src
```

Additional focused checks were run:

```bash
rg -n "^@app\.get|^@app\.post|^@app\.delete|^@app\.patch|def api_debug_data_readiness|auto_repair|enqueue_data_repair|def api_stock_detail|def api_quotes" review_src/app.py
rg -n "def .*price_volume|price_volume_profile_daily|price_volume_score_daily|def calculate_price_volume|def compute_price_volume|record_next_day_outlook|next_day_outlook_daily|stock_state_history|def save_stock_state|def previous_stock_state|def recent_state_counts" review_src/app.py
rg -n "CREATE TABLE|CREATE INDEX|ALTER TABLE|INSERT|UPDATE|DELETE|SELECT|conn.commit|db\(" review_src/auth review_src/core review_src/adapter
rg -n "^\s*set_status\(" review_src/app.py
rg -n "^\s*.*get_status\(" review_src/app.py
```

## 1. DB Schema / Table List

### Core application tables

All core application table definitions below are in `review_src/core/db.py`.

| Table | Definition | Purpose observed from source | Risk | Phase 4B first candidate? |
| --- | --- | --- | --- | --- |
| `watchlist` | `review_src/core/db.py:26` | Local unauthenticated watchlist. Read by quote/detail/update flows; written by `/api/watchlist`. | Medium: user-facing list and repair preload trigger. | No, affects UI and POST behavior. |
| `eod_price` | `review_src/core/db.py:32` | TWSE EOD latest market rows. Read by row/detail/readiness/scoring flows. | Medium/high: price correctness. | No, heavily used by analysis. |
| `valuation` | `review_src/core/db.py:48`; ALTER at `review_src/core/db.py:246`, `review_src/core/db.py:248` | PE/PB/dividend/eps values. Read by row/detail/readiness. | Medium/high: visible valuation numbers. | No, user-facing financial values. |
| `history_price` | `review_src/core/db.py:60`; ALTER at `review_src/core/db.py:251`, `review_src/core/db.py:253`; UPDATE at `review_src/core/db.py:255` | OHLCV history for RSI, scoring, support/resistance, readiness, price-volume fallback. | High: core analysis input. | No, too central. |
| `institution_daily` | `review_src/core/db.py:74` | Foreign/trust/dealer flows for chip cost, chip light, outlook. | High: chip analysis input. | No, affects chip conclusions. |
| `margin_daily` | `review_src/core/db.py:84` | Margin/short balances and deltas for chip/risk logic. | High: chip/risk analysis input. | No, affects warnings. |
| `lending_daily` | `review_src/core/db.py:95` | Lending data table. | Medium; limited usage found in this audit. | Not first; usage needs separate focused audit. |
| `fetch_status` | `review_src/core/db.py:104` | Source/update status messages for UI/debug/status metadata. | Low/medium: status metadata, not scoring formula. | Yes, best first candidate. |
| `corporate_actions` | `review_src/core/db.py:110` | Dividend/split/corporate action metadata for adjusted logic and chip-cost engine. | High: affects adjusted cost estimates. | No. |
| `foreign_shareholding` | `review_src/core/db.py:121` | Foreign holding anchor for chip cost engine. | High: chip-cost input. | No. |
| `stock_state_history` | `review_src/core/db.py:129` | Persisted daily practical status/state history. | Medium/high: state history; write side was part of GET side-effect cleanup. | No. |
| `mis_quote_snapshot` | `review_src/core/db.py:142`; indexes at `review_src/core/db.py:156`, `review_src/core/db.py:158` | Intraday MIS snapshots for watchlist quote fallback/history. | Medium/high: intraday price source. | No, needs quote correctness tests. |
| `price_volume_profile_daily` | `review_src/core/db.py:160` | Price-volume profile rows. | High: price-volume analysis input. | No. |
| `price_volume_score_daily` | `review_src/core/db.py:180` | Computed price-volume scores. | High: price-volume/referee input. | No. |
| `next_day_outlook_daily` | `review_src/core/db.py:214` | Persisted next-day outlook history. | Medium/high: outlook history and GET side-effect history. | No. |

Core DB connection and bootstrap:

- `db()` is defined in `review_src/core/db.py:10`.
- `init_db()` is defined in `review_src/core/db.py:18`.
- Main schema transaction commits at `review_src/core/db.py:264`.

### Auth tables

Auth schema is separate from core market schema:

| Table | Definition | Purpose observed from source | Risk | Phase 4B first candidate? |
| --- | --- | --- | --- | --- |
| `users` | `review_src/auth/models.py:5` | Registered users. | High: auth/security. | No. |
| `email_verifications` | `review_src/auth/models.py:16` | Registration/password reset verification records. | High: auth/security. | No. |
| `user_watchlist` | `review_src/auth/models.py:26` | Per-user watchlist after login. | High: user data. | No. |
| `login_attempts` | `review_src/auth/models.py:37` | Login/rate-limit audit. | High: security. | No. |
| `auth_sessions` | `review_src/auth/models.py:46` | Auth sessions. | High: security. | No. |

Auth indexes are defined at `review_src/auth/models.py:57-66`.

### Existing repository package status

- `review_src/repository/`: Unverified / Not found in current source.

## 2. Reads Per Table

This audit lists verified primary read locations. The application still has many
inline SQL statements in `review_src/app.py`; Phase 4B should extract one
repository boundary at a time and preserve behavior.

### `fetch_status`

- `get_status()` reads all statuses in `review_src/core/status.py:24-27`.
- `GET /api/status` returns `get_status()` in `review_src/app.py:4707-4709`.
- `price_volume_source_diagnostics()` reads `get_status().get("price_volume")`
  in `review_src/app.py:1120`.
- `_build_row_uncached()` reads `get_status().get("corporate_actions")` in
  `review_src/app.py:4366`.
- `api_stock_detail()` reads `get_status().get("corporate_actions")` in
  `review_src/app.py:5719`.
- `api_quotes()` includes status metadata via `get_status()` at
  `review_src/app.py:6348` and `review_src/app.py:6366`.
- `background_price_volume_update()` reads `get_status().get("price_volume")`
  at `review_src/app.py:4609`.

Impact: status metadata only. It can affect UI status messages and debug output,
but should not own scoring formulas or the referee conclusion.

### `watchlist`

- `mis_quote_daemon()` reads watchlist codes in `review_src/app.py:800-802`.
- `/api/watchlist` reads rows in `review_src/app.py:4712-4714`.
- `/api/update/chip` reads watchlist rows in `review_src/app.py:4763-4765`.
- `/api/update/ensure-complete` reads watchlist rows in
  `review_src/app.py:4781-4783`.
- `/api/update/price-volume` reads watchlist rows in
  `review_src/app.py:4800-4802`.
- `api_stock_detail()` checks watchlist membership in
  `review_src/app.py:5699`.
- `api_quotes()` reads watchlist items in `review_src/app.py:6315-6316`.
- `api_debug_data_readiness()` reads watchlist items in
  `review_src/app.py:6194-6196`.

Impact: visible rows, update target selection, intraday MIS daemon target list.

### Price and valuation tables

- Latest verified market date reads `eod_price` and `history_price` at
  `review_src/app.py:308-309`.
- `source_trace_for_code()` reads `eod_price`, `history_price`, `valuation`,
  `institution_daily`, and `margin_daily` at `review_src/app.py:377-381`.
- `source_trace_for_code_v2()` additionally reads `foreign_shareholding` at
  `review_src/app.py:419-424`.
- `_build_row_uncached()` reads `eod_price`, `history_price`, and `valuation` at
  `review_src/app.py:4245-4248`.
- `api_stock_detail()` reads `valuation`, `eod_price`, and `history_price` at
  `review_src/app.py:5694-5697`.
- `_latest_price_and_history_for_readiness()` reads `eod_price` and
  `history_price` at `review_src/app.py:5897-5898`.

Impact: quote rows, detail page, readiness, RSI/scoring/support/resistance.

### Institution / margin / foreign holding tables

- `latest_history_dates()` reads `history_price` at `review_src/app.py:2241-2243`.
- `calc_cost()` reads net fields by table/field at `review_src/app.py:2283-2284`.
- chip-cost input fetch reads `history_price`, `institution_daily`,
  `foreign_shareholding`, and `corporate_actions` at
  `review_src/app.py:2397-2430`.
- `chip_periods()` reads `institution_daily` and `margin_daily` at
  `review_src/app.py:2694-2701`.
- `chip_light()` reads recent institution/margin rows at
  `review_src/app.py:3240-3242`.
- `component_states()` counts institution/margin rows at
  `review_src/app.py:3270-3272`.
- `build_institution_info()` reads 20 recent institution rows at
  `review_src/app.py:3635-3637`.
- `margin_pressure()` reads latest margin row at `review_src/app.py:3713-3714`.

Impact: chip-cost estimates, chip warnings, status reasons, next-day outlook
chip factor.

### `stock_state_history`

- `previous_stock_state()` reads previous status at `review_src/app.py:3990-3991`.
- `recent_state_counts()` reads recent states at `review_src/app.py:3996-3997`.

Impact: trend/status history display or internal status context.

### Price-volume tables

- `price_volume_source_diagnostics()` reads profile counts and latest profile at
  `review_src/app.py:1099-1116`.
- `ensure_ohlcv_reconstructed_price_volume()` reads history and existing profile
  status at `review_src/app.py:1324-1331`.
- `price_volume_profiles_for_code()` reads usable profile rows at
  `review_src/app.py:1380-1381`.
- `compute_price_volume_score_for_code()` reads history at
  `review_src/app.py:1390-1391` and writes score later.
- `latest_price_volume_summary()` exists at `review_src/app.py:1476`.
- `/api/debug/price-volume/{code}` reads score rows at `review_src/app.py:4808-4814`.

Impact: price-volume scoring and detail/debug blocks. High risk.

### `mis_quote_snapshot`

- `get_mis_quote_latest_snapshot()` reads latest snapshot at
  `review_src/app.py:761-789`.

Impact: watchlist intraday quote fallback. Medium/high risk due price accuracy.

### Auth tables

- `auth/dependencies.py` reads users in `review_src/auth/dependencies.py:34-36`.
- `auth/service.py` reads/writes auth and user watchlist tables throughout
  `review_src/auth/service.py:39-284`.
- Auth API routes are in `review_src/auth/router.py:160-182` for authenticated
  watchlist endpoints.

Impact: security and per-user state. Not a safe early repository extraction.

## 3. Writes Per Table

### `fetch_status`

- `set_status()` writes `fetch_status` in `review_src/core/status.py:15-21`.
- `review_src/app.py` imports it at `review_src/app.py:82`.
- Verified direct `set_status(...)` call lines in `review_src/app.py`: 62 call
  sites found with `rg -n "^\s*set_status\(" review_src/app.py`.

Trigger sources include:

- Background repair worker: `enqueue_data_repair()` writes `auto_repair` status
  at `review_src/app.py:469-510`.
- MIS background update path: `fetch_mis_quotes_batch()` and
  `mis_quote_daemon()` write `mis_quote` status at `review_src/app.py:723-725`
  and `review_src/app.py:809-814`.
- TWSE update paths write `twse_eod`, `twse_stock_day`, and `twse_valuation`
  at `review_src/app.py:868`, `review_src/app.py:970`, and
  `review_src/app.py:999`.
- Fugle/price-volume paths write `fugle` / `price_volume` at
  `review_src/app.py:1041-1070`, `review_src/app.py:1376`,
  `review_src/app.py:4589-4621`.
- FinMind/Yahoo/data ensure paths write many per-code statuses at
  `review_src/app.py:1725-2234`.
- Row-cache/status module load paths write at `review_src/app.py:4545-4651`.

Direct GET status writes found in current source: no direct `set_status(...)`
inside `/api/quotes`, `/api/quotes/watchlist`, `/api/stock/{code}/detail`, or
`/api/debug/data-readiness` after Phase 2 cleanup. These GET routes read status
or return metadata, but do not directly write `fetch_status`.

### `watchlist`

- `/api/watchlist` POST writes rows at `review_src/app.py:4719-4741`.
- `/api/watchlist/{code}` DELETE deletes rows at `review_src/app.py:4745-4749`.
- POST add also starts `background_ensure_complete_data()` at
  `review_src/app.py:4742`.

Trigger source: explicit POST/DELETE endpoints. Risk: medium because it affects
user-visible list and background preload behavior.

### EOD/history/valuation/institution/margin/foreign holding

- TWSE EOD/history writes at `review_src/app.py:836-867`.
- TWSE stock-day writes at `review_src/app.py:926-970`.
- TWSE valuation writes at `review_src/app.py:984-999`.
- FinMind history writes at `review_src/app.py:1751-1771`.
- FinMind institution writes at `review_src/app.py:1802-1809`.
- FinMind margin writes at `review_src/app.py:1826-1850`.
- FinMind foreign shareholding writes at `review_src/app.py:1865-1876`.
- Yahoo history writes at `review_src/app.py:1922-1990`.
- Yahoo valuation writes at `review_src/app.py:2055-2065`.

Trigger sources: update functions/background tasks/POST update endpoints.
Risk: high because these are financial source-of-truth tables.

### `mis_quote_snapshot`

- `persist_mis_quote_snapshots()` writes rows and deletes old snapshots at
  `review_src/app.py:645-680`.
- `fetch_mis_quotes_batch()` invokes that persistence path at
  `review_src/app.py:684-725`.

Trigger source: MIS daemon/background update path, not ordinary quote GET.
Risk: medium/high because watchlist price correctness depends on it.

### `price_volume_profile_daily` and `price_volume_score_daily`

- Fugle profile upsert writes `price_volume_profile_daily` at
  `review_src/app.py:1192-1232`.
- OHLCV reconstructed profile writes `price_volume_profile_daily` at
  `review_src/app.py:1320-1375`.
- Price-volume scoring writes `price_volume_score_daily` at
  `review_src/app.py:1386-1472`.
- `cleanup_price_volume_history()` deletes older rows from both tables at
  `review_src/app.py:1156-1174`.

Trigger sources: price-volume update/background or debug/update flows. Risk:
high because this feeds price-volume analysis quality and scoring.

### `stock_state_history`

- `save_stock_state()` writes/upserts state history at `review_src/app.py:3974-3984`.
- Phase 2 changed high-risk GET callers to use `persist_state=False`; this audit
  did not modify code.

Trigger source: row-building when called with persistence enabled. Risk:
medium/high because GET-side persistence was previously a concern.

### `next_day_outlook_daily`

- `record_next_day_outlook()` writes next-day outlook history at
  `review_src/app.py:5571-5590`.
- `synthesize_next_day_outlook()` calls it only when `persist` is true at
  `review_src/app.py:5535`.
- Phase 2 changed detail GET to use `persist=False`; this audit did not modify
  code.

Trigger source: explicit calculation paths that leave default persistence true.
Risk: medium/high because it records analysis history.

### Auth writes

- Login attempts, registration, verification, session, password reset, and
  authenticated user watchlist writes occur in `review_src/auth/service.py:51-284`.

Trigger source: auth endpoints. Risk: high/security-sensitive.

## 4. `fetch_status` Special Audit

### Schema

- Table definition: `review_src/core/db.py:104`.

### Access wrapper

- `set_status()` definition: `review_src/core/status.py:15`.
- `set_status()` SQL write: `review_src/core/status.py:18`.
- `set_status()` commit: `review_src/core/status.py:21`.
- `get_status()` definition: `review_src/core/status.py:24`.
- `get_status()` SQL read: `review_src/core/status.py:27`.

### Imports

- `review_src/app.py` imports both functions at `review_src/app.py:82`.
- No other `from core.status import ...` imports were found by the required
  search.

### Call-site counts

- `set_status(...)`: 62 direct call lines in `review_src/app.py`.
- `get_status(...)`: 7 direct call lines in `review_src/app.py`.

### Current GET interaction

Confirmed direct GET `set_status(...)` writes:

- None found in the audited high-risk GETs after Phase 2 cleanup.

Current GET status reads:

- `GET /api/status` returns all statuses at `review_src/app.py:4707-4709`.
- `GET /api/stock/{code}/detail` reads `corporate_actions` status at
  `review_src/app.py:5719`.
- `GET /api/quotes` includes status metadata at `review_src/app.py:6348` and
  `review_src/app.py:6366`.

GET debug repair check:

- `GET /api/debug/data-readiness` is read-only in current source. It calls
  `data_readiness_for_items(..., enqueue_missing=False)` and returns repair
  endpoint recommendations at `review_src/app.py:6191-6214`. If `auto_repair`
  is requested, it returns a reason string instead of enqueueing repair at
  `review_src/app.py:6210-6213`.

### Phase 4B extraction suitability

`fetch_status` is the best first repository extraction candidate because:

- The schema is simple.
- Read/write access already goes through `core/status.py`.
- It does not own scoring formulas, practical status, RSI, support/resistance,
  price-volume formulas, chip-cost formulas, or next-day outlook formulas.
- A repository can be introduced behind the existing `set_status()` /
  `get_status()` API to preserve all call-site behavior.

## 5. DB Operations Mixed With Analysis / API Logic

These areas mix SQL access with analysis, route handling, formatting, or
background orchestration and should not be extracted until lower-risk
repositories are proven.

| Area | Verified locations | Why mixed / risky |
| --- | --- | --- |
| Row building | `_build_row_uncached()` at `review_src/app.py:4238`; DB reads at `review_src/app.py:4245-4248` | Reads DB, consumes MIS quote/cache, computes RSI/scoring/status/support/cost display, builds API row. |
| Detail endpoint | `api_stock_detail()` at `review_src/app.py:5690`; DB reads at `review_src/app.py:5694-5699` | Route + DB reads + analysis/detail response assembly. |
| Data readiness | `data_readiness_for_items()` around `review_src/app.py:5964` and debug route at `review_src/app.py:6191` | Reads many DB tables, historically interacted with repair flows. |
| Price-volume | `price_volume_source_diagnostics()` at `review_src/app.py:1097`; upsert/score at `review_src/app.py:1192-1472` | External source diagnostics + DB writes + scoring quality rules. |
| Stock state history | `save_stock_state()` at `review_src/app.py:3974`; reads at `review_src/app.py:3990-3997` | Persistence linked to practical-status row building. |
| Next-day outlook history | `synthesize_next_day_outlook()` persists via `record_next_day_outlook()` at `review_src/app.py:5535`; write at `review_src/app.py:5571` | Analysis result and persistence coupled. |
| Auth service | `review_src/auth/service.py:39-284` | Auth DB logic already partly isolated, but security-sensitive and separate from market repository work. |

## 6. Repository Target Split Recommendations

Current repository package:

- `review_src/repository/`: Unverified / Not found in current source.

Suggested future repositories, in safer order:

1. `review_src/repository/status_repository.py`
   - Tables: `fetch_status`.
   - Risk: Low/medium.
   - Keep `core/status.py` API stable and call the repository internally.
   - Does not change API response or business logic.

2. `review_src/repository/watchlist_repository.py`
   - Tables: `watchlist`.
   - Risk: Medium.
   - Affects unauthenticated watchlist GET/POST/DELETE and update target
     selection.
   - Should wait until status repository pattern is proven.

3. `review_src/repository/mis_quote_repository.py`
   - Tables: `mis_quote_snapshot`.
   - Risk: Medium/high.
   - Affects intraday watchlist price source and fallback.

4. `review_src/repository/history_repository.py` and
   `review_src/repository/valuation_repository.py`
   - Tables: `history_price`, `eod_price`, `valuation`.
   - Risk: High.
   - Affects quote rows, RSI, scoring, support/resistance, detail.

5. `review_src/repository/institution_repository.py`
   - Tables: `institution_daily`, `margin_daily`, `foreign_shareholding`.
   - Risk: High.
   - Affects chip-cost and warnings.

6. `review_src/repository/price_volume_repository.py`
   - Tables: `price_volume_profile_daily`, `price_volume_score_daily`.
   - Risk: High.
   - Affects price-volume quality gate and scoring.

7. `review_src/repository/outlook_repository.py`
   - Table: `next_day_outlook_daily`.
   - Risk: Medium/high.
   - Affects persisted outlook history; must preserve `persist=False` behavior
     for GET detail.

8. Auth repositories
   - Tables: `users`, `email_verifications`, `user_watchlist`,
     `login_attempts`, `auth_sessions`.
   - Risk: High/security-sensitive.
   - Should be handled separately from market-data repository work.

## 7. Suggested Extraction Order

### Phase 4B candidate: `fetch_status` repository

- Why first:
  - Access is already centralized in `review_src/core/status.py`.
  - It is mostly metadata/status, not core financial scoring.
  - It can be tested by preserving `set_status()` and `get_status()` behavior.
  - Rollback is simple: restore the small `core/status.py` implementation and
    delete the new repository file.

- Expected files:
  - Add `review_src/repository/__init__.py`.
  - Add `review_src/repository/status_repository.py`.
  - Modify `review_src/core/status.py` to delegate SQL to the repository while
    keeping public functions unchanged.

- Explicitly not modified:
  - scoring formulas.
  - practical status / referee logic.
  - RSI and technical indicators.
  - support/resistance.
  - chip-cost estimates.
  - price-volume scoring.
  - next-day outlook calculations.
  - API response format.
  - endpoint paths.
  - DB schema.

- Affected endpoints:
  - `GET /api/status`.
  - Any endpoint that includes status metadata, including quotes/detail/debug.
  - Behavior should remain identical.

- Risk level: Low/medium.

- Tests:
  - `python -m py_compile review_src/core/status.py review_src/repository/status_repository.py`.
  - Phase 0 smoke test.
  - Focused status check if server is available: `GET /api/status`.

- Rollback:
  - Revert `review_src/core/status.py`.
  - Delete `review_src/repository/status_repository.py` and
    `review_src/repository/__init__.py` if no other repository exists.

### Phase 4C candidate: `watchlist` repository

- Risk level: Medium.
- Affected endpoints: `GET /api/watchlist`, `POST /api/watchlist`,
  `DELETE /api/watchlist/{code}`, `/api/quotes`, update endpoints selecting
  watchlist mode.
- Rollback: restore inline SQL in `app.py`.

### Later candidates

- MIS quote repository: medium/high, because user-visible intraday price
  correctness is sensitive.
- Price/history/valuation/chip/price-volume repositories: high, because they
  touch financial calculations and must wait for repository pattern confidence.

## 8. Phase 4B Recommendation

Recommended first implementation phase:

- Phase 4B: Extract `fetch_status` DB access behind a status repository.

Reason:

- It is the lowest-risk DB boundary with existing wrapper functions.
- It improves layering without touching analysis formulas or API response shape.
- It gives the project a repository pattern example before moving user-facing
  market-data tables.

Do not start Phase 4B until the user explicitly says to start it.

## 9. Phase 4B Completion Note

Status: Completed on 2026-06-12.

Implemented files:

- `review_src/repository/__init__.py`
- `review_src/repository/status_repository.py`
- `review_src/core/status.py`

What changed:

- `review_src/core/status.py` keeps the existing public API:
  - `set_status(key, status, message="")`
  - `get_status()`
- `review_src/core/status.py` now delegates fetch-status SQL to
  `review_src/repository/status_repository.py`.
- `review_src/repository/status_repository.py` owns only `fetch_status`
  read/write helpers:
  - `set_fetch_status(...)`
  - `get_fetch_status()`

What did not change:

- `review_src/app.py` was not modified.
- Existing `set_status()` / `get_status()` call sites were not changed.
- API response format was not changed.
- DB schema was not changed.
- `fetch_status` table schema was not changed.
- scoring, practical status / referee, RSI, support/resistance, chip-cost,
  price-volume, and next-day outlook logic were not changed.

Dependency boundary after Phase 4B:

```text
core/status.py -> repository/status_repository.py -> core/db.py
```

Confirmed forbidden imports are not present:

- `review_src/app.py` does not import `repository.status_repository`.
- `review_src/repository/status_repository.py` does not import `app.py`.
- `review_src/repository/status_repository.py` does not import FastAPI.
- `review_src/repository/status_repository.py` does not import
  `core.status`.
- `review_src/core/db.py` does not import `core.status`.
- `review_src/core/db.py` does not import `repository.status_repository`.

Validation:

- `python -m py_compile review_src/core/status.py review_src/repository/status_repository.py review_src/repository/__init__.py`
- `python -m py_compile review_src/core/db.py review_src/app.py`
- Phase 0 smoke test passed after starting a local server.
- Focused `GET /api/status` check returned HTTP 200 and a `statuses` object.

Next recommended phase:

- Phase 4C: consider extracting a `watchlist` repository.
- Do not start Phase 4C until the user explicitly approves it.

## 10. Phase 4C Completion Note

Status: Completed on 2026-06-12.

Implemented files:

- `review_src/repository/watchlist_repository.py`
- `review_src/app.py`

Verified table ownership:

- Global app watchlist table:
  - Schema: `review_src/core/db.py:26`
  - Table name: `watchlist`
  - Columns: `code`, `name`, `sort_order`, `updated_at`
- Auth member watchlist table:
  - Schema: `review_src/auth/models.py:26`
  - Table name: `user_watchlist`
  - Columns include `user_id`, `stock_code`, `stock_name`, `sort_order`,
    `added_at`, `updated_at`

Conclusion:

- `app.py` global watchlist and `auth/` member watchlist are not the same table.
- Phase 4C intentionally did not modify `review_src/auth/*`.

What changed:

- Added `review_src/repository/watchlist_repository.py` for global
  `watchlist` DB helpers only.
- Replaced direct global `watchlist` SQL in `review_src/app.py` with repository
  helper calls.
- Preserved existing endpoint paths, response shape, sort order, and global
  watchlist behavior.

Repository functions added:

- `list_watchlist_items()`
- `list_watchlist_code_name_items()`
- `get_watchlist_codes()`
- `get_watchlist_codes_unordered()`
- `get_watchlist_item(code)`
- `watchlist_contains(code)`
- `insert_watchlist_if_absent(...)`
- `upsert_watchlist_item_with_limit(...)`
- `delete_watchlist_item(code)`

What did not change:

- DB schema was not changed.
- `watchlist` table schema was not changed.
- `user_watchlist` auth flow was not changed.
- API response format was not changed.
- endpoint paths were not changed.
- scoring, practical status / referee, RSI, support/resistance, chip-cost,
  price-volume, and next-day outlook logic were not changed.

Validation:

- `python -m py_compile review_src/app.py review_src/repository/watchlist_repository.py review_src/repository/__init__.py`
- Phase 0 smoke test passed after starting a local server.
- Focused `GET /api/watchlist` returned HTTP 200 and parseable JSON.
- Focused `GET /api/quotes?mode=watchlist` returned HTTP 200 and parseable
  JSON.

Next recommended phase:

- Phase 4D: consider another low-risk repository extraction only after user
  approval.

## 11. Phase 4D Next Repository Candidate Review

Status: Completed on 2026-06-12 as a documentation-only candidate review.

This section is an assessment only. It does not mean any repository has been
implemented.

### 11.1 Completed repositories

#### fetch_status repository

- Repository file: `review_src/repository/status_repository.py`
- Schema: `review_src/core/db.py:104`
- Public interface remains in `review_src/core/status.py:8`.
- App call sites still use `core.status.set_status()` / `core.status.get_status()`.
- Validation already completed in Phase 4B:
  - `py_compile` passed.
  - Phase 0 smoke test passed.
  - Focused `GET /api/status` passed.
- It did not require changing app.py call sites.

#### global watchlist repository

- Repository file: `review_src/repository/watchlist_repository.py`
- Global app table schema: `review_src/core/db.py:26`
- App import: `review_src/app.py:93`
- App endpoint examples:
  - `GET /api/watchlist`: `review_src/app.py:4717`
  - `GET /api/quotes/watchlist`: `review_src/app.py:6295`
  - `GET /api/quotes`: `review_src/app.py:6300`
- Scope: app-level global `watchlist` only.
- Auth member watchlist was not touched:
  - Auth schema: `review_src/auth/models.py:26`
  - Auth service: `review_src/auth/service.py:229`
  - Auth routes: `review_src/auth/router.py:160`
- Validation already completed in Phase 4C:
  - `py_compile` passed.
  - Phase 0 smoke test passed.
  - Focused `GET /api/watchlist` passed.
  - Focused `GET /api/quotes?mode=watchlist` passed.

### 11.2 Candidate repository list

The remaining DB areas are materially riskier than `fetch_status` and global
`watchlist`. Most of them feed user-facing financial values, scoring inputs,
background jobs, or intraday quote freshness.

| Candidate | Tables | Schema location | Key reads | Key writes | GET use | POST/update/background use | API / analysis impact | Test difficulty | Risk | Phase 4E fit |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `valuation_repository` | `valuation` | `review_src/core/db.py:48` | `review_src/app.py:390`, `432`, `2112`, `4256`, `5684`, `5985` | `review_src/app.py:1004`, `2072` | quotes/detail/readiness | TWSE valuation update `review_src/app.py:985`, Yahoo valuation fallback `review_src/app.py:2024`, background EOD `review_src/app.py:4556` | Affects valuation display, readiness, and `build_valuation_tags()` at `review_src/app.py:3735`; can influence practical status risk tags. | Medium | Medium | Best remaining candidate, but not truly low risk. |
| `corporate_actions_repository` | `corporate_actions` | `review_src/core/db.py:110` | `review_src/app.py:2440`, `3542` | `review_src/app.py:3413` | detail/practical-status context | corporate-action updater `review_src/app.py:3467`, background EOD `review_src/app.py:4556` | Affects ex-dividend/ex-rights adjustments and practical-status context. | Medium/high | High | Exclude for now. |
| `mis_quote_snapshot_repository` | `mis_quote_snapshot` | `review_src/core/db.py:142` | `review_src/app.py:779`, `4251` | `review_src/app.py:682`, cleanup at `690` | quotes/detail via fallback | MIS daemon `review_src/app.py:807`, `start_mis_quote_daemon()` at `828`, startup at `4633` | Affects intraday quote freshness, watchlist price, and user-visible current price. | High | High | Exclude for Phase 4E. |
| `eod_history_repository` | `eod_price`, `history_price` | `review_src/core/db.py:32`, `60` | many, including `review_src/app.py:319`, `388`, `4254`, `4255`, `5685`, `5686`, `5886`, `5887` | `review_src/app.py:865`, `870`, `941`, `946`, `1766`, `1941`, `1996` | quotes/detail/readiness/debug | TWSE, FinMind, Yahoo fallback, background EOD/repair | Core price, RSI, scoring, support/resistance, readiness. | High | High | Exclude for now. |
| `institution_repository` | `institution_daily` | `review_src/core/db.py:74` | `review_src/app.py:391`, `2420`, `2706`, `3250`, `3645`, `5275`, `5979` | `review_src/app.py:1815` | quotes/detail/readiness | FinMind chip update/background repair | Affects chip light, institution note, practical status, next-day outlook chip factor. | High | High | Exclude for now. |
| `margin_repository` | `margin_daily` | `review_src/core/db.py:84` | `review_src/app.py:392`, `2710`, `3251`, `3722`, `5276`, `5980` | `review_src/app.py:1852` | quotes/detail/readiness | FinMind margin update/background repair | Affects chip warning, margin deltas, next-day outlook chip factor. | High | High | Exclude for now. |
| `foreign_shareholding_repository` | `foreign_shareholding` | `review_src/core/db.py:121` | `review_src/app.py:435`, `2430`, `5981`, `5984`; chip engine requirement `review_src/chip_cost_engine.py:239` | `review_src/app.py:1881` | readiness/detail indirectly | FinMind foreign holding update | Affects foreign accumulated cost estimates and data quality. | High | High | Exclude for now. |
| `price_volume_profile_repository` | `price_volume_profile_daily` | `review_src/core/db.py:160` | `review_src/app.py:1112`, `1120`, `1126`, `1341`, `1391` | `review_src/app.py:1230`, `1371` | debug/detail price-volume diagnostics | Fugle/update/reconstructed OHLCV update | Affects price-volume quality gate and score input. | High | High | Exclude for now. |
| `price_volume_score_repository` | `price_volume_score_daily` | `review_src/core/db.py:180` | `review_src/app.py:1171`, `4803` | `review_src/app.py:1463` | detail/debug | price-volume scoring update | Affects price-volume score/grade. | High | High | Exclude for now. |
| `next_day_outlook_repository` | `next_day_outlook_daily` | `review_src/core/db.py:214` | No primary read path found in current app.py audit; persistence path exists. | `review_src/app.py:5566` | detail calculation uses `persist=False` after Phase 2B-2 | outlook persistence when enabled | Affects historical outlook persistence; must preserve GET no-write rule. | Medium/high | Medium/high | Exclude for now. |
| `stock_state_history_repository` | `stock_state_history` | `review_src/core/db.py:129` | `review_src/app.py:3999`, `4005` | `review_src/app.py:3988` | quotes/detail state-change context | save state flow | Affects status-change display/history and practical-state persistence. | Medium/high | Medium/high | Exclude for now. |
| `auth_user_watchlist_repository` | `user_watchlist` | `review_src/auth/models.py:26` | `review_src/auth/service.py:232`, `245`, `248` | `review_src/auth/service.py:253`, `261`, `271`, `281` | Auth API only | Auth POST/PATCH/DELETE | Security and member data path. Separate auth refactor needed. | Medium/high | High/security | Exclude from market-data Phase 4. |
| `lending_repository` | `lending_daily` | `review_src/core/db.py:95` | Generic helper allow-list only: `review_src/app.py:1570`, `1580` | No direct write found in current audit. | Not found | Not found | Currently underused / unverified. | Low but not useful | Low/unknown | Not recommended because it lacks meaningful active behavior to extract. |

### 11.3 mis_quote_snapshot special assessment

`mis_quote_snapshot` must not be treated as a simple low-risk table.

- Schema: `review_src/core/db.py:142`
- Indexes:
  - `review_src/core/db.py:156`
  - `review_src/core/db.py:158`
- In-memory cache:
  - `_mis_quote_cache`: `review_src/app.py:177`
  - pruning: `review_src/app.py:246`
- Writer:
  - `persist_mis_quote_snapshots()`: `review_src/app.py:657`
  - `INSERT OR REPLACE INTO mis_quote_snapshot`: `review_src/app.py:682`
  - retention cleanup: `review_src/app.py:690`
- Reader:
  - `get_mis_quote_latest_snapshot()`: `review_src/app.py:773`
  - `SELECT * FROM mis_quote_snapshot`: `review_src/app.py:779`
  - fallback use in row building: `review_src/app.py:4251`
- Daemon / background:
  - `fetch_mis_quotes_batch()`: `review_src/app.py:696`
  - updates `_mis_quote_cache`: `review_src/app.py:727`
  - calls `persist_mis_quote_snapshots(updated)`: `review_src/app.py:731`
  - `mis_quote_daemon()`: `review_src/app.py:807`
  - `start_mis_quote_daemon()`: `review_src/app.py:828`
  - starts `threading.Thread(...)`: `review_src/app.py:833`
  - startup call: `review_src/app.py:4633`

Risk assessment:

- It is written by a background daemon.
- It is high-frequency intraday data.
- It affects watchlist quote freshness and displayed current price.
- It participates in the price fallback chain.
- It involves both in-memory cache and SQLite persistence.

Decision:

- `mis_quote_snapshot` is High risk for Phase 4E.
- It is not recommended as the next repository extraction unless a separate
  quote-cache/daemon test plan is created first.

### 11.4 Explicitly excluded high-risk candidates

The following candidates should not be extracted next:

- `mis_quote_snapshot`: high-frequency daemon write, quote freshness, cache and
  displayed current price.
- `price_volume_profile_daily` and `price_volume_score_daily`: price-volume
  quality gate and scoring impact.
- `next_day_outlook_daily`: persistence must preserve `persist=False` for GET
  detail.
- `stock_state_history`: status-change history and state persistence.
- `history_price` / `eod_price`: core price, RSI, technical indicators,
  support/resistance, and data readiness.
- `institution_daily`, `margin_daily`, `foreign_shareholding`: chip logic,
  chip-cost estimates, practical status, and next-day outlook chip factor.
- `corporate_actions`: ex-dividend/ex-rights logic and practical-status
  context.
- Auth `user_watchlist`: security/member-data path, should be handled as a
  separate auth refactor.

### 11.5 Low-risk candidate criteria

Phase 4E should only proceed if the target:

- Has clear DB read/write boundaries.
- Does not change DB schema.
- Does not change API response shape.
- Does not affect scoring formulas.
- Does not affect practical status / main conclusion logic.
- Does not affect price-volume scoring.
- Does not affect chip-cost estimates.
- Does not affect next-day outlook formulas.
- Does not involve background repair.
- Does not involve a daemon.
- Does not involve external data-source fetch behavior.
- Does not involve high-frequency intraday writes.
- Can be verified with `py_compile`, Phase 0 smoke test, and one or two focused
  endpoints.
- Can be rolled back with a small patch.

### 11.6 Phase 4E recommendation

There is no remaining candidate as low-risk as Phase 4B `fetch_status` or Phase
4C global `watchlist`.

If continuing Phase 4 is still desired, the least risky remaining practical
candidate is:

- Recommended repository name: `valuation_repository`
- Table: `valuation`
- Schema: `review_src/core/db.py:48`
- Why it is the lowest remaining risk:
  - It is less central than `history_price`, `eod_price`, chip tables, or MIS
    quote snapshot.
  - Its read/write SQL is relatively compact compared with price/history/chip
    tables.
  - It can be tested through quote/detail/readiness endpoints.
- Why it is not truly low risk:
  - It affects valuation display.
  - It affects data-readiness gating.
  - It can affect practical-status risk tags through `build_valuation_tags()`
    at `review_src/app.py:3735`.

Expected files if Phase 4E is approved:

- Add `review_src/repository/valuation_repository.py`
- Modify only the smallest necessary valuation SQL call sites in
  `review_src/app.py`
- Update docs and review packet

Explicitly not modified in Phase 4E:

- scoring formulas
- practical status / referee rules
- RSI / technical indicators
- support/resistance calculations
- chip-cost estimates
- price-volume scoring
- next-day outlook formulas
- DB schema
- API response shape
- endpoint paths
- external data-source fetch behavior
- daemon / background task behavior

Expected impact:

- API response: No intended format change.
- DB schema: No change.
- scoring / practical status: No formula/rule change, but valuation rows are
  inputs to existing display/readiness/risk-tag behavior.
- daemon / background tasks: No intended behavior change.
- external fetch: No intended behavior change.

Verification:

- `python -m py_compile review_src/app.py review_src/repository/valuation_repository.py`
- Phase 0 smoke test
- Focused:
  - `GET /api/quotes?mode=watchlist`
  - `GET /api/quotes?mode=tw50`
  - `GET /api/stock/2330/detail`
  - debug/readiness endpoint if available

Rollback:

- Restore the direct valuation SQL in `review_src/app.py`.
- Delete `review_src/repository/valuation_repository.py`.

Risk level:

- Medium.

Recommendation:

- If the user requires a strictly low-risk next repository, pause Phase 4
  repository extraction and switch to feature work or a new audit.
- If the user accepts a medium-risk, carefully scoped repository extraction,
  Phase 4E can target `valuation_repository`.
