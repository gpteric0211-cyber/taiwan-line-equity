# Single-Track V3 Stage 0 Contracts V11

Status: `awaiting_user_approval`  
Contract set: `SingleTrackV3Stage0ContractSetV11`  
Normative timezone: `Asia/Taipei`  
Objective: `Codex 單一主軌最終目標指令 V11`

## Approval boundary

This document freezes the candidate contract for A–O. It is documentation-only and does not authorize a production behavior change, a database migration, a scheduler installation, a rollout change, a new non-zero factor weight, a predictive claim, or a public launch.

The working tree already contains additive Single-Track V3 work produced before the V11 approval boundary was discovered. Stage 0 characterizes that work in place; it does not delete it, promote it, or represent it as approved. Stable production behavior remains authoritative. New event, chip, technical, magnitude, or provisional-intraday contributions remain `weight=0` or Shadow until their later statistical and release gates pass.

The valid implementation states in this review are:

- `implemented`
- `implemented_but_unverified`
- `partial`
- `missing`
- `invalid_or_stale`
- `out_of_scope`

## Implementation matrix

| ID | Contract | Current state | Principal evidence | Blocking gap |
|---|---|---|---|---|
| A | StockUniverseContractV1 | `partial` | Official TWSE/TPEx stock master; active-as-of batch reader | No immutable official membership revision proves every historical as-of universe or all security-type exclusions |
| B | StableBaselineContractV1 | `invalid_or_stale` | Historical Stage 0 baseline and protected-file manifest | Current protected check is 10/11; old test, schema, rollout, and runtime observations are stale |
| C | RequestNormalizationAndProfileContractV1 | `partial` | Entity normalizer, conversation projection, ModelFactPacketV2 profiles | No single versioned request contract covers every requested field and canonical normalized request key |
| D | ScoreAggregationContractV1 | `partial` | Unique legacy referee and candidate artifact metadata | Unified fixed-bucket aggregator and independent confidence function are not released |
| E | MaterialityClassificationContractV1 | `partial` | Event safety scan and V3 event revision schema | Current classifier is service-layer and only material/non-material/unknown; target materiality and five-level classification are absent |
| F | ChipAnalysisContractV1 | `partial` | Official institution/credit/TDCC data and estimated-cost contracts | Known red flags and insufficient walk-forward evidence prevent candidate chip weights or subweights from release |
| G | TechnicalFormulaAndAdjustmentContractV1 | `implemented_but_unverified` | TechnicalFormulaV1-candidate.1, TechnicalEnsembleV1, scheduled materializer, tests | Candidate formula freeze has not completed a V11 evidence cycle; protected baseline also has one unrelated mismatch |
| H | EntityResolutionContractV1 | `partial` | StockEntityRegistryV1 resolver, alias table/materializer, tests | Registry lifecycle/rollback evidence and predictive zero-error denominator are incomplete |
| I | LegalComplianceLaunchContractV1 | `missing` | Engineering fail-closed restrictions only | LegalReviewPacket and written external legal decision do not exist |
| J | SourceRightsAndNewsGovernanceContractV1 | `partial` | SourceAuthorityPolicyV1, news rights registry, EventSourcePlanV3, retrieval adapters | Full geopolitical licensed/official coverage and current production scheduler evidence are missing |
| K | PrivacyImageSecurityContractV1 | `partial` | Shared ImageInputContractV1, Web POST, LINE path, encrypted memory, tests | Complete privacy/notice/retention and SSRF regression evidence has not been frozen in one V11 cycle |
| L | OperationalMonitoringContractV1 | `invalid_or_stale` | Durable scheduler code and offline probes | Stored audit is stale; live Windows task is absent; restart/wake/production catch-up is unverified |
| M | ConcurrencyAndTerminalCascadeContractV1 | `partial` | Existing V3 assessment repositories, CAS, terminal cascade, outbox, reconciler tests | End-to-end model worker/admission/scheduler runtime and multi-process readiness are unverified |
| N | ImpactMagnitudeAndCalibrationContractV1 | `partial` | Existing `magnitude_probabilities` fields and strict target assessment validator | No released backend magnitude calibrator, target sigma contract, or mature prospective evidence |
| O | AnalysisSessionBoundaryContractV1 | `partial` | Close-batch snapshot, event artifacts, selector/repositories | Three digest layers and explicit provisional-intraday target/factor contract are not complete |

## Shared invariants

