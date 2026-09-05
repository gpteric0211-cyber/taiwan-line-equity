---
name: maintainable-refactor
description: Repo-specific workflow for the Taiwan stock analysis dashboard and LINE bot. Use for architecture planning, staged refactoring, bug fixing, large-file decomposition, backend data-flow changes, or user-facing stock-analysis messages that require financial-data, LINE delivery, privacy, and compliance safeguards.
---

# Maintainable Refactor

## Maintenance Metadata

- Structure version: 2.1.1
- Last reviewed: 2026-08-31
- Version 2.1.1 change: reference 06 adds reusable offline test-contract characterization,
  including baseline/DB-isolation boundaries, dependency-based evidence, diagnostic red categories,
  and the transition to scoped fix authorization without redundant root-cause review.
- Stable rules live in this entrypoint and focused references.
- Current extraction and LINE implementation status lives in `references/04-refactor-progress-and-layering.md`; verify it against the working tree before relying on a checkbox.
- External LINE and legal facts are time-sensitive. Use the official links and verification dates in references 07–09, and re-check them before launch or when platform behavior changes.

## Purpose

Use this skill for maintainable changes to the Taiwan stock analysis system while preserving its financial-data correctness, source traceability, single-verdict architecture, and existing business behavior.

The repository currently serves both a web dashboard and a LINE stock-analysis bot. Do not assume that the bot or operator is a licensed securities investment adviser. Compliance references are engineering risk controls, not legal clearance.

## When To Use

Use this skill when the task involves:

- Refactoring, reorganizing, or splitting large files, especially `app.py`
- Fixing bugs in existing backend, data, analysis, cache, or background workflows
- Adding or aligning offline test contracts for existing validators or services
- Adding or changing an adapter, repository, service, analysis module, API, schema, model, or task
- Changing quote/detail source routing, readiness, scoring inputs, or data-quality behavior
- Changing stock-specific text, Flex Messages, alerts, replies, or push content sent through LINE
- Changing a LINE webhook, Messaging API adapter, conversation flow, subscription, notification, or retention behavior

Do not use this skill for isolated web visual styling that does not touch data flow or output semantics. LINE message/Flex presentation remains in scope because wording and platform constraints are material.

## Reference Routing

Read this file first, then read every reference listed for the work in scope. Do not load unrelated references.

| Reference | Read when the task touches |
|---|---|
| [01-data-source-and-quality.md](references/01-data-source-and-quality.md) | Watchlist/Taiwan50 boundaries, listed/OTC routing, freshness, date normalization, daily chip momentum, verdicts, GET safety, price-volume gates, market-foundation or supplemental sources |
| [02-bootstrap-and-onboarding.md](references/02-bootstrap-and-onboarding.md) | Watchlist add/bootstrap, readiness, direct-detail onboarding, POST idempotency, polling |
| [03-valuation-and-cost-estimation.md](references/03-valuation-and-cost-estimation.md) | Classification imports, PE/PB/yield, chip-cost estimates, inner/outer-volume signals |
| [04-refactor-progress-and-layering.md](references/04-refactor-progress-and-layering.md) | Any `app.py` extraction or change to current LINE/backend module boundaries |
| [05-web-dashboard-presentation-and-portable.md](references/05-web-dashboard-presentation-and-portable.md) | Canonical output sanitization for API/web/LINE, web presentation, or portable packaging |
| [06-commit-and-testing.md](references/06-commit-and-testing.md) | Regression selection, commit preparation, and offline test-contract characterization of existing validators/services (test-only; no production changes) |
| [07-compliance-and-disclaimer.md](references/07-compliance-and-disclaimer.md) | Stock-specific conclusions, wording, scores, signals, alerts, onboarding terms, or legal-review gates |
| [08-line-bot-integration.md](references/08-line-bot-integration.md) | LINE webhook ingress, reply/push delivery, loading animation, quota, Flex, signature, retries |
| [09-notification-and-subscription.md](references/09-notification-and-subscription.md) | Push subscriptions, schedules, alert throttling, LINE user data, retention, deletion |
| [10-line-model-research-and-reasoning.md](references/10-line-model-research-and-reasoning.md) | LINE request planning, local-model FACTS projection, context profiles, controlled news research, evidence grounding, GPU queueing, validation, and model release gates |

Common bundles:

- LINE stock reply or Flex change: read 05, 07, 08, and 10 when routing, local-model analysis, research, or financial-answer generation can change.
- Scheduled/conditional LINE push: read 05, 07, 08, and 09.
- Watchlist onboarding shown in LINE: read 02, 05, 07, and 08.
- Valuation/cost signal shown to users: read 03, 05, and 07; add 08 for LINE delivery.

## Required Workflow

### 1. Analyze Before Editing

Inspect the current project and relevant path before modifying files. Identify:

- Entry points and files involved
- Source → adapter → DB/repository → service/analysis → API/message → user data flow
- DB tables, endpoints, cache, global state, background task, and external-source dependencies
- Existing equivalent logic elsewhere
- Whether scoring, RSI, practical status, support/resistance, chip cost, price-volume, next-day outlook, freshness, or confidence can change
- Whether output text or personal/LINE user data is affected, and therefore which references apply

