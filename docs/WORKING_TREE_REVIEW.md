# Working Tree Review

## Summary

The working tree is currently not clean. It contains a mix of completed phase artifacts, production-code changes from earlier tasks, documentation reports, scripts, tests, and unclear text files.

This review did not run `git add`, `git commit`, `git stash`, `git reset`, delete files, move files, or modify production code. It only documents the current state and suggests commit grouping for human review.

Current tracked diff summary:

```text
11 files changed, 1831 insertions(+), 424 deletions(-)
```

Current tracked dirty files include:

```text
.agents/skills/maintainable-refactor/SKILL.md
docs/CODEX_REVIEW_PACKET.md
review_src/adapter/yahoo.py
review_src/app.py
review_src/core/config.py
review_src/core/db.py
review_src/price_volume.py
review_src/scoring.py
review_src/static/detail.html
review_src/static/index.html
修改命令方式.txt
```

Current untracked files include:

```text
docs/calendar_shadow_context_examples.md
docs/calendar_shadow_matrix_report.md
docs/data_quality_gate_regression_report.md
docs/local_values_2317.md
docs/next_day_outlook_coverage_audit.md
docs/outlook_shadow_diff_report.md
odex 做一個跟ig結合的交友網站.txt
review_src/adapter/twse_valuation.py
review_src/core/outlook_context.py
review_src/market/calendar_data.py
review_src/market/calendar_override.py
review_src/market/calendar_status.py
review_src/outlook/
review_src/repository/taiwan50_close_batch_repository.py
review_src/repository/twse_valuation_repository.py
review_src/services/
scripts/
tests/
```

## Files by Phase

### Source Alignment / Data Source Review

Likely related files:

```text
docs/DATA_SOURCE_ADAPTER_AUDIT.md
docs/DB_REPOSITORY_AUDIT.md
docs/local_values_2317.md
scripts/export_local_values_2317.py
review_src/adapter/twse_valuation.py
review_src/repository/twse_valuation_repository.py
review_src/services/twse_valuation_service.py
tests/test_twse_valuation_adapter.py
tests/test_twse_valuation_repository.py
```

Human review notes:

- `review_src/adapter/twse_valuation.py`, `twse_valuation_repository.py`, and `twse_valuation_service.py` appear to be implementation artifacts, not just audit docs.
- Confirm whether these were intended to ship before committing.

### Calendar-C

Likely related files:

```text
review_src/core/outlook_context.py
review_src/market/calendar_data.py
review_src/market/calendar_override.py
review_src/market/calendar_status.py
review_src/outlook/shadow_builder.py
docs/calendar_shadow_context_examples.md
scripts/export_calendar_shadow_context.py
```

Human review notes:

- These files look like shadow calendar / in-memory override prototype artifacts.
- They should be grouped separately from production fixes because they introduce new architecture surfaces.

### Calendar-D1

Likely related files:

```text
scripts/test_calendar_shadow_matrix.py
docs/calendar_shadow_matrix_report.md
```

Human review notes:

- This group appears test/report oriented.
- Confirm whether generated report should be committed with the test script.

### Calendar-D2

Likely related files:

```text
scripts/compare_outlook_vs_shadow.py
docs/outlook_shadow_diff_report.md
```

Human review notes:

- This group compares legacy vs shadow outlook.
- Report currently shows skip behavior when shadow is disabled in earlier test runs; confirm desired snapshot before commit.

### Calendar-D3A

Likely related files:

```text
scripts/audit_next_day_outlook_coverage.py
docs/next_day_outlook_coverage_audit.md
```

Human review notes:

- These are formal `next_day_outlook` coverage audit artifacts.
- They are useful to keep together.

### Calendar-D3B

Likely related files:

```text
review_src/app.py
docs/next_day_outlook_coverage_audit.md
docs/CODEX_REVIEW_PACKET.md
```

Human review notes:

- `review_src/app.py` is high risk because it contains many unrelated tracked changes beyond D3B.
- Do not commit the entire `app.py` diff without reviewing hunks.
- The D3B-specific section is around `data_readiness_for_items()` and uses `Phase Calendar-D3B` comments.

### Calendar-D3B-Verify

Likely related files:

```text
scripts/audit_data_quality_gate_regression.py
docs/data_quality_gate_regression_report.md
docs/CODEX_REVIEW_PACKET.md
```

Human review notes:

- This group is read-only regression audit tooling and generated report.
- It did not modify production behavior.

### Pre-existing / Unclear

Unclear files:

```text
修改命令方式.txt
odex 做一個跟ig結合的交友網站.txt
```

Human review notes:

- These filenames do not clearly belong to the Taiwan stock analysis phases.
- Do not commit without manual confirmation.

### Needs Human Review

High-priority manual review files:

```text
.agents/skills/maintainable-refactor/SKILL.md
review_src/app.py
review_src/scoring.py
review_src/static/detail.html
review_src/static/index.html
review_src/core/db.py
review_src/adapter/yahoo.py
review_src/core/config.py
review_src/price_volume.py
review_src/repository/
review_src/services/
tests/
```

Reasons:

- `review_src/app.py` is large and has broad production behavior risk.
- `review_src/scoring.py` affects scoring and final analysis behavior.
- `review_src/static/detail.html` and `review_src/static/index.html` affect user-facing UI.
- `review_src/core/db.py` affects DB lifecycle and schema/bootstrap behavior.
- `review_src/adapter/yahoo.py` affects external data source behavior.
- `repository/` and `services/` introduce new architecture surfaces.
- `tests/` may depend on uncommitted implementation files and should be reviewed with their matching code.

## Suggested Commit Groups

Only suggested. No git staging or commit was performed.

1. **docs workflow / review packet**
   - `.agents/skills/maintainable-refactor/SKILL.md`
   - `docs/WORKFLOW.md`
   - `docs/CODEX_REVIEW_PACKET.md`
   - `docs/WORKING_TREE_REVIEW.md`

2. **source alignment tools**
   - `docs/DATA_SOURCE_ADAPTER_AUDIT.md`
   - `docs/DB_REPOSITORY_AUDIT.md`
   - `docs/local_values_2317.md`
   - `scripts/export_local_values_2317.py`

3. **TWSE valuation / repository support**
   - `review_src/adapter/twse_valuation.py`
   - `review_src/repository/twse_valuation_repository.py`
   - `review_src/services/twse_valuation_service.py`
   - related tests

4. **calendar shadow prototype**
   - `review_src/core/outlook_context.py`
   - `review_src/market/calendar_data.py`
   - `review_src/market/calendar_override.py`
   - `review_src/market/calendar_status.py`
   - `review_src/outlook/shadow_builder.py`
   - `docs/calendar_shadow_context_examples.md`
   - `scripts/export_calendar_shadow_context.py`

5. **calendar test matrix**
   - `scripts/test_calendar_shadow_matrix.py`
   - `docs/calendar_shadow_matrix_report.md`

6. **outlook diff and audit tools**
   - `scripts/compare_outlook_vs_shadow.py`
   - `docs/outlook_shadow_diff_report.md`
   - `scripts/audit_next_day_outlook_coverage.py`
   - `docs/next_day_outlook_coverage_audit.md`

7. **missing_data_quality fix**
   - carefully selected hunks from `review_src/app.py`
   - matching `docs/next_day_outlook_coverage_audit.md`
   - matching `docs/CODEX_REVIEW_PACKET.md`

8. **regression audit tools**
   - `scripts/audit_data_quality_gate_regression.py`
   - `docs/data_quality_gate_regression_report.md`

## Do Not Commit Yet

Do not directly commit these without human review:

```text
review_src/app.py
review_src/scoring.py
review_src/static/detail.html
review_src/static/index.html
review_src/core/db.py
review_src/adapter/yahoo.py
.agents/skills/maintainable-refactor/SKILL.md
tests/
review_src/repository/
review_src/services/
修改命令方式.txt
odex 做一個跟ig結合的交友網站.txt
```

Reasons:

- They either affect production behavior, user-facing output, DB/bootstrap behavior, data-source behavior, repository/service architecture, or have unclear provenance.
- `review_src/app.py` should be reviewed by hunk; do not commit all tracked changes as one blob.
- `tests/` should be paired with the implementation files they validate.

## Risk Notes

Highest risk files:

1. `review_src/app.py`
   - Large central file.
   - Mixes API, data readiness, analysis, cache, background concerns.
   - D3B fix exists inside it, but the file also contains other dirty hunks.

2. `review_src/scoring.py`
   - Scoring logic is financially sensitive.
   - Even small changes can alter user-facing conclusions.

3. `review_src/core/db.py`
   - DB connection/bootstrap changes can affect stability and schema assumptions.

4. `review_src/static/detail.html` and `review_src/static/index.html`
   - Directly affect user-facing rendering.
   - Need visual/manual smoke review before commit.

5. `review_src/adapter/yahoo.py`
   - External data source behavior and fallback handling are sensitive.

6. `review_src/repository/` and `review_src/services/`
   - New architecture surface.
   - Should be reviewed as one coherent phase, not mixed with bug fixes.

## Verification Performed

Python compile checks executed:

```text
python -m py_compile review_src/app.py
python -m py_compile review_src/scoring.py
python -m py_compile review_src/core/data_quality.py
python -m py_compile scripts/audit_next_day_outlook_coverage.py
python -m py_compile scripts/compare_outlook_vs_shadow.py
python -m py_compile scripts/test_calendar_shadow_matrix.py
python -m py_compile scripts/audit_data_quality_gate_regression.py
python -m py_compile review_src/core/outlook_context.py
python -m py_compile review_src/market/calendar_status.py
python -m py_compile review_src/market/calendar_data.py
python -m py_compile review_src/market/calendar_override.py
python -m py_compile review_src/outlook/shadow_builder.py
```

Result: PASS.

Smoke test executed with temporary local server on `http://127.0.0.1:8057`:

```text
powershell -ExecutionPolicy Bypass -File docs\smoke_test.ps1
```

Result: PASS.

Checked:

```text
GET /
GET /api/quotes?mode=watchlist
GET /api/quotes?mode=tw50
GET /api/stock/2330/detail
```

## Recommended Next Step

Do not start D3C yet.

Recommended next step:

1. Human reviews this document.
2. Human chooses one commit group at a time.
3. For `review_src/app.py`, review hunks manually and stage only the intended D3B or phase-specific changes.
4. After commit groups are accepted, then decide whether to enter D3C.

