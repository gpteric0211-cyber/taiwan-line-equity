# DATA_SOURCE_ADAPTER_AUDIT.md - Phase 3A Data Source Audit

Phase 3A is documentation-only. It audits current data-source code before any
adapter extraction. No Python code, API behavior, database schema, route,
function, class, or business logic is changed by this file.

## Commands Used

Required searches were run:

```bash
rg -n "MIS|mis|twse|TWSE|FinMind|finmind|Fugle|fugle|Yahoo|yfinance|TAIFEX|taifex|requests\.get|httpx|urlopen|openapi|quote|fetch|download|read_csv|csv" review_src
rg -n "def .*mis|def .*twse|def .*finmind|def .*fugle|def .*yahoo|def .*taifex|def .*quote|def .*fetch|requests\.get|yfinance|FinMind|TWSE|TAIFEX|Fugle|Yahoo" review_src/app.py
rg -n "TAIFEX|taifex|futures|future|night|openapi|cache|fetch|requests\.get|httpx|urlopen" review_src/market
```

Additional focused searches inspected call sites and support modules:

```bash
rg -n "get_mis_quote|get_fugle_quote|parse_fugle_quote|futures_night_signal_for_stock|yfinance_quote|related_us_assets_for_code|synthesize_next_day_outlook|us_sentiment_factor|futures_night_factor|price_volume_source_diagnostics|upsert_price_volume_profile_from_fugle" review_src/app.py
rg -n "def request_json|requests\.get|def request_text|TAIFEX_OPENAPI_BASE|TWSE_MIS_STOCK_INFO|FINMIND_API|FUGLE_BASE|YAHOO|STOCK_DAY|BWIBBU" review_src/core review_src/price_volume.py review_src/app.py
rg -n "read_csv|csv|CSV|import.*csv|open\(" review_src/app.py review_src/price_volume.py review_src/core review_src/market
```

## 1. Current Data Sources

### HTTP Helper Layer

- Shared JSON request helper: `request_json()` in `review_src/core/http.py:11`.
- It calls `requests.get()` in `review_src/core/http.py:23`.
- Text request helper for CSV/text endpoints: `request_text()` in `review_src/app.py:3335`.
- `request_text()` calls `requests.get()` in `review_src/app.py:3339`.
- Source URL constants are defined in `review_src/core/config.py:146-154`.

These helpers are not adapters yet. They are shared transport utilities used by
multiple source-specific functions.

### MIS / TWSE MIS Realtime Quote

Current code locations:

- MIS endpoint constant: `TWSE_MIS_STOCK_INFO` in `review_src/core/config.py:147`.
- `mis_headers()` in `review_src/app.py:559`.
- `_mis_channels_for_codes()` in `review_src/app.py:568`.
- `_mis_code_from_row()` in `review_src/app.py:583`.
- `_mis_parse_volume()` in `review_src/app.py:592`.
- `parse_mis_quote()` in `review_src/app.py:601`.
- `fetch_mis_quotes_batch()` in `review_src/app.py:684`.
- `get_mis_quote_cached()` in `review_src/app.py:728`.
- `get_mis_quote_latest()` in `review_src/app.py:741`.
- `get_mis_quote_latest_snapshot()` in `review_src/app.py:761`.
- `persist_mis_quote_snapshots()` in `review_src/app.py:645`.
- `mis_quote_daemon()` in `review_src/app.py:795`.
- `start_mis_quote_daemon()` in `review_src/app.py:817`.

Behavior:

- Calls external API: Yes. `fetch_mis_quotes_batch()` calls
  `request_json(TWSE_MIS_STOCK_INFO, ...)` in `review_src/app.py:695`.
- Reads DB: Yes. `get_mis_quote_latest_snapshot()` reads
  `mis_quote_snapshot` in `review_src/app.py:761-789`; `mis_quote_daemon()`
  reads `watchlist` in `review_src/app.py:800-802`.
- Writes DB: Yes. `persist_mis_quote_snapshots()` writes
  `mis_quote_snapshot` in `review_src/app.py:645-679`.
- Writes cache: Yes. `fetch_mis_quotes_batch()` writes `_mis_quote_cache` in
  `review_src/app.py:715` and clears `_row_cache` in `review_src/app.py:721`.
- Direct or indirect GET use: Yes. `_build_row_uncached()` uses MIS quote for
  watchlist intraday rows in `review_src/app.py:4237-4242`. `/api/quotes`
  returns rows built from this path at `review_src/app.py:6468-6532`.
- Affects API response: Yes, quote price/source/change fields in watchlist rows.
- Affects analysis: It can affect row price used by downstream row/detail
  presentation. It should be treated as high-risk for adapter extraction because
  users depend on intraday quote correctness.

### TWSE EOD / TWSE Stock Day / TWSE Valuation / TWSE Corporate Action CSV

Current code locations:

- TWSE endpoint constants:
  - `TWSE_STOCK_DAY_ALL` in `review_src/core/config.py:149`.
  - `TWSE_STOCK_DAY_BY_CODE` in `review_src/core/config.py:150`.
  - `TWSE_BWIBBU_ALL` in `review_src/core/config.py:151`.
  - `TWSE_EX_DIVIDEND_CSV` in `review_src/core/config.py:153`.
- `fetch_twse_eod_all()` in `review_src/app.py:826`.
- `fetch_twse_stock_day_for_code()` in `review_src/app.py:872`.
- `refresh_twse_stock_day_codes()` in `review_src/app.py:945`.
- `fetch_twse_valuation_all()` in `review_src/app.py:974`.
- `_csv_rows_from_text()` in `review_src/app.py:3352`.
- `fetch_twse_ex_dividend_calendar()` in `review_src/app.py:3411`.
- `update_corporate_actions()` in `review_src/app.py:3458`.

Behavior:

- Calls external API: Yes. `fetch_twse_eod_all()` uses `TWSE_STOCK_DAY_ALL` at
  `review_src/app.py:827`; `fetch_twse_stock_day_for_code()` uses
  `TWSE_STOCK_DAY_BY_CODE` at `review_src/app.py:890`;
  `fetch_twse_valuation_all()` uses `TWSE_BWIBBU_ALL` at
  `review_src/app.py:975`; `fetch_twse_ex_dividend_calendar()` uses
  `request_text(TWSE_EX_DIVIDEND_CSV, ...)` at `review_src/app.py:3412`.
- Reads DB: Yes. Per-code and orchestration flows compare existing dates and
  component/watchlist codes in nearby update functions.
- Writes DB: Yes. EOD and daily history writes are in
  `review_src/app.py:853-864`; per-code stock-day writes are in
  `review_src/app.py:925-941`; valuation writes are in
  `review_src/app.py:983-997`; corporate action writes are in
  `review_src/app.py:3402-3405`.
- Writes cache/status: Yes. EOD update clears compute caches at
  `review_src/app.py:868`; update functions call `set_status()` in the same
  update blocks.
- Direct or indirect GET use: Read paths consume persisted TWSE data through
  `eod_price`, `history_price`, `valuation`, and corporate-action tables.
- Affects API response: Yes, price fallback, valuation, historical rows,
  support/resistance, and detail fields.
- Affects analysis: Yes. Historical OHLCV affects scoring, RSI, support /
  resistance, practical status, and price-volume reconstruction.

### FinMind

Current code locations:

- FinMind endpoint constant: `FINMIND_API` in `review_src/core/config.py:148`.
- `_finmind_parse_response()` in `review_src/app.py:1695`.
- `_is_finmind_token_illegal_error()` in `review_src/app.py:1702`.
- `finmind_get()` in `review_src/app.py:1707`.
- `upsert_finmind_stock_data()` in `review_src/app.py:1731`.
- `fetch_finmind_dividend_result()` in `review_src/app.py:3424`.

Behavior:

- Calls external API: Yes. `finmind_get()` calls `request_json(FINMIND_API, ...)`
  in `review_src/app.py:1718` and `review_src/app.py:1727`.
- Reads DB: Yes. Update orchestration and corporate action fallback read stock
  lists from components/watchlist (`review_src/app.py:3429-3432` for dividend
  fallback).
- Writes DB: Yes. `upsert_finmind_stock_data()` writes `history_price` in
  `review_src/app.py:1750-1770`, `institution_daily` in
  `review_src/app.py:1801-1807`, `margin_daily` in `review_src/app.py:1825-1848`,
  and `foreign_shareholding` in `review_src/app.py:1864-1874`.
- Writes cache/status: Yes. The update path calls `set_status()` and update
  progress functions in the same area.
- Direct or indirect GET use: GET reads persisted FinMind data through DB tables;
  Phase 2 removed high-risk GET repair triggers for quotes/detail/debug paths.
- Affects API response: Yes, institutional cost/chip fields, readiness, detail
  metadata, and computed stock rows.
- Affects analysis: Yes. It affects chip costs, practical status inputs,
  margin/chip warnings, history-based scoring, and corporate action fallback.

### Fugle

Current code locations:

- Fugle base URL constant: `FUGLE_BASE` in `review_src/core/config.py:146`.
- `FugleRateLimiter` in `review_src/app.py:170`.
- `fugle_headers()` in `review_src/app.py:1004`.
- `get_fugle_quote_cached()` in `review_src/app.py:1008`.
- `fetch_fugle_quote_network()` in `review_src/app.py:1020`.
- `fetch_fugle_price_volume_network()` in `review_src/app.py:1045`.
- `extract_fugle_price_volume_rows()` in `review_src/app.py:1073`.
- `price_volume_source_diagnostics()` in `review_src/app.py:1096`.
- `upsert_price_volume_profile_from_fugle()` in `review_src/app.py:1191`.
- `parse_fugle_quote()` in `review_src/app.py:1525`.
- `background_price_volume_update()` in `review_src/app.py:4582`.

