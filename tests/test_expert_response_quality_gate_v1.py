from __future__ import annotations

import sys
from pathlib import Path


REVIEW_SRC = Path(__file__).resolve().parents[1] / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from evaluation.expert_response_quality_corpus import (  # noqa: E402
    EXPERT_RESPONSE_QUALITY_CORPUS,
    RATING_DIMENSIONS,
)
from evaluation.expert_response_quality_gate_v1 import (  # noqa: E402
    build_blinded_quality_manifest,
    evaluate_expert_response_quality,
)


def _outputs(prefix: str) -> dict[str, str]:
    return {
        str(case["case_id"]): f"{prefix}-{case['case_id']}"
        for case in EXPERT_RESPONSE_QUALITY_CORPUS
    }


def _passing_ratings(role_key: list[dict]) -> list[dict]:
    rows = []
    for assignment in role_key:
        candidate_label = (
            "answer_a"
            if assignment["answer_a_role"] == "candidate"
            else "answer_b"
        )
        stable_label = "answer_b" if candidate_label == "answer_a" else "answer_a"
        rows.append(
            {
                "case_id": assignment["case_id"],
                "rater_id": "human-reviewer-1",
                "rating_source": "independent_human",
                "synthetic": False,
                "human_attestation": {
                    "human_reviewer": True,
                    "independent_of_generation": True,
                    "role_key_not_accessed": True,
                },
                "preferred_answer": candidate_label,
                "scores": {
                    candidate_label: {dimension: 5 for dimension in RATING_DIMENSIONS},
                    stable_label: {dimension: 4 for dimension in RATING_DIMENSIONS},
                },
                "labels": {
                    candidate_label: {
                        "critical_factual_or_entity_error": False,
                        "unique_alias_unnecessary_confirmation": False,
                        "ambiguity_misresolution": False,
                        "policy_violation": False,
                    },
                    stable_label: {
                        "critical_factual_or_entity_error": False,
                        "unique_alias_unnecessary_confirmation": False,
                        "ambiguity_misresolution": False,
                        "policy_violation": False,
                    },
                },
            }
        )
    return rows


def test_quality_gate_fails_closed_without_outputs_or_human_ratings() -> None:
    result = evaluate_expert_response_quality([], [])

    assert result["status"] == "insufficient_human_evidence"
    assert result["passed"] is False
    assert result["missing_output_case_count"] == 100
    assert result["missing_human_rating_case_count"] == 100


def test_blind_manifest_is_complete_deterministic_and_role_separated() -> None:
    first = build_blinded_quality_manifest(_outputs("stable"), _outputs("candidate"))
    second = build_blinded_quality_manifest(_outputs("stable"), _outputs("candidate"))

    assert first == second
    assert first["complete"] is True
    assert len(first["public_cases"]) == 100
    assert len(first["role_key"]) == 100
    assert all("role" not in row for row in first["public_cases"])
    assert all("answer_a" not in row["stable_output_sha256"] for row in first["role_key"])
    assert len(first["public_manifest_sha256"]) == 64


def test_blind_manifest_rejects_blank_and_unexpected_outputs() -> None:
    stable = _outputs("stable")
    candidate = _outputs("candidate")
    first_case = str(EXPERT_RESPONSE_QUALITY_CORPUS[0]["case_id"])
    stable[first_case] = " "
    candidate["unexpected-case"] = "not part of the frozen corpus"

    manifest = build_blinded_quality_manifest(stable, candidate)

    assert manifest["complete"] is False
    assert manifest["invalid_output_case_ids"] == [first_case]
    assert manifest["unexpected_candidate_case_ids"] == ["unexpected-case"]


def test_quality_gate_accepts_complete_human_fixture_and_rejects_critical_error() -> None:
    manifest = build_blinded_quality_manifest(_outputs("stable"), _outputs("candidate"))
    ratings = _passing_ratings(manifest["role_key"])

    passed = evaluate_expert_response_quality(manifest["role_key"], ratings)
    assert passed["status"] == "pass"
    assert passed["overall_candidate_preference"] == 1.0
    assert passed["passed"] is True

    candidate_label = (
        "answer_a"
        if manifest["role_key"][0]["answer_a_role"] == "candidate"
        else "answer_b"
    )
    ratings[0]["labels"][candidate_label]["critical_factual_or_entity_error"] = True
    rejected = evaluate_expert_response_quality(manifest["role_key"], ratings)
    assert rejected["status"] == "reject_quality_regression"
    assert rejected["critical_factual_or_entity_errors"] == 1
    assert rejected["passed"] is False


def test_quality_gate_requires_explicit_independent_human_attestation() -> None:
    manifest = build_blinded_quality_manifest(_outputs("stable"), _outputs("candidate"))
    ratings = _passing_ratings(manifest["role_key"])
    ratings[0]["human_attestation"]["role_key_not_accessed"] = False

    rejected = evaluate_expert_response_quality(manifest["role_key"], ratings)

    assert rejected["status"] == "insufficient_human_evidence"
    assert rejected["passed"] is False
    assert "independent_human_attestation_missing" in rejected["reason_codes"]
