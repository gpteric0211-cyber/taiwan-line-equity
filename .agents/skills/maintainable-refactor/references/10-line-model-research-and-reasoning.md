# LINE Local Model Research And Reasoning Rules

Last reviewed: 2026-08-29.

Read this reference when work touches LINE request classification, multi-scope questions, local-model
input/output, comprehensive analysis, financial answer generation, current-news research, evidence
grounding, model context, GPU queueing, validation, or model rollout.

This reference defines the contract for the local model and controlled research. It does not authorize
runtime, database, model, environment, push, subscription, or financial-formula changes by itself.

## Contents

1. Outcome and non-goals
2. Immutable analysis logic
3. Responsibility boundaries
4. Request classification ownership
5. Requested-plan and execution-plan contracts
6. Data authority and quality
7. Time and conflict semantics
8. Controlled online research
9. Untrusted-content boundary
10. ModelFactPacketV2
11. Context profiles and model residency
12. Model output contract
13. Validation and rendering
14. LINE deadline, concurrency, and queueing
15. Fallback and delivery
16. Observability and privacy
17. Verification and release gates
18. Rollout order and official sources

## 1. Outcome And Non-Goals

The local large model is the substantive analysis and explanation layer. It must be allowed to:

- combine eligible facts across requested scopes;
- explain agreement, divergence, causes, limitations, and uncertainty;
- compare bullish and bearish evidence;
- produce conditional scenarios and invalidation conditions;
- explain how missing, delayed, stale, or conflicting data limits a conclusion;
- answer naturally in Traditional Chinese.

It must not be reduced to intent classification, fixed-template selection, or surface paraphrasing.

`comprehensive` means all requested, relevant, eligible analytical dimensions are included or listed
with an explicit omission reason. It never means exposing raw tables, sending the entire database, or
including unbounded records.

The model must not:

- read the database directly;
- browse arbitrary URLs directly;
- create or update canonical market facts;
- invent missing values;
- change the single referee conclusion;
- create a second stock score, main status, or top-level recommendation;
- promise returns, prices, or certainty;
- issue affirmative personalized trade commands.

## 2. Immutable Analysis Logic

Unless the user separately approves a behavior change, LINE/model/research work must not alter:

- stock scoring and state classification;
- referee or practical-status classification;
- RSI or other technical calculations;
- support and resistance calculations;
- estimated chip cost;
- price-volume scoring or its quality gate;
- next-day outlook weighting;
- freshness, confidence, or data-quality rules;
- night-futures `can_override_main_status=False`.

Before runtime work starts, record the approved protected-file hashes and behavioral fixtures. A hash
or golden-output difference is a stop condition until the user approves and the change is reviewed as
a separate analysis-logic phase.

## 3. Responsibility Boundaries

### Backend referee

The backend owns canonical values, source dates, quality, main status, practical status, approved
advisory text, and risk controls.

### Request planner

The backend request-planning service owns semantic classification and execution planning. The final
analysis model does not decide whether network access, push delivery, or canonical-data mutation is
allowed.

### Retrieval layer

The retrieval layer owns query construction, allowlisted sources, time windows, verification state,
deduplication, cache, source rights, and timeout handling.

### Local model

The local model owns evidence synthesis, explanation, conditional inference, scenarios, and readable
organization. It receives typed evidence, not raw database access.

### Renderer and validator

The backend owns canonical number insertion, immutable referee insertion, citations, disclaimer,
policy validation, LINE formatting, and fallback selection.

## 4. Request Classification Ownership

Classification and execution planning are separate operations:

1. `requested_plan` records what the user asked for.
2. `execution_plan` records what can safely execute under current data, cache, model, queue, and LINE
   deadline constraints.

Classification and execution planning MUST be owned by exactly one backend planning service in
`services/`. Do not distribute competing scope logic across the webhook, market service, prompt
builder, and renderer.

### Classification methods

```text
rule_based | rule_plus_lightweight | llm_pre_pass
```

`classification_method` is backend-produced provenance. User text cannot choose or override it.

#### Deterministic multi-label planner

The first implementation and normal `reply_only` path use `rule_based`:

