# Single-Track V3 frozen contracts

Status: Stage 0 baseline, additive candidate contracts only. This document does not approve
Release Phase A, candidate replies, formula replacement, or database migration.

## Release boundary

- Stable production behavior remains authoritative until Stages 1–8 are complete and the
  final freeze is reviewed.
- Existing LINE shadow observations predate this contract and are historical diagnostics.
  They do not count toward V3 Phase A–E evidence.
- Legacy scoring, support/resistance, next-day outlook, referee, and data-quality formulas
  remain protected by `docs/LINE_MODEL_SPEC_V2_BASELINE.json`.
- Candidate code is additive and off by default. A candidate result cannot replace a stable
  reply before its regime has passed the statistical and safety gates.

## Source authority and event-time contract

Authority order is:

1. `canonical_official`
2. `canonical_normalized_supplemental`
3. `licensed_secondary`
4. `news_radar`
5. `model_inference`

`news_radar` discovers candidates only. An unverified candidate has directional weight zero,
and duplicated coverage never raises its authority. Web text is untrusted data and can never
be promoted into an instruction layer. Raw article retention is zero; only URL, timestamps,
publisher/source identifiers, content hash, a necessary short excerpt, and evidence mapping
may be retained.

Every fact must carry `fact_id`, entity, field, value, unit/currency, period, trade date,
`as_of`, `available_at`, source-market timestamp, session, adjustment basis, authority tier,
quality, availability reason, use scope, snapshot ID, provenance, and formula/source version.
It is eligible only when `available_at <= analysis_cutoff`. Information published after a
close cannot explain that earlier close and only applies to a later effective trading phase.

Event states are exactly `verified_none`, `verified_material`, `pending_reconciliation`, and
`scan_incomplete`. Event invalidation and event direction are independent: a later verified
material event always supersedes an affected old outlook, while an unclear or mixed event has
direction score zero. Timeout, HTTP 429, offline operation, or incomplete source coverage must
produce `scan_incomplete` and cannot support high confidence.

## Durable scheduler persistence foundation

Stage 1 schema version `single-track-v3-stage8.11` additively introduces
`news_retrieval_run`, `news_run_event`, `single_track_v3_job_lease`,
`single_track_v3_worker_heartbeat`, and `single_track_v3_outbox`; it does not modify legacy market
tables. The only scheduler slot keys are `evening_1800`, `evening_2100`, `preopen_0600`,
`preopen_final_scan`, and `high_signal_sentinel_15m`. Durable run states are `queued`, `running`,
`success`, `partial`, `failed`, `late`, and `skipped`.

Run creation is immutable-idempotent by a versioned idempotency key. The same key with different
content fails closed. State changes use compare-and-swap so stale workers cannot overwrite the
winner. A terminal state requires an offset-aware `completed_at` and retains source coverage and
typed failures. Leases may replace only a released/expired lease; heartbeat renewal binds the exact
lease token and run, stores only a pseudonymous worker identity digest, and cannot revive an expired
lease. Outbox events are immutable-idempotent by `dedupe_key`.

The second additive tranche introduces `event_cluster`, immutable `event_revision`,
`content_assessment`/`content_assessment_attempt`, and
`target_impact_assessment`/`target_impact_assessment_attempt`. An event revision stores no raw article
body, bounds short excerpts to 600 characters, and caps hot-content retention at seven calendar
days. `content_assessment_key` is a SHA-256 identity over only revision/evidence/cutoff and
model/prompt/schema/validator/contract versions; it cannot contain a stock or forecast target. One
sealed validator-pass content result may therefore feed independent per-stock target rows without a
content rerun. Each target key additionally binds the content result digest, entity, trade date,
TargetLabelContractV1 target, fact/context digests, and target model contract versions.

Queue wait and lease acquisition do not create an attempt. The repository inserts an attempt only
when crossing the durable model-dispatch boundary, computes the hard deadline as the minimum of
dispatch +300 seconds and the fixed recovery/publish deadlines, limits content and target attempts
to four, and limits contract repair to one per logical key. Content sealing uses state-version and
active-attempt compare-and-swap, requires the typed content fields, and rejects per-stock direction
or weighting fields.

The third additive tranche makes pre-content target jobs explicit as `waiting_for_content` with zero
target attempts. A content success CAS validates both `state_version` and `promotion_epoch`, seals the
immutable digest, and activates its waiting targets in the same transaction. Target sealing validates
finite positive/neutral/negative and five volatility-magnitude probability buckets, typed evidence
lists and boolean eligibility, rejects model-authored return/price-target fields, and keeps
uncalibrated Shadow results at `eligible_for_weight=false`.

Failure transitions use a database-derived Asia/Taipei clock, typed failure classes, fixed 1/5/15
minute delays plus deterministic 0–30 second jitter, fixed recovery/publish deadlines, four-attempt
and one-repair caps, and promotion-epoch CAS. Content terminalization atomically stores the immutable
transition, increments the promotion epoch, blocks every zero-attempt waiting target, and inserts one
deduplicated `CONTENT_TERMINAL_CASCADE` outbox row. Terminal-first late success is retained only as a
`stale_completion`; success-first prevents the cascade. Database-clock watchdogs and the zero-GPU
reconciler prevent expired queued rows or `waiting_on_terminal_content` from remaining stranded.
The Stage 8.7 zero-GPU repository producer seals a typed suppression artifact for that terminal
outbox event. Stage 8.9 adds the metadata-only retrieval worker described below; content/target
model workers remain later work. Passing repository tests does not claim those runtime gates.

