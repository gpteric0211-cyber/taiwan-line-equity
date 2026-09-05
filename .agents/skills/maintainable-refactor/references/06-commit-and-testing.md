# Commit Safety And Regression Test Rules

Read this before every commit, after any watchlist/detail/LINE-message change, and for offline
test-contract characterization of existing validators or services.

## Commit Safety Rules

Before committing, verify that only relevant source, scripts, tests, and docs are staged.

- Do not commit `.db`, `*.sqlite`, `*.sqlite3`, generated DB snapshots, or local runtime databases unless the user explicitly opens a DB artifact phase.
- Do not commit `.env`, secrets, API keys, tokens, or local credentials — this includes the LINE Channel Access Token and Channel Secret.
- Do not commit `dist/`, `portable/`, `Taiwan50Dashboard_Portable_*/`, runtime bundles, or generated portable packages.
- Do not commit `__pycache__/`, `*.pyc`, logs, virtual environments, or package caches.
- Before commit, run `git status --short` and inspect staged file names.
- If DB/dist/portable/secrets appear, do not add them; report them and continue only with source/doc/test files.
- Keep unrelated personal text files out of commits.

## Required Regression Tests For Watchlist And Detail Changes

When a change touches watchlist onboarding, bootstrap, detail readiness, market-type resolution, frontend missing-data display, or the LINE message/webhook layer, run the smallest relevant subset of this matrix.

- `python -m py_compile` for modified Python files.
- Smoke checks:
  - `GET /`
  - `GET /api/quotes?mode=watchlist`
  - `GET /api/quotes?mode=tw50`
  - `GET /api/stock/{code}/detail`
- Ready Taiwan50 sample: `2330`.
- Existing ready watchlist sample from the current watchlist.
- Newly added listed stock sample.
- OTC-style sample such as `5425` if available in local data/source support.
- Direct non-ready detail page such as `/stock/3491` or another currently non-ready code.
- Duplicate `POST /api/watchlist` for the same code must be idempotent.
- After POST, verify GET detail returns `is_watchlist=true`, `bootstrap_status` defined, and `readiness` present.
- Confirm ordinary GET/Bot market-data routes remain read-only; separately confirm webhook ingress is bounded, signature-verified, and idempotent.
- Scan normal frontend/API output for raw leak terms listed in `05-web-dashboard-presentation-and-portable.md` § API Sanitization And Frontend Display Rules.
- **If the LINE message layer changed**: run the relevant tests from `tests/test_line_bot_gateway.py`, `tests/test_bot_market_data_api.py`, and `tests/test_start_line_bot_stack.py`; render the exact outbound payload; apply references 05 and 07; verify at most five message objects and validate Flex/reply/push JSON using the applicable LINE validation endpoint.
- Test invalid signatures, empty `events=[]`, duplicate `webhookEventId`, bounded reply latency, no raw identifier/secret logs, and read-only Bot API behavior when those paths change.

## Offline Test-Contract Characterization Work

Use this track for authorized test-only additions or contract alignment against an existing
validator/service: no production edits, live model/LINE calls, restart, or deployment. A later
production fix is a separately authorized step, not characterization under another name.
Applicable grounding, protected-analysis, privacy, and evidence-integrity rules still apply.
Reference 10's live load matrix, hardware/p95 benchmarks, and shadow-day release requirements are
not prerequisites for this offline diagnostic track; its results cannot satisfy those release gates.
Documentation-only edits are outside this track: validate documents/links/skill metadata instead.

### Baseline and change scope

- Record exact writable files (and functions/constants when constrained), evidence output directory,
  approved dirty-worktree state, and expected baseline failures by test ID. Do not clean the tree.
- Freeze authored source paths/hashes under `review_src/`, `scripts/`, `tests/`, plus root launchers;
  exclude environments, runtime/dist bundles, caches, generated logs, and archived source copies.
  Include non-Python inputs/configuration that the tests actually depend on; never expose secrets.
- Keep the approved protected-analysis manifest (currently 11 files), relevant immutable historical
  JSONL, S1-R fixtures, and shared golden cases protected even when outside the source manifest.
  Reuse the recorded path lists, not a hard-coded source/test count. Missing required inputs,
  unapproved protected-input changes, and writes outside the whitelist are stop conditions,
  not automatic re-baselines.
- Reuse approved baselines and isolation-audit evidence while their relevant sources, dependencies,
  fixtures, and effective test configuration still match. Check the manifest and changed dependencies;
  do not repeat a repository-wide investigation each turn. Record authorized deltas separately and
  preserve old evidence. A test change still requires the before/after runs below.

### Live database boundary

- Live SQLite main-file hashes and WAL/SHM state are informational, not immutable-source gates.
  Do not add live DB hashing/inspection just for this track. If drift is observed, retain the
  observation; background writes are possible, but an unidentified writer stays unidentified.
  Never infer test pollution or blame a scheduler from a hash alone.
- Before executing tests, establish that the tests under review and suite setup/fixtures use isolated
  DBs or no DB access. Start with `rg` for the DB filename, connection factories, and configuration
  names; trace imported helpers, environment/default path resolution, bootstrap side effects,
  `conftest.py`, and setup/teardown. A filename search with zero hits is not proof of isolation.