- Scan every supported cue group and build an ordered set of scopes.
- Never stop at the first matched scope.
- `primary_scope` may control display order but must not suppress another requested scope.
- Parse explicit exclusions such as `不要新聞`; same-clause exclusions override inclusions.
- Parse stock/entity, requested date, depth, scopes, exclusions, and current-vs-historical intent
  separately.
- Explicit whole-market language must not attach to an old active stock merely because conversation
  context contains one.
- If the current question explicitly names both a stock and the wider market, preserve both and use
  `mixed_analysis`.
- Conversation context may resolve pronouns or ellipsis only when the current message does not
  explicitly establish a new entity or whole-market scope.
- Classification never creates financial facts.

For:

```text
昨日收盤幫我評估整體走勢及抓取今日重大財經新聞、美伊現況及股市分析
```

the deterministic result must include at least:

```json
{
  "route": "market_analysis",
  "requested_depth": "comprehensive",
  "requested_scopes": [
    "taiwan_market_close",
    "current_news",
    "geopolitics"
  ],
  "classification_method": "rule_based"
}
```

An old active-stock context must not add `stock_snapshot` unless the current question explicitly asks
for that stock or its impact.

#### Optional lightweight classifier

`rule_plus_lightweight` may be enabled only when deterministic processing leaves an unresolved
fragment whose interpretation materially changes retrieval.

The classifier must:

- receive only the bounded current question, resolved entity, and minimum required context;
- return strict schema-validated JSON;
- use deterministic settings, thinking disabled, and bounded output;
- never remove an explicit rule-matched scope;
- never turn user text into a financial fact;
- fall back to deterministic scopes on timeout, malformed output, or low confidence;
- request clarification rather than silently attaching a possible new company to the old stock;
- remain resident without evicting, reloading, or CPU-offloading the main synthesis model.

Enable it only after measured queue and latency evidence proves it fits the supported concurrency
profile.

#### LLM pre-pass

`llm_pre_pass` is a separate model call before retrieval and final synthesis.

- It is forbidden when `delivery_mode=reply_only`.
- It may be used only in a durable asynchronous workflow or offline evaluation.
- Queue wait, inference, parsing, and validation count toward total latency.
- It must use the same allowlisted scope schema and cannot initiate arbitrary retrieval.
- Final financial synthesis remains a separately budgeted model call.

## 5. Requested-Plan And Execution-Plan Contracts

### Requested plan

```json
{
  "contract_version": "request-plan-v1",
  "route": "stock_analysis | market_analysis | mixed_analysis | general_investment | clarify",
  "requested_depth": "focused | comprehensive",
  "requested_scopes": [],
  "excluded_scopes": [],
  "requested_trade_date": null,
  "analysis_cutoff": null,
  "classification_method": "rule_based | rule_plus_lightweight | llm_pre_pass",
  "router_version": "",
  "scope_decisions": [
    {
      "scope": "",
      "decision": "include | exclude",
      "source": "explicit_rule | context_rule | lightweight_classifier | llm_pre_pass",
      "confidence": 0.0,
      "matched_fragment": ""
    }
  ],
  "ambiguous_fragments": []
}
```

### Execution plan

After semantic classification, a deterministic budget planner produces:

```json
{
  "contract_version": "execution-plan-v1",
  "effective_depth": "focused | comprehensive",
  "effective_scopes": [],
  "retrieval_mode": "cache_only | bounded_live",
  "delivery_mode": "reply_only | durable_followup",
  "omitted_scopes": [],
  "omission_reasons": {},
  "classification_calls": 0,
  "synthesis_calls_max": 1
}
```

`retrieval_mode` and `delivery_mode` are system-policy outputs, not language-classifier outputs.
Reducing depth or scopes must be visible in coverage metadata and user wording. Do not silently call a
reduced answer comprehensive.

A non-rule classifier may run only when:

```text
remaining_deadline >
    queue_p95
  + classifier_p95
  + retrieval_p95(effective_scopes)
  + synthesis_p95(depth, token_profile)
  + validation_p95
  + reply_send_buffer
  + jitter_margin
```

