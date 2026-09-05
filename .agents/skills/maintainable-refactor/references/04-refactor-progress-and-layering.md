# Refactor Strategy And Current Layering Status

Last verified against the working tree: 2026-08-28.

Read this before an `app.py` extraction or a change to the LINE/backend surface. This is a living implementation snapshot, not a fixed specification. Verify paths and responsibilities before updating a checkbox.

## Contents

1. Preferred staged extraction order
2. `app.py` split policy
3. Current dashboard split progress
4. Partial extraction notes
5. Known current deviations
6. Current LINE bot surface
7. Planned LINE extensions

## Preferred Staged Extraction Order

1. `core/`: config, DB setup, shared status/cache/data-quality helpers
2. `adapter/`: MIS, TWSE, TPEx, FinMind, Fugle, Yahoo, TAIFEX, LINE Messaging API
3. `repository/`: history, institution, margin, valuation, watchlist, price-volume, LINE subscription storage when approved
4. `analysis/`: support/resistance, practical status, chip cost, price-volume, next-day outlook
5. `services/`: stock row/detail, readiness, repair, Bot market-data, LINE message/subscription orchestration
6. `api/`: quotes, detail, update, debug, watchlist, auth, Bot market-data, LINE webhook route shells
7. `task/`: durable schedulers, repairs, and notification dispatch when introduced

The repository currently uses `services/` (plural). Do not create a competing `service/` tree. Move route shells after their service boundaries are stable.

## app.py Split Policy

- Do not add new feature logic directly to `app.py`.
- Keep only FastAPI initialization, lifespan, static mounts, router inclusion, and a small number of static-page routes in `app.py`.
- Put new HTTP/webhook route shells in `api/`.
- Put use-case orchestration in `services/`.
- Put DB reads/writes in `repository/`.
- Put external sources, including LINE Messaging API calls, in `adapter/`.
- Put pure calculations in `analysis/`.
- Put shared helpers in `core/`.
- Put durable background/scheduled work in `task/` when that layer is introduced.
- Move one functional category at a time.
- During extraction, move behavior without changing formulas, financial meaning, or approved message copy.
- Ordinary GET routes remain read-only.
- A LINE webhook POST is not a GET route: its ingress may verify, deduplicate, and dispatch bounded event work under reference 08, but must not block acceptance on slow market I/O or hide unrelated repair work.
- Keep Watchlist realtime and Taiwan50 close-batch flows separate.

## Current Dashboard app.py Split Progress

- [x] `core/market_session.py`
- [x] `core/cache.py`
- [x] `adapter/mis.py`
- [x] `adapter/yahoo_history.py`
- [x] `adapter/fugle.py`
- [x] `adapter/twse.py`
- [x] `adapter/finmind.py`
- [x] `repository/source_trace_repository.py`
- [x] `repository/history_repository.py`
- [x] `analysis/technical.py`
- [ ] `analysis/support_resistance.py`
- [ ] `analysis/chip_cost.py`
- [ ] `analysis/next_day_outlook.py`
- [ ] `analysis/practical_status.py`
- [ ] `services/data_repair_service.py`
- [x] `services/price_volume_service.py`
- [ ] `services/finmind_update_service.py`
- [ ] `services/stock_detail_service.py`
- [x] `services/industry_profile_service.py`
- [ ] `services/build_row_service.py`
- [x] `api/pages.py`
- [x] `api/config.py`
- [x] `api/status.py`
- [x] `api/watchlist.py`
- [ ] `api/quotes.py`
- [ ] `api/stock_detail.py`
- [ ] `api/update.py`
- [ ] `api/debug.py`

Unchecked files may already contain partial extractions; a checkbox means the full functional ownership has not moved.

## Partial Extraction Notes

### R5

- `analysis/support_resistance.py` now contains a versioned OHLCV support/resistance assembler in addition to formatting/level helpers, but app-level orchestration remains.
- `analysis/practical_status.py` now contains a versioned core classifier, while `app.py` still assembles DB-backed/context inputs and wrappers.
- `analysis/chip_cost.py` still mainly contains display/payload helpers.
- `analysis/next_day_outlook.py` still mainly contains low-risk factor/external-context helpers.
- Keep the four items unchecked until their full ownership and callers are verified outside `app.py`.

### R6–R8

- `repository/source_trace_repository.py`, `repository/history_repository.py`, `analysis/technical.py`, `services/price_volume_service.py`, and the currently extracted industry-profile/page/config/status/watchlist groups are complete for their stated scope.
- `services/data_repair_service.py` is partial; app-level background-thread/update-slot wiring remains.
- `services/finmind_update_service.py` remains a placeholder while update locks, DB writes, sleeps, statuses, and cache pruning remain coupled in `app.py`.
- `services/stock_detail_service.py` is partial. `services/build_row_service.py` does not yet exist.
- Quotes, detail, update, and debug route bodies remain in `app.py`; move them only after narrower service boundaries exist.

