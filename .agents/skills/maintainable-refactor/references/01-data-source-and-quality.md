# Data Source And Quality Rules

Read this when touching: data freshness/trust, watchlist vs Taiwan50 boundary, listed/OTC routing, date/time normalization, futures night-session signals, price-volume scoring gate, market-foundation OHLCV storage, or supplemental data sources (Yahoo/PChome/Fugle).

## Table of Contents
1. Data Integrity Rules
2. Quote And Detail Data Source Boundary (Watchlist vs Taiwan 50)
3. Listed And OTC Source Boundary
4. Data Quality Layer
5. Date And Time Normalization Rules
6. Single Verdict Rule
7. Futures Night Session Boundary
8. No Side Effects On GET
9. Price Volume Quality Gate
10. Market Foundation Data Rules
11. Supplemental Time-Sales And Price-Volume Rules
12. Supplemental Source Engineering Rules
13. PChome Adapter Rule
14. Market Foundation Automation Rules
15. Fugle Intraday Supplemental DB Rule

## 1. Data Integrity Rules

Stock data must be treated as financial data.

- Do not invent missing values.
- Do not silently replace a trusted source with a weaker fallback without marking source and confidence.
- Keep source, date, freshness, and confidence metadata where possible.
- If a value is estimated, label it as estimated.
- If a value is unavailable, return an explicit unavailable state instead of a fake number.
- Do not expose raw API keys, tokens, or full scraping implementation details to frontend or LINE message responses.

## 2. Quote And Detail Data Source Boundary

Two separate quote/detail data flows exist and must not be mixed.

### Watchlist Flow (realtime / intraday)

Covers: watchlist quote list, stock detail opened from watchlist, single-stock detail opened by user search.

- Watchlist list and detail data may use realtime or intraday sources.
- May use Yahoo time-sales/volume-profile data for intraday support, pressure, and POC.
- During market hours may show intraday support zone, pressure zone, POC, data time.
- After close may show today support zone, pressure zone, POC, data date/time.
- A single Yahoo fetch failure must not crash the watchlist or detail page.
- If realtime data is unavailable, return explicit unavailable/fallback metadata.
- PE, PB, dividend yield must use TWSE BWIBBU official data when available; do not recompute from intraday price unless explicitly requested.

### Taiwan 50 Flow (previous-close / close-batch, not realtime)

Covers: Taiwan 50 quote list, detail opened from Taiwan 50 list, Taiwan 50 close-batch analysis.

- Must not use frontend-triggered realtime Yahoo requests, for list or detail.
- Should primarily use WIS/MIS if available, else TWSE official sources (`twse.com.tw`, `openapi.twse.com.tw`).
- Analyzes the most recent complete trading day close; if current time is intraday, still shows the latest completed close-batch result.
- API responses must include `data_date`, `updated_at`, `timezone`, `update_mode = "close_batch"`, `is_realtime = false`.
- Frontend/LINE display must show: title "前收盤台灣50大分析", data date, last updated time, update mode: close batch.
- Must not show intraday support/pressure/POC/data time.
- Support/pressure/POC must come from close-batch price-volume structure when available; if unavailable, return explicit unavailable/estimated state. Do not fake support/pressure using daily high/low.

### Detail Page Source Boundary

Detail behavior depends on entry source.

- `source=watchlist`: realtime/intraday flow, `update_mode="realtime_or_intraday"`, `is_realtime` may be true during market hours, Yahoo time-sales/volume-profile allowed for that single stock.
- `source=taiwan50`: close-batch flow, `update_mode="close_batch"`, `is_realtime=false`, reads latest SQLite close-batch result, does not fetch realtime Yahoo data during GET.
- If `source` is missing: preserve existing behavior first; prefer watchlist-compatible behavior only if it matches current app behavior; do not silently change Taiwan 50 links — Taiwan 50 links must explicitly pass `source=taiwan50`.

### GET Safety For Quote And Detail Pages

Allowed on GET: reading SQLite close-batch data, reading existing repositories, fetching realtime data for watchlist/single-stock detail only if already part of the read flow and no DB write occurs.

Not allowed on GET: running Taiwan 50 close-batch update, repairing missing Taiwan 50 data, writing new close-batch rows, enqueueing background jobs, fetching Taiwan 50 realtime Yahoo pages from the request path.

Missing data during GET must return explicit unavailable/stale/pending state, never silently start a repair or batch update.

### Detail Missing-Data UX

