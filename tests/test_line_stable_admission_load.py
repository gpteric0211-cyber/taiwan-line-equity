from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import run_line_stable_admission_load as stable_load  # noqa: E402


def _attempt(path: str, elapsed_ms: int) -> dict:
    return {
        "http_status": 200,
        "http_round_trip_ms": elapsed_ms + 5,
        "body": {
            "answer_path": path,
            "end_to_end_ms": elapsed_ms,
            "completed_within_internal_reply_budget": True,
            "reply_has_required_disclaimer": True,
        },
    }


def test_stable_load_summary_distinguishes_model_and_predicted_early_fallback() -> None:
    summary = stable_load._summary(
        [
            _attempt("model", 8000),
            _attempt(
                "admission_fallback:predicted_deadline_admission_rejected",
                900,
            ),
        ]
    )

    assert summary["model_answer_count"] == 1
    assert summary["predicted_deadline_early_fallback_count"] == 1
    assert summary["end_to_end_p95_ms"] == 8000
    assert summary["all_within_internal_reply_budget"] is True
    assert summary["all_have_required_disclaimer"] is True


def test_stable_load_summary_fails_budget_gate_when_any_reply_exceeds_it() -> None:
    attempt = _attempt("model", 46_000)
    attempt["body"]["completed_within_internal_reply_budget"] = False

    summary = stable_load._summary([attempt])

    assert summary["all_within_internal_reply_budget"] is False