The repository implementation is behavior-preservingly separated by responsibility. The existing
`single_track_v3_assessment_repository` import surface owns event/assessment identity, idempotent row
creation, and shared SQLite helpers. Content dispatch/seal/retry/cascade/reconciliation transitions
live in `single_track_v3_content_assessment_state_repository`; target dispatch/seal/retry/watchdog
transitions live in `single_track_v3_target_assessment_state_repository`. Lazy compatibility exports
keep the prior caller contract intact. This split does not change SQL, state values, CAS predicates,
deadlines, attempt accounting, or promotion fences; the same race tests remain authoritative.
Separately, timestamp serialization preserves non-zero database-clock fractional seconds. This closes
a verified truncation defect where a nominal 60-second retry could become slightly shorter than 60
seconds; retry availability now equals the fixed 1/5/15-minute delay plus its deterministic jitter.

The fourth additive tranche introduces `research_news_item` in the dedicated noncanonical research
namespace. It stores source/publisher/URL metadata, separately typed publisher/index/retrieval/
availability timestamps, bounded key points and excerpt, entity/event mappings, source-rights policy,
content/event hashes, dedup cluster, and explicit retention metadata. It has no raw article-body
column, enforces `untrusted_text=true` and `raw_body_retained=false`, caps source-permitted hot content
at seven calendar days, and rejects GDELT index time used as publisher time unless publisher time was
separately verified. `first_retrieved_at` is immutable; `available_at` may be corrected earlier but
never moved later; content expiry cannot be extended. The explicit prune operation clears title/key
points/excerpt while preserving URL/hash/event audit metadata and does not delete canonical event
revisions. Point-in-time reads perform no schema DDL or repair work. Stage 8.9 invokes prune in the
same terminal savepoint even when every source fails or returns zero results.

The fifth additive tranche introduces `premarket_intelligence_artifact` and
`event_delta_artifact` as upstream intelligence records only. They do not replace, alias, or compete
with the single `canonical_analysis_artifact` authority. A premarket artifact accepts exactly the
four scheduled slots (the 15-minute sentinel is excluded), binds one unique terminal retrieval run,
and must exactly match that run's target date, slot, cutoff, source policy, calendar revision,
coverage, failures, and compatible terminal status. Every referenced event revision must exist,
have `available_at <= cutoff_at`, and already be sealed when the artifact is sealed. Identity and
content digests are deterministic; replay is idempotent and conflicting content fails closed.

The read-only selector requires both artifact cutoff and seal time to be visible at the requested
cutoff, selects only the same target trade date, and may fall back to an earlier completed slot on
that date. It never borrows a prior target date. A newly detected post-base event creates a separate
pending event-delta row whose event revisions must be later than the selected base artifact and
available by the delta cutoff. Terminalization is a pending-to-sealed/partial/incomplete CAS with an
immutable digest. Historical reads reconstruct a not-yet-sealed delta as pending from its timestamps.
Creating a delta does not mutate the premarket artifact or the single canonical analysis artifact.
The Stage 8.7 atomic seal-and-enqueue boundary hands a terminal delta to the separate zero-GPU
producer, which may apply only typed canonical invalidation metadata.

The sixth additive tranche introduces immutable statistical manifests around the existing mutable
prediction/outcome staging tables. `statistical_gate_manifest` and its holdout members seal the exact
`StatisticalReleaseGateV1` JSON/hash, actual evaluator source SHA-256, `TargetLabelContractV1` source
SHA-256, 10,000/bootstrap seed/five-day-block contract, and every predeclared sample/target identity.
The gate rejects altered specs, changed source digests, duplicate members, and empty partitions.

Separate stable and candidate `prediction_snapshot_manifest` rows must contain an explicit real
prediction row for every predeclared identity. Completed rows require the exact target classes and
finite probabilities summing to one; failures/abstentions/omissions remain explicit ineligible rows
with no probabilities and therefore stay in the denominator. Synthetic rows are forbidden. Each
member copies the exact canonical analysis/cutoff/regime/event metadata and row digest, so a later
legacy upsert cannot rewrite sealed evidence.

`outcome_snapshot_manifest` opens only after the gate is sealed, copies the exact official-adjusted
outcome revision for the complete partition, and preserves suspended/no-trade/quality failures as
explicit unavailable members rather than silently dropping them. Evaluation requires both prediction
snapshots to have sealed strictly before the holdout opens, verifies paired metadata and
`outcome.available_at > analysis_cutoff`, then runs the imported frozen evaluator itself. A caller
cannot supply a fabricated pass: the repository seals the computed result/digest, exact eligible and
unavailable counts, zero synthetic rows, bootstrap contract, and all manifest identities. This is
offline evidence persistence only; no production holdout, outcome rows, power, or pass is claimed.

The seventh additive tranche introduces `content_terminal_artifact` and
`single_track_v3_artifact_production_receipt`. The deterministic producer consumes only
`CONTENT_TERMINAL_CASCADE` and `EVENT_DELTA_SEALED`. Terminal content yields one immutable
`suppressed_unresolved` artifact. Delta sealing and outbox enqueue share one SQLite savepoint; a
delta with explicit invalidation targets resolves only canonical events whose revision and evidence
are both verified/material and whose `available_at` is later than each analysis cutoff. It then
changes only `validity`, `superseded_by_event_ids`, and `superseded_reason`; the canonical answer
bytes and hash remain unchanged. Mixed-direction material events therefore still invalidate without
inventing a directional score. A delta with no explicit targets is an auditable delivered no-op.

Each delivery and receipt commit atomically through pending-to-delivered compare-and-swap. The
receipt binds the input payload digest, producer version, output IDs/digest, timestamp, and the
database-enforced `zero_gpu_model_calls=0`. Exact replay is idempotent; altered delivered payloads,
missing output artifacts, identity conflicts, unavailable work, or stale delivery races fail closed
and roll back output mutations. The pending selector is bounded and read-only. The producer imports
no adapter, service, or Qwen/model module and performs no generation, retrieval, or GPU work.