If the inequality is false, preserve deterministic scopes, skip probabilistic classification, and use
a safe execution plan or one concise clarification. Do not spend the final-answer budget on routing.

## 6. Data Authority And Quality

| Evidence lane | Permitted use | Replace canonical value | Override referee |
|---|---|---:|---:|
| Canonical DB fact with valid field/date/unit/quality | Numeric and analytical fact | N/A | Only through existing referee |
| Official external event or newer official candidate | Event context or reconciliation candidate | No, until normalized and ingested | No |
| Approved/licensed news | Attributed context | No | No |
| GDELT/news radar lead | Discovery and clearly labelled lead | No | No |
| User message or conversation history | Intent and quoted claim only | No | No |
| Model inference | Explanation and scenarios | No | No |

Use the canonical quality states from reference 01. Do not create a competing model-only quality
system. `estimated` must be labelled; `stale`, `source_delayed`, `missing`, and `unavailable` cannot
support a definitive current claim.

Distinguish absence without changing the canonical quality enum:

```json
{
  "quality": "unavailable",
  "availability_reason": "no_eligible_history | newly_listed | source_not_supported | source_failure | not_published_yet"
}
```

Structured price, EPS, revenue, ROE, margin, institutional, and volume values may become canonical only
after adapter normalization, DB ingestion, and shared data-quality validation.

## 7. Time And Conflict Semantics

- `yesterday` means the latest completed relevant trading session, accounting for weekends and
  holidays.
- Historical analysis uses a point-in-time cutoff and must not use future information.
- News published after a close cannot be presented as the cause of that earlier close; it may be
  labelled next-session background.
- Keep `trade_date`, `period`, `as_of`, `publisher_published_at`, `index_seen_at`, `retrieved_at`, and
  `effective_tw_trade_date` distinct.
- Keep shares/lots, currency scale, percentage/ratio, intraday/close, and adjusted/unadjusted semantics
  distinct.

Conflict types:

```text
value_mismatch | unit_mismatch | period_mismatch | as_of_mismatch |
session_mismatch | adjustment_mismatch
```

Conflict resolutions:

```text
canonical_retained | external_rejected | not_comparable |
pending_reconciliation | claim_suppressed | flag_only
```

- `canonical_retained`: valid canonical data remains authoritative; the conflict may remain context.
- `external_rejected`: source, date, schema, or verification is insufficient.
- `not_comparable`: period, session, unit, or adjustment basis differs.
- `pending_reconciliation`: a newer official candidate has not completed adapter-to-DB quality flow;
  suppress the affected current claim.
- `claim_suppressed`: a material unresolved conflict blocks the affected number or downstream claim.
- `flag_only`: a noncanonical, nonmaterial disagreement is shown only as risk context.

Every conflict records `resolution_reason_code` and `affected_claim_ids`. Do not define
`external_promoted`; external data becomes canonical only through a new validated canonical snapshot.

## 8. Controlled Online Research

Research priority:

1. Fresh canonical database facts.
2. Official primary sources.
3. Approved/licensed secondary sources.
4. News-index or radar discovery sources such as GDELT.

The model never performs network calls itself. A bounded retrieval adapter uses normalized public
entities, aliases, topics, and time windows. It must not use raw LINE identifiers, private profile data,
or unbounded conversation history as search keys.

Verification states:

```text
discovered | unverified | primary_verified | secondary_corroborated |
contradicted | pending_reconciliation | expired
```

- Treat GDELT `seendate` as `index_seen_at` unless publisher time is separately verified.
- Syndicated copies are not independent corroboration.
- Entity aliases come from a versioned registry/stock master, not a short hard-coded company list.
- Search results can write only to a dedicated noncanonical research/event namespace, never canonical
  market, analysis, or referee tables.
- Full article bodies are not retained unless a reviewed source-rights policy explicitly allows it.

Initial configurable cache policy:

```ini
NEWS_SEARCH_POSITIVE_TTL_SECONDS=600
NEWS_SEARCH_NEGATIVE_TTL_SECONDS=120
NEWS_SEARCH_METADATA_RETENTION_DAYS=30
NEWS_SEARCH_RAW_BODY_RETENTION_SECONDS=0
```