- Diagnose missing detail data with an explicit script or POST update flow before assuming the frontend/bot is wrong.
- Always confirm the active `DB_PATH`; portable builds may point to a separate bundled database.
- Show one concise missing-data card/message instead of repeating the same placeholder across every metric.
- Apply reference 05's canonical output-sanitization policy to missing-data output.

### Implementation Checklist

- Identify the current quote-list API, stock-detail API, and frontend/detail-link construction before changing routing.
- Preserve or explicitly pass `source=watchlist` and `source=taiwan50` at the boundary.
- Move only data-source selection in that phase.
- Do not combine routing changes with formula, scoring, practical-status, RSI, chip-cost, support/resistance, or verdict changes.

## 3. Listed And OTC Source Boundary

- Do not assume every 4-digit code is TWSE-listed.
- Listed stocks: TWSE-compatible official flows, Yahoo `.TW`, listed-compatible FinMind paths.
- OTC stocks: TPEx-compatible official flows, Yahoo `.TWO`, OTC-compatible FinMind paths.
- TWSE failure for an OTC code is not proof the stock is unsupported.
- If `market_type` is missing, resolve it before returning unsupported; missing `market_type` alone is not an unsupported reason.
- If unresolved, return `market_type="unknown"` plus `unsupported_reason`; do not crash.
- Yahoo fallback must be market-aware: listed → `.TW`, otc → `.TWO`, unknown → try `.TW` then `.TWO` with diagnostics showing both attempts.
- If `yahoo_symbol` is missing, infer it from resolved `market_type` before calling Yahoo.
- Diagnostics/payloads must include `market_type`, `exchange`, `yahoo_symbol`, `bootstrap_supported`, `unsupported_reason`, `suggested_action` when relevant.
- Apply reference 05's canonical output-sanitization policy to normal user-facing output.
- Resolver order: local DB/components first, then official lists/adapters.
- Adapter calls must explicitly pass `source_type` (`listed`, `otc`, `tw50`, `watchlist`) when behavior depends on source context; adapters should not guess market context from code alone when the caller already knows it.

## 4. Data Quality Layer

The project must have one clear data-quality boundary before analysis consumes market data. Use `core/data_quality.py` or one equivalent authority for source-date validation, T/T-1/delayed labeling, mixed-source confidence, unavailable/estimated/stale handling, and freshness normalization. Analysis modules must not invent competing trust logic. Normalize explicit quality metadata before data enters `analysis/`.

Quality states:

- `ok`: available, fresh, and trusted for the intended scoring/use
- `stale`: data exists but is older than the expected market date
- `source_delayed`: the upstream source has not published the expected update
- `estimated`: a supported estimate that must be labeled as such and gated by its consumer
- `unavailable`: the value cannot be obtained and must not be faked
- `missing`: a required input/row is absent

Do not invent missing financial values.

## 5. Date And Time Normalization Rules

- Trading dates: `YYYY-MM-DD`. Local timestamps: Asia/Taipei, `YYYY-MM-DD HH:MM:SS`.
- ROC dates, Yahoo timestamps, TWSE/TPEx/FinMind/TDCC dates, and Fugle times must be normalized inside their adapter/parser layer. Service/analysis layers must not guess raw source date formats. Output layer (frontend or LINE message) should display normalized dates/times only.
- `updated_at` = system update/import time. `trade_date`/`date`/`week_date` = underlying market data date. Do not mix them for freshness checks.
- Freshness/stale/latest-date/lag-days/trading-day checks should use shared helpers.
- Avoid `datetime.utcnow()` and naive UTC datetime objects in new code.
- TPEx ROC-year parsing must stay inside TPEx adapter/helper code.

## 6. Single Verdict Rule

Only one top-level decision path may produce the final practical stock conclusion shown to the user (web or LINE). The referee/practical-status layer is the only allowed source of the main conclusion. Legacy scoring signals (`eligible_for_watchlist`, `signal` from `scoring.py`) may only be used internally inside the analysis layer, never exposed directly as top-level output if they conflict with or bypass `classify_practical_status()`. When refactoring, preserve legacy scoring internally first, but never let multiple modules independently produce competing final conclusions — this matters even more for a LINE bot, where a user only sees one short message and cannot cross-check two conflicting verdicts.

## 7. Futures Night Session Boundary

- `adapter/taifex.py`: fetches futures quotes/settlement/raw TAIFEX data only.
- `analysis/futures_signal.py`: computes night-session direction, strength, confidence.
- service/referee layer: consumes futures signal only as an adjustment factor.
- Night-session futures signals must not override the main stock practical status directly; `can_override_main_status` must always be `False`. Referee layer may use it only as weighting/confidence adjustment/contextual note.

## 8. No Side Effects On GET