This remains off-by-default infrastructure. The metadata-only retrieval worker is now present, but
no premarket intelligence generation worker, content/target model worker, production migration,
installed Windows task, or actual boot/wake evidence exists yet. Repository or temporary-database
success is not production scheduler execution evidence. `SingleTrackV3SchedulerAuditV1` therefore
remains failed, and the presence of market-data tasks must not be presented as the Single-Track
research scheduler.

The eighth additive tranche introduces immutable
`single_track_v3_calendar_revision`/`single_track_v3_calendar_session` and
`single_track_v3_scheduler_tick`. Calendar revisions freeze exact official-evidence digests,
publication/availability/seal timestamps, Asia/Taipei open/close times, and explicit scheduled,
cancelled, delayed, special-session, or early-close state. A tick selects only a revision visible at
its point-in-time cutoff; the target is the first formally scheduled open after that cutoff rather
than a weekday/holiday heuristic.

`SingleTrackV3DurableScheduleV1` plans T-1 18:00 and 21:00, T 06:00 and 06:45, with the final-scan
deadline fixed at 07:00, plus a 15-minute sentinel over the full 24-hour clock including weekends and
holidays. Boot/wake catch-up uses the actual observation time and a future current cutoff; missed
historical sentinel intervals and expired fixed slots are persisted as typed `skipped` evidence and
never receive a backdated cutoff. Tick/run identity is immutable-idempotent, and tick materialization
is one SQLite savepoint.

The portable CLI defaults to read-only preflight. Write mode requires explicit `--run --allow-write`,
an existing schema, and a visible future calendar session. The Windows installer defaults to what-if,
requires explicit `-Install`, refuses an implicit task replacement, checks Taipei local time, and
requires a fully ready preflight before registration. Its proposed task starts from local midnight
and uses the maximum supported repetition duration at 15-minute intervals, also triggers at startup,
uses StartWhenAvailable/WakeToRun/IgnoreNew, and has a 14-minute execution
limit. Preflight now derives `retrieval_worker_ready` from the complete Stage 8.10 worker schema; an
unmigrated production database still reports false. A temporary SQLite close/reopen probe verifies
source-level catch-up and idempotency only; it is not actual Windows boot/wake or production
execution evidence.

The ninth additive tranche introduces
`single_track_v3_retrieval_source_attempt`, `single_track_v3_research_run_item`, and
`single_track_v3_retrieval_worker_receipt`. A dedicated worker first acquires a token-bound lease and
durably compares `queued` to `running`, then executes bounded injected adapters outside database
transactions. Timeout, HTTP 429, offline, source error, policy-disabled, and invalid-response states
are normalized into durable failure classes. Queries are retained only as SHA-256 digests. Adapter
results that report any raw article body fetch/retention or canonical-table write are rejected before
research persistence.

The terminal savepoint atomically stores source attempts, bounded research metadata, the noncanonical
run/item links, retention pruning, the run terminal CAS, `NEWS_RETRIEVAL_RUN_TERMINAL` outbox event,
immutable worker receipt, and lease release. A completed receipt makes restart replay perform zero
adapter calls. A live lease conflict performs zero adapter calls; an expired lease may resume an
already-running row without rewriting its original `started_at`. Unverified Radar metadata is never
inserted into `canonical_event_evidence` or `news_run_event`, has no referee override, and causes zero
model calls. It receives only a deterministic research dedup key; verified promotion remains a
separate later boundary.

The tenth additive tranche first froze `SingleTrackV3EventSourcePlanV1` and
`SingleTrackV3EventQueryPlanV1`. The seven required scan scopes are evaluated from explicit
all-of/any-of source requirements; absent, timeout, HTTP-429, offline, invalid, or policy-disabled
sources are never treated as neutral. A successful RSS fallback cannot erase a failed GDELT attempt.
Queries are built only from an official entity packet, are limited to four strings of at most 512
characters, and remain discovery-only. The query and source-plan versions, official entity refs,
and query digests are sealed into the terminal worker receipt, so a restart with changed provenance
performs zero adapter calls and fails closed.

`SingleTrackV3EventSourcePlanV2` subsequently splits the former combined sanctions/export-control
requirement into two independently evidenced sources. Treasury policy, OFAC sanctions, Federal
Reserve monetary policy, and BIS export control therefore have separate physical attempts and
logical coverage keys. One successful source cannot silently satisfy another missing obligation.

Stage 8.11 adds nullable `publisher_published_date` to the noncanonical research item. A source that
publishes only a calendar date can now retain that exact date without fabricating a midnight or
publisher timestamp. The worker rejects future/invalid dates and conflicts between a date and an
exact publisher timestamp; reconciliation carries the date into bounded source references.

`SingleTrackV3RetrievalWorkerV2` accepts separately declared source specs with scope, execution mode,
and authority tier. `canonical_official` and `canonical_normalized_supplemental` events require
primary verification; `news_radar` remains unverified. Event-provided entity refs must be a nonempty
subset of the run identity for canonical sources. Deduplication binds normalized title to that entity
set, preventing identical generic disclosure titles for two issuers from sharing a cluster.

The MOPS retrieval boundary reuses the existing official listed/OTC adapter once per run, filters to
the official four-digit entity set, and copies only bounded subject, exact disclosure timestamp,
source URL, and attribution. The MOPS explanation field and all article bodies are excluded. Listed
success plus OTC failure retains the successful metadata but seals the run as `partial`. Timeout,
rate-limit, offline, and source errors remain typed physical attempts. Rights are versioned by
`official-research-policy-v1`; raw-body retention and canonical-event writes are both zero.

