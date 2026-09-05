# ExpertResponseQualityCorpusV1

`review_src/evaluation/expert_response_quality_corpus.py` freezes 100 blinded prompts:
30 focused, 30 comprehensive, 20 multi-turn/entity/context, and 20 event/image/missing/conflict
cases. Stable and candidate outputs must be randomized as `answer_a`/`answer_b`; the corpus does
not contain an expected winner and Qwen may not be the sole evaluator.

Human raters score factual grounding, context continuity, entity correctness, specificity,
conditional usefulness, naturalness, non-template quality, and uncertainty handling on 1–5.
Promotion requires zero critical factual/entity errors, at least 70% overall candidate
preference, at least 60% preference in every major scenario, median at least 4/5 for every core
dimension, zero unnecessary confirmations for unique aliases, zero ambiguity misresolution, and
no increase in policy violations. These results are intentionally unfilled until Stage 7 produces
actual stable/candidate outputs and independent human ratings; the corpus itself is not evidence
that the candidate passed.

## Integrity-bound review workflow

`scripts/run_expert_response_quality_review.py` keeps the reviewer document and private role key
in separate directories, binds every prompt/output and every document by SHA-256, refuses missing,
blank, duplicate, unexpected, or overwritten evidence, and re-evaluates the stored result during
the Stage 8 freeze. It does not generate ratings.

Each stable/candidate input is an `ExpertResponseQualityOutputSetV1` JSON object with:

- `role`: exactly `stable` or `candidate`;
- timezone-aware `captured_at`, a 64-character `release_source_digest`, `collection_method`, and
  `source_label`;
- exactly 100 `records`, each containing `case_id`, the actual non-empty `output`, a traceable
  `source_reference`, timezone-aware `captured_at`, and exact `prompt_sha256`/`output_sha256`.

Build a new, non-overwriting kit with relative paths:

```powershell
python scripts/run_expert_response_quality_review.py build `
  --stable-outputs logs/line_model_shadow/single_track_v3_stage7/stable_outputs.collected.json `
  --candidate-outputs logs/line_model_shadow/single_track_v3_stage7/candidate_outputs.collected.json `
  --output-directory logs/line_model_shadow/single_track_v3_stage7/expert_response_quality_v1
```

Only `reviewer/blind_review.json` and a copy renamed from
`reviewer/ratings.template.json` to `reviewer/ratings.completed.json` go to the human reviewer.
The coordinator must withhold `coordinator_private/`. Every completed row must contain exactly one
rating, a non-empty `rater_id`, `rating_source=independent_human`, `synthetic=false`, and affirmative
attestations that the rater is human, independent of generation, and did not access the role key.
These attestations are required provenance claims, not automatic proof of reviewer identity.

After the reviewer returns the completed file, evaluate it without overwriting prior evidence:

```powershell
python scripts/run_expert_response_quality_review.py evaluate `
  --public-review logs/line_model_shadow/single_track_v3_stage7/expert_response_quality_v1/reviewer/blind_review.json `
  --private-role-key logs/line_model_shadow/single_track_v3_stage7/expert_response_quality_v1/coordinator_private/role_key.json `
  --ratings logs/line_model_shadow/single_track_v3_stage7/expert_response_quality_v1/reviewer/ratings.completed.json `
  --stable-outputs logs/line_model_shadow/single_track_v3_stage7/expert_response_quality_v1/coordinator_private/stable_outputs.json `
  --candidate-outputs logs/line_model_shadow/single_track_v3_stage7/expert_response_quality_v1/coordinator_private/candidate_outputs.json `
  --output logs/line_model_shadow/single_track_v3_stage7/expert_response_quality_v1/evaluation.json
```

Until those six source/review/evaluation files exist and Stage 8 independently recomputes the same
passing result, the release status remains `insufficient_human_evidence`.