Read-only API GET paths must not trigger DB writes, repair jobs, background jobs, or enqueue missing-data tasks. `enqueue_missing=True` or similar write-triggering behavior is only allowed in `task/`, explicit POST/update endpoints, or scheduled background jobs. Do not hide repair/scheduling inside ordinary quote/detail/Bot market-data GET requests. If data is missing during GET, return explicit unavailable/stale/pending state.

A LINE webhook is a POST event ingress, not a GET route. Its bounded dispatch and explicit event-side effects follow reference 08; this does not authorize hidden market repair or writes inside read-only data services.

## 9. Price Volume Quality Gate

Price-volume analysis may output a score to the referee layer only when `coverage_days >= required_days * 0.8` and status is `ok`. States that must not contribute score: `source_delayed`, `missing`, `unavailable`, and `stale` (unless a later rule explicitly allows it with reduced confidence). `available=True` alone is not enough for scoring — the module must return explicit metadata explaining why it was excluded when not qualified.

## 10. Market Foundation Data Rules

- Daily OHLCV main storage uses the existing `history_price` table when it has `date, code, open, high, low, close, volume`. Do not create a duplicate table unless `history_price` cannot safely represent daily OHLCV; document the reason in `docs/DATA_FILE_MAP.txt`.
- OHLCV source priority: `TWSE_OFFICIAL`/`TPEX_OFFICIAL` → `FINMIND` → supplemental `YAHOO`/`PCHOME`.
- Do not use `INSERT OR REPLACE` for OHLCV imports; use upsert logic preserving stronger official sources over weaker supplemental ones.
- Retention: latest 600 distinct trading days for market foundation; data-source audit rows ~2 years, pruned by `started_at`.
- Prune jobs: bounded batches, keep SQLite indexes, run `PRAGMA optimize`, avoid automatic `VACUUM`.
- `docs/DATA_FILE_MAP.txt` must be generated from schema introspection by an independent generator, not hand-written guesses; if generation fails, stop and report rather than produce a misleading partial map.
- Watchlist detail pages may use realtime data during market hours; sections needing complete intraday records show `台股盤中，暫停顯示` when same-day data is incomplete (TODO gate — do not mix into unrelated data-foundation work). US/industry ETF sections and TDCC equity concentration are excluded from this pause rule.

## 11. Supplemental Time-Sales And Price-Volume Rules

- Official market data preferred for all-market batch updates. Yahoo time-sales or similar are supplemental only and must be marked `SCRAPED`.
- Supplemental time-sales may be aggregated by price only when explicitly enabled by the user.
- Unknown/blank/missing buy-sell side counts as neutral volume; only explicit BUY/SELL source fields become buy/sell volume. Do not infer buy/sell side with tick rules unless a separate approved phase defines that behavior.
- Single-stock supplemental fetches must sleep at least one second between requests.
- Full-market supplemental scraping requires both `--include-scraped` and `--allow-full-scrape`. `--official-only` overrides `--include-scraped` and must emit a warning.

## 12. Supplemental Source Engineering Rules

- API keys never hard-coded in source, docs, logs, reports, or commits — always from config/env.
- Future supplemental data, if formally persisted, must record `trade_date`/`date` on every row.
- Intraday supplemental data only available for the current trading day must be collected same-day after market close; do not assume next-day backfill is possible.
- Same-day supplemental collection failures must be marked `SOURCE_DELAYED`/`FAILED`/`PARTIAL` — never pretend complete.
- Supplemental retention/prune rules stay separate from official `history_price` retention (official stays 600 trading days). Prune decisions based on `trade_date`/`date`, not `fetched_at`.
- Do not infer true inner/outer volume from tick rules when source lacks explicit side fields. Tick-rule inferred side, if approved separately, must be marked `estimated`, never labeled true inner/outer volume.
- If `volumeAtBid`/`volumeAtAsk` or equivalent fields are used, confirm source semantics before mapping to inner/outer volume; avoid reversed direction mapping.
- Fugle/other supplemental intraday data, if approved for formal DB storage, retains latest 300 trading days unless the user explicitly approves a different policy.

## 13. PChome Adapter Rule

- PChome megatime support remains skeleton/TODO until an explicit source-approval phase.
- Do not implement Cloudflare/session/cookie bypass, browser automation, or aggressive retry logic.
- PChome rows must not be marked official.

## 14. Market Foundation Automation Rules