Freshness TTL and storage retention are separate. A stale cached item must not appear as current news.
Pruning runs independently even when the latest retrieval returns zero rows. A source policy may impose
a shorter limit.

The source-rights registry records `allow_fetch`, `allow_model`, `allow_display`,
`allow_store_excerpt`, retention, attribution, and last terms review.

## 9. Untrusted-Content Boundary

User text, conversation memory, headlines, excerpts, RSS fields, retrieved pages, and source metadata
are untrusted data, never instructions. Mark external text with `untrusted_text=true` and separate
retrieval/extraction from final synthesis.

If URL fetching is introduced, require:

- HTTPS allowlist;
- redirect limit and DNS/IP revalidation after redirects;
- rejection of loopback, private, link-local, file, and data destinations;
- no browser cookies or local credentials;
- MIME and byte-size limits;
- removal of scripts, styles, comments, and hidden content.

## 10. ModelFactPacketV2

The model receives a typed, sanitized, nonduplicated packet:

```json
{
  "contract_version": "model-fact-packet-v2",
  "request": {
    "depth": "comprehensive",
    "scopes": [],
    "analysis_cutoff": "",
    "locale": "zh-TW"
  },
  "referee": {
    "version": "",
    "main_status": "",
    "reasons": [],
    "decision_ready": true,
    "immutable": true
  },
  "facts": [
    {
      "fact_id": "F001",
      "domain": "institutional",
      "field": "foreign_net",
      "value": 0,
      "unit": "shares",
      "currency": null,
      "period": "daily",
      "trade_date": "",
      "as_of": "",
      "authority_tier": "canonical_db",
      "quality": "ok",
      "availability_reason": null,
      "use_scope": ["explanation", "numeric_claim"]
    }
  ],
  "events": [
    {
      "event_id": "E001",
      "title": "",
      "publisher": "",
      "publisher_published_at": null,
      "index_seen_at": "",
      "retrieved_at": "",
      "verification_state": "unverified",
      "untrusted_text": true
    }
  ],
  "conflicts": [
    {
      "canonical_fact_id": "F001",
      "external_claim_id": "E001:C01",
      "conflict_type": "value_mismatch",
      "resolution": "canonical_retained",
      "resolution_reason_code": "",
      "affected_claim_ids": []
    }
  ],
  "coverage": {
    "included_sections": [],
    "omitted_sections": [],
    "omission_reasons": {}
  }
}
```

The model projection preserves abstract authority, quality, dates, units, permitted use, and evidence
IDs. It removes credentials, raw provider internals, local paths, user identifiers, SQL details, and
unnecessary raw rows.

Numbers from the user question, memory, or news text never enter the canonical numeric allowlist.
Every canonical numeric claim binds `fact_id + field + value + unit + period + as_of`.

## 11. Context Profiles And Model Residency

Before every call, calculate tokens with the actual model tokenizer/template or a conservative tested
estimator.

```text
safety_margin = max(2048, ceil(actual_context * 0.10))

effective_prompt_budget = min(
    profile_prompt_cap,
    actual_context - reserved_output_tokens - safety_margin
)
```

The prompt cap covers system prompt, user question, conversation projection, and FACTS packet.

Initial benchmark profiles:

| Profile | Prompt token cap | Facts total/per scope | Events total/per scope |
|---|---:|---:|---:|
| `focused-16k-v1` | 6,000 | 48 / 16 | 4 / 2 |
| `comprehensive-16k-v1` | 11,000 | 96 / 24 | 8 / 3 |
| `comprehensive-32k-candidate-v1` | 20,000 | 160 / 32 | 12 / 4 |

Shared initial limits:

```text
headline <= 160 Unicode characters
summary/excerpt <= 320 Unicode characters
conflicts <= 16
evidence IDs per analysis block <= 8
reserved output tokens = 900
```

These are versioned benchmark profiles, not financial-business constants. Token limits override count
limits. Required canonical facts that exceed a count limit are deterministically aggregated, never
randomly removed; every omission appears in coverage metadata.

Priority order:

1. System and safety contract.
2. Immutable referee.
3. Date and quality metadata.
4. Requested canonical facts.
5. Conflicts and missing-data manifest.
6. Verified official events.
7. Approved/licensed news.
8. Unverified radar leads.
9. Summarized conversation memory.

Do not duplicate the same values under raw and display fields. Summarize price-volume records into
eligible aggregates and bounded significant levels.

### Context capacity and residency

Increasing `num_ctx` is a capacity candidate, not a substitute for packet reduction.

- Keep a stable model profile and create a separately named 32K candidate; do not overwrite stable.
- The configured context, model Modelfile `num_ctx`, running `/api/ps.context_length`, and packet
  preflight limit must agree. Otherwise `comprehensive_ready=false`.
- OpenAI-compatible requests do not set `num_ctx` per request; context changes require a separately
  created model/Modelfile.
- Start candidate benchmarks with one inference slot and the current validated KV precision.
- Do not enable more parallel slots until VRAM and latency tests pass; required memory scales with
  parallel slots and context allocation.
- Release requires only expected runners, no stale/orphan runner, no CPU weight/KV offload, no OOM,
  no context HTTP 400, and a configured peak free-VRAM safety margin.
- Test text-only and text/vision residency separately.
- A model merely loading successfully is not sufficient; p95 deadline and grounding gates must pass.

## 12. Model Output Contract

The model returns structured analysis only:

```json
{
  "contract_version": "model-analysis-v2",
  "explanation_blocks": [
    {
      "block_type": "fact | inference | scenario | limitation",
      "text_template": "",
      "evidence_ids": ["F001"],
      "uncertainty": "low | medium | high",
      "conditions": []
    }
  ],
  "missing_data": [],
  "used_event_ids": [],
  "research_limitations": []
}
```

- The model does not return a competing main status, signal, or recommendation.
- The renderer inserts the referee conclusion independently.
- Canonical numbers and dates use typed fact references/placeholders and are inserted by the backend.
- Every inference cites evidence IDs.
- Scenarios state conditions and uncertainty.
- Missing data may be explained but never guessed.
- Words such as buy or sell may appear in neutral, educational, quoted, negative, or conditional
  analysis. Evaluate the complete sentence; do not use a brittle word-only ban.
- Prohibit personalized imperative trades, guaranteed returns, guaranteed target prices, and
  unsupported certainty.

## 13. Validation And Rendering

Validation order:

1. JSON/schema validation.
2. Evidence-ID existence.
3. Scope and analysis-cutoff validation.
4. Field, unit, date, quality, and use-scope validation.
5. Immutable referee validation.
6. Numeric placeholder resolution.
7. Prompt-injection/instruction-following validation.
8. Sentence-level financial-policy validation.
9. Citation and attribution validation.
10. LINE length and message-count validation.

### Exact claim binding

A numeric or date claim about canonical financial data is renderable only when one typed placeholder
resolves to exactly one eligible canonical fact whose `fact_id`, `field`, `value`, `unit`, `period`,
`as_of`, `quality`, and `use_scope` all match. An eligible numeric fact must have
`authority_tier=canonical_db`, a quality state permitted for that use, and `numeric_claim` in
`use_scope`. An `estimated` value additionally requires the approved estimated-data label.

An attributed event date is renderable only through one eligible event evidence record with matching
`event_id`, publisher time semantics, verification state, and permitted display use. Event/news dates
never become canonical financial facts merely because they are cited.

Facts with `quality=missing`, `quality=unavailable`, or an absence-only `availability_reason` may
support `limitation` blocks only. They must not authorize a numeric placeholder, a definitive current
claim, a referee mutation, or substitution from user text, conversation memory, news text, or model
output. Evidence-ID existence alone is never sufficient.

Reject the affected analysis block when binding is absent, ambiguous, duplicated, wrong-field,
wrong-unit, wrong-period, wrong-date, wrong-quality, or outside `use_scope`. Reject the entire model
response when the affected claim is required, changes the referee meaning, or cannot be removed
without making the answer misleading. The renderer must never print an unresolved placeholder or the
model's raw numeric/date text.

Final rendering order:

1. Immutable referee conclusion.
2. Analysis date and quality state.
3. Model explanation.
4. News/event context with citations.
5. Conditional risks and scenarios.
6. Missing data and limitations.
7. Reference 07 disclaimer.

At most one model repair attempt is allowed, and only when:

```text
repair_allowed =
    remaining_to_reply_deadline >
        gpu_queue_wait_p95
      + repair_generation_p95
      + repair_validation_p95
      + render_p95
      + reply_reserve
      + jitter_margin
```

If any required p95 is not measured, `repair_allowed=false`. Cold model, GPU busy, or imminent timeout
also disables repair. Prefer deterministic schema repair or evidence-based fallback.

## 14. LINE Deadline, Concurrency, And Queueing

Reference 08 is the single authority for current LINE platform timing and transport facts. Do not copy
mutable platform numbers into multiple references.

```text
t_ingress = webhook-route entry monotonic time
reply_deadline = t_ingress + internal_reply_budget
reply_reserve = LINE_REPLY_TIMEOUT_SECONDS + LINE_FINAL_SAFETY_SECONDS
work_stop = reply_deadline - reply_reserve

predicted_work_p95 =
    webhook_queue_wait_p95
  + gpu_queue_wait_p95
  + classification_p95
  + retrieval_p95
  + packet_build_p95
  + generation_p95
  + validation_p95
  + render_p95

admit_full_analysis = predicted_work_p95 < work_stop - now
```

Measure from webhook ingress, not background-handler start.

### Concurrency and admission

- All text synthesis, model classification, vision analysis, and model-based conversation compaction
  that share a GPU use one deadline-aware admission controller.
- Interactive LINE work has priority over memory compaction and other noninteractive model jobs.
- A queued interactive request MUST signal cooperative cancellation of an already-running shadow
  generation. Shadow uses a streaming response so the client connection can close at the next stream
  boundary; the cancelled sample is recorded as `preempted`, never silently dropped or reused.
- A post-reply shadow MUST NOT cold-load the text model while the live gateway is accepting traffic.
  If the configured text model is not resident, record `deferred_model_not_resident`; warm/cold load
  behavior is measured only by an explicit benchmark or approved maintenance operation.
- Startup or benchmark prewarming outside this admission controller is disabled by default while the
  gateway accepts traffic. Any explicit exception is maintenance evidence, not live Load Matrix data.
- Initial synthesis concurrency is one. Scheduler capacity must not exceed the benchmarked inference
  slots or configured model-server parallelism.
- Queue admission is bounded by size and deadline. A request predicted to miss `work_stop` must not
  enter the GPU queue.
- Interactive scheduling uses earliest deadline first; equal deadlines may preserve FIFO.
- If HTTP workers become multi-process, a process-local semaphore is insufficient; admission moves to
  a shared serving boundary.
- Queue wait, model residency/load, prompt evaluation, token generation, and reply HTTP are measured
  separately.
- Admission observability includes preemption requests, completed preemptions, preempted jobs, cold
  deferrals, and the actual interactive wait from cancellation request until GPU work begins.

## 15. Fallback And Delivery

Fallback order when a comprehensive request cannot meet its execution plan:

1. Use fresh canonical/search cache and stop bounded live retrieval.
2. Reduce lower-priority detail; any omitted scope is listed with `deadline_budget` or
   `queue_overload`, and the answer is not called comprehensive.
3. Use an approved resident fast model only if its evidence and policy gates already pass.
4. Return a deterministic DB/referee answer with explicit coverage and research limitations.
5. Use `durable_followup` only after reference 09's durable job, protected identity, consent, quota,
   cancellation, retry, and retention requirements are implemented and tested.

Do not promise an automatic later answer while the product is reply-only. A model timeout, invalid
output, missing data, search failure, queue overload, and policy rejection are distinct fallback
categories.

## 16. Observability And Privacy

Record de-identified structured metrics:

- route, depth, scopes, classification method, and router version;
- packet/profile versions, input tokens, trimming, and omitted sections;
- model identifier, runner/residency state, queue wait, load, prompt, generation, and finish reason;
- retrieval coverage, cache state, source verification states, and timeout class;
- validator rejection code and fallback category;
- webhook queue wait, reply-token age, render time, and reply HTTP latency.