Behavior:

- Calls external API: Yes. `fetch_fugle_quote_network()` builds
  `/intraday/quote/{code}` in `review_src/app.py:1031`; 
  `fetch_fugle_price_volume_network()` builds `/intraday/volumes/{code}` in
  `review_src/app.py:1053`.
- Reads DB: Yes. `price_volume_source_diagnostics()` reads
  `price_volume_profile_daily` in `review_src/app.py:1101-1115`; 
  `upsert_price_volume_profile_from_fugle()` reconciles against EOD volume in
  `review_src/app.py:1200-1202`.
- Writes DB: Yes. `upsert_price_volume_profile_from_fugle()` writes
  `price_volume_profile_daily` in `review_src/app.py:1219-1229`; it also calls
  `cleanup_price_volume_history()` in `review_src/app.py:1230`.
- Writes cache/status: Yes. Fugle quote cache writes occur in
  `fetch_fugle_quote_network()` at `review_src/app.py:1035-1036`; price-volume
  update uses `set_status()` in `review_src/app.py:4588` and
  `review_src/app.py:4618-4620`.
- Direct or indirect GET use: Current GET detail/debug reads persisted
  price-volume data. Fugle network fetch is in explicit update/background paths,
  not adapter-isolated yet.
- Affects API response: Yes, price-volume block and diagnostics; quote fallback
  may affect config/debug visibility.
- Affects analysis: Yes. Fugle intraday volumes feed price-volume scoring after
  quality gates.

### Yahoo / yfinance

Current code locations:

- Yahoo request sleep setting: `YAHOO_REQUEST_SLEEP_SECONDS` in
  `review_src/core/config.py:136`.
- `yahoo_tw_symbols()` in `review_src/app.py:1885`.
- `upsert_yahoo_chart_tw_history()` in `review_src/app.py:1891`.
- `upsert_yfinance_tw_history()` in `review_src/app.py:1958`.
- `upsert_yfinance_tw_valuation()` in `review_src/app.py:2013`.
- `fetch_yfinance_quote()` in `review_src/adapter/yahoo.py:34`.
- Compatibility wrapper `yfinance_quote()` in `review_src/app.py:4951-4952`.
- `related_us_assets_for_code()` import/fallback area in `review_src/app.py:4940`.
- US-asset quote consumption in detail: `yfinance_quote()` is called in
  `review_src/app.py:5755`.

Behavior:

- Calls external API: Yes. `upsert_yahoo_chart_tw_history()` calls Yahoo chart
  through `request_json()` at `review_src/app.py:1902-1909`;
  `upsert_yfinance_tw_history()` calls `yf.Ticker(...).history(...)` at
  `review_src/app.py:1971-1972`; `upsert_yfinance_tw_valuation()` calls yfinance
  info/fast_info in `review_src/app.py:2030-2047`; `fetch_yfinance_quote()` calls
  Yahoo chart in `review_src/adapter/yahoo.py:62`.
- Reads DB: Yahoo TW fallback is invoked by data-completeness flows that inspect
  existing history/valuation state.
- Writes DB: Yes for Taiwan history/valuation fallback:
  `history_price` writes in `review_src/app.py:1921-1945` and
  `review_src/app.py:1976-1989`; `valuation` writes in
  `review_src/app.py:2054-2065`. US quote function `yfinance_quote()` itself
  writes no DB.
- Writes cache/status: Yes. `fetch_yfinance_quote()` writes `_us_market_cache`
  after quote retrieval in `review_src/adapter/yahoo.py:120` and
  `review_src/adapter/yahoo.py:190`; cached reads are checked in
  `review_src/adapter/yahoo.py:52-56`.
- Direct or indirect GET use: Yes. `/api/stock/{code}/detail` calls
  `yfinance_quote()` through US-related assets in `review_src/app.py:5751-5756`.
- Affects API response: Yes, related US assets and next-day outlook context.
- Affects analysis: Yes for next-day outlook; Taiwan history/valuation fallback
  can affect quote/detail/scoring after explicit update flows.

### TAIFEX / Futures / Night Session

Current code locations:

- TAIFEX base URL constant: `TAIFEX_OPENAPI_BASE` in
  `review_src/core/config.py:152`.
- Import into app: `from market.futures import futures_night_signal_for_stock`
  in `review_src/app.py:91`.
- `review_src/market/futures.py` exists.
- `_taifex_cache` in `review_src/market/futures.py:13`.
- `_taifex_lock` in `review_src/market/futures.py:14`.
- `TAIFEX_CACHE_TTL_SECONDS` in `review_src/market/futures.py:15`.
- `fetch_taifex_openapi()` in `review_src/market/futures.py:25`.
- External call inside `fetch_taifex_openapi()` at
  `review_src/market/futures.py:32`.
