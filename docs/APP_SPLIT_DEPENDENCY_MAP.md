# APP_SPLIT_DEPENDENCY_MAP.md — Refactor R8 Dependency Map

## Summary

R8 scanned the remaining `review_src/app.py` top-level functions after R7. The file had 4163 lines before this phase and 4132 lines after the safe route-shell extraction.

This phase intentionally moved only low-coupling API route shells that could be extracted without changing formulas, response schema, DB schema, or GET side effects.

## Moved In R8

| Target module | Endpoints / functions | Risk | Notes |
| --- | --- | --- | --- |
| `review_src/api/pages.py` | `GET /`, `GET /stock/{code}`, `GET /detail/{code}` | safe_move | Static page responses only. Uses configured `ROOT`; no DB, no network. |
| `review_src/api/config.py` | `GET /api/config` | safe_move | Reads env/config and injected truststore state. No DB write, no background work. |
| `review_src/api/status.py` | `GET /api/status` | safe_move | Reads `get_status()` and current Taipei timestamp. Existing read-only behavior preserved. |
| `review_src/api/watchlist.py` | `GET /api/watchlist`, `POST /api/watchlist`, `DELETE /api/watchlist/{code}` | medium_risk | Route shell moved; POST keeps existing write/preload behavior through callback injection. GET remains read-only. |

## Remaining app.py Function Groups

| Group | Examples | Reads DB | Writes DB | Background / thread | Recommended target | Risk | Decision |
| --- | --- | --- | --- | --- | --- | --- | --- |
| FastAPI lifecycle / wiring | `lifespan`, `startup_core` | yes | yes | yes | keep_in_app | high | Keep in app. Owns startup/background wiring. |
| Update orchestration | `update_finmind_codes`, `upsert_finmind_stock_data`, `background_ensure_complete_data`, update POST routes | yes | yes | yes | services/task later | high | Blocked. Too coupled to locks, status, repair, quotas. |
| Quote/build-row orchestration | `_build_row_uncached`, `build_row`, `warm_row_cache`, quote routes | yes | optional historical state via flags | cache only unless caller triggers POST | `services/quotes_service.py` | high | Blocked. Depends on scoring, practical status, support/resistance, chip cost, valuation, price-volume, cache, and data source selection. |
| Stock detail orchestration | `api_stock_detail`, readiness/source trace assembly | yes | no on GET by design | no new thread | `api/stock_detail.py` + `services/stock_detail_service.py` | high | Blocked. Response is broad and regression surface is high. |
| Practical status referee core | `_classify_practical_status_core`, `classify_practical_status`, cached wrapper | yes | no except state persistence elsewhere | cache only | `analysis/practical_status.py` or service | high | Blocked. Main user-facing verdict; do not move without before/after JSON comparison. |
| Support/resistance + chip-cost orchestration | `calc_support_resistance_detail`, `calc_main_force_cost`, `calculate_public_chip_costs` | yes | no | no | analysis/service later | medium/high | Partially extracted in previous phases; remaining functions still DB/service coupled. |
| Next-day outlook | `synthesize_next_day_outlook`, `record_next_day_outlook`, factors | yes | `record_next_day_outlook` writes | no | analysis/service later | high | Blocked. Weighting and persistence must be separated first. |
| Debug routes | price-volume, readiness, UI completeness, outlook shadow | yes | no on GET | no new thread | `api/debug.py` | medium/high | Blocked for this phase. Many debug routes call broad app helpers; moving shells alone would add callback sprawl. |

## Route Extraction Status

Moved endpoint paths:

- `GET /`
- `GET /stock/{code}`
- `GET /detail/{code}`
- `GET /api/config`
- `GET /api/status`
- `GET /api/watchlist`
- `POST /api/watchlist`
- `DELETE /api/watchlist/{code}`

Still in `app.py`:

- update routes
- debug routes
- stock detail route
- quote routes

## Side-Effect Review

- No GET route gained DB writes.
- No new background task was added.
- `POST /api/watchlist` still starts the same preload background thread through callback injection; this is pre-existing POST behavior.
- No endpoint path or method was changed.
- No DB schema was changed.

## Recommendation

Stop large `app.py` refactor after this phase unless there is a new, narrowly scoped extraction request. The remaining groups are high-coupling and should be moved only after dedicated before/after payload snapshots for quotes, detail, practical status, and next-day outlook.