Do not log raw LINE identifiers, reply tokens, raw user messages, full FACTS packets, credentials,
source secrets, or article bodies.

Rollout controls:

```text
off | shadow | canary | on
```

Provide an immediate kill switch. A configuration-only health check is not proof that comprehensive
analysis works; readiness must verify model/context/profile agreement and a bounded contract probe.

## 17. Verification And Release Gates

### Required tests

- Protected analysis-file hashes and approved golden outputs match baseline.
- Existing scoring, RSI, support/resistance, chip cost, price-volume, outlook, and referee fixtures are
  unchanged.
- The original market/news/geopolitics sentence produces all required scopes.
- `台積電基本面、籌碼、技術面與最新新聞完整分析` preserves every explicit scope.
- `只看籌碼，不要新聞` preserves the exclusion.
- `台積電和整體台股誰比較強` becomes `mixed_analysis`.
- Word order, punctuation, and conjunction variations do not change the scope set.
- Old active-stock context does not hijack an explicit whole-market request.
- `reply_only` performs at most one 27B/28B synthesis call.
- Missing canonical fundamentals may still use one admitted synthesis call for a grounded limitation or
  conditional explanation; missing data alone must not silence the model.
- A model-supplied numeric/date claim without exact eligible typed binding is rejected and never
  rendered, even when its evidence ID exists.
- When deadline admission fails, missing-data analysis skips the model and returns the deterministic
  honest fallback.
- Worst-case packet stays under the actual context/profile limit.
- Real model calls do not return context HTTP 400.
- User-supplied fake numbers never become canonical evidence.
- Wrong-field, wrong-date, Chinese-number, shares/lots, intraday/close, and adjusted/unadjusted
  substitutions are rejected.
- News figures cannot override canonical data.
- Stale DB plus a newer official candidate produces `pending_reconciliation`.
- After-close news cannot cause the earlier close.
- Prompt-injection headlines/excerpts remain untrusted.
- Search timeout, 429, offline, irrelevant, old, and duplicate results degrade honestly.
- Search writes zero rows to canonical market/referee tables.
- Multi-event, restart, duplicate, and redelivery behavior is tested.
- Shadow mode sends only the stable answer while scoring the candidate.

### Load matrix

- Concurrent arrivals: 1, 2, 4, and 8.
- Warm and cold model.
- Focused and comprehensive profiles.
- News cache hit and miss.
- Text with memory compaction contention.
- Text with vision residency/usage.
- One webhook containing 5 and 20 events.
- Search timeout/429, model busy, and reply-network delay.
- At least 100 representative completed samples per release profile before calculating p95.

### Evidence integrity and maintenance windows

- A maintenance window that stops the LINE webhook, market-data gateway, vision service, or ordinary
  competing workload MUST record timestamped health probes and the observed availability gap. It is
  maintenance evidence only and MUST NOT be labelled live shadow or Load Matrix evidence.
- Model-only or otherwise resource-reduced samples may prove call correctness, context-overflow
  absence, and validator classification only. They MUST NOT substitute for concurrency, queueing,
  vision co-residency, memory-compaction contention, or end-to-end LINE latency evidence.
- Phase A evidence is bound to the packet builder, validator, renderer, prompt contract, admission
  controller, model digest, and serving profile source hashes used to create it. A behavior-affecting
  change to any bound component invalidates that evidence for final acceptance unless a written
  dependency analysis proves the measured property is unaffected. When uncertain, recollect it.
- Selecting the first or best validator-pass sample is allowed for content inspection only. The same
  artifact MUST contain that scenario's attempt count, pass count, reject count, reject rate, and the
  complete selected attempt. A selected pass alone is never quality or stability evidence.
- Post-reply shadow reliability MUST expose queued, started, completed, preempted, deferred, abandoned,
  and noncompletion counts. Silent loss is a failed evidence-integrity gate.
- A preemption probe measures only interactive priority and cancellation latency. It MUST carry
  `valid_for_load_matrix=false` until every required Load Matrix case has been executed.

### ReleaseBenchmarkEvidence