- Cache write inside `fetch_taifex_openapi()` at
  `review_src/market/futures.py:33-34`.
- `_stock_futures_contracts()` in `review_src/market/futures.py:82`; it calls
  `fetch_taifex_openapi("SingleStockFuturesMargining")` at
  `review_src/market/futures.py:86`.
- `futures_relation_candidates()` in `review_src/market/futures.py:100`.
- `futures_night_signal_for_stock()` in `review_src/market/futures.py:149`.
- `futures_night_signal_for_stock()` calls
  `fetch_taifex_openapi("DailyMarketReportFut")` at
  `review_src/market/futures.py:164`.
- The signal explicitly sets `can_override_main_status` to `False` in
  `review_src/market/futures.py:155`.
- App detail flow calls `futures_night_signal_for_stock(code)` in
  `review_src/app.py:5923`.

Behavior:

- Calls external API: Yes, via `fetch_taifex_openapi()`.
- Reads DB: No direct DB reads found in `review_src/market/futures.py`.
- Writes DB: No direct DB writes found in `review_src/market/futures.py`.
- Writes cache: Yes, `_taifex_cache`.
- Direct or indirect GET use: Yes. `/api/stock/{code}/detail` calls it when the
  next-day outlook gate is open in `review_src/app.py:5921-5935`.
- Affects API response: Yes, `futures_night` and `next_day_outlook` detail data.
- Affects analysis: Yes, next-day outlook / night-session context. It must not
  override main practical status directly.

### Price Volume Module

Current code locations:

- `review_src/price_volume.py` exists and is imported by app in
  `review_src/app.py:109-114`.
- Source level definitions in `review_src/price_volume.py:13-20` include:
  `twse_official`, `finmind_tick`, `fugle_intraday_volumes`,
  `broker_authorized`, `ohlcv_estimated`, and `ohlcv_reconstructed`.
- Normalization helper `normalize_profile_rows()` in
  `review_src/price_volume.py:60`.
- Evaluation function `evaluate_profile()` in `review_src/price_volume.py:203`.
- Coverage gate in `evaluate_profile()` at `review_src/price_volume.py:219-227`.
- App-side persisted computation entry: `compute_price_volume_score_for_code()`
  in `review_src/app.py:1385`.
- OHLCV reconstruction fallback:
  `ensure_ohlcv_reconstructed_price_volume()` in `review_src/app.py:1319`.

Behavior:

- Calls external API: Not directly in `review_src/price_volume.py`. External
  Fugle/TWSE/FinMind source ingestion remains in app update functions.
- Reads DB: `compute_price_volume_score_for_code()` reads
  `price_volume_profile_daily` in `review_src/app.py:1380-1452`.
- Writes DB: `compute_price_volume_score_for_code()` writes
  `price_volume_score_daily` in `review_src/app.py:1452-1457`.
- Writes cache: Not directly in `review_src/price_volume.py`.
- Direct or indirect GET use: Phase 2 changed debug/detail paths to avoid
  high-risk GET writes. Explicit POST update uses price-volume update paths.
- Affects API response: Yes, price-volume summary/detail diagnostics.
- Affects analysis: Yes, price-volume scoring and referee context when qualified.

### CSV / Local Import

Current code locations:

- Components CSV path constant: `COMPONENTS_FILE` in
  `review_src/core/config.py:17`.
- `read_components()` reads local CSV in `review_src/core/components.py:11-13`.
- `_csv_rows_from_text()` parses downloaded CSV/text in `review_src/app.py:3352`.
- TWSE/TPEx corporate-action CSV sources are used at
  `review_src/app.py:3411-3420`.

Behavior:

- Calls external API: Local component CSV does not. TWSE/TPEx corporate-action
  CSV uses `request_text()` and external URLs.
- Reads DB: Component resolver may combine with watchlist in other app paths.
- Writes DB: Corporate-action CSV rows are upserted by
  `upsert_corporate_action_rows()` in `review_src/app.py:3402-3405`.
- Writes cache: No direct cache write found for `read_components()`.
- Direct or indirect GET use: Components are used to build Taiwan 50 lists and
  resolve stock codes; corporate-action data is read by analysis paths.
- Affects API response: Yes, component lists and corporate action adjustment
  context.
- Affects analysis: Yes, component scope and corporate-action adjusted scoring.

### Other Sources

- `us_relations.py` is a local static mapping consumed from app at
  `review_src/app.py:4940`; it is not an external network source by itself.
- `review_src/market/futures.py` is already a partial market module, but its
  TAIFEX fetch/cache helper is still source access plus signal logic in one file.

### Local Static US Relation Map

Phase 3B-2 reviewed `review_src/us_relations.py` as the second low-risk local /
static data-source adapter candidate.

