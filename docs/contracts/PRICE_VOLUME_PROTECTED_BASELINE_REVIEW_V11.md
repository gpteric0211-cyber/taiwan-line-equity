# Price-volume protected baseline review — V11

Captured at: `2026-09-03T09:18:12+08:00`  
Review type: read-only mismatch characterization  
Disposition: `reviewed_not_rebaselined`  
Release effect: none

## Scope and hashes

- Protected manifest: `docs/LINE_MODEL_SPEC_V2_BASELINE.json`
- Changed protected file: `review_src/services/price_volume_service.py`
- Approved expected SHA-256: `ab1e9990efa081e25c3f30407f7babd15f058a9846b6c1b8187ccdfb3148ebed`
- Current actual SHA-256: `c51a03e8c29ef79808f1f56ab96342fedd6f7f00bf00cd5d4af4c4128995789c`
- Diff against current HEAD: 1,266 additions, 115 deletions
- Protected set result: 10/11

The working tree was already dirty and the changed file is user-owned existing work. This review neither restores nor rewrites it.

## Verified behavior represented by the current file

The current implementation adds or tightens the following behavior:

- Resolves the published full-market analysis date instead of silently selecting an arbitrary latest row.
- Requires explicit source dates and same-day official TWSE/TPEx EOD reconciliation before promotion.
- Separates captured/scoped price-volume rows from analysis-eligible rows.
- Excludes OHLCV-reconstructed profiles from the true price-volume score path.
- Requires a single high-quality Fugle source, `status=ok`, a matching source hash and close, and coverage of at least `ceil(required_days * 0.8)` before exposing a score.
- Makes `latest_true_price_volume_distribution` and `latest_price_volume_summary` read-only through `PRAGMA query_only=ON`; GET-facing summary reads persisted outcomes rather than calculating or writing them.
- Returns explicit unavailable, stale, source-mismatch, scope-excluded and accumulating states instead of filling decision values.

These controls are directionally consistent with the repository GET read-only rule, centralized data-quality rules and the approved V11 price-volume quality gate. This is a compatibility observation, not approval of every changed line or financial formula.

## Evidence executed

- `python -m py_compile review_src/services/price_volume_service.py`: PASS.
- Targeted price-volume/read-only/display/canonical surface suite: 80 passed in 2.55s.
- The broader Stage 0 evidence before this review remains 1,735 passed in 34.41s.

The targeted suite covers read-only SQL, published-date selection, stale and mixed-source rejection, reconstructed-source rejection, the 80% coverage boundary, `status=ok`, capture lifecycle, official-volume reconciliation, scoped display isolation and canonical surface guards.

## Evidence gap and decision

`docs/LINE_MODEL_SPEC_V2_BASELINE.json` binds three LINE gateway behavioral fixtures, but none is a golden representative output for this 1,381-line price-volume diff. The current evidence therefore shows internal consistency and fail-closed behavior, but it is insufficient to declare the entire protected behavior an accepted new Stable comparator.

Decision:

- Do not restore the user-owned worktree change.
- Do not change the protected hash or claim 11/11.
- Carry `protected_hash_mismatch` as a blocker for Phase A and the Stage 5 code-freeze.
- Before rebaseline, freeze representative Web and LINE outputs for validated, scoped, stale, mixed-source, sub-80%-coverage and unavailable cases; then require an explicit protected-baseline approval tied to those artifacts.

This decision does not block additive Stage 1 schema characterization or migrations that do not depend on treating this file as the Stable comparator. It does block release evidence, candidate replacement and later code-freeze claims until resolved.