The Taiwan policy boundary is restricted to an explicit Executive Yuan/ministry/MOEA official RSS
whitelist. Relevance is deterministic and may use only audited official names, aliases, and industry
terms from the entity packet. It retains title, exact publisher timestamp, official HTTPS link, and
source identity only; RSS summaries and article bodies are excluded. Each physical feed keeps its own
typed attempt so one timeout cannot erase another feed's successful metadata.

The Federal Reserve boundary uses the Board's official monetary-policy RSS endpoint, rejects
redirects, non-Federal-Reserve links, DTD/entity-bearing or oversized XML, future/invalid publisher
timestamps, and responses above the bounded item count. It retains title, exact publisher timestamp,
official HTTPS link, and source identity only. Association to the requested stock is retrieval scope,
not a materiality or directional claim: reconciliation starts at unknown materiality, performs zero
model calls, and leaves content assessment and canonical promotion downstream.

The U.S. official-index boundary fetches three explicit HTTPS pages once per run: U.S. Treasury
press releases, OFAC Recent Actions, and BIS all press releases. It accepts only Treasury
`/news/press-releases/*`, OFAC `/recent-actions/YYYYMMDD`, and BIS `/press-release/*` detail links on
their exact official hosts. Dates are preserved as date-only metadata; publisher time is explicitly
unverified, so retrieval time remains the point-in-time availability boundary. Page excerpts and
article bodies are excluded, redirects are rejected, responses are bounded, and each physical source
retains its own timeout/429/offline/invalid status. OFAC's retired RSS feed is deliberately not used.

`SingleTrackV3EventReconciliationV1` is the separate promotion boundary. Radar-only clusters stay
unlinked with `unverified_radar`. A primary official/normalized source, or a corroborated licensed
source, may create a sealed `event_cluster`/`event_revision`; same-title Radar metadata may be linked
as untrusted corroboration but never supplies verification. Revisions retain only bounded title
metadata, hashes, URLs, timestamps, policy/source refs, and at most seven days of hot content. They
start with unknown materiality and no direction, make zero model calls, and still write zero rows to
`canonical_event_evidence`; content/materiality assessment and canonical event promotion remain
downstream. The existing scan persistence was also corrected so Radar-only events cannot enter
`canonical_event_evidence` merely because the scan record itself is persisted.

MOPS, Taiwan official policy RSS, Federal Reserve monetary-policy RSS, U.S. Treasury policy, OFAC
sanctions, BIS export-control metadata, and controlled Radar are now composed by the off-by-default
execution plan. A qualified geopolitical source, related-overseas price reaction,
US-market/Taiwan-night, and dilution/valuation snapshot boundaries are still incomplete. Therefore
the seven-scope gate remains fail closed and no complete-scan or high-confidence claim is allowed.

`OfficialTWSEExactSessionSourceV1` is the fail-closed official-calendar materializer input. Every
session must contain explicit official state evidence; every open session must also contain explicit
official open/close evidence. Scheduled, cancelled, delayed, special-session, and early-close states
are preserved exactly. A closure-date or annual holiday payload without a complete exact session set
is rejected, so the materializer never invents a fixed 09:00/13:30 calendar. The current TWSE annual
holiday adapter therefore remains useful source evidence but is not, by itself, sufficient to create
a production revision. No production calendar source fetch or materialization is claimed by the
offline fixtures.

## CanonicalAnalysisArtifactV1

The shared Web/LINE artifact has these required fields:

`analysis_id`, `snapshot_id`, `snapshot_digest`, `request_received_at`, `analysis_cutoff`,
`snapshot_sealed_at`, `context_digest`, `component_snapshot_ids`, `event_watermark`,
`source_policy_version`, `weight_version`, `formula_version`, `referee_version`,
`model_digest`, `prompt_version`, `validator_version`, `renderer_version`,
`entity_registry_version`, `conversation_projection_version`, `response_style_version`,
`coverage`, `omissions`, `conflicts`, `validity`, `superseded_by_event_ids`,
`superseded_reason`, `canonical_answer_text`, and `canonical_answer_text_hash`.

One artifact is generated once. Web and LINE consume the same persisted artifact and answer
hash; they may not independently sample two answers. GET routes only read sealed snapshots and
never compute, repair, enqueue, or write.

Stage 4 implementation uses `CanonicalAnalysisOrchestratorV1` behind one write-authorized POST
use case. The Web and LINE transports may differ only in delivery-channel metadata; channel is
excluded from artifact identity. Identity is derived from normalized stock/date, exact cutoff,
conversation-context digest, profile, and orchestrator version. Repeated requests first read the
sealed artifact and do not rebuild the snapshot or render a second answer. The artifact stores a
versioned `canonical_payload` beside the immutable answer text so both transports consume the
same main conclusion, factor coverage/scores, TechnicalEnsemble state, referee, event/evidence
IDs, omissions, and conflicts. The payload also retains bounded `canonical_sections` copied from
the already computed sealed snapshot for technical, support/resistance, valuation, institutional,
global, TAIFEX-night, trading-state, recommendation-safety, decision-audit, advisory, and
price/volume scopes. This is a projection boundary only: it does not recalculate or reinterpret
protected analysis formulas.

When legacy OHLCV rows do not expose an ingest timestamp, a canonical close fact uses the exact
analysis cutoff as a conservative upper bound for `available_at`, with availability reason
`observed_in_sealed_snapshot_at_cutoff`; it never asserts an earlier ingest time. Missing event
research scopes remain omissions and yield `scan_incomplete`; their event-direction factor is
null rather than a fabricated neutral zero. The deterministic Stage 4 renderer is an additive
pre-model implementation and does not constitute Qwen acceptance or Release Phase A evidence.