Current code locations:

- Review version constant: `REVIEW_VERSION` in `review_src/us_relations.py:11`.
- Asset row helper: `asset()` in `review_src/us_relations.py:14`.
- Static relation map: `US_RELATION_MAP` in `review_src/us_relations.py:28`.
- Read-only lookup helper: `related_us_assets_for_code()` in
  `review_src/us_relations.py:413`.
- Read-only coverage helper: `relation_coverage()` in
  `review_src/us_relations.py:423`.
- App import / fallback block: `review_src/app.py:4937-4947`.
- Detail call site: `related_us_assets_for_code(code)` in
  `review_src/app.py:5751`.
- Readiness summary call site: `related_us_assets_for_code(code)` in
  `review_src/app.py:6141`.
- Coverage debug endpoint: `api_us_relations_coverage()` in
  `review_src/app.py:6215-6218`.

Boundary assessment:

- Calls external API: No.
- Reads DB: No.
- Writes DB: No.
- Writes cache: No.
- Calls `set_status()`: No.
- Starts background task/thread: No.
- Imports FastAPI: No.
- Imports `app.py`: No.
- Performs API response shaping: No. It returns static mapping payloads consumed
  by existing app/detail logic.
- Contains scoring / practical-status / next-day-outlook formula: No.
- Affects API response: Yes, indirectly, because app detail/readiness/debug
  endpoints consume the static mapping. No response shape change is needed.

Conclusion:

`review_src/us_relations.py` already behaves like a clean read-only static-data
module. Adding `review_src/adapter/us_relations.py` would add an unnecessary
import layer without reducing risk or coupling. Phase 3B-2 should therefore be
documented as docs-only completion.

## 2. Current Data Flow

The current real flow is mixed and not adapter-isolated:

1. External source constants are defined mostly in `review_src/core/config.py`.
2. Network calls are made through `request_json()` in `review_src/core/http.py`
   or `request_text()` in `review_src/app.py`.
3. Source-specific functions mostly live in `review_src/app.py`:
   MIS, TWSE, FinMind, Fugle, Yahoo/yfinance, and corporate-action CSV.
4. TAIFEX futures code is split into `review_src/market/futures.py`, which both
   fetches TAIFEX OpenAPI data and computes night-session signal payloads.
5. Many update paths write source data into SQLite tables such as
   `eod_price`, `history_price`, `valuation`, `institution_daily`,
   `margin_daily`, `foreign_shareholding`, `mis_quote_snapshot`,
   `price_volume_profile_daily`, and `price_volume_score_daily`.
6. In-memory caches include `_mis_quote_cache`, `_fugle_quote_cache`,
   `_us_market_cache`, and `_taifex_cache`.
7. Row/detail builders read DB/cache data and feed analysis paths:
   `_build_row_uncached()` starts at `review_src/app.py:4237`;
   detail endpoint starts at `review_src/app.py:5847`.
8. API responses are produced directly from app functions rather than through a
   separated service/repository/adapter layer.

Text flow summary:

```text
MIS / TWSE / FinMind / Fugle / Yahoo / TAIFEX / CSV
  -> app.py source functions or market/futures.py
  -> SQLite tables and/or in-memory caches
  -> build_row / stock detail / scoring / price-volume / next-day outlook
  -> FastAPI endpoints
  -> static frontend
```

## 3. Adapter Target Split Suggestions

Current adapter directory status after Phase 3B-1:

```text
review_src/adapter/: exists
review_src/adapter/__init__.py: adapter package marker
review_src/adapter/yahoo.py: Yahoo / yfinance read-only US quote adapter
```

Future target split, not implemented:

- `review_src/adapter/mis.py`
  - Own MIS headers, channel building, batch fetch, quote parsing.
  - Should not own DB snapshot persistence or row shaping.
- `review_src/adapter/twse.py`
  - Own TWSE EOD, STOCK_DAY, BWIBBU, and corporate-action CSV fetch/parse.
  - Should return normalized source rows; repository should write DB.
- `review_src/adapter/finmind.py`
  - Own FinMind token handling, API request, response parsing, dataset fetch.
  - Should not write `history_price`, `institution_daily`, etc. directly.
- `review_src/adapter/fugle.py`
  - Own Fugle headers/rate limit/network fetch and raw price-volume parsing.
  - Should not decide price-volume scoring quality or write profile tables.
- `review_src/adapter/yahoo.py`
  - Own Yahoo chart/yfinance quote and Taiwan fallback fetch normalization.
  - Consider splitting read-only US quote from TW history/valuation upserts.
- `review_src/adapter/taifex.py`
  - Own TAIFEX OpenAPI fetch/cache and raw future rows.
  - `analysis/futures_signal.py` or equivalent should compute the signal later.

## 4. Suggested Extraction Order

