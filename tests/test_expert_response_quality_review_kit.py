from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest


REVIEW_SRC = Path(__file__).resolve().parents[1] / "review_src"
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from evaluation.expert_response_quality_corpus import (  # noqa: E402
    EXPERT_RESPONSE_QUALITY_CORPUS,
    RATING_DIMENSIONS,
)
from evaluation.expert_response_quality_review_kit import (  # noqa: E402
    OUTPUT_SET_VERSION,
    build_review_kit,
    evaluate_review_kit,
    write_review_kit,
)
import freeze_single_track_v3_stage8 as freeze_stage8  # noqa: E402


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _output_set(role: str, prefix: str, digest: str) -> dict:
    captured_at = "2026-09-01T18:00:00+08:00"
    return {
        "output_set_version": OUTPUT_SET_VERSION,
        "role": role,
        "captured_at": captured_at,
        "release_source_digest": digest,
        "collection_method": "captured_fixture_or_execution",
        "source_label": f"{role}-source-v1",
        "records": [
            {
                "case_id": str(case["case_id"]),
                "output": f"{prefix} response for {case['case_id']}",
                "source_reference": f"{role}:{case['case_id']}",
                "captured_at": captured_at,
                "prompt_sha256": _sha256(str(case["prompt"])),
                "output_sha256": _sha256(f"{prefix} response for {case['case_id']}"),
            }
            for case in EXPERT_RESPONSE_QUALITY_CORPUS
        ],
    }


def _completed_ratings(kit: dict) -> dict:
    role_by_case = {
        row["case_id"]: row for row in kit["private_role_key"]["role_key"]
    }
    rows = []
    for case in EXPERT_RESPONSE_QUALITY_CORPUS:
        case_id = str(case["case_id"])
        candidate_label = (
            "answer_a"
            if role_by_case[case_id]["answer_a_role"] == "candidate"
            else "answer_b"
        )
        stable_label = "answer_b" if candidate_label == "answer_a" else "answer_a"
        rows.append(
            {
                "case_id": case_id,
                "rater_id": "independent-reviewer-1",
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
    ratings = copy.deepcopy(kit["ratings_template"])
    ratings["ratings"] = rows
    return ratings


def test_review_kit_separates_role_key_and_binds_human_evidence() -> None:
    stable = _output_set("stable", "baseline", "a" * 64)
    candidate = _output_set("candidate", "proposed", "b" * 64)
    kit = build_review_kit(stable, candidate)

    assert kit["public_review"]["case_count"] == 100
    assert "role_key" not in kit["public_review"]
    assert "stable_output_set" not in kit["public_review"]
    assert kit["private_role_key"]["stable_output_set"]["release_source_digest"] == "a" * 64
    result = evaluate_review_kit(
        kit["public_review"],
        kit["private_role_key"],
        _completed_ratings(kit),
        stable,
        candidate,
    )
    assert result["integrity_errors"] == []
    assert result["status"] == "pass"
    assert result["passed"] is True


def test_review_kit_rejects_tampered_blind_answer() -> None:
    stable = _output_set("stable", "baseline", "a" * 64)
    candidate = _output_set("candidate", "proposed", "b" * 64)
    kit = build_review_kit(stable, candidate)
    public_review = copy.deepcopy(kit["public_review"])
    public_review["public_cases"][0]["answer_a"] += " tampered"

    result = evaluate_review_kit(
        public_review,
        kit["private_role_key"],
        _completed_ratings(kit),
        stable,
        candidate,
    )

    assert result["status"] == "invalid_review_bundle"
    assert result["passed"] is False
    assert "public_manifest_sha256_mismatch" in result["integrity_errors"]


def test_review_kit_requires_all_source_bound_outputs_and_never_overwrites(tmp_path: Path) -> None:
    stable = _output_set("stable", "baseline", "a" * 64)
    candidate = _output_set("candidate", "proposed", "b" * 64)
    stable["records"].pop()
    with pytest.raises(ValueError, match="incomplete"):
        build_review_kit(stable, candidate)

    kit = build_review_kit(
        _output_set("stable", "baseline", "a" * 64),
        candidate,
    )
    paths = write_review_kit(tmp_path / "quality-review", kit)
    assert all(path.is_file() for path in paths.values())
    with pytest.raises(FileExistsError):
        write_review_kit(tmp_path / "quality-review", kit)


def test_stage8_freeze_recomputes_human_quality_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stable = _output_set("stable", "baseline", "a" * 64)
    candidate = _output_set("candidate", "proposed", "b" * 64)
    kit = build_review_kit(stable, candidate)
    ratings = _completed_ratings(kit)
    evaluation = evaluate_review_kit(
        kit["public_review"],
        kit["private_role_key"],
        ratings,
        stable,
        candidate,
    )
    documents = {
        "public_review": kit["public_review"],
        "ratings": ratings,
        "private_role_key": kit["private_role_key"],
        "stable_outputs": stable,
        "candidate_outputs": candidate,
        "evaluation": evaluation,
    }
    for key, relative in freeze_stage8.HUMAN_QUALITY_EVIDENCE_FILES.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(documents[key], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    monkeypatch.setattr(freeze_stage8, "PROJECT_ROOT", tmp_path)

    evidence = freeze_stage8._human_quality_evidence()

    assert evidence["status"] == "pass"
    assert evidence["passed"] is True
    assert evidence["evaluation_matches_recomputation"] is True

    evaluation_path = tmp_path / freeze_stage8.HUMAN_QUALITY_EVIDENCE_FILES["evaluation"]
    stored = json.loads(evaluation_path.read_text(encoding="utf-8"))
    stored["passed"] = False
    evaluation_path.write_text(json.dumps(stored), encoding="utf-8")
    rejected = freeze_stage8._human_quality_evidence()
    assert rejected["passed"] is False
    assert "stored_quality_evaluation_does_not_match_recomputation" in rejected["reason_codes"]