1. Data flow remains `source -> adapter -> repository/DB -> analysis -> service -> canonical artifact -> API -> Web/LINE renderer`.
2. GET routes are read-only. Missing data returns an explicit quality state and never silently schedules or repairs data.
3. Qwen does not read the DB, browse, create canonical facts, override the referee, or create a second `main_status`.
4. Every model-originated numeric/date/factual claim requires an eligible `evidence_id` with cutoff, unit, quality, and use-scope binding.
5. Missing or ineligible weight is not redistributed. It reduces coverage and, through a separately versioned function, confidence.
6. All persisted times are offset-aware Asia/Taipei values. `available_at`, `first_seen_at`, and `usable_from` are immutable; revisions create new rows.
7. Stable is the actual production path immediately preceding candidate promotion, not a deliberately weak comparator.
8. Web and LINE share market facts, data-quality rules, factor snapshots, referee, selector, canonical artifact, conclusions, omissions, and evidence. Channel-specific rendering cannot change the core conclusion.
9. Completion maturity is reported separately as implementation, Shadow evidence, predictive evidence, and public-release eligibility.

## A. StockUniverseContractV1

Version: `StockUniverseContractV1`  
Approval state: `candidate_unapproved`  
Implementation state: `partial`

Normative universe:

- Include TWSE-listed and TPEx-listed ordinary shares from official stock masters, including formally listed KY ordinary shares.
- Exclude ETF, ETN, funds, warrants, convertible bonds, preferred shares, TDR, and Emerging Market securities.
- Retain a suspended security as an entity, while determining forecast eligibility independently.
- If listing history or indicator history is insufficient, lower coverage or return `unavailable`; do not impute history.
- Backtests and evaluation use membership effective on the target date. Current membership must not be projected backward.
- Every universe snapshot records source IDs, source dates, membership revision, security type, effective interval, and a sorted-code digest.

Forecast eligibility requires a member on the target date, an eligible official calendar session, sufficient target-specific data, no terminal trading restriction, and a valid prediction cutoff. Suspension/no-trade produces an explicit reason, not deletion from the entity registry.

Current evidence: `stock_master`, `sync_official_stock_master`, and `active_stock_codes_asof` provide a useful current/historical approximation. The current first/last-seen fields and history-derived fallback do not constitute a complete immutable official membership history, so survivor-bias protection is not yet proven.

## B. StableBaselineContractV1

Version: `StableBaselineContractV1`  
Approval state: `candidate_unapproved`  
Implementation state: `invalid_or_stale`

The baseline must bind:

- Git commit and a precisely defined dirty-worktree status digest.
- Protected analysis hashes and mismatch list.
- Formula, referee, schema, rollout, model, prompt, validator, renderer, and source-policy versions.
- Golden fixtures, representative Web/LINE outputs, and source dependency manifest.
- The actual production route and selector; candidate-only code is not the Stable comparator.

Current characterization:

- HEAD is `6d9e34b2875058394004800a605509f049b45ea2` and the tree is dirty.
- The status digest is SHA-256 over raw `git status --porcelain=v1 -z`; it is an inventory digest, not a content digest.
- `docs/SINGLE_TRACK_V3_STAGE0_BASELINE.json` is historical evidence captured on 2026-09-01. Its 1,450-test result, Stage 0 `complete` claim, schema inventory, runtime facts, and 11/11 protected result are not current V11 evidence.
- The current protected check is 10/11. `review_src/services/price_volume_service.py` differs from the approved protected baseline. Stage 0 does not decide whether that change is valid; it records the mismatch.
- Current source schema is `single-track-v3-stage8.13`; the production DB currently reports all 44 required V3 tables and the same schema-state version.
- Current effective rollout is model `shadow`, research `shadow`, no requested profile override, no valid canary percentage, and no release authorization. Candidate delivery therefore remains fail closed.

No Stage 1 work may rely on the old baseline as if it were current. Before a later code-freeze cycle, the protected mismatch must be reviewed, then either restored by an authorized change or explicitly rebaselined with new golden evidence.

## C. RequestNormalizationAndProfileContractV1

Version: `RequestNormalizationAndProfileContractV1`  
Approval state: `candidate_unapproved`  
Implementation state: `partial`

Request normalization is deterministic:

1. Decode as Unicode, apply NFKC, HTML-unescape where applicable, normalize whitespace, and normalize non-semantic punctuation.
2. Extract explicit four-digit code, official full/trading name, reviewed alias, intent, forecast target, requested date, depth, scopes, and exclusions.
3. Resolve entity in H-order. An explicit target in the current request outranks conversation context.
4. Apply conversation context only when it is unexpired, unambiguous, and does not conflict with an explicit target/date/scope.
5. Select a profile after intent and scopes are known. Unknown profiles deterministically fall back to the applicable stable 16K profile and record `omission_reason`/fallback reason.
6. Build `normalized_request_key = sha256(canonical_json({contract_version, entities, intent, forecast_target, requested_date, depth, scopes, exclusions, session_id, analysis_cutoff, selected_profile}))`. Lists are deduplicated and sorted unless order is semantically meaningful.

Frozen profiles:

| Profile | Prompt cap | Minimum model context | LINE reply-only internal budget | Release state |
|---|---:|---:|---:|---|
| `focused-16k-v1` | 6,000 tokens | 16,384 | 31 seconds | release profile |
| `comprehensive-16k-v1` | 11,000 tokens | 16,384 | 45 seconds | release profile |
| `comprehensive-32k-candidate-v1` | 20,000 tokens | 32,768 | no relaxed LINE SLA | candidate-only; Shadow/offline |

The 32K profile is selected only by an explicit eligible request. It may replace comprehensive 16K only after a separate Phase D on identical hardware and competitive load, at least 100 completed samples per release profile, and ingress-to-reply p95 below 45 seconds. Samples are not interchangeable across profiles. Failure causes deterministic 16K downgrade with an omission reason or continued offline/Shadow use.

## D. ScoreAggregationContractV1

Version: `ScoreAggregationContractV1`  
Approval state: `candidate_unapproved`  
Implementation state: `partial`

All factor direction scores are dimensionless values in `[-1,1]`. Coverage, freshness, source quality, and calibration quality remain separate `[0,1]` fields. The candidate directional aggregation is:

`raw_direction = sum(weight_i * eligibility_i * factor_score_i)`

- Eligible and available: `eligibility=1`.
- Unavailable, stale, source-delayed beyond grace, or not calibrated for the requested use: `eligibility=0`.
- Missing weights are never redistributed.
- `coverage` is the sum of available fixed weights.
- Conflicting eligible factors emit a versioned `conflict_state` and lower confidence.
- Confidence is produced by a separate versioned function; it is not raw direction or coverage.
- Only the existing referee/practical-status layer produces `main_status`. Qwen cannot produce a competing formal direction.

Candidate family weights, not authorized for Stable:

| Family | Normal | Material-event |
|---|---:|---:|
| Verified event/company context | 10% | 35% |
| Overseas/industry reaction | 15% | 20% |
| Target price, trend, volume, volatility | 30% | 15% |
| U.S. market and Taiwan night market | 15% | 15% |
| Institution, margin, short, SBL chips | 20% | 10% |
| Dilution, valuation, other risk | 10% | 5% |

Only a verified high/critical event with eligible target direction and impact may select the Material-event candidate regime. Pending direction/calibration yields `material_pending` or `suppressed_pending`, `eligibility=0`, and no weight redistribution. Stable retains its existing regime until a separate statistical promotion decision.

## E. MaterialityClassificationContractV1

Version: `MaterialityClassificationContractV1`  
Approval state: `candidate_unapproved`  
Implementation state: `partial`

The contract keeps distinct fields for `content_materiality`, `target_materiality`, `target_direction`, `target_impact_magnitude`, `verification_state`, and `regime_selection`. Materiality levels are `critical`, `high`, `medium`, `low`, and `unknown/pending`.

Conservative deterministic candidate rules:

- Any unverified, conflicted, source-incomplete, entity-unresolved, or revision-incomplete event is `unknown/pending` for formal weighting.
- `critical` requires verified official evidence, a direct target relationship, and one of: target-specific trading halt/suspension caused by the event; bankruptcy/dissolution; an effective control change; an official sanction/export prohibition directly naming the target; or an official catastrophic operational event that prevents core operations.
- `high` requires verified evidence and a direct target relationship for financial results/guidance, material financing/dilution, merger/control proposal, major order/capacity/asset action, formal enforcement/litigation/recall/cyber/operational incident, or directly applicable government policy. It does not imply a direction or calibrated magnitude.
- `medium` covers a verified direct event without all high/critical predicates, or a verified parent/subsidiary, customer, supplier, peer, industry, rate, FX, commodity, geopolitical, or shipping event with strong target relevance.
- `low` covers verified routine disclosures, weak indirect relevance, and article mentions that do not establish target impact.
- Any class can remain target-direction or magnitude pending. Materiality alone never changes regime or creates a directional weight.