### Phase 3B Candidate: Yahoo / yfinance quote read adapter

- Why early:
  - `yfinance_quote()` is relatively self-contained in `review_src/app.py:4954`.
  - It uses an in-memory cache and external Yahoo chart data, but the US quote
    function itself does not write DB.
  - It affects detail/outlook context, not the main practical status directly.
- Affected endpoints:
  - `GET /api/stock/{code}/detail` through US related assets
    (`review_src/app.py:5908-5914`).
- Testability:
  - Can compare pre/post shape of related US asset quote fields in detail.
  - Can run `py_compile` and Phase 0 smoke test.
- DB writes:
  - Phase 3B should not move Yahoo Taiwan history/valuation upsert functions
    yet, because those write `history_price`/`valuation`.
- Scoring/practical status:
  - Should not affect scoring formulas or main practical status.
- Futures/night/outlook:
  - It affects `next_day_outlook` inputs when detail displays outlook, so risk
    is not zero.
- Risk: Medium.
- Rollback:
  - Revert the adapter file and restore `yfinance_quote()` import/call to the
    original app-local function.

### Phase 3C Candidate: TAIFEX raw fetch adapter

- Why not first:
  - `review_src/market/futures.py` already mixes fetch/cache with futures signal
    computation.
  - It feeds `/api/stock/{code}/detail` night-session and next-day outlook.
- Affected endpoints:
  - `GET /api/stock/{code}/detail`.
- Testability:
  - Can compare `futures_night` response fields before/after.
- DB writes:
  - No direct DB writes found in `review_src/market/futures.py`.
- Scoring/practical status:
  - It should not override main status; code has `can_override_main_status=False`
    at `review_src/market/futures.py:155`.
- Futures/night/outlook:
  - Directly affected, so this is medium risk.
- Risk: Medium.
- Rollback:
  - Restore TAIFEX fetch/cache inside `market/futures.py`.

### Phase 3D Candidate: Fugle price-volume adapter

- Why later:
  - Fugle paths include rate limiting, network fetch, source parsing, DB writes,
    and price-volume quality decisions.
  - It affects `price_volume_profile_daily` and price-volume score readiness.
- Affected endpoints:
  - `POST /api/update/price-volume`.
  - `GET /api/debug/price-volume/{code}` reads resulting persisted diagnostics.
  - `GET /api/stock/{code}/detail` displays resulting price-volume fields.
- Testability:
  - Requires environments with Fugle key or OHLCV fallback validation.
- DB writes:
  - Yes, price-volume profile writes at `review_src/app.py:1219-1229`.
- Scoring/practical status:
  - Price-volume can feed referee context once qualified.
- Futures/night/outlook:
  - Not directly.
- Risk: Medium to High.
- Rollback:
  - Restore Fugle functions in app.py and remove adapter import.

### Later Candidates: MIS, TWSE, FinMind

- MIS:
  - High user-facing correctness risk because watchlist intraday price depends
    on it.
  - It includes daemon, cache, DB snapshot, and row-cache interactions.
  - Risk: High.
- TWSE:
  - Broad impact on EOD price, history, valuation, corporate actions, scoring,
    support/resistance, and fallback rows.
  - Risk: High.
- FinMind:
  - Broad impact on history, institution, margin, foreign shareholding, chip cost,
    and corporate-action fallback.
  - Risk: High.

These should be split only after lower-risk adapters establish the pattern.

### Phase 3B-2 Candidate: Local static US relation map

- Candidate reviewed:
  - `review_src/us_relations.py`.
- Decision:
  - Do not create a new adapter wrapper.
- Why:
  - The module is already read-only and adapter-like.
  - It has clear constants and helper functions.
  - It does not call network APIs, DB, cache, `set_status()`, or background work.
  - A wrapper would only increase import depth and route confusion.
- Affected endpoints:
  - `GET /api/stock/{code}/detail` via `review_src/app.py:5751`.
  - `GET /api/debug/us-relations/coverage` via
    `review_src/app.py:6215-6218`.
  - Data-readiness summary via `review_src/app.py:6141`.
- Scoring/practical status:
  - No scoring formula or main practical-status logic should be changed.
- Futures/night/outlook:
  - No TAIFEX or futures night-session code should be changed.
- Risk: Low.
- Rollback:
  - Documentation-only. Revert this audit update if needed.

## 5. Phase 3B Recommendation

Recommended first adapter:

```text
Yahoo / yfinance read-only US quote adapter
```

Scope:

- Move only read-only US quote fetching/normalization currently represented by
  `yfinance_quote()` in `review_src/app.py:4954`.
- Do not move Yahoo Taiwan history/valuation fallback upsert functions in the
  first adapter step.

Reasons:

- It is smaller and more self-contained than MIS/TWSE/FinMind/Fugle.
- It has no direct DB write in the quote path.
- It does not own scoring formulas or main practical status.
- It creates an adapter pattern without touching the most critical Taiwan quote
  or EOD data paths.

