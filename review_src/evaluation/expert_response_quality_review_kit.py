from __future__ import annotations

"""Integrity-bound artifacts for independent ExpertResponseQualityCorpus review."""

import hashlib
import json
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from evaluation.expert_response_quality_corpus import EXPERT_RESPONSE_QUALITY_CORPUS
from evaluation.expert_response_quality_gate_v1 import (
    QUALITY_GATE_SPEC_SHA256,
    build_blinded_quality_manifest,
    evaluate_expert_response_quality,
)


OUTPUT_SET_VERSION = "ExpertResponseQualityOutputSetV1"
REVIEW_KIT_VERSION = "ExpertResponseQualityReviewKitV1"
PRIVATE_KEY_VERSION = "ExpertResponseQualityPrivateRoleKeyV1"
RATINGS_VERSION = "ExpertResponseQualityHumanRatingsV1"
EVALUATION_VERSION = "ExpertResponseQualityEvaluationEvidenceV1"
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def document_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _require_timestamp(value: Any, field: str) -> str:
    timestamp = str(value or "").strip()
    if not timestamp:
        raise ValueError(f"{field} is required")
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone offset")
    return timestamp


def validate_output_set(
    document: Mapping[str, Any],
    *,
    expected_role: str,
) -> dict[str, Any]:
    """Validate a complete, source-bound stable or candidate output set."""

    if document.get("output_set_version") != OUTPUT_SET_VERSION:
        raise ValueError(f"output_set_version must be {OUTPUT_SET_VERSION}")
    if document.get("role") != expected_role:
        raise ValueError(f"output set role must be {expected_role}")
    captured_at = _require_timestamp(document.get("captured_at"), "captured_at")
    release_source_digest = str(document.get("release_source_digest") or "").strip()
    if not _SHA256_PATTERN.fullmatch(release_source_digest):
        raise ValueError("release_source_digest must be a lowercase SHA-256 digest")
    collection_method = str(document.get("collection_method") or "").strip()
    source_label = str(document.get("source_label") or "").strip()
    if not collection_method:
        raise ValueError("collection_method is required")
    if not source_label:
        raise ValueError("source_label is required")

    records = document.get("records")
    if not isinstance(records, list):
        raise ValueError("records must be a list")
    expected = {
        str(case["case_id"]): dict(case) for case in EXPERT_RESPONSE_QUALITY_CORPUS
    }
    normalized_records: list[dict[str, str]] = []
    outputs: dict[str, str] = {}
    for index, raw_record in enumerate(records):
        if not isinstance(raw_record, Mapping):
            raise ValueError(f"records[{index}] must be an object")
        case_id = str(raw_record.get("case_id") or "").strip()
        if case_id not in expected:
            raise ValueError(f"records[{index}] has an unexpected case_id")
        if case_id in outputs:
            raise ValueError(f"duplicate output record for {case_id}")
        output = raw_record.get("output")
        if not isinstance(output, str) or not output.strip():
            raise ValueError(f"output for {case_id} must be a non-empty string")
        source_reference = str(raw_record.get("source_reference") or "").strip()
        if not source_reference:
            raise ValueError(f"source_reference for {case_id} is required")
        record_captured_at = _require_timestamp(
            raw_record.get("captured_at"), f"records[{index}].captured_at"
        )
        prompt_sha256 = str(raw_record.get("prompt_sha256") or "").strip()
        expected_prompt_sha256 = _text_sha256(str(expected[case_id]["prompt"]))
        if prompt_sha256 != expected_prompt_sha256:
            raise ValueError(f"prompt_sha256 mismatch for {case_id}")
        output_sha256 = str(raw_record.get("output_sha256") or "").strip()
        expected_output_sha256 = _text_sha256(output)
        if output_sha256 != expected_output_sha256:
            raise ValueError(f"output_sha256 mismatch for {case_id}")
        outputs[case_id] = output
        normalized_records.append(
            {
                "case_id": case_id,
                "output": output,
                "source_reference": source_reference,
                "captured_at": record_captured_at,
                "prompt_sha256": prompt_sha256,
                "output_sha256": output_sha256,
            }
        )

    missing = sorted(set(expected).difference(outputs))
    if missing:
        raise ValueError(f"output set is incomplete: {len(missing)} cases missing")
    if len(normalized_records) != len(expected):
        raise ValueError("output set must contain exactly one record per corpus case")
    normalized_records.sort(key=lambda item: item["case_id"])
    return {
        "output_set_version": OUTPUT_SET_VERSION,
        "role": expected_role,
        "captured_at": captured_at,
        "release_source_digest": release_source_digest,
        "collection_method": collection_method,
        "source_label": source_label,
        "records": normalized_records,
        "outputs": outputs,
        "document_sha256": document_sha256(document),
    }


