# Next Day Outlook Coverage Audit — After D3B

## Summary

* generated_at: `2026-06-19T18:59:39+08:00`
* base_url: `http://127.0.0.1:8057`
* before_with_formal: `2`
* after_with_formal: `5`
* before_missing: `3`
* after_missing: `0`
* improved_codes: `2308, 2330, 2454`
* still_missing_codes: `none`
* total codes: `5`
* with_formal_next_day_outlook: `5`
* missing_formal_next_day_outlook: `0`
* warn: `0`
* fail: `0`

## Per-Code Coverage

| Code | Before | After | Score | Direction | Confidence | Root Cause | Fix Applied | Still Missing Reason |
| ---- | ------ | ----- | ----- | --------- | ---------- | ---------- | ----------- | -------------------- |
| 2317 | present | present | 50.0 | 中性 | 中 | N/A | N/A | N/A |
| 2330 | missing | present | 60.0 | 偏多 | 中高 | readiness gate treated source-delayed institution/margin/foreign-shareholding data as blocking | front-close mode treats sufficient source-delayed chip rows as warning metadata | N/A |
| 2382 | present | present | 60.0 | 中性 | 中高 | N/A | N/A | N/A |
| 2454 | missing | present | 58.0 | 中性 | 中高 | readiness gate treated source-delayed institution/margin/foreign-shareholding data as blocking | front-close mode treats sufficient source-delayed chip rows as warning metadata | N/A |
| 2308 | missing | present | 56.0 | 中性 | 中 | readiness gate treated source-delayed institution/margin/foreign-shareholding data as blocking | front-close mode treats sufficient source-delayed chip rows as warning metadata | N/A |

## Exact Gate Fixed

* file: `review_src/app.py`
* function: `data_readiness_for_items()`
* original condition: in front-close / Taiwan 50 mode, sufficient `institution_daily`, `margin_daily`, and `foreign_shareholding` rows could still be appended to blocking `issues` when their latest date lagged the latest K-line.
* new condition: in front-close / Taiwan 50 mode, sufficient-but-delayed institution, margin, and foreign shareholding rows are recorded under `data_quality_warnings` with `source_delayed` status instead of blocking formal detail/outlook.
* why minimal: no formula changed, no API schema changed, no DB schema changed, no frontend changed, no new source added.

## Data Quality Policy Applied

* Usable for formal outlook/readiness in front-close mode: sufficient rows with expected `source_delayed` status for institution, margin, and foreign shareholding data.
* Still blocking: missing price, insufficient K-line rows, technical indicators unavailable, support/resistance unavailable, valuation required fields missing, required numeric cost cells invalid, source trace missing, true row-count shortage.
* This audit does not treat unavailable data as neutral score and does not invent missing financial values.

## Missing Outlook Analysis

* No missing formal next_day_outlook in checked codes.

## Factor Timestamp Source Inventory

| Factor | Function | File/Line | Current Time Field | Freshness/Decay Exists | Safe for Shadow? | Missing Pieces | Notes |
| ------ | -------- | --------- | ------------------ | ---------------------- | ---------------- | -------------- | ----- |
| us | us_sentiment_factor() | review_src/app.py:5668 | `regular_market_time` from `_source_quotes`; fallback quote `date` from yfinance quote payload. | `_source_age_decay_from_quote_time()` uses quote timestamp/date and returns freshness/decay. | Partial | No normalized `data_time`/`published_at` contract; source metadata remains ad hoc. | Reads `us_forecast` and `us_assets` built in detail endpoint; no DB required inside factor. |
| futures_night | futures_night_factor() / futures_night_signal_for_stock() | review_src/app.py:5703 + review_src/market/futures.py:149 | `futures_night.date` from TAIFEX `Date` field in `DailyMarketReportFut` rows. | `futures_night_factor()` uses `_source_age_decay_from_date(date)`. | Partial | `belongs_to_trade_date`, session close time, and normalized `data_time` are not available. | TAIFEX adapter has `_taifex_cache`; factor itself consumes already-built payload. |
| chip | chip_factor_for_stock() | review_src/app.py:5735 | Latest max date from `institution_daily.date` and `margin_daily.date`. | Uses `_source_age_decay_from_date(latest_date)`. | Partial | No `published_at`; no distinction between expected T+1 delay and unexpected stale data. | Reads DB through `db()`; moving to shadow directly could create coupling unless a read model is added. |
| tech | tech_factor_for_stock() | review_src/app.py:5829 | `technical_context_from_rows(rows_asc).date`, sourced from history_price rows. | Uses `_source_age_decay_from_date(latest_date)`. | Partial | No explicit source quality object; depends on caller-provided rows. | No DB read inside function, but source rows are built by detail endpoint from DB. |
| gate | next_day_outlook_gate() | review_src/app.py:5992 | `tw_market_session_now()` session object. | No factor freshness; only display gate by Taiwan market session. | No | No normalized market calendar contract in formal path. | Can hide/replace formal outlook when regular/closing/holiday logic applies. |
| synthesize | synthesize_next_day_outlook() | review_src/app.py:5894 | Aggregates factor `date`/`freshness` into `sources`; no top-level `as_of_time`. | Consumes per-factor freshness but applies fixed weights. | No | No target trade date, no normalized factor contract, no shadow score path. | Formal scoring function; D3A does not modify it. |
| record | record_next_day_outlook() | review_src/app.py:6033 | `recent_market_date_for_eod()` as calc_date; `time.time()` as generated_at. | Persists payload sources as JSON, but not a normalized freshness table. | No | DB write path; not suitable for read-only shadow diagnostics. | Formal persistence function; detail currently calls synthesize with `persist=False`. |
| detail endpoint | api_stock_detail() | review_src/app.py:6156 | Builds `rows_asc`, `us_forecast`, `futures_night`, `practical`, `sr_detail`, and calls gate/synthesize. | No top-level formal freshness; details are scattered under factor payloads. | No | No formal diagnostics for why next_day_outlook is absent. | Formal API response must not receive shadow fields in this phase. |

## Formal API Protection

* 2317: PASS no forbidden shadow keys found in formal detail response.
* 2330: PASS no forbidden shadow keys found in formal detail response.
* 2382: PASS no forbidden shadow keys found in formal detail response.
* 2454: PASS no forbidden shadow keys found in formal detail response.
* 2308: PASS no forbidden shadow keys found in formal detail response.

Forbidden keys checked:
* `shadow_outlook`
* `freshness_adjusted_outlook_shadow`
* `factor_freshness`
* `calendar_status`
* `calendar_debug`
* `debug_shadow`
* `override`
* `market_calendar`

## Production Safety

* No DB writes are performed by this script.
* No API or frontend files are modified by this script.
* No external data sources are fetched by this script.
* No production outlook takeover is performed.
* No shadow adjusted score is calculated.
* No background task, scheduler, or server is started by this script.

## Machine Summary

```json
{
  "base_url": "http://127.0.0.1:8057",
  "fail": 0,
  "formal_api_protection_failures": 0,
  "generated_at": "2026-06-19T18:59:39+08:00",
  "missing": 0,
  "missing_categories": {},
  "total": 5,
  "warn": 0,
  "with_formal": 5
}
```