Expected files:

- Add `review_src/adapter/yahoo.py`.
- Modify `review_src/app.py` only to import and call the moved function.
- No DB schema changes.

Explicit non-goals:

- Do not change Yahoo quote formula, fallback, market-state handling, or cache
  semantics.
- Do not change `upsert_yahoo_chart_tw_history()`.
- Do not change `upsert_yfinance_tw_history()`.
- Do not change `upsert_yfinance_tw_valuation()`.
- Do not change scoring, practical status, price-volume, chip cost,
  support/resistance, or next-day outlook formula.
- Do not change API response shape.
- Do not change TAIFEX/futures/night-session logic.

Expected impact:

- API response: No intended change.
- DB schema: No change.
- Scoring/practical status: No intended change.
- Price-volume/chip/support/resistance: No intended change.
- Next-day outlook: Same inputs and output shape, but implementation source moves.
- TAIFEX/futures/night-session: No intended change.

Verification:

- `python -m py_compile review_src/app.py review_src/adapter/yahoo.py`
- Phase 0 smoke test.
- Compare `GET /api/stock/2330/detail` pre/post for related US asset quote shape.

Rollback:

- Delete `review_src/adapter/yahoo.py`.
- Restore the original `yfinance_quote()` implementation in `review_src/app.py`.
- Restore original imports.

## 6. Phase 3A Status

- Status: Completed as documentation-only audit.
- Python code changed: No.
- Business logic changed: No.
- API response changed: No.
- DB schema changed: No.
- Adapter directories created: No.

## 7. Phase 3B-1 Status

- Status: Completed.
- Scope handled: Yahoo / yfinance read-only US quote adapter only.
- Python code changed: Yes.
- Business logic changed: No intended change.
- API response changed: No intended change.
- DB schema changed: No.
- TAIFEX / futures / night-session changed: No.
- MIS / TWSE / FinMind / Fugle changed: No.

Implemented locations:

- Adapter package marker: `review_src/adapter/__init__.py`.
- Yahoo adapter function: `fetch_yfinance_quote()` in `review_src/adapter/yahoo.py:34`.
- Yahoo adapter cache TTL: `US_MARKET_CACHE_TTL_SECONDS` in `review_src/adapter/yahoo.py:29`.
- Yahoo adapter in-memory cache: `_us_market_cache` in `review_src/adapter/yahoo.py:30`.
- Yahoo adapter lock: `_us_market_lock` in `review_src/adapter/yahoo.py:31`.
- App import: `from adapter.yahoo import fetch_yfinance_quote` in `review_src/app.py:38`.
- App compatibility wrapper: `yfinance_quote()` in `review_src/app.py:4951-4952`.
- Existing detail call site remains: `q = yfinance_quote(r["ticker"])` in `review_src/app.py:5755`.

Boundary checks:

- `review_src/adapter/yahoo.py` does not import FastAPI or `review_src/app.py`.
- `review_src/adapter/yahoo.py` does not call `set_status()`.
- `review_src/adapter/yahoo.py` does not read or write DB.
- `review_src/adapter/yahoo.py` does not start background tasks or threads.
- `review_src/app.py` no longer owns `_us_market_cache`; Yahoo US quote cache now
  belongs to `review_src/adapter/yahoo.py`.

Verification performed:

- `python -m py_compile review_src/app.py review_src/adapter/yahoo.py review_src/adapter/__init__.py`: PASS.
- Phase 0 smoke test with temporary local server:
  - `GET /`: PASS.
  - `GET /api/quotes?mode=watchlist`: PASS.
  - `GET /api/quotes?mode=tw50`: PASS.
  - `GET /api/stock/2330/detail`: PASS.

## 8. Phase 3B-2 Status

- Status: Completed as documentation-only evaluation.
- Final decision: Scenario A.
- Scope handled: local/static US relation data only.
- Python code changed: No.
- Business logic changed: No.
- API response changed: No.
- DB schema changed: No.
- Adapter wrapper created: No.
- TAIFEX / futures / night-session changed: No.
- MIS / TWSE / FinMind / Fugle / Yahoo changed: No.

Reason for not adding `review_src/adapter/us_relations.py`:

- `review_src/us_relations.py` is already a clean read-only static-data module.
- It exposes stable helpers directly used by app code:
  - `related_us_assets_for_code()` in `review_src/us_relations.py:413`.
  - `relation_coverage()` in `review_src/us_relations.py:423`.
- It does not use network, DB, cache writes, `set_status()`, FastAPI, app imports,
  scoring formulas, practical-status logic, or background tasks.
- Adding a wrapper would be form-over-function refactoring and would not improve
  behavior, testability, or safety.

Verification performed:

- Required Phase 3B-2 `rg` searches were run against `review_src` and
  `review_src/app.py`.
