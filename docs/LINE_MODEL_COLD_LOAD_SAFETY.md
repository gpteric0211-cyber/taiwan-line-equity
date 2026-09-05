# LINE model cold-load safety — implementation and maintenance runbook

Reviewed: 2026-08-30, Asia/Taipei. Status: source/tests passed; **not deployed, no new live cold run**.

## Findings and causal limits

- The old collector always executed `cold-load-probe` before memory/vision probes. A contention rerun
  could therefore unload the production text model without a separately requested cold test.
- The old service unloaded the model outside the admission controller, then scheduled an interactive
  call with a 125-second deadline. The adapter capped its background model request at 120 seconds.
- The v6 request failed after 120,888 ms. The server log observed a client disconnect during model
  loading. This proves the request/load lifecycle failed, not why the hardware took so long.
- The six orphan runners were created between 02:09 and 03:26, before v6 at 07:11–07:13. The previous
  wording that v6 “left/created” them was unsupported. They were discovered after v6 and removed;
  whether their resource use caused the timeout remains unproven.

## Implemented safeguards

1. `run_line_model_phase_d_probes.py` excludes cold load by default. `--include-cold` requires
   `--maintenance-window-confirmed` before any work starts.
2. Before a cold POST, the collector performs an authenticated, read-only capability handshake. It
   requires `line-model-maintenance-cold-load-v2`, `enabled=true`, maintenance-only operation, exclusive
   admission and a bounded timeout. An old/disabled server cannot receive a cold POST from this runner.
3. The service additionally requires `LINE_MODEL_COLD_PROBE_ENABLED=true` and an explicit request
   confirmation. Both gates run before residency queries, unloading or queue submission.
4. Unload, native preload, residency verification and a bounded READY generation execute in one
   non-preemptible **maintenance** admission slot. Interactive work remains higher priority while
   queued; once loading starts, its conservative occupancy budget supports early DB fallback.
5. Native preload uses an empty `/api/generate` request. It does not alter ordinary text inference,
   the normal 120-second background cap, stock formulas, packet construction or LINE delivery.
6. The maintenance preload read timeout defaults to 1,260 seconds: the current launcher’s 20-minute
   load allowance plus a 60-second buffer. It is configurable in 30–3,600 seconds. This is a maintenance
   ceiling, **not a P95 target and not permission to extend the LINE reply deadline**. The collector
   reads the server timeout and allows another 90 seconds for unload, readiness generation and transport.
7. A preload failure records a reason code and final residency, sets `recovery_required` when needed,
   and does not automatically retry loading or kill arbitrary processes.
8. The collector fsyncs `started` before each request and `finished`/`skipped` after it. Transport failure
   is recorded without raw exception URLs/secrets; remaining probes are skipped. Post-run health
   failure still produces an artifact. A crashed collector leaves an identifiable unmatched start.
9. HTTP 200 alone cannot pass: cold requires completed lifecycle and residency; memory requires
   observed preemption; vision requires completion plus an in-budget stable reply and no candidate send.
10. Cold results use `maintenance_cold_lifecycle`, `valid_for_live_reply_deadline=false`, and
    `valid_for_load_matrix=false`. A safe long maintenance load does not satisfy the live cold/fallback gate.

Official API semantics checked 2026-08-30: [Ollama preload/keep-alive FAQ](https://docs.ollama.com/faq)
and [generate endpoint and load metrics](https://docs.ollama.com/api/generate).

## Tests and live read-only check

- `python -m py_compile`: passed for changed Python sources and tests.
- `PYTHONPATH=review_src python -m pytest tests -q`: **610 passed in 12.58s**.
  On PowerShell, set `$env:PYTHONPATH='review_src'` before invoking the project Python.
- Added 23 collected test cases; changed 3 existing cases. Coverage includes old-server refusal,
  disabled gate, explicit confirmation, unload/preload inside one slot, separate timeout, classified
  failure without retry, journal retention, failed health check and rejection of HTTP-200-only evidence.
- 07:36:38 live capability GET: `cold_probe_not_enabled_or_unsupported`; no cold POST sent. The running
  server is still the previous deployment. This is fail-closed collector evidence, not a deployed-server test.
- Protected analysis hashes remain 11/11 matching the approved baseline.

## Required maintenance procedure — not executed this turn

1. Confirm a maintenance window and recovery authority with the operator. These flags are operator
   assertions, not proof that traffic stopped. Save timestamped 8010/8020/8021 health and actual LINE
   ingress/reply telemetry throughout. Do not claim zero impact without those observations.
2. Capture exact GPU/VRAM/driver/CUDA/model digest/quantization/context/KV/parallel settings and a runner
   process census (PID, parent, start time, model association). Require no orphan, no offload and the
   configured VRAM reserve. On Windows, use a scoped native process query; never bulk-kill by name.
3. Deploy the reviewed source as a measured maintenance operation, keeping all services configured.
   Keep `LINE_MODEL_COLD_PROBE_ENABLED=false` outside this window. Verify health and official LINE webhook.
4. Enable the cold flag only for the confirmed window. Verify the read-only capability response. Ensure
   the server load allowance, maintenance timeout and collector timeout agree; do not reuse 120 seconds.
5. Execute the explicit cold case once, with a unique evidence filename:

   ```text
   python scripts/run_line_model_phase_d_probes.py --output logs/line_model_shadow/phase_d/<unique-run>.json --include-cold --maintenance-window-confirmed
   ```

   A normal contention-only run omits both maintenance switches. Both are diagnostic workloads, not
   authentic user LINE reply measurements.
6. On failure, stop further probes. Inspect authoritative process handles, residency and logs before
   any recovery attempt. Do not restart merely because observation timed out, and do not silently retry.
   Remove only individually verified orphan identities if authorized; revalidate identities immediately
   before termination. Never terminate a current server or runner based only on its executable name.
7. Verify text/vision residency, absence of unexpected runners, health and official webhook. Disable the
   cold flag at the end of the window. Record any outage and each failed attempt; do not select only passes.

This patch does not implement automatic process-tree cleanup, guaranteed crash recovery or durable job
resumption. Those claims remain unproven. Startup background warmup still uses the existing path and is
not silently switched to the longer maintenance timeout.

## Evidence dependencies and remaining goal gates

The adapter/service/API source hashes changed, so old artifacts must not be called fully current-source.
Dependency review: ordinary `qwen_chat`, packet builder, validator, renderer, referee, stable reply and
warm shadow/preemption paths were not changed. Prior Phase A/B content and warm diagnostics can remain
historical evidence of those properties. Old **cold lifecycle** evidence cannot certify this new path.

Remaining: deploy/verify the cold safeguard in an approved maintenance window; clean-runtime cold and
contention measurements; candidate high-concurrency latency failures; Phase B rejection rates; five
trading days and 100 representative samples/profile; actual ingress-to-LINE-reply telemetry; manual
candidate-versus-stable review. Canary and candidate replacement remain disabled.