### Canonical model runtime and release boundary

LINE shadow preparation captures only an in-memory point-in-time request. After the stable LINE
reply is accepted, the shadow worker calls the authenticated
`POST /api/bot/market-data/analysis/model-packet`, which creates/reuses the same sealed canonical
artifact and returns one policy-filtered `ModelFactPacketV2`; the legacy LINE fact projection is
not a candidate input. Persisted shadow evidence is sanitized and includes request/artifact IDs,
packet-token counts, timing, finish reason, validator/reject classification, grounding counts, and
hashes, but never the question, packet, raw model text, identity, or reply token.

User-visible canary selection is fail closed. `LINE_MODEL_V2_ROLLOUT=canary` is insufficient by
itself: a time-bounded HMAC-SHA256 authorization must bind the frozen runtime digest, Phase A-D
completion, the statistical/human/security/environment gates, every zero-violation counter, and
an allowed 5/10/25/50/100 percentage. Cohort assignment is deterministic from the authorization ID
and the ephemeral webhook event ID. A selected candidate must complete before the LINE work-stop,
pass the validator without referee override, and reach the authenticated
`POST /api/bot/market-data/analysis/model-answer` finalization boundary. The backend independently
revalidates authorization/source digests, packet and compacted-packet identity, the immutable base
answer hash, candidate/prompt/schema/validator/renderer versions, actual runtime model ID/digest,
and the submitted output hash. It then reruns `validate_model_analysis_v2` and renders only the
validator-approved blocks.

An approved result is sealed once in `canonical_model_answer_extension`, an immutable child keyed
by `analysis_id`. The base `canonical_analysis_artifact` and its deterministic answer hash remain
unchanged and continue to define packet identity. Identical retries are idempotent; any semantic
conflict for the same analysis ID fails closed. The child stores only bounded structured blocks,
used event IDs, limitations, canonical rendered text/hash, and release identity metadata; raw model
output is never persisted. Artifact reads overlay the child answer for both Web and LINE, so both
channels consume the same stored text, hash, facts, referee, and evidence rather than sampling a
second generation. LINE may reuse the extension only while its authorization ID and release-source
digest still match the current signed decision. A mismatch, finalization error, missing model digest,
deadline exhaustion, or invalid fallback hash returns the hash-verified deterministic base answer
or the local deterministic fallback and never sends the unsealed candidate text.

`line-reply-telemetry-v2` stores only deidentified latency/admission/canary fields and hashes.
The fixed authenticated Phase-D sample endpoint now measures the canonical packet/candidate path,
returns no raw candidate or packet, and fails its work-stop gate if classification, retrieval,
generation, validation, or render timing is missing. Its sanitized result also reports observed
canonical artifact reuse and whether the final packet actually contains `news_radar` events, with
explicit hit/miss assertions available to the Phase-D runner; legacy LINE research-cache state is
not accepted as canonical news evidence. Phase-B uses a separate fixed-scenario POST
that returns the actual compacted packet, omission reasons, validated explanation blocks, rendered
preview, and Web/LINE hash comparison for controlled human audit; it never returns raw model text
or sends a LINE reply.

## CandidateWeightV1

Weights are fixed candidate starting points, not proven optimal weights. Missing factors are
not redistributed and cannot be filled with a fake neutral value.

| Factor | Material event | Normal |
| --- | ---: | ---: |
| Verified event content, scale, direction / verified non-material update | 35% | 10% |
| Overseas or industry price reaction | 20% | 15% |
| Stock price, volume, trend, volatility | 15% | 30% |
| US market and Taiwan night-session environment | 15% | 15% |
| Institutional, margin, and short positioning | 10% | 20% |
| Dilution, valuation, and other risk | 5% | 10% |

The stock price/volume factor is split into raw structure and TechnicalEnsembleV1: 5/10
percentage points in material-event regime and 10/20 in normal regime. Insufficient coverage
returns `insufficient_factor_coverage`, the missing reason, and reduced confidence.

TechnicalEnsembleV1 family weights are trend/directional strength 30%, momentum/rate of
change 25%, volume/accumulation 25%, volatility/range 10%, and psychology/buying pressure 10%.
KD and KDJ are one family vote. Correlated indicators are first combined within a bounded
family score and never counted as independent top-level votes.

## TechnicalFormulaV1 candidate definition

This version is additive and does not change legacy calculations. Inputs are official adjusted
daily OHLCV in ascending trade-date order. Price unit is TWD, volume unit is shares, daily
adjustment basis is the official corporate-action adjusted basis, division by zero yields null,
and NaN/Inf is stored as unavailable rather than zero. Rolling metrics are decision-ready only
after their complete warm-up. Wilder smoothing uses `alpha=1/n`, seeded with the first complete
`n`-row simple average. EMA uses `alpha=2/(n+1)`, seeded with the first complete simple average.

- Trend: SMA 5/10/20/60/120/240; EMA 12/26; MACD DIF=`EMA12-EMA26`, signal=`EMA9(DIF)`,
  histogram=`DIF-signal`; DMI/ADX 14 with Wilder TR, +DM and -DM.
- Momentum: Wilder RSI 5/10/14; RSV 9 and K/D seeded at 50 with 1/3 current plus 2/3 prior,
  J=`3K-2D`; Williams %R 14=`-100*(HH-C)/(HH-LL)`; CCI 20 on typical price with mean
  absolute deviation and constant 0.015; MTM 10=`C-C[10]`; ROC 10=`100*(C/C[10]-1)`;
  BIAS 6/12/24=`100*(C-SMA)/SMA`.