## Known Current Deviations

- The Taiwan50 `GET /api/quotes` path currently calls `latest_taiwan50_close_batch()`, whose repository reader calls `ensure_taiwan50_close_batch_schema()` before reading. That schema/commit behavior violates the ordinary-GET read-only invariant even though `init_db()` already creates the tables.
- Treat removal of that reader-side DDL as a separate behavior-preserving fix with SQL side-effect tracing and response-contract comparison. Do not hide it inside a quotes-route extraction.
- The quotes handlers and their orchestration still live in `app.py`. A future `api/quotes.py` extraction must avoid an `api -> app` dependency and must not silently change current query/default/readiness behavior.

## Current LINE Bot Surface

The LINE bot is already implemented as a separate FastAPI stack; it is not an unstarted extension.

- [x] `core/line_bot_config.py`
- [x] `auth/bot_dependencies.py`
- [x] `adapter/line_messaging.py` — signature/reply plus bounded LINE-hosted image-content download; push/quota support is not implemented
- [x] `adapter/bot_market_data_client.py`
- [x] `adapter/qwen_local.py` — local text and vision inference clients; image bytes are request-only
- [x] `repository/market_microstructure_repository.py` — read-only SQLite data contract for the Bot API
- [x] `repository/line_conversation_repository.py` — separate authenticated-encryption SQLite for conversation state, delivered exchanges, summaries, event receipts, deletion and compaction leases
- [x] `services/bot_market_data_service.py`
- [x] `services/chart_image_service.py` — vision extraction, centralized estimated-data quality gate handoff, stock resolution, and official read-only cross-check
- [x] `services/conversation_memory_service.py` — per-Channel/user/chat HMAC isolation, post-delivery persistence, restart recovery, delete/unsend/unfollow handling and rolling-summary coordination
- [x] `services/conversation_context_builder.py` / `conversation_compaction_service.py` — bounded recent two-sided turns, historical global/per-stock summaries and hallucination rejection
- [x] `core/line_model_contract.py` / `services/line_model_shadow_service.py` — versioned ModelFactPacketV2 projection, tested token preflight, strict grounding validator and deidentified post-reply candidate generation in `shadow`; candidate output never replaces the stable reply, cold-model shadow is deferred, and active warm shadow is cancellable when interactive work arrives
- [x] `services/line_bot_service.py` — current text/image reply, policy guard, natural-language follow-up routing, general investment education, memory handoff and model orchestration; oversized and a future split candidate
- [x] `api/bot_market_data.py`
- [x] `api/line_webhook.py`
- [x] `bot_app.py`
- [x] `line_bot_app.py`
- [x] `scripts/start_line_bot_stack.py`
- [x] LINE deployment/market-data docs and targeted tests

Current flow:

```text
LINE webhook -> signature verification / bounded dispatch
-> text, or bounded in-memory LINE image download -> local vision extraction
-> encrypted per-user/scope conversation context (historical context only)
-> LINE service -> authenticated local read-only Bot API
-> read-only repository / shared analysis-referee logic
-> optional local model wording -> LINE reply
-> only after successful reply: optional DB-only ModelFactPacketV2 candidate generation,
   strict validation and deidentified shadow evidence (never replaces reply)
```

The LINE gateway does not need to wait for a future web `build_row_service.py`; it already reuses shared analysis/referee logic through the read-only Bot API. Do not duplicate financial formulas in the LINE layer.
Image-derived values remain `estimated`, never enter scoring/referee inputs, never override the shared main conclusion, and image bytes are not persisted. A bounded encrypted non-image chart summary may remain for follow-up questions within the configured retention period.

## Planned LINE Extensions

These are planned targets, not existing behavior:

- [ ] Push-message and quota endpoints in the LINE adapter
- [ ] Approved subscription schema and `repository/line_subscription_repository.py`
- [ ] Subscription/notification orchestration service
- [ ] Durable `task/` notification scheduler/dispatcher
- [ ] Quiet hours, per-user caps, batching, and quota reservation
- [x] Technical deletion workflow for current scope, all principal scopes, LINE unsend and unfollow
- [x] Versioned privacy notice is available in docs and through the LINE `隱私告知` command; encrypted
  session／summaries retain 30 days while raw exchanges retain 24 hours. External public-launch legal review
  remains a separate operator responsibility.

Do not infer authorization to implement these items from this checklist. Each requires a separate scoped phase, schema/data-flow review, and reference 07–09 checks.