- Market data updates run via CLI, batch files, scheduled tasks, or explicit POST/update flows — never GET routes. GET must not run market-foundation imports, write DB rows, enqueue repair jobs, or start background work.
- Daily official updates default to official-only sources; Yahoo/PChome supplemental sources are not enabled by default for all-market scheduled updates. PChome remains skeleton/TODO.
- Daily scheduled updates may start at 15:00 and retry only until 23:59. `time-window-start` is informational only and must not block manual runs before 15:00.
- Retry logic uses machine exit codes only, never parses log/report text. Exit codes: `0=SUCCESS`, `1=CLI_ERROR`, `2=FATAL_ERROR`, `3=SAFETY_ABORT`, `4=PARTIAL_RETRYABLE`, `5=SOURCE_DELAYED_RETRYABLE`. Retry helpers may retry only `4` and `5`. `max-retries` and `time-window-end` are both hard limits; stop at whichever is reached first.
- All-market official OHLCV updates must compare TWSE and TPEx official data dates before writing DB rows; if they differ, stop with `SOURCE_DELAYED_RETRYABLE`, keep `writes_db=false`, no audit rows written.
- Do not fill delayed TWSE listed OHLCV with Yahoo/PChome/Fugle/FinMind or any weaker fallback.
- Verification must distinguish `global_latest_date` from `official_latest_date`; supplemental/non-official rows must not hide official completeness.
- `--official-only` overrides `--include-scraped`, skips supplemental sources, exits successfully with a warning logged.
- `--quiet` suppresses console progress but logs/reports stay complete. Manual/scheduled/backfill runs must report progress and completeness. Backfill must be resumable, write state atomically, keep a backup state file, and must not update formal DB/backfill state during dry-run. Backfill must handle missing/empty DB state gracefully (skipped/source-delayed result, not a crash).
- Do not commit runtime logs, runtime report archives, backfill state, DB files, CSV/XLSX/JSONL extracts, or generated package files. `docs/DAILY_UPDATE_REPORT.txt` and `docs/BACKFILL_UPDATE_REPORT.txt` may be committed only as placeholders without runtime data. `logs/market_foundation/.gitkeep` may be committed; `.gitignore` must not ignore it.
- Python cache ignore must use exact `__pycache__/`. Windows `.bat`/`.cmd`/PowerShell scripts must use script-relative paths, never hard-code the developer machine path. Ordinary developer launchers should use UTF-8 code page and prefer `.venv/Scripts/python.exe` before a configured system-Python fallback; portable launchers must follow reference 05 and never fall back to system Python.

## 15. Fugle Intraday Supplemental DB Rule

- Fugle intraday supplemental DB writes must be explicit script or POST/update flows; ordinary GET/Bot read endpoints must not write Fugle data. Any bounded, non-persistent single-stock read fallback must follow the existing Watchlist boundary and reference 08's LINE latency rule.
- `FUGLE_API_KEY` must never be printed in full, committed, written into docs/logs, or exposed in frontend/LINE payloads.
- Formal intraday supplemental imports must preserve source `trade_date`/`date`; never use `fetched_at` as the market data date.
- Fugle intraday data is a supplemental family, retained/pruned separately from official `history_price` (latest 300 trading days vs official 600).
- `volumeAtBid`/`volumeAtAsk` stored as source bid/ask fields until their Chinese inner/outer meaning is confirmed. Do not infer formal inner/outer volume from aggregate volume fields with tick rules.
- Per-trade `side_inferred` allowed only as a supplemental inferred field when an explicit phase requests it; never labeled exchange-original inner/outer volume.
- Side inference priority: `price >= ask → ASK`, `price <= bid → BID`, `bid < price < ask → MID`. Tick fallback only when usable bid/ask is missing; first trade uses local `history_price` previous close, later trades use previous trade price. Same-price tick fallback may carry previous known ASK/BID/MID, no separate carry-forward method.
- Invalid quote spreads labeled `INVALID_QUOTE`; missing previous close labeled `UNKNOWN`. Persist method, confidence, reason, previous-price metadata with the row; never leave `side_inferred`/`side_label_zh`/`side_method`/`side_confidence`/`side_reason`/`prev_price_source` as NULL — use explicit `UNKNOWN`/`無法判斷`/`fallback unknown`.
- Backfill of existing Fugle trade rows only via explicit `--backfill-side-inferred` flag; must not call Fugle API or mutate original price/bid/ask/volume fields. Dry-run unless `--write` present; without `--date` processes all NULL/blank rows, with `--date YYYY-MM-DD` processes only that date.
- Verification reports NULL and UNKNOWN separately for `side_inferred` and `side_method`; do not merge them.
- If Fugle same-day data is delayed/partial/missing, mark `SOURCE_DELAYED`/`PARTIAL`/`FAILED` — never fake complete data.