- No Python files were modified, so no `py_compile` was required.

## 9. Phase 3C Adapter Isolation Review

Phase 3C is documentation-only closing review for Phase 3 adapter isolation.
No Python code, API behavior, database schema, frontend code, or business logic
was changed by this review.

### 9.1 Phase 3A Audit Summary

Phase 3A audited the current data-source surface before adapter extraction. The
following sources were found and documented with source locations above:

- MIS / TWSE MIS realtime quote path.
- TWSE EOD / STOCK_DAY / BWIBBU / ex-dividend CSV paths.
- FinMind data paths.
- Fugle quote and price-volume paths.
- Yahoo / yfinance paths.
- TAIFEX data paths.
- `review_src/market/futures.py` futures / night-session paths.
- Price-volume module and persisted price-volume profile paths.
- CSV / local import paths.
- `review_src/us_relations.py` local US relation table.

### 9.2 Phase 3B-1 Yahoo / yfinance Adapter Acceptance

Acceptance result: accepted.

Verified current locations:

- `review_src/adapter/yahoo.py` exists.
- `fetch_yfinance_quote()` is in `review_src/adapter/yahoo.py:34`.
- `_us_market_cache` is in `review_src/adapter/yahoo.py:30`.
- `review_src/app.py` imports the adapter with
  `from adapter.yahoo import fetch_yfinance_quote` in `review_src/app.py:38`.
- Compatibility wrapper `yfinance_quote()` remains in
  `review_src/app.py:4951-4952`.
- Existing detail call site remains `q = yfinance_quote(r["ticker"])` in
  `review_src/app.py:5755`.

Boundary review:

- `review_src/app.py` does not directly own `_us_market_cache` anymore.
- `review_src/adapter/yahoo.py` owns the US market quote cache.
- `review_src/adapter/yahoo.py` does not import FastAPI.
- `review_src/adapter/yahoo.py` does not import `review_src/app.py`.
- `review_src/adapter/yahoo.py` does not read or write DB.
- `review_src/adapter/yahoo.py` does not call `set_status()`.
- `review_src/adapter/yahoo.py` does not start background tasks, threads, or
  `asyncio.create_task()`.

API behavior expectation:

- `GET /api/stock/{code}/detail` response shape should remain unchanged because
  app-level `yfinance_quote()` wrapper and existing call sites were preserved.
- Phase 0 smoke test passed after Phase 3B-1.

### 9.3 Phase 3B-2 `us_relations.py` Acceptance

Acceptance result: accepted as read-only static-data module; no adapter wrapper
is needed.

Verified current locations:

- Static relation map: `US_RELATION_MAP` in `review_src/us_relations.py:28`.
- Read-only lookup helper: `related_us_assets_for_code()` in
  `review_src/us_relations.py:413`.
- Read-only coverage helper: `relation_coverage()` in
  `review_src/us_relations.py:423`.
- App import / fallback area: `review_src/app.py:4937`.
- Detail call site: `review_src/app.py:5751`.
- Readiness summary call site: `review_src/app.py:6141`.
- Coverage endpoint call path: `review_src/app.py:6216-6218`.

Reason no `review_src/adapter/us_relations.py` was created:

- `review_src/us_relations.py` already behaves like a small read-only adapter for
  local/static relation data.
- It does not call external APIs.
- It does not read or write DB.
- It does not write cache.
- It does not call `set_status()`.
- It does not start background work.
- It does not import FastAPI or app code.
- It does not own scoring, practical-status, next-day outlook, price-volume,
  support/resistance, or chip-cost formulas.
- Adding a wrapper would add an import layer without reducing coupling or risk.

### 9.4 High-Risk Data Sources Not Handled In Phase 3

The following data-source areas were intentionally not extracted in Phase 3:

- MIS.
- TWSE.
- FinMind.
- Fugle.
- TAIFEX.
- `review_src/market/futures.py`.
- Price-volume profile/update paths.

Reason:

- These sources can affect main quote values, DB writes, data repair flows,
  scoring inputs, futures night signals, next-day outlook, or API response
  fields.
- They are not suitable as low-risk sample extractions.
- They should be handled later as separate, small phases with targeted smoke and
  data-quality checks.

### 9.5 Phase 3 Completion Decision

Completion criteria reviewed:

- Source audit completed: yes.
- First real low-risk adapter sample completed: Yahoo / yfinance read-only US
  quote adapter.
- `us_relations.py` reviewed and accepted as already adapter-like: yes.
- High-risk sources left untouched: yes.
- API response changed: no intended change.
- DB schema changed: no.
- Scoring / practical status / price-volume / chip cost / support-resistance /
  next-day outlook logic changed: no intended change.
- Phase 0 smoke test passed after the Yahoo adapter extraction: yes.

Phase 3 can be considered complete for low-risk adapter isolation samples.