- Volume/accumulation: raw volume and SMA 5/20/60; OBV seeded at 0 and changed by signed
  volume; Chaikin AD seeded at 0 with close-location value times volume; VR 26=`100*(up
  volume+0.5*flat volume)/(down volume+0.5*flat volume)`; EOM 14 is the 14-day SMA of
  `((H+L)/2-(prevH+prevL)/2)*(H-L)/V`; NVI/PVI are seeded at 1000 and apply close return only
  on respectively lower/higher volume days; VAO=`V*(C-(H+L)/2)` with 14-day sum.
- Volatility/range: Bollinger 20 with population standard deviation and bands at 2 standard
  deviations; true range=`max(H-L,abs(H-prevC),abs(L-prevC))`; ATR 14 uses Wilder smoothing;
  WC=`(H+L+2C)/4`.
- Psychology/pressure: PSY 12=`100*up_days/12`; AR 26=`100*sum(H-O)/sum(O-L)`; BR 26=
  `100*sum(max(0,H-prevC))/sum(max(0,prevC-L))`.

All components persist parameters, formula version, input digest/date range/row count,
adjustment basis, source/data quality, decision readiness, reason, and computation time.
Cumulative OBV, AD, NVI, and PVI use continuous per-stock state and persist deltas, slopes,
and standardized values so moving a request window cannot rebase history. Retention remains at
least 600 trading days. Scheduled materialization is idempotent; request paths read only.

`TechnicalEnsembleScoreV1-candidate.1` maps component diagnostics to a bounded directional
score without changing the family weights. Each sub-signal is clipped to [-1,1] or transformed
with `tanh`; a family score is 100 times the unweighted mean of its available predeclared
sub-signals. Trend uses close/MA20, MA20/MA60, EMA12/EMA26, DIF, histogram and ADX-bounded DMI;
momentum uses RSI14, the single KD/KDJ family vote, Williams %R, CCI, MTM/ATR, ROC and BIAS24;
volume uses continuous-state OBV/AD deltas, log VR, EOM direction, NVI/PVI 20-day return and VAO
direction; volatility uses Bollinger position and close/weighted-close relative to ATR;
psychology uses centered PSY and log AR/BR ratios. The overall score is the fixed 30/25/25/10/10
weighted sum only when all five family scores exist and history has at least 240 rows. Missing
families yield a null overall score plus coverage; weights are never redistributed.

Input digests use `sha256-chain-v1`, starting from an empty byte string and hashing the previous
digest plus the next canonical adjusted OHLC/raw-volume row. Full available history is always
read for OBV/AD/NVI/PVI, seed/state metadata is persisted, and an unchanged digest/content causes
zero database updates. MACD cross age is numeric and its latest cross trade date is stored in the
component text value; both use the same formula version.

## StockEntityRegistryV1 and ConversationProjectionV1

The registry is built from the official active stock master plus reviewed aliases. Each entry
stores alias, stock code, canonical name, trading name, source, effective range, confidence,
ambiguity set, and registry version. Resolution order is code/name consistency, exact official
or trading name, unique reviewed alias, unique high-confidence active prefix, conversation
entity only when no new stock text exists, then clarification for real ambiguity/conflict/typo.

Required direct resolutions are 星宇/星宇呢/所以星宇呢→2646, 台積→2330, 聯發科→2454,
M31→6643, 長榮→2603, and 長榮航→2618. `2317 台積電` requires clarification. 星雨 may
only be suggested. Explicit new-stock text overrides prior active stock; comparisons retain two
entities; a pronoun can inherit the sole active entity.

Conversation projection is limited to the most recent 12 user/assistant turns and 4,000 tokens,
plus a versioned rolling summary, active and comparison stocks, last intent/topic, unresolved
question, investment horizon, explicitly stated position state, and latest analysis ID/cutoff.
Existing raw/summary TTL and privacy boundaries remain unchanged. Old numeric claims are marked
historical and cannot replace current canonical facts.

Stage 5 implements this contract through `StockEntityRegistryV1`. Request-time resolution reads
the official active stock master and versioned reviewed aliases without mutating the database;
the scheduled all-market updater is the only path that materializes aliases. Direct matches must
be unique, while typo, ambiguity, and code/name conflict fail closed and require clarification.
Comparison requests retain every resolved entity in text order and run an independent referee
decision for each stock rather than creating a synthetic cross-stock trading verdict.

`ConversationProjectionV1` keeps at most 12 recent user/assistant turns and uses a conservative
4,000 UTF-8-byte upper bound. It records only bounded rolling context, entity/intent state,
explicit position and horizon, unresolved questions, and the latest analysis reference. Derived
projections use the same encrypted storage and TTL/privacy scope as their source conversation;
unsending a source message also deletes the derived projection. Historical conversation numbers
remain labelled `can_replace_canonical_fact=false`.

## ResponseStyleV1, packet, and validator

ModelFactPacketV2 contains cutoff, target/comparison set, canonical facts, event evidence,
factor coverage, technical components, positioning, global/related-US facts, conversation
projection, typed image observations, omissions, conflicts, evidence IDs, and quality/source
metadata. The model cannot read arbitrary rows or raw web text.

Every rendered number and date binds to an evidence ID. Wrong entity/period/unit/date, an
unbound number/date, or a historical conversation claim presented as current is rejected.
Non-core claim failures support field-level repair or omission. Identity, cutoff, required
market data, material-event scan, and referee failures reject the whole answer. The model may
vary prose, but cannot override identity, facts, point-in-time rules, quality, referee,
no-return-guarantee, or no-human/no-license impersonation rules.

