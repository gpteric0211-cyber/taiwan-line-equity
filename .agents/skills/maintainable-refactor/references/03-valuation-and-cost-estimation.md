# Valuation And Cost Estimation Rules

Read this when touching: PE/PB/dividend yield, chip cost estimation, inner/outer volume accumulation signal, or curated classification import.

## Curated Classification Import Rules

- User-maintained classification spreadsheets are analysis metadata, not normal user-facing content.
- User-maintained classification names are the authoritative taxonomy when the user explicitly requests replacement.
- Curated classification imports update backend peer-group/industry comparison logic through `stock_theme_profile`. Do not create a parallel display-only classification table unless explicitly asked.
- User-facing output must not expose classification source files, source hashes, internal table names, or raw `MANUAL_CURATED_EXCEL` labels.
- Validate peer-group mapping with representative stocks before replacing the full curated set.
- Ambiguous mapping stops and asks the user; never guess column semantics.
- Full replacement requires dry-run, old-data count, transaction, validation, rollback on failure. First import with no old curated rows requires an explicit force flag.
- Peer group classification should not rely on a single broad category when richer primary/secondary taxonomy is available; broad primary groups must be refined by secondary tags or documented as insufficiently specific. Refinement by primary + secondary tags uses intersection/AND logic, not OR.
- Primary revenue classification stays semantically available even if not directly used as `subindustry`; must not be slash-split automatically. Secondary classification columns may be slash-split only after trimming and empty-value filtering.
- Normalize spreadsheet headers before matching required columns.
- GET routes must never read spreadsheet files or import classification data.
- If a classification table cannot distinguish known different business groups, document the limitation instead of pretending precision.
- Detail title must never render literal `null`, `undefined`, `None`, or blank source values as a stock name.
- Direct detail source resolution uses local readiness before declaring missing data.
- TW50/local universe membership comes from local DB, config, constants, or component lists in GET paths; GET must not fetch external data to decide membership.
- User taxonomy display, when shown, must be deduped and not blindly concatenate semantic tags with compatibility tags; dedupe preserves first occurrence by priority: primary revenue, secondary revenue, official category, then sector/theme fallback.
- Valuation unavailable must not block technical, chip, support/resistance, or peer-group sections when those are locally ready.
- Related industry ETF/US stock mapping uses user-authoritative taxonomy fallback for clean context, shows a clean unavailable state when no mapping exists. Identify the existing mapping table location before changing fallback behavior.

## Valuation Audit And Display Gate Rules

- Official PE, PB, dividend-yield values must not be recomputed from intraday/current price in detail GET paths.
- OTC stocks use official TPEx daily valuation before Yahoo/yfinance fallback. TPEx "latest" means latest official available trading day, not today's calendar date.
- Check official TPEx endpoint availability before implementing or relying on TPEx valuation import.
- Yahoo/yfinance `dividendYield` must never overwrite official TWSE/TPEx valuation. Historical Yahoo rows may remain in local fallback metadata, but selected valuation follows official source priority.
- Suspicious Yahoo dividend yield may remain raw fallback metadata but must not appear in normal UI, peer comparison, or advice text.
- GET detail must not fetch external valuation sources; updates belong in scripts/jobs/explicit POST flows.
- PE/PB/dividend-yield payloads must carry valuation date and, when available, the valuation close price.
- Negative or zero PE/PB is unavailable/non-meaningful for display and peer comparison. Dividend yield of zero can be valid when the source unit is known.
- Suspicious dividend yields (above 20%) must be gated before display/peer-comparison/advice text and reviewed at the source row.
- High PE between 100–1000 may show as high-but-not-outlier; above 1000 is an extreme outlier.
- Official native dividend yield wins when normal. Correct official yield should be displayed when available; suspicious-value gating is a safety fallback, not the desired end state for a supported stock. If blank/unavailable, a derived dividend yield may be used only from official cash dividend per share and official reference/close price from the same official row — never derived from current quote, Yahoo/yfinance, inferred price, industry peers, or stock name. A suspicious official value stays suspicious; never overwritten by a derived value.
- Derived dividend yield must carry a machine-readable reason: `derived_from_official_cash_dividend`, `missing_official_dividend_or_price`, `official_zero_dividend`, or `derived_yield_out_of_range`.
- Valuation unavailable/suspicious states are nonblocking for technical, chip, support/resistance, and detail readiness sections.
- All-market valuation audits are report-first, never batch-update DB values without explicit user approval.
- Audit scripts, diagnose scripts, detail payloads, and peer valuation share the same normalizer. Audit scripts use SQLite read-only mode (`file:<db>?mode=ro`).
- Committed audit summaries avoid raw SQL dumps, full raw rows, large raw DB excerpts, DB files, CSV/JSONL details.
- Numeric normalization strips commas, percent signs, fullwidth spaces/digits when safe, and records parse warnings instead of crashing.