Prefer `rg` for search.

### 2. Plan A Small, Testable Step

Before editing, state:

- What changes and why
- What intentionally does not change
- Expected risk level
- How the step will be verified
- For user-facing output, which sanitization, confidence, disclaimer, and restricted-wording gates apply

Split larger work into phases. Complete one reversible phase at a time.

### 3. Preserve Behavior Before Improving It

Do not delete, simplify, or reinterpret existing business logic unless explicitly requested. Preserve first:

- Stock scoring and RSI/technical rules
- Referee/practical-status classification
- Support/resistance and price-volume calculations
- Chip-cost estimation
- Next-day outlook weighting
- Data freshness and confidence rules

Behavior changes require a separate explanation, risk assessment, and verification step.

### 4. Keep Refactors Runnable

Prefer this extraction order:

1. Pure helpers
2. External-source adapters
3. Read-only repositories
4. Services/orchestration
5. API/webhook route shells

Move one functional category at a time. Do not mix formulas or product-copy changes into a file move.

### 5. Verify Every Meaningful Phase

For test-only characterization of an existing validator/service, use reference 06's
[Offline Test-Contract Characterization Work](references/06-commit-and-testing.md#offline-test-contract-characterization-work).
It preserves applicable safety and grounding rules without requiring live release benchmarks.
Documentation-only skill/review edits use document validation; do not automatically rerun the
application suite or portray a cited earlier run as newly executed.

Run the smallest relevant checks after each phase:

- `python -m py_compile <changed Python files>`
- Targeted unit tests
- Local app or LINE gateway health checks when affected
- `GET /`
- `GET /api/quotes?mode=watchlist`
- `GET /api/quotes?mode=tw50`
- `GET /api/stock/{code}/detail`
- Exact outbound LINE payload rendering and LINE validation when message code changes

If a check cannot run, report the exact reason and remaining uncertainty.

### 6. Report And Update Review State

After the task:

- Update `docs/CODEX_REVIEW_PACKET.md` with the latest review summary.
- Update reference 04 only when verified implementation/progress changed.
- Report files changed, reasons, intentionally unchanged logic, risk, tests, and remaining risks.
- For LINE output, report the applicable compliance/disclaimer rule and whether legal review is still required.

## Layering Preference

Use the repository's existing directory names; do not create parallel singular/plural trees merely to match an example.

- `api/`: HTTP/webhook route shells, request parsing, response shaping, auth dependencies
- `services/`: Use-case orchestration and user-facing payload assembly
- `repository/`: DB reads/writes and SQL; no network calls or UI formatting
- `adapter/`: External market/LINE APIs and normalization
- `analysis/`: Pure calculations and scoring
- `model/`: Internal domain models/dataclasses when a dedicated layer is warranted
- `schema/`: Request/response schemas when a dedicated layer is warranted
- `task/`: Durable background jobs, repairs, schedulers, notification dispatch when introduced
- `core/`: Config, DB bootstrap, data quality, security/output helpers, shared constants

Preferred dependency direction:

```text
api -> services -> repository / adapter / analysis -> core
```

Lower layers must not import higher layers. Avoid circular imports.

## Cross-Cutting Invariants

- Financial values must retain source/date/freshness/confidence metadata internally; never invent missing data.
- Shared data quality belongs in `core/data_quality.py` or one equivalent boundary before analysis.
- Only the referee/practical-status layer may produce the top-level user conclusion.
- Ordinary GET routes are read-only. Missing data returns explicit status instead of starting repair work.
- Watchlist realtime/intraday and Taiwan50 close-batch flows remain separate.
- Price-volume scoring enters the referee only after its coverage/status quality gate passes.
- Normal web/LINE output must use the canonical sanitization rule in reference 05 while preserving internal provenance.
- LINE webhook ingress must verify signatures and acknowledge promptly; slow work may be dispatched only under reference 08's bounded, idempotent delivery rules.
- The local language model is the analysis and explanation layer, not merely a router or template rewriter. Give it the complete relevant eligible FACTS under reference 10's packet, token, evidence, queue, and deadline contracts. Control canonical facts, data quality, the single referee conclusion, and prohibited claims; do not disable model reasoning merely because the topic is fundamentals, chips, news, global markets, or night markets.
- A disclaimer does not make regulated or misleading conduct lawful; reference 07's launch gate still applies.

## Hard Rules

- Do not remove existing business logic unless explicitly requested.
- Do not merge unrelated refactors into a bug fix.
- Do not change formulas merely because code is moved.
- Do not add hidden DB writes, repair work, or scheduling to ordinary GET routes.
- Do not expose secrets, raw user identifiers, or debug/runtime internals.
- Do not claim data correctness without naming the source and verification.
- Do not claim the bot is licensed, legally cleared, or guaranteed compliant without documented human review.
- Do not ship guaranteed-return claims, imperative personalized trades, or promised future prices.
- Do not leave an affected app unable to start.
- Do not finish Python changes without syntax checks.