Answers lead with conclusion and cutoff, explain the most important 2–4 drivers and conflicts,
state event impact first when present, use conditional scenarios/invalidation, answer reliable
parts despite non-core omissions, and place one short disclaimer at the end. The renderer does
not force a sentence template.

`ExpertResponseRendererV1` is the deterministic grounded fallback used by the canonical artifact
path. It emits evidence-linked explanation blocks, puts material events before the conclusion,
lowers confidence for incomplete scans, names the missing side of one-sided technical structure,
and derives position scenarios only from explicit user context. A frozen 100-case blinded corpus
(30 focused, 30 comprehensive, 20 context/entity, 20 edge/event/image/missing/conflict) contains
no expected winner. Actual stable/candidate outputs and independent human ratings remain a Stage 7
release gate; no preference-rate claim is made before that evaluation.

## ImageInputContractV1

Web and LINE share one ingestion, quality, vision, and artifact path. Accepted formats are PNG,
JPEG, and WebP obtained only from the LINE official content API or direct Web multipart upload.
Remote URL inputs are rejected. Effective limits are 8 MiB, 8,192 pixels on either side, and
40 megapixels. Client Content-Type is non-authoritative; magic bytes and complete safe decode
must both pass. Empty, truncated, corrupt, conflicting-format, and pixel-bomb inputs fail before
model invocation; metadata is ignored/removed. A non-LINE content provider fails before any
external request.

Original bytes exist only in bounded memory and never enter DB, normal logs, cache,
conversation/research packets; full OCR is not retained. `is_stock_chart` is a required strict
boolean and fails closed on missing, non-boolean, uncertain, low-quality, or low-confidence
output. Non-stock images trigger no quote lookup, analysis, referee, or market-DB write. Image
observations are estimated, cross-checked against official facts, and always have
`can_enter_referee=false`. Equal inputs must yield equal Web/LINE classification, observations,
quality status, and artifact digest.

Stage 6 implements the shared boundary in `image_input_service.py`. The Web surface is the explicit
`POST /api/analysis/image` multipart endpoint and uses a bounded streaming parser; JSON,
`image_url`, and `url` inputs fail before analysis. LINE continues to download only from the
official content API, and a non-`line` content provider is rejected before credential lookup or
network access. The client MIME value is ignored. PNG/JPEG/WebP signature, decoded format, complete
`verify()` plus `load()`, single-frame shape, 8 MiB, 8,192-side, and 40-megapixel gates all pass
before the model can run. Accepted pixels are re-encoded deterministically without source metadata.

`ImageDataQualityV1` wraps the protected legacy image-quality assessor without changing its hash.
It requires an exact boolean classification and makes classification readiness depend on medium/high
quality and confidence at least 0.60. A confidently classified non-stock image returns one short
natural response and never resolves a stock or reads market data. Stock-chart observations remain
typed estimates, may be compared with read-only official facts, and carry
`can_enter_referee=false`. `ImageAnalysisArtifactV1` hashes only normalized classification, quality,
typed observations, and the sanitized input digest, so delivery channel and client MIME cannot
change parity. Neither image bytes nor the artifact digest enter the conversation projection.

## TargetLabelContractV1 and StatisticalReleaseGateV1

This contract is additive; it does not alter a legacy label. Using official adjusted outcomes:

- next-open gap is T+1 open versus T close: up at >=+1%, down at <=-1%, otherwise flat;
- continuation/reversal is eligible only at absolute open gap >=1%: open-to-close in the gap
  direction by >=0.5% is continuation, opposite by >=0.5% is reversal, otherwise neutral;
- next-close direction is T+1 close versus T close: up at >=+1%, down at <=-1%, otherwise flat.

Suspension, no trade, or quality failure is unavailable. High confidence means maximum class
probability >=0.70; large gap/close move means absolute change >=3%; material direction miss
means an actual non-neutral direction receives probability <0.20.

Stable/candidate comparisons are paired on identical stock/date/cutoff/regime/target/outcome
revision/sample IDs, retain failures and abstentions in the denominator, and are released by
regime. Primary metric is Brier; secondaries are clipped log-loss (`epsilon=1e-6`), classwise
adaptive ECE with 10 equal-frequency bins, and balanced accuracy. Safety covers high-confidence
wrong/correct coverage, large-gap/material-event misses, and eligible completion.

The predeclared bootstrap uses 10,000 resamples, a fixed seed recorded with artifact hash,
5-trading-day moving blocks for normal regime, and the worse confidence bound from trading-day
blocks and independent event clusters for material events. No synthetic row can satisfy sample
minimums. Hierarchical Holm-Bonferroni controls family-wise alpha 0.05: Stage 1 all
non-inferiority/safety hypotheses; Stage 2 macro-Brier and the three predeclared target Brier
superiority hypotheses.

Non-inferiority margins are Brier `min(0.002, stable*1%)`, log-loss
`min(0.005, stable*1%)`, ECE +0.005, balanced accuracy -0.005, high-confidence wrong +0.005,
high-confidence correct coverage -0.010, material/large-gap miss +0.010, and completion not
below stable. Superiority requires all target Briers non-inferior, at least two target Brier
improvements >=`max(0.002, stable*2%)` with paired superiority, equal-weight macro-Brier
improvement at the same threshold with superiority, and all secondary/safety metrics
non-inferior.

Allowed outcomes are `pass_for_canary`, `remain_shadow_noninferior_but_not_superior`,
`remain_shadow_insufficient_power`, `reject_safety_regression`,
`reject_statistical_regression`, and `invalid_evidence`.

Normal minimums per target are 2,000 paired observations, 120 trade dates, 30 stocks, 100 per
class, and >=80% power. Material-event minimums per target are 300 paired observations,
50 independent event clusters, 60 trade dates, 20 stocks, 3 event types with 10 clusters each,
20 clusters per outcome class, a 30-cluster large-event slice, and >=80% power.