## Cost Estimation Data Audit Rules

- Audit local data availability before implementing institutional, margin, or broker-branch cost estimates.
- Do not assume `financing_amount` equals full purchase cost; distinguish loan balance from purchase value.
- Do not assume TDCC data contains investment-trust holdings unless verified in the local DB.
- Do not implement main-force branch cost without local broker branch buy/sell data.
- Margin cost display should distinguish financing loan amount per share from estimated purchase cost.
- Financing amount unit checks handle shares vs lots, skip zero/null balances. Ambiguous units are not treated as computable cost. When the unit is explicit, unused ratio fields are marked `not_applicable_due_to_explicit_unit`.

## Estimated Chip Cost Storage Rules

- Estimated chip cost uses moving-average inventory method in the free-data phase; do not claim FIFO without complete transaction-level lots.
- Daily rows retain latest 720 trading days per code and cost_type.
- GET routes must not compute, import, prune, or write estimated chip cost rows.
- Margin balance delta may support margin incremental estimated cost only, not reliable financing cost. Margin balance unit must be verified before computing incremental cost; unknown units return unavailable/unit_unknown. Reliable margin financing cost requires financing amount or equivalent authorized amount field.
- Broker-branch main-force cost requires broker branch buy/sell shares and buy/sell amount. Market POC/volume profile may support main-force reference zone only, not broker-branch main-force cost. Do not derive main-force volume by subtracting institutional/margin flows from total volume.
- Do not use unauthorized scraping, captcha bypass, Cloudflare bypass, or Goodinfo scraping. User-provided source files must not be committed.
- All estimated chip cost writes use idempotent UPSERT. Sort by code and trade_date before stateful moving-average calculations.
- NaN, Infinity, zero volume, and missing price must not be written as valid costs.
- Main-force reference zone may only use an existing POC/price-volume helper; if none exists, return unavailable with `no_valid_reference_zone_source`.
- `accumulation_status` must be unavailable whenever `cost_status` is unavailable, invalid, insufficient_data, or missing_required_source.

## Inner/Outer Volume Accumulation Watch Rule

- Inner/outer volume may only come from a legal official, licensed, or explicitly authorized source. Do not infer it by splitting total volume, daily high/low, OHLCV, price-volume distribution, or institution net buy.
- If no authorized inner/outer source is configured, return `signal_status=unavailable`, never write fake rows.
- Inner volume greater than outer volume while price is stable is only an accumulation/support observation signal, not a buy recommendation. User-facing wording must preserve that meaning using approved semantic-equivalent copy under reference 07. This signal may only supplement observation context; it must not enter main status, scoring, next_day_outlook weighting, TDCC, or broker flow.
- Three-institution net buy is not a substitute for `chip_concentration`. Missing `chip_concentration` caps signal strength at `weak`, never `medium`/`strong`.
- RSI14 must reuse existing technical data or a clearly documented helper; do not create a competing RSI formula unless explicitly approved. RSI14 below 25 must not be treated as accumulation. RSI14 declining for the latest 3 valid observations blocks formal accumulation signals. Missing/incomplete recent RSI14 history must be handled conservatively.
- Suggested RSI14 accumulation watch band: 30–45. Inner ratio should be at least 55% before an accumulation watch is considered. Total volume below 50% of 20-day average volume must not trigger accumulation.
- Large one-day price jumps, extreme turnover, ex-dividend days, halted/special-event days, or inconsistent data must block the signal.
- Valid `signal_status`: `unavailable`, `normal`, `conflict_watch`, `possible_accumulation_weak`, `possible_accumulation_medium`, `possible_accumulation_strong`, `rejected_distribution_risk`. Valid `signal_strength`: `strong`, `medium`, `weak`, `none`.
- Daily inner/outer rows retain the latest 600 trading days per stock.
- GET APIs must not import, delete, repair, or enqueue inner/outer volume jobs.
- Apply reference 05's canonical output-sanitization policy to this signal.
