# Bootstrap And Onboarding Rules

Read this when touching: watchlist add flow, a new stock's detail-readiness, POST idempotency for bootstrap, or polling behavior (web or LINE "查詢中" follow-up).

## Daily Chip Momentum Rules

- Watchlist intraday analysis may use realtime/intraday data; Taiwan50 stays close-batch/after-hours only.
- Fugle intraday collection disabled by default, opt-in through config; used for watchlist intraday realtime only, never historical backfill or Taiwan50 replacement.
- Fugle may help identify listed/OTC market labels, but quote calls use the stock code directly — do not append `.TW`/`.TWO`.
- If Fugle key/quota/rate limit is unavailable, return explicit unavailable/pending metadata, do not crash.
- GET routes only read already-persisted chip momentum; they must not call Fugle/TWSE/TDCC, repair queues, background tasks, or write DB. Only POST endpoints or explicit scripts may refresh daily chip momentum or intraday snapshots.
- Apply reference 05's canonical output-sanitization policy.
- Keep backend valuation payloads available for internal comparison, but user-facing valuation copy should be plain-language, not a large peer valuation table unless explicitly requested.
- Watchlist realtime and Taiwan50 close-batch flows stay separate.

## Watchlist Onboarding Bootstrap Rules

- A newly added watchlist stock may show realtime quote data before its detail-analysis tables are complete.
- Test stock codes are samples only, never functional scope. Do not hardcode sample symbols (e.g. 3491, 5425, 8261, 2481, 6435) into functional scope.
- Any valid listed/OTC stock added to watchlist enters the same bootstrap path when source support exists.
- Bootstrap readiness is based on current watchlist contents or explicit codes, not hard-coded sample symbols.
- If `market_type` is missing, attempt resolution before returning unsupported; missing `market_type` alone is not unsupported.
- Listed bootstrap: TWSE/Yahoo `.TW`/listed-compatible FinMind paths. OTC bootstrap: TPEx/Yahoo `.TWO`/OTC-compatible FinMind paths.
- TWSE source failure for an OTC code must not become `no_local_source_rows` without trying OTC-capable paths.
- Unresolved market type returns `unknown` plus `unsupported_reason`, never crashes.
- Diagnose/bootstrap responses report `market_type`, `exchange`, `yahoo_symbol`, `bootstrap_supported`, `unsupported_reason`, `suggested_action`.
- A stock is not detail-ready just because it has a price or daily chip momentum; enough history/K-line rows and required base tables must exist first.
- Do not prefill the entire market by default; full-market backfill requires explicit user request and quota planning.
- `POST /api/watchlist` may enqueue one bounded bootstrap pass or return a clear manual-required status.
- `GET /api/stock/{code}/detail`, quote GET routes, and normal page/bot reads must not write DB, call external repair sources, or enqueue bootstrap work.
- Watchlist bootstrap uses existing official/batch data-repair flows and persisted tables; do not use Fugle realtime quota for historical/detail bootstrap.
- Bootstrap status is request/runtime state unless a separate explicit persistence phase is approved; do not add hidden status tables casually.
- Missing detail data renders one concise human-readable pending message, not repeated placeholders or raw repair internals.
- Incomplete watchlist rows show human pending text such as `資料補齊中`, `技術資料補齊中`, `資料補齊後顯示`, `成本資料補齊後顯示`; do not show noisy RSI update spam or half-baked cost/support values.
- API/display payloads must pass reference 05's canonical typed sanitization before reaching web or LINE.
- Bootstrap workers must be bounded: no infinite retry loop, no unbounded background queue; failures become warnings, never crash the server.
- Detail readiness definition must be shared between API response, bootstrap worker, diagnose scripts, and frontend/bot polling — no conflicting checks.
- Taiwan50 stays close-batch oriented, never pulled into watchlist realtime/bootstrap behavior.

## Bootstrap Readiness Rules

Use one shared detail-readiness definition across API, bootstrap workers, diagnostics, and polling.

- Avoid a vague single `ready` flag as the only explanation.
- Split readiness into explicit parts: `technical_ready`, `valuation_ready`, `chip_ready`, `tdcc_ready`, `overall_ready`.
- Technical readiness requires enough OHLCV/history rows for the displayed sections. Price alone is not detail-ready. Daily chip momentum alone is not detail-ready.
- `chip_ready=false` must not block technical or valuation sections from rendering. Chip score must not contribute to displayed scoring if chip inputs are incomplete. TDCC insufficiency only affects equity-concentration display.
- Incomplete readiness returns human `pending`, `manual_required`, or `unsupported` states instead of raw diagnostics.
- Bootstrap worker, detail API, and frontend/bot polling must agree on the same readiness criteria.

## Direct Detail Onboarding Rules

Direct stock detail requests for non-ready codes show an actionable onboarding state instead of a blank result.

- Non-ready responses expose `onboarding_available`, `onboarding_action`, `bootstrap_needed`, `bootstrap_status`, `readiness`, `retry_after_seconds`, and a concise user-facing message.
- The action should be clear, e.g. `加入自選並補資料`.
- The onboarding action must call a POST endpoint, never a GET/webhook-reply-only path.
- After POST, a web client or a bounded server-side LINE worker may poll readiness; a LINE reply call itself cannot poll.
- GET/detail stays read-only — no DB write, no repair enqueue, no external bootstrap calls.
- Missing detail states render one concise missing/pending card or message.
- Same readiness definition used by bootstrap worker, API response, and polling.

## POST Idempotency And Bootstrap Race Rules

Watchlist add and bootstrap POST endpoints must be idempotent per stock code.

- Repeated POST for the same code must not create duplicate watchlist rows or enqueue duplicate jobs.
- If work is already queued/running, return the current status.
- Stable terminal statuses: `already_ready`, `manual_required`, `unsupported`, `failed`, `completed`.
- No unbounded queues. Per-code failure becomes a warning/status for that code, not a server crash.
- POST may write DB only for its explicit update/bootstrap purpose.

## Frontend/Bot Readiness Polling Rules

Polling after an explicit POST must be bounded and human-readable.

- Default upper bound: poll every 5 seconds for at most 24 attempts unless a task explicitly sets a smaller bound.
- Stop polling when `ok=true`, `readiness.overall_ready=true`, `unsupported`, `manual_required`, or `failed`.
- Disable repeated trigger actions while bootstrap is queued/running (for LINE: ignore duplicate taps/messages for the same code while a bootstrap is in flight, and tell the user it's already in progress rather than silently dropping the request).
- Do not show raw diagnostics to normal users.
- After POST, verify GET `/api/stock/{code}/detail` returns `is_watchlist=true`, `bootstrap_status` defined, `readiness` present.
- GET polling must not trigger DB writes, external fetches, background tasks, or repair queues.
- For LINE, follow reference 08's delivery decision. Long bootstrap work may use a follow-up push only when push eligibility, consent, quota, and retention controls are ready; otherwise return an honest pending/retry instruction.