Stage 7 implements the frozen evaluator in `statistical_release_gate_v1.py`; its spec hash is
`59958b9838f3944ea4f0d360031578334ad63275b728320f4ff8acf527333b89`. The read-only production
evidence inspection found that the prediction/outcome and immutable manifest tables have not been
migrated and paired rows are zero. Both regimes therefore remain `remain_shadow_insufficient_power`; no synthetic
row was generated or accepted. The additive schema and repository support event cluster/type,
large-safety-slice, and explicit synthetic provenance, but production migration remains a later
operator-controlled action.

## Canonical candidate and ExpertResponseQualityGateV1

`CanonicalModelFactPacketV2ProjectionV1` projects a sealed canonical artifact into the one
candidate packet used for both Web and LINE evaluation. It includes the exact cutoff, entities,
immutable referee, official OHLCV, technical aggregate/family facts, event evidence, bounded
conversation context, image estimates, omissions/conflicts, and source/evidence bindings. A
requested scope without eligible evidence receives an explicit unavailable limitation fact.
Delivery channel is excluded from packet identity. Daily market facts keep their trading-date
`as_of`, while the exact offset-aware artifact cutoff remains the point-in-time request boundary.
Stored `canonical_sections` are projected through the existing ModelFactPacketV2 fact builder so
authority, quality, units, dates, and source fields remain explicit. Event text enters the packet
only when rights allow model use; attribution-required evidence also requires display permission
and a normalized public HTTPS URL. Metadata-only or unknown rights yield a limitation rather than
a non-renderable citation.

`canonical-model-candidate-v1` accepts only explicit `offline` or `shadow` execution and runs
through shadow GPU admission. It reuses the existing LINE candidate prompt, output schema,
preflight/compaction, validator, and deterministic repair; evaluation decoding is fixed at
temperature zero and seed 20260901. Its output can neither replace stable nor override referee.
After repairing the projection and generation boundaries, the retained Stage 8 local-hardware
matrix contains 30 actual executions (15 focused and 15 comprehensive), zero synthetic rows, and
30 validator passes with zero ungrounded claims or referee overrides. The controlled 1/2/4/8
concurrency p95 queue-plus-execution results are 8.970/13.625/29.292/60.699 seconds for focused
and 7.953/9.802/21.605/45.884 seconds for comprehensive. The 8-concurrency results fail the
31-second and 45-second limits respectively. This is diagnostic evidence only: it does not meet
the five-trading-day or 100-executions-per-profile Phase D gate, and the 32K candidate remains
unqualified.

Stage 8 readiness and later release evidence are ordered, not circular. Stages 0–8 must have no
pending release-bound source, safety, quality, or security work before Phase A may start. Phase D's
five trading days and 100 executions per profile are collected only after Phase A shadow and cannot
be treated as a Phase A prerequisite. They remain mandatory before Phase E canary. An insufficient
statistical outcome may keep the candidate in shadow but cannot be filled with synthetic rows.

Every live benchmark collector must first compare an authenticated, read-only runtime source
fingerprint captured when the LINE process imported its release path with the current workspace
hashes. A missing endpoint, incomplete mapping, source digest mismatch, or per-file hash mismatch
stops before any model probe POST. HTTP 200 from `/healthz` is insufficient when its structured
`ready` value is false. Benchmark evidence must retain degraded readiness checks and may not bind
newer on-disk source hashes to an older running process.

`ExpertResponseQualityGateV1` deterministically blinds the frozen 100-case corpus. The
`ExpertResponseQualityReviewKitV1` workflow writes public reviewer material separately from the
private role/source key, SHA-256 binds every prompt, answer, input set, role key, rating document,
and evaluation, refuses overwrite, and makes Stage 8 recompute the stored evaluation. Exactly one
non-synthetic `independent_human` rating with an explicit human/independence/no-role-key attestation
is required per case. Attestation is required provenance, not automatic identity proof. The frozen
critical-error, preference, per-scenario, dimension-median, alias, ambiguity, and policy gates still
apply. Current output and human-rating coverage is zero, so the only valid status is
`insufficient_human_evidence`; the corpus itself is not proof of quality.

## Frozen reason-code namespace

The additive V3 namespace includes:

- evidence: `fact_after_cutoff`, `source_not_authoritative`, `unverified_radar`,
  `event_pending_reconciliation`, `event_scan_incomplete`, `event_superseded_artifact`;
- coverage: `insufficient_factor_coverage`, `component_unavailable`,
  `component_not_decision_ready`, `source_delayed`, `stale`, `unavailable`, `estimated`;
- entity/context: `entity_not_found`, `entity_ambiguous`, `entity_code_name_conflict`,
  `entity_typo_suggestion_only`, `conversation_entity_unavailable`;
- image: `image_empty`, `image_too_large`, `image_dimensions_exceeded`,
  `image_pixels_exceeded`, `image_magic_mismatch`, `image_decode_failed`,
  `image_format_unsupported`, `image_provider_not_line`, `image_remote_url_rejected`,
  `image_not_stock_chart`, `image_quality_not_ready`;
- generation: `identity_contract_failed`, `cutoff_contract_failed`,
  `required_market_fact_missing`, `referee_contract_failed`, `evidence_binding_failed`,
  `validator_rejected`, `deadline_admission_failed`;
- release: `insufficient_sample`, `insufficient_power`, `safety_regression`,
  `statistical_regression`, `environment_mismatch`, `protected_hash_mismatch`,
  `invalid_release_evidence`.

New reasons may be added only additively and must be documented, tested, and included in the
artifact or release manifest that first uses them.