def build_review_kit(
    stable_document: Mapping[str, Any],
    candidate_document: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    stable = validate_output_set(stable_document, expected_role="stable")
    candidate = validate_output_set(candidate_document, expected_role="candidate")
    blinded = build_blinded_quality_manifest(stable["outputs"], candidate["outputs"])
    if not blinded["complete"]:
        raise ValueError("stable and candidate outputs did not produce a complete blind manifest")

    public_manifest = {
        "manifest_version": blinded["manifest_version"],
        "gate_spec_sha256": blinded["gate_spec_sha256"],
        "public_cases": blinded["public_cases"],
    }
    public_manifest_sha256 = document_sha256(public_manifest)
    if public_manifest_sha256 != blinded["public_manifest_sha256"]:
        raise RuntimeError("public blind manifest digest is internally inconsistent")

    public_review = {
        "review_kit_version": REVIEW_KIT_VERSION,
        "gate_spec_sha256": QUALITY_GATE_SPEC_SHA256,
        "public_manifest_sha256": public_manifest_sha256,
        "case_count": len(blinded["public_cases"]),
        "rating_instructions": {
            "blind_labels": ["answer_a", "answer_b", "tie"],
            "score_range": [1, 5],
            "rating_source": "independent_human",
            "role_key_must_remain_hidden": True,
        },
        **public_manifest,
    }
    private_role_key = {
        "role_key_version": PRIVATE_KEY_VERSION,
        "gate_spec_sha256": QUALITY_GATE_SPEC_SHA256,
        "public_manifest_sha256": public_manifest_sha256,
        "stable_output_set": {
            "document_sha256": stable["document_sha256"],
            "captured_at": stable["captured_at"],
            "release_source_digest": stable["release_source_digest"],
            "collection_method": stable["collection_method"],
            "source_label": stable["source_label"],
        },
        "candidate_output_set": {
            "document_sha256": candidate["document_sha256"],
            "captured_at": candidate["captured_at"],
            "release_source_digest": candidate["release_source_digest"],
            "collection_method": candidate["collection_method"],
            "source_label": candidate["source_label"],
        },
        "role_key": blinded["role_key"],
    }
    ratings_template = {
        "ratings_version": RATINGS_VERSION,
        "gate_spec_sha256": QUALITY_GATE_SPEC_SHA256,
        "public_manifest_sha256": public_manifest_sha256,
        "ratings": [
            {
                "case_id": str(case["case_id"]),
                "rater_id": None,
                "rating_source": None,
                "synthetic": None,
                "human_attestation": {
                    "human_reviewer": None,
                    "independent_of_generation": None,
                    "role_key_not_accessed": None,
                },
                "preferred_answer": None,
                "scores": {
                    "answer_a": {dimension: None for dimension in case["rating_dimensions"]},
                    "answer_b": {dimension: None for dimension in case["rating_dimensions"]},
                },
                "labels": {
                    "answer_a": {
                        "critical_factual_or_entity_error": None,
                        "unique_alias_unnecessary_confirmation": None,
                        "ambiguity_misresolution": None,
                        "policy_violation": None,
                    },
                    "answer_b": {
                        "critical_factual_or_entity_error": None,
                        "unique_alias_unnecessary_confirmation": None,
                        "ambiguity_misresolution": None,
                        "policy_violation": None,
                    },
                },
            }
            for case in EXPERT_RESPONSE_QUALITY_CORPUS
        ],
    }
    return {
        "public_review": public_review,
        "private_role_key": private_role_key,
        "ratings_template": ratings_template,
        "stable_output_set": dict(stable_document),
        "candidate_output_set": dict(candidate_document),
    }


def evaluate_review_kit(
    public_review: Mapping[str, Any],
    private_role_key: Mapping[str, Any],
    ratings_document: Mapping[str, Any],
    stable_document: Mapping[str, Any],
    candidate_document: Mapping[str, Any],
) -> dict[str, Any]:
    integrity_errors: list[str] = []
    try:
        stable = validate_output_set(stable_document, expected_role="stable")
        candidate = validate_output_set(candidate_document, expected_role="candidate")
    except ValueError:
        stable = None
        candidate = None
        integrity_errors.append("source_output_set_invalid")
    public_manifest = {
        "manifest_version": public_review.get("manifest_version"),
        "gate_spec_sha256": public_review.get("gate_spec_sha256"),
        "public_cases": public_review.get("public_cases"),
    }
    public_manifest_sha256 = document_sha256(public_manifest)
    expected_digest = str(public_review.get("public_manifest_sha256") or "")
    if public_review.get("review_kit_version") != REVIEW_KIT_VERSION:
        integrity_errors.append("review_kit_version_mismatch")
    if expected_digest != public_manifest_sha256:
        integrity_errors.append("public_manifest_sha256_mismatch")
    if private_role_key.get("role_key_version") != PRIVATE_KEY_VERSION:
        integrity_errors.append("private_role_key_version_mismatch")
    if private_role_key.get("public_manifest_sha256") != public_manifest_sha256:
        integrity_errors.append("private_role_key_binding_mismatch")
    if ratings_document.get("ratings_version") != RATINGS_VERSION:
        integrity_errors.append("ratings_version_mismatch")
    if ratings_document.get("public_manifest_sha256") != public_manifest_sha256:
        integrity_errors.append("ratings_manifest_binding_mismatch")
    if any(
        document.get("gate_spec_sha256") != QUALITY_GATE_SPEC_SHA256
        for document in (public_review, private_role_key, ratings_document)
    ):
        integrity_errors.append("quality_gate_spec_binding_mismatch")
    if stable is not None and candidate is not None:
        stable_binding = private_role_key.get("stable_output_set")
        candidate_binding = private_role_key.get("candidate_output_set")
        if not isinstance(stable_binding, Mapping) or stable_binding.get(
            "document_sha256"
        ) != stable["document_sha256"]:
            integrity_errors.append("stable_output_set_binding_mismatch")
        if not isinstance(candidate_binding, Mapping) or candidate_binding.get(
            "document_sha256"
        ) != candidate["document_sha256"]:
            integrity_errors.append("candidate_output_set_binding_mismatch")
        expected_kit = build_review_kit(stable_document, candidate_document)
        if dict(public_review) != expected_kit["public_review"]:
            integrity_errors.append("public_review_does_not_match_source_outputs")
        if dict(private_role_key) != expected_kit["private_role_key"]:
            integrity_errors.append("private_role_key_does_not_match_source_outputs")

    public_cases = public_review.get("public_cases")
    role_key_rows = private_role_key.get("role_key")
    if not isinstance(public_cases, list) or not isinstance(role_key_rows, list):
        integrity_errors.append("public_cases_or_role_key_invalid")
    else:
        public_by_case = {
            str(row.get("case_id") or ""): row
            for row in public_cases
            if isinstance(row, Mapping)
        }
        role_by_case = {
            str(row.get("case_id") or ""): row
            for row in role_key_rows
            if isinstance(row, Mapping)
        }
        expected_ids = {str(case["case_id"]) for case in EXPERT_RESPONSE_QUALITY_CORPUS}
        if set(public_by_case) != expected_ids or set(role_by_case) != expected_ids:
            integrity_errors.append("review_case_set_incomplete")
        for case_id in sorted(expected_ids.intersection(public_by_case, role_by_case)):
            public_case = public_by_case[case_id]
            key = role_by_case[case_id]
            if _text_sha256(str(public_case.get("answer_a") or "")) != key.get(
                "answer_a_output_sha256"
            ) or _text_sha256(str(public_case.get("answer_b") or "")) != key.get(
                "answer_b_output_sha256"
            ):
                integrity_errors.append("blinded_answer_hash_mismatch")
                break

    ratings = ratings_document.get("ratings")
    if not isinstance(ratings, list):
        integrity_errors.append("ratings_rows_invalid")
        ratings = []
    if integrity_errors:
        return {
            "evaluation_version": EVALUATION_VERSION,
            "status": "invalid_review_bundle",
            "passed": False,
            "public_manifest_sha256": public_manifest_sha256,
            "integrity_errors": list(dict.fromkeys(integrity_errors)),
        }

    gate_result = evaluate_expert_response_quality(role_key_rows, ratings)
    return {
        "evaluation_version": EVALUATION_VERSION,
        "public_manifest_sha256": public_manifest_sha256,
        "public_review_document_sha256": document_sha256(public_review),
        "private_role_key_document_sha256": document_sha256(private_role_key),
        "ratings_document_sha256": document_sha256(ratings_document),
        "integrity_errors": [],
        **gate_result,
    }


def _write_json_exclusive(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_review_kit(output_directory: Path, kit: Mapping[str, Mapping[str, Any]]) -> dict[str, Path]:
    """Write reviewer and coordinator artifacts without overwriting existing evidence."""

    output_directory = output_directory.resolve()
    paths = {
        "public_review": output_directory / "reviewer" / "blind_review.json",
        "ratings_template": output_directory / "reviewer" / "ratings.template.json",
        "private_role_key": output_directory / "coordinator_private" / "role_key.json",
        "stable_output_set": output_directory / "coordinator_private" / "stable_outputs.json",
        "candidate_output_set": output_directory / "coordinator_private" / "candidate_outputs.json",
    }
    if any(path.exists() for path in paths.values()):
        raise FileExistsError("review kit output already exists; choose a new output directory")
    created: list[Path] = []
    try:
        for key, path in paths.items():
            _write_json_exclusive(path, kit[key])
            created.append(path)
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
        raise
    return paths


def load_json_document(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON document must be an object: {path}")
    return value