The taxonomy includes earnings/guidance/investor conference/monthly revenue; orders/partnerships/products/capacity; M&A/control/investment/assets; capital actions/buybacks/convertibles/dividends/dilution; regulation/litigation/penalties/recalls/cyber/operations; industry/peer/supply chain; rates/FX/commodities; U.S./Taiwan policy; war/sanctions/geopolitics/shipping; and unverified community claims.

The classifier belongs in `analysis/`; services coordinate, repositories persist, and retrieval only acquires evidence. Qwen may propose extraction, rationale, and counterevidence but cannot decide the released regime. The current service-layer keyword classifier and three-state schema remain characterized as partial and must not be promoted as this contract.

## F. ChipAnalysisContractV1

Version: `ChipAnalysisContractV1`  
Approval state: `candidate_unapproved`  
Implementation state: `partial`

Eligible candidate facts are separate foreign/trust/dealer buy, sell, and net shares; the full margin and exchange-short flow/balance fields; SBL short balance/change; official TDCC weekly buckets; and official shares outstanding/corporate actions.

The following are prohibited: double voting institution total and constituents; adding shares to lots; adding exchange shorts to SBL; voting institution net and share-of-volume twice; treating short-to-margin ratio as another vote; calling a volume-price concentration true main-force cost; or inferring a TDCC bucket as an investor identity.

Cost fields are explanation-only estimates:

- Foreign/trust values are recent incremental-position weighted-average estimates, never true total holding cost.
- Dealer true cost, margin-holder true cost, and margin utilization without an official limit are unavailable.
- Broker/main-force cost is unavailable without authorized branch amount/volume.
- Estimated costs have `weight=0`, `referee_eligible=false`, and `next_day_outlook_eligible=false`.

Candidate chip-family allocation, not activated: institutions 50%, credit/SBL 30%, TDCC 20%, branches 0%, estimated cost 0%.

Credit/SBL is split into margin financing, exchange short selling, SBL, and capped interactions. Each first becomes a versioned dimensionless `[-1,1]` score with unit, period, `usable_from`, quality, and normalization version. The internal nonnegative subweights must sum to 1, but this Stage 0 has insufficient unit-correct walk-forward/correlation evidence to propose a safe non-zero split. Therefore all new credit/SBL subfactor contributions remain 0 until Stage 2 evidence supports a separately reviewed candidate. Missing inputs are not redistributed.

TDCC weekly freshness candidate:

- `source_week_date` is the official last business day represented by the row; no fake daily observations are created.
- `publisher_published_at`, `available_at`, revision, source policy, and the next expected publication are stored independently.
- [TDCC's official dataset description](https://www.tdcc.com.tw/portal/zh/smWeb/qryStock) states the dataset is compiled after the last business day each week but does not promise an exact public timestamp. The conservative candidate `expected_next_publish_at` is 23:59:59 Asia/Taipei on the next official weekly source business day.
- Approved-grace candidate: 72 hours after `expected_next_publish_at`. The prior revision remains `ok` during this grace window.
- After grace: `source_delayed`, `eligibility=0`.
- Hard-stale candidate: 14 calendar days after `expected_next_publish_at`, or after two expected weekly releases are missed, whichever occurs first; then `stale`, `eligibility=0`.
- No linear/exponential decay is allowed before walk-forward evidence. One artifact cites a TDCC revision once.

Known Stage 2 red gates remain: polluted legacy `margin_delta`; incorrectly derived TDCC small-holder bucket; estimated costs lacking complete corporate-action handling; possible zero-row branch/margin amount data; and `daily_chip_momentum` not being a new total chip score.

## G. TechnicalFormulaAndAdjustmentContractV1

Version: `TechnicalFormulaAndAdjustmentContractV1`  
Approval state: `candidate_unapproved`  
Implementation state: `implemented_but_unverified`

The candidate source freeze is `TechnicalFormulaV1-candidate.1`, `TechnicalEnsembleV1`, and `TechnicalEnsembleScoreV1-candidate.1`. Inputs use verified split-adjusted OHLC with raw volume under `verified-split-adjusted-ohlc-raw-volume-v1`. Per-component formula, parameters, unit, warm-up, input digest/range/count, adjustment basis, and quality are persisted. Full-ensemble minimum history is 240 rows and retained component/vector history is at least 600 trading days. Request paths neither calculate nor write candidate technical vectors.

The materialized set includes RSI, MA, EMA, MACD, Bollinger Bands, volume, KD/KDJ, DMI/ADX, BR, AR, OBV, BIAS, PSY, Williams %R, CCI, MTM, ROC, weighted close, AD, VR, EOM, NVI, PVI, and VAO. Cumulative indicators use their recorded continuous state/anchor; missing or invalid history returns an explicit quality status.

Technical family candidates are trend/direction strength 30%, momentum/rate-of-change 25%, volume/accumulation 25%, volatility/range 10%, and psychology/buy-sell pressure 10%. Missing family weights are not redistributed and correlated indicators must first be grouped/capped.

When MACD is a principal reason or explicitly requested, the explanation may cite DIF, signal/DEA, histogram, zero-axis positions, cross direction/date/age, slopes, histogram expansion/contraction, period, and price/volume confirmation. A DIF/signal cross is not described as a literal long-term/short-term line crossover. This is an explanation contract and does not modify the formula.

V11 approval freezes these candidate definitions for later evidence collection; it does not promote their weight into Stable.

## H. EntityResolutionContractV1

Version: `EntityResolutionContractV1`  
Approval state: `candidate_unapproved`  
Implementation state: `partial`

Resolution order is: explicit code; official full/trading name; unique reviewed high-confidence alias; unexpired explicit conversation target; otherwise minimum necessary clarification. Name/code conflicts fail closed. Comparison intent may retain multiple explicit entities; an ordinary ambiguous request may not.

Relationships are typed as direct company, parent/subsidiary/group, supply chain, customer, peer, or incidental article mention. Only direct target identity can inherit direct-event rules without a separate relationship assessment.

Alias rows bind alias, normalized alias, stock code, official/trading name, source, confidence, effective interval, ambiguity set, reviewed/approved state, and registry version. Rollback selects a prior reviewed registry version; it does not mutate history. `wrong_entity_mapping=0` is a predictive-canary hard gate.

Current `StockEntityRegistryV1` implements NFKC-based matching, official rows, reviewed aliases, context inheritance, conflict/ambiguity handling, and a versioned alias table. Runtime lifecycle, explicit rollback evidence, and a mature zero-error prospective denominator are incomplete.

## I. LegalComplianceLaunchContractV1

Version: `LegalComplianceLaunchContractV1`  
Approval state: `candidate_unapproved`  
Implementation state: `missing`

A later `LegalReviewPacket` must state operator, audience, private/limited/public scope, payment, advertising/affiliate/third-party compensation, personalization, stock direction/price/allocation output, user data, disclaimer, record retention, and unresolved legal questions.

External gate states are `external_gate_not_started`, `external_gate_pending`, `external_gate_approved_with_conditions`, and `external_gate_rejected`. Current state is `external_gate_not_started`.

Codex does not claim to be counsel, decide licensing requirements, fabricate approval, treat a disclaimer as legal clearance, or substitute a keyword blacklist for semantic review. Before written external approval, local development, tests, Shadow, and operator-allowlisted/offline comparison are allowed; public directional candidate output, payment, personalized allocation, imperative trading, and guaranteed prices are forbidden. External output fails closed to timestamped, sourced factual/educational material.

Engineering completion may be reported as `shadow_only/external_gate_pending`; it cannot be called public-production complete.

## J. SourceRightsAndNewsGovernanceContractV1

Version: `SourceRightsAndNewsGovernanceContractV1`  
Approval state: `candidate_unapproved`  
Implementation state: `partial`

Source authority classes remain canonical official, canonical normalized supplemental, licensed secondary, metadata-only news radar, community claim, and model inference. Only eligible official/normalized or explicitly licensed evidence can become canonical. Radar and community claims are discovery-only and have direction weight 0 until independently verified.

Every source policy records fetch/model/display/excerpt permissions, metadata and raw-body retention, attribution requirement, terms-review date, policy version, exact host/path constraints, and failure state. Unlicensed full article bodies are not retained. Current official and radar policies use raw-body retention 0; event hot excerpts, when rights permit, are bounded to 600 characters and seven days. Cluster, revision, provenance, and outcome metadata may be retained longer according to their research contract.

Source-plan scopes are official company; Taiwan policy; U.S. policy/geopolitics; company/industry news; related overseas reaction; U.S. market/Taiwan night; and dilution/valuation risk. `SingleTrackV3EventSourcePlanV3` includes implemented-off-by-default MOPS, Taiwan-government, Federal Reserve, Treasury, OFAC, BIS, and controlled-radar adapters plus four immutable snapshot-receipt sources. The general official/licensed geopolitical source remains missing, so a complete-scan claim fails closed.

CMoney forum URLs are discovery-only. The same event across days is classified as exact duplicate, headline rewrite, syndicated copy, new-source confirmation, substantive revision, market-reaction update, priced-in, still-developing, or direction reversal.

Source-rights policy is engineering governance, not a legal opinion; public use still depends on I.

## K. PrivacyImageSecurityContractV1

Version: `PrivacyImageSecurityContractV1`  
Approval state: `candidate_unapproved`  
Implementation state: `partial`

Shared `ImageInputContractV1` limits are frozen as candidates:

- PNG, JPEG, or WebP only.
- Maximum encoded input: 8 MiB; multipart overhead: 64 KiB.
- Maximum side: 8,192 pixels; maximum decoded pixels: 40,000,000.
- MIME is not trusted; magic bytes, decoded format, complete single-frame decode, dimensions, and pixel count must agree.
- Empty, corrupted, forged, multi-frame/animated, and decompression-bomb inputs fail closed.
- Metadata is removed by re-encoding before model use.
- User-provided remote image URLs are categorically rejected; the server does not fetch them.
- Raw image and complete OCR are not stored in DB or ordinary logs. Image-derived values are `estimated`, `weight=0`, and cannot override the referee. Non-stock images return `is_stock_chart=false`.

The Web route `POST /api/analysis/image` and LINE image path share the verifier. Because arbitrary user URLs are disabled, URL rejection is the primary image SSRF control. Any separate approved server-side retriever must test, using mock resolver/client only: HTTPS allowlist, redirect cap, DNS/IP validation at every hop, loopback, RFC1918, link-local, metadata IP, file/data/ftp, public-to-private redirect, encoded/mixed IP, and DNS rebinding.

Conversation retention candidates are raw exchanges 24 hours and encrypted summaries 30 days. The current effective settings match those values, use encrypted SQLite, and record privacy notice `2026-08-28` with long-term approval enabled. Delete-all, scope reset, unsend suppression, unfollow handling, expiry, and cross-user/group/image/cache isolation require a bound V11 regression cycle before this contract is `implemented`.

## L. OperationalMonitoringContractV1

Version: `OperationalMonitoringContractV1`  
Approval state: `candidate_unapproved`  
Implementation state: `invalid_or_stale`

The required Asia/Taipei cumulative event slots are T-1 18:00, T-1 21:00, T 06:00, and T 06:45 final scan with sealing by 07:00. A 24/7 sentinel runs every 15 minutes, including non-trading days, and only handles high/critical candidates or material revisions.

The scheduler is durable and idempotent, persists ticks/leases/receipts, catches up missed slots after restart/wake, exposes source failures and artifact age, and never depends on a continuously open UI process. Web and LINE use the same selector.

Operational readiness monitors at minimum: process/worker heartbeat; GPU admission queue and p95 stage latency; slot due/started/completed/missed state; source coverage/failure; latest sealed artifact; schema/calendar readiness; CAS conflicts/stale completions; terminal cascade/outbox lag; duplicate contribution count; waiting-on-terminal count; prediction/outcome completion; parity/drift; and rollout/legal gate state.

Current evidence: durable scheduler/source code and temporary offline catch-up tests exist. `docs/STAGE8_SCHEDULER_AUDIT.json` is stale because it refers to older schema/source-plan versions. A live read-only query finds no `Taiwan Stock Single Track V3 Scheduler` Windows task. Production DB schema is now 44/44 at `single-track-v3-stage8.13`, but actual boot, sleep/wake, task launch, and production catch-up remain unverified. No installation is authorized by Stage 0.

## M. ConcurrencyAndTerminalCascadeContractV1

Version: `ConcurrencyAndTerminalCascadeContractV1`  
Approval state: `candidate_unapproved`  
Implementation state: `partial`

Resolution clocks are frozen:

- `resolution_eligible_at = max(content_input_eligible_at, target_input_eligible_at)`.
- Start deadline is the earlier of eligible+10 minutes and prediction issue deadline.
- Resolution deadline is the earlier of eligible+20 minutes and prediction issue deadline.
- Target recovery deadline is the earlier of eligible+60 minutes and prediction issue deadline.
- Retry/repair/restart/re-enqueue never resets clocks. DB time fences persisted deadlines; monotonic time measures queue durations. Promotion uses the half-open condition `DB now < deadline`. SLA breach is sticky.

GPU base priority is interactive LINE/Web with EDF; 07:00 final; scheduled event assessment; then Shadow/compaction/other low-priority work. Equal deadlines use FIFO. The protected resolution window uses predicted remaining p95, validation p95, seal p95, and jitter. It never preempts running interactive work; it pauses new full interactive generation, serves an honest deterministic fallback, and permits at least one urgent assessment dispatch. The default is one inference slot. Multi-process model/HTTP workers fail readiness unless admission is cross-process.

Content and target each allow at most four total attempts with retry delays 1, 5, and 15 minutes plus deterministic 0–30 second jitter, and at most one lifetime Qwen repair. Queue wait/GPU wait/lease-only/pre-dispatch crash does not consume an attempt; durable dispatch, worker loss, timeout, validator rejection, and model repair do. Local JSON normalization stays in the same attempt. Hard deadline is the earliest of dispatch+300 seconds, logical recovery, and target prediction issue deadline where applicable.

Success/terminal CAS binds logical ID, state version, promotion epoch, active attempt ID, expected state, and DB-time fence. Late losing completions become `stale_completion` and cannot promote. Terminal content atomically changes its state, records an immutable transition, increments promotion epoch, blocks untouched dependent targets, and inserts one transactional outbox row. A deterministic zero-GPU producer creates idempotent suppression artifacts after commit. Reconciler is deterministic, zero-GPU, reentrant, and must enforce `waiting_on_terminal_content=0`.

One event revision/evidence/cutoff/model-prompt-schema-validator-contract identity has one content attempt chain; each entity/forecast target has its own target-impact chain and fact snapshot. Sibling failures do not contaminate each other. One revision/entity/trade-date/forecast-target/weight-version has at most one active contribution.

Existing repositories and tests implement much of this state/CAS/outbox behavior and must be reused. Runtime wiring, queue protection, cross-process readiness, and production reconciliation evidence remain incomplete.

## N. ImpactMagnitudeAndCalibrationContractV1

Version: `ImpactMagnitudeAndCalibrationContractV1`  
Approval state: `candidate_unapproved`  
Implementation state: `partial`

Each forecast target versions its realized-return formula, raw or benchmark-adjusted basis, corporate-action adjustment, cutoff-safe volatility estimator, rolling lookback, minimum observations, winsorization/outlier rule, volatility floor, limit/suspension/no-trade handling, and sigma formula. Sigma uses only data available by cutoff.

Predeclared ordinal magnitude candidates use `z = abs(target return) / target-specific sigma_asof`:

- negligible: `z < 0.5`
- low: `0.5 <= z < 1`
- medium: `1 <= z < 2`
- high: `2 <= z < 3`
- extreme: `z >= 3`

These are versioned candidate bins, not market laws. Training-only/walk-forward evidence may propose a new version before final holdout/prospective evaluation; changing bins after observing holdout invalidates the old evidence.

Qwen may emit evidence-bound rationale, ordinal candidate, uncertainty, and explicitly `uncalibrated_internal` raw scores. It may not expose raw scores or call them probabilities. Direction and magnitude probability distributions come only from a backend calibrator trained on training folds with temporally separate threshold selection and evaluation. The release state is `released` only after holdout and prospective gates.

The validator rejects Qwen-generated future prices, unbound future percentages/return ranges, and guaranteed prices, while permitting evidence-bound historical percentage changes and official financial ratios. Even calibrated predictive percentages remain hidden unless I is approved; ordinal magnitude may be shown.

Existing V3 `magnitude_probabilities` fields are the only schema to extend. No second magnitude schema is allowed. The present validator accepts internal `shadow/calibrated` states, but no backend calibrator or released evidence exists; formal eligibility therefore remains 0.

## O. AnalysisSessionBoundaryContractV1

Version: `AnalysisSessionBoundaryContractV1`  
Approval state: `candidate_unapproved`  
Implementation state: `partial`

Three input boundaries are distinct:

1. Watchlist realtime/intraday snapshots may display current quotes, zone position, and approved provisional observations. They cannot rewrite immutable `main_status`, a sealed prediction, unapproved next-session factors, or complete-close OHLCV.
2. Taiwan50/individual close-batch snapshots use the latest complete official close and may feed formal technical/chip/referee factors only when close-batch readiness passes. GET never triggers an update.
3. News/event slots and sentinel update research context only. They do not create/overwrite OHLCV. At cutoff, the canonical artifact combines the latest eligible close-batch factor snapshot and latest eligible sealed event artifact.

A 07:00 prediction for that session's open, continuation/reversal, and close targets is immutable. At 10:30 the service may show current observations, a latest event delta, the sealed prediction, and next-session research context, but incomplete intraday data cannot masquerade as a close factor.

Formal use of intraday quotes for the next session requires a separate `provisional_intraday_to_next_session` target/factor contract with its own cutoff, label, outcome, and gate. Until approved its weight is 0.

Artifact selection requires the same target trade date, `artifact.cutoff <= request_time`, sealed status, eligible source revisions, and the newest cutoff. A failed slot is explicit; it cannot be silently replaced by stale context.

Digest layers are frozen:

- `canonical_core_digest`: entity/target/trade date/session/cutoff, selected fact and event snapshots, formula/weight/referee versions, formal conclusion; excludes channel and answer profile.
- `projection_digest`: normalized request intent/profile, included/omitted sections, model packet, and model/prompt/schema/validator versions.
- `render_digest`: channel renderer version, format, and bounded rendered content.

For the same entity, target, session, cutoff, and versions, Web and LINE must share the same `canonical_core_digest`. Different sessions may have different analysis IDs; channel alone may never invert the conclusion. The current artifact has several related digests but not this complete three-layer contract.

## Statistical and release evidence frozen for later stages

Stable and Candidate use identical stock, target, cutoff, calendar/event/outcome revisions, eligible/excluded IDs, and denominator. Each target and regime is evaluated separately.

Normal minimum: 2,000 paired observations, 120 target trading dates, 30 as-of-universe stocks, 100 observations per frozen class, and power at least 80%. Material-event minimum: 300 paired target-event observations, 50 independent clusters, 60 target dates, 20 as-of-universe stocks, at least three event types with ten clusters each, 20 independent clusters per outcome class, 30 large-impact safety clusters, and power at least 80%.

The frozen method is chronological walk-forward; 10,000 paired bootstraps; five-trading-day moving blocks for Normal; the worse of trading-day and independent-event-cluster resampling for Material-event; Holm-Bonferroni family-wise alpha 0.05. Synthetic news/returns never enter the denominator.

Primary metric is multiclass Brier sum. Secondary metrics are clipped log-loss, ECE, and balanced accuracy. Safety includes high-confidence wrong, material-direction miss, completion, leakage, wrong entity, unverified event weight, ungrounded claims, and referee override. Magnitude later adds Ranked Probability Score, ordinal log-loss, and high-impact recall.

Non-inferiority margins are Brier `min(0.002, Stable*1%)`, log-loss `min(0.005, Stable*1%)`, ECE +0.005, balanced accuracy -0.005, high-confidence wrong +0.005, high-confidence correct coverage -0.010, material miss +0.010, and no loss in prediction completion. Predictive canary additionally requires all non-inferiority gates, at least two target Brier wins and equal-weight macro-Brier improvement each at `max(0.002, Stable*2%)` with paired significance, safety hard gates 0, and denominator shrinkage 0.

Historical replay cannot prove Qwen predictive skill unless its training cutoff is demonstrably earlier than the replayed events. Predictive release therefore requires prospectively sealed Shadow predictions. Phase D latency evidence cannot substitute for predictive evidence.

## Candidate decisions requiring the written approval

Approval accepts the contract semantics and candidate numbers in this document, including profile caps/SLA, family weights as Shadow candidates, technical family proportions, magnitude bins, retry/deadline limits, image limits, retention limits, TDCC freshness windows, statistical floors, and non-inferiority/superiority margins.

Approval does not waive these unresolved gates:

- Review or explicitly rebaseline the 10/11 protected-file mismatch.
- Build immutable as-of stock-universe membership evidence.
- Produce evidence-backed credit/SBL subweights; current contribution stays 0.
- Implement the five-level analysis-layer materiality classifier and target relationship/magnitude separation.
- Complete the unified request key and three digest layers.
- Complete the external LegalReviewPacket and written legal decision.
- Fill the missing geopolitical official/licensed source and re-review source rights before public use.
- Install and verify the scheduler only in a later authorized deployment stage.
- Produce the backend calibrator and prospective statistical evidence.
- Run a single bound Phase A–E code-freeze cycle after implementation is stable.

Until approval, `docs/contracts/STAGE0_CONTRACT_APPROVAL_MANIFEST.json` remains `awaiting_user_approval`, and Stage 1 must not begin.