- Isolation/mocks must take effect before any connection or import-time initialization; runtime
  connection/network tripwires may supplement, not replace, the source check. Do not import a
  potentially connecting module merely to inspect it. Existing hash-bound isolation audits can be
  reused for unchanged paths; inspect newly reachable dependencies and changed tests.
- A credible live-DB access/write path or isolation uncertainty blocks the affected test run until
  resolved. Do not run an unsafe suite just to obtain a baseline, and do not silently repair fixtures
  outside the whitelist. Report the path/evidence and request separate authority when needed.
- Once isolation is established, unexplained live-DB drift alone does not block this track. Record
  the residual uncertainty without repeatedly reopening writer attribution. Do not stop schedulers,
  terminate processes, open/change live DB contents, checkpoint, or delete WAL/SHM for cleaner hashes.
  An explicitly frozen offline DB fixture is different: its integrity remains a gate.

### Evidence proportional to dependency risk

1. Before changing tests, save the relevant source/fixture baseline and run the complete
   `python -m pytest tests -q`. Afterward compile the changed Python files (or the frozen authored-source
   set when requested) and run the complete suite again. Save commands, timestamps, raw stdout/stderr,
   exit codes, and test IDs/statuses. Focused runs are supplemental. Do not introduce filters, skips,
   xfails, or changed expectations to conceal red cases; report pre-existing skips separately.
2. Pin new cases' exact inputs, provenance, and expectations independently of validator observations;
   stable count/IDs alone are insufficient. Detect missing, duplicate, and unexpected case IDs.
   Reuse immutable fixture builders where appropriate and
   bind their contents/source hashes. Preserve synthetic-derivative labels, full input/output JSON,
   and raw/repair/final verdicts; do not call a projected service verdict a live integration result.
   Assert exact reasons and rendered content as well as pass/fail, retaining schema/numeric priority.
3. Produce a source-dependency/AST scope report when fixture/provenance extraction depends on another
   file's functions or line positions. If edits can affect shared fixtures, builders, golden hashes,
   or other cases' provenance, capture cases before editing and structurally compare afterward with
   explicitly authorized difference paths. Reject unapproved case additions/deletions and unexpected type/value
   changes. Use an existing suitable checker rather than redesigning one each round.
   A purely additive independent test file with frozen content and working self-checks does not need
   a standalone AST report or general-purpose deep-diff tool by default; still verify source scope.
4. Deliver a red/green table separating known baseline reds, intended diagnostic reds, unexpected
   regressions, and false-positive/safety regressions. Give each red's exact input, expected/observed
   reason and one-sentence evidence-backed cause, using these standard diagnostic categories:

   - `over_rejection`: the supported, contract-valid input should pass but is rejected.
   - `wrong_reason_code`: the verdict is correct but required reasons are missing, wrong, or extra.
   - `missing_capability`: the contract requires a capability with no implementing path. Record the
     responsible dispatch/guard location and missing behavior; do not infer this from a red alone.

   Record one primary cause per case; a missing path that causes rejection is primarily
   `missing_capability`, with over-rejection recorded as its symptom, not a second case.
   These three categories are not exhaustive: unsafe acceptance/under-rejection, harness or fixture
   faults, environment failures, and unresolved causes must remain separately visible. Do not force
   them into a diagnostic category or declare them complete to satisfy a reporting format.
   A red assertion stays a recorded failure, not an xfail or a claimed pass.
   Compare old test IDs/statuses, not counts alone; distinguish negative cases, positive controls,
   and harness self-checks in totals.
5. Verify protected inputs and the write whitelist. Link complete changed test code/diff and raw
   evidence with paths/line numbers. Keep failures, invalidated evidence, and limitations visible.
   Update the review index when permitted; an explicit narrower whitelist takes precedence, so link
   the allowed report and identify any stale index rather than editing outside authorization.

### Completion and next authorization

The characterization step is complete when required regression evidence is recorded, the test's
internal integrity checks pass, protected/whitelist/isolation gates hold, and every diagnostic red
has an evidence-backed explanation. Unexpected baseline drift, harness failures, uncertain isolation,
or a previously correct rejection turning into an unsafe pass require investigation within scope
or a stop/report; they are not explained away as intentional reds. Completion does not mean the
full suite is green, production is fixed, or release is approved.

A pre-existing unsafe acceptance may complete characterization only when its contract violation
and root cause are evidenced and all other gates hold; report it separately as an unresolved safety
defect, not a legitimate pass or release clearance. A newly added test exposing it does not alone
prove a regression: use preserved before/after behavior to distinguish existing defects from drift.

Once a red's cause is precise, propose the smallest fix authorization with exact file/function/line,
case IDs, expected transitions, invariant negative cases, and exclusions. Link that one-sentence
cause to the raw expected/observed result and responsible code; a category label alone is not proof.
Do not require another round merely to restate the same root cause.
Obtain explicit production-fix approval unless that
exact fix is already authorized; evidence that explains a bug is not permission to change it.