Every p95 decision requires a stored, reviewable benchmark artifact with at least this schema:

```json
{
  "contract_version": "release-benchmark-evidence-v1",
  "benchmark_id": "",
  "captured_at": "",
  "deployment_target": {
    "gpu_vendor_model": "",
    "gpu_vram_bytes": 0,
    "cpu_model": "",
    "system_ram_bytes": 0,
    "operating_system": "",
    "driver_version": "",
    "cuda_version": ""
  },
  "model_profile": {
    "model_id": "",
    "model_digest": "",
    "parameter_size": "",
    "quantization": "",
    "runner_name": "",
    "runner_version": "",
    "context_length": 0,
    "kv_cache_type": "",
    "gpu_layers": 0,
    "parallel_slots": 1,
    "prompt_profile": ""
  },
  "load_case": {
    "concurrent_arrivals": 1,
    "model_state": "warm | cold",
    "news_cache": "hit | miss",
    "contention": []
  },
  "measurements": {
    "completed_samples": 0,
    "failed_samples": 0,
    "end_to_end_p95_ms": 0,
    "predicted_work_p95_ms": 0,
    "reply_budget_ms": 0,
    "work_stop_budget_ms": 0
  }
}
```

For a single-workstation deployment, the artifact must be measured on that target workstation. For a
host class, every released host must match the reviewed hardware and serving profile. Benchmark
evidence is not transferable across a change to GPU model/VRAM, NVIDIA driver, CUDA runtime, model
digest, quantization, runner or runner version, context length, KV-cache type, GPU-layer placement,
parallel slots, prompt profile, or internal reply budget. Any such change invalidates the prior gate
and requires a new load matrix.

Do not store serial numbers, usernames, local paths, or other machine identifiers in this artifact.
The identity is the reviewed deployment class plus exact model/serving configuration, not the person
or filesystem location.

### Hard release gates

```text
full_test_suite = pass
protected_analysis_diff = 0
release_benchmark_evidence = verified
benchmark_target_match = true
p95_gate = pass
ungrounded_numeric_or_date_claims = 0
referee_overrides = 0
canonical_table_writes_from_search = 0
prompt_injection_failures = 0
context_overflow_failures = 0
expired_or_reused_reply_tokens = 0
```

With the current default internal budget/profile, additionally require:

```text
p95(queue + classification + retrieval + packet + model + validation + render)
    < work_stop - t_ingress

p95(webhook ingress -> LINE reply completed)
    < internal_reply_budget
```

Do not replace p95 with an average or warm-only measurement. Cold and contention paths must pass the
approved downgrade/admission behavior. Accumulate at least five trading days of reviewed shadow
evidence before canary user output.

## 18. Rollout Order And Official Sources

1. Freeze protected analysis hashes and approved behavioral fixtures.
2. Commit/approve this skill contract only; no runtime behavior change.
3. Build ModelFactPacketV2, tokenizer preflight, and observability in shadow mode using DB facts only.
4. Add deterministic multi-scope request planning.
5. Benchmark stable 16K and separately named 32K candidate profiles.
6. Add deadline-aware GPU admission and queueing.
7. Add controlled retrieval in shadow mode.
8. Expose verified or clearly labelled news with backend-rendered citations.
9. Canary the candidate after all hard gates and shadow evidence pass.
10. Build durable push only as a separate approved phase.

Official sources, last checked 2026-08-29:

- [LINE Messaging API reference](https://developers.line.biz/en/reference/messaging-api/)
- [LINE webhook events](https://developers.line.biz/en/docs/messaging-api/receiving-messages/)
- [LINE webhook error statistics](https://developers.line.biz/en/docs/messaging-api/check-webhook-error-statistics/)
- [Ollama context length](https://docs.ollama.com/context-length)
- [Ollama FAQ: concurrency and KV cache](https://docs.ollama.com/faq)
- [Ollama OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility)
- [Ollama running-model details](https://docs.ollama.com/api/ps)
- [GDELT DOC API](https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/amp/)

Re-check time-sensitive platform behavior and source terms before runtime release. This reference is an
engineering contract, not legal or investment-adviser clearance; reference 07's launch gate remains.
