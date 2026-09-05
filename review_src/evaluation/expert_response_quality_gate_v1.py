from __future__ import annotations

"""Fail-closed human quality gate for ExpertResponseQualityCorpusV1."""

import hashlib
import json
import statistics
from collections import Counter, defaultdict
from typing import Any, Iterable, Mapping

from evaluation.expert_response_quality_corpus import (
    EXPERT_RESPONSE_QUALITY_CORPUS,
    RATING_DIMENSIONS,
)


QUALITY_GATE_SPEC = {
    "version": "ExpertResponseQualityGateV1",
    "corpus_size": 100,
    "critical_factual_or_entity_errors_max": 0,
    "overall_candidate_preference_min": 0.70,
    "major_scenario_candidate_preference_min": 0.60,
    "candidate_dimension_median_min": 4.0,
    "unique_alias_unnecessary_confirmation_max": 0,
    "ambiguity_misresolution_max": 0,
    "candidate_policy_violation_increase_max": 0,
    "rating_source": "independent_human",
    "ratings_per_case": 1,
    "human_attestation_required": True,
    "ties_count_in_denominator": True,
}
QUALITY_GATE_SPEC_SHA256 = hashlib.sha256(
    json.dumps(QUALITY_GATE_SPEC, sort_keys=True, separators=(",", ":")).encode("utf-8")
).hexdigest()


def _sha256(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _json_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def build_blinded_quality_manifest(
    stable_outputs: Mapping[str, str],
    candidate_outputs: Mapping[str, str],
) -> dict[str, Any]:
    """Randomize roles deterministically while keeping the answer text out of the key."""

    expected_case_ids = {str(case["case_id"]) for case in EXPERT_RESPONSE_QUALITY_CORPUS}
    stable_case_ids = {str(case_id) for case_id in stable_outputs}
    candidate_case_ids = {str(case_id) for case_id in candidate_outputs}
    missing_stable = sorted(expected_case_ids.difference(stable_case_ids))
    missing_candidate = sorted(expected_case_ids.difference(candidate_case_ids))
    unexpected_stable = sorted(stable_case_ids.difference(expected_case_ids))
    unexpected_candidate = sorted(candidate_case_ids.difference(expected_case_ids))
    invalid_output_case_ids: list[str] = []
    public_cases: list[dict[str, Any]] = []
    role_key: list[dict[str, str]] = []
    for case in EXPERT_RESPONSE_QUALITY_CORPUS:
        case_id = str(case["case_id"])
        if case_id not in stable_outputs or case_id not in candidate_outputs:
            continue
        stable_value = stable_outputs[case_id]
        candidate_value = candidate_outputs[case_id]
        if (
            not isinstance(stable_value, str)
            or not stable_value.strip()
            or not isinstance(candidate_value, str)
            or not candidate_value.strip()
        ):
            invalid_output_case_ids.append(case_id)
            continue
        stable = stable_value
        candidate = candidate_value
        candidate_is_a = int(_sha256(f"{QUALITY_GATE_SPEC_SHA256}|{case_id}")[-1], 16) % 2 == 0
        answers = (
            {"answer_a": candidate, "answer_b": stable}
            if candidate_is_a
            else {"answer_a": stable, "answer_b": candidate}
        )
        public_cases.append(
            {
                "case_id": case_id,
                "category": case["category"],
                "scenario": case["scenario"],
                "prompt": case["prompt"],
                **answers,
            }
        )
        role_key.append(
            {
                "case_id": case_id,
                "answer_a_role": "candidate" if candidate_is_a else "stable",
                "answer_b_role": "stable" if candidate_is_a else "candidate",
                "stable_output_sha256": _sha256(stable),
                "candidate_output_sha256": _sha256(candidate),
                "answer_a_output_sha256": _sha256(answers["answer_a"]),
                "answer_b_output_sha256": _sha256(answers["answer_b"]),
            }
        )
    public_document = {
        "manifest_version": "ExpertResponseQualityBlindManifestV1",
        "gate_spec_sha256": QUALITY_GATE_SPEC_SHA256,
        "public_cases": public_cases,
    }
    complete = not any(
        (
            missing_stable,
            missing_candidate,
            unexpected_stable,
            unexpected_candidate,
            invalid_output_case_ids,
        )
    ) and len(public_cases) == len(EXPERT_RESPONSE_QUALITY_CORPUS)
    return {
        **public_document,
        "public_manifest_sha256": _json_sha256(public_document),
        "role_key": role_key,
        "complete": complete,
        "missing_case_count": len(EXPERT_RESPONSE_QUALITY_CORPUS) - len(public_cases),
        "missing_stable_case_ids": missing_stable,
        "missing_candidate_case_ids": missing_candidate,
        "unexpected_stable_case_ids": unexpected_stable,
        "unexpected_candidate_case_ids": unexpected_candidate,
        "invalid_output_case_ids": sorted(invalid_output_case_ids),
    }


def evaluate_expert_response_quality(
    role_key: Iterable[Mapping[str, Any]],
    ratings: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    expected = {str(case["case_id"]): dict(case) for case in EXPERT_RESPONSE_QUALITY_CORPUS}
    assignment_rows = [dict(item) for item in role_key]
    assignment_case_ids = [str(item.get("case_id") or "") for item in assignment_rows]
    assignments = {
        str(item.get("case_id") or ""): dict(item)
        for item in assignment_rows
        if str(item.get("case_id") or "")
    }
    rating_rows = [dict(item) for item in ratings]
    reasons: list[str] = []
    if set(assignments) != set(expected) or len(assignment_case_ids) != len(set(assignment_case_ids)):
        reasons.append("stable_candidate_outputs_incomplete")
    if any(str(item.get("rating_source") or "") != "independent_human" for item in rating_rows):
        reasons.append("non_human_or_unverified_rating_source")
    if any(item.get("synthetic") is not False for item in rating_rows):
        reasons.append("synthetic_rating_forbidden")
    if any(not str(item.get("rater_id") or "").strip() for item in rating_rows):
        reasons.append("human_rater_identity_missing")
    if any(
        not isinstance(item.get("human_attestation"), Mapping)
        or item["human_attestation"].get("human_reviewer") is not True
        or item["human_attestation"].get("independent_of_generation") is not True
        or item["human_attestation"].get("role_key_not_accessed") is not True
        for item in rating_rows
    ):
        reasons.append("independent_human_attestation_missing")

    ratings_by_case: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rating_rows:
        case_id = str(row.get("case_id") or "")
        if case_id in expected:
            ratings_by_case[case_id].append(row)
    missing_ratings = sorted(case_id for case_id in expected if not ratings_by_case.get(case_id))
    if missing_ratings:
        reasons.append("independent_human_ratings_incomplete")
    rating_case_counts = Counter(str(row.get("case_id") or "") for row in rating_rows)
    if any(rating_case_counts.get(case_id, 0) != 1 for case_id in expected):
        reasons.append("exactly_one_human_rating_per_case_required")
    if set(rating_case_counts).difference(expected):
        reasons.append("unexpected_human_rating_case")

    if reasons:
        return {
            "gate_version": QUALITY_GATE_SPEC["version"],
            "gate_spec_sha256": QUALITY_GATE_SPEC_SHA256,
            "status": "insufficient_human_evidence",
            "passed": False,
            "case_count": len(expected),
            "paired_output_count": len(set(assignments).intersection(expected)),
            "human_rated_case_count": len(expected) - len(missing_ratings),
            "human_rating_count": len(rating_rows),
            "missing_output_case_count": len(set(expected).difference(assignments)),
            "missing_human_rating_case_count": len(missing_ratings),
            "reason_codes": list(dict.fromkeys(reasons)),
        }

    candidate_preferences = 0
    preference_total = 0
    category_candidate = defaultdict(int)
    category_total = defaultdict(int)
    candidate_scores: dict[str, list[float]] = defaultdict(list)
    critical_errors = 0
    unique_alias_confirmations = 0
    ambiguity_misresolutions = 0
    candidate_policy_violations = 0
    stable_policy_violations = 0
    invalid_rating = False
    for case_id, rows in ratings_by_case.items():
        assignment = assignments[case_id]
        category = str(expected[case_id]["category"])
        candidate_label = (
            "answer_a"
            if assignment.get("answer_a_role") == "candidate"
            else "answer_b"
        )
        stable_label = "answer_b" if candidate_label == "answer_a" else "answer_a"
        for row in rows:
            preferred = str(row.get("preferred_answer") or "tie")
            if preferred not in {"answer_a", "answer_b", "tie"}:
                invalid_rating = True
                continue
            preference_total += 1
            category_total[category] += 1
            if preferred == candidate_label:
                candidate_preferences += 1
                category_candidate[category] += 1
            scores = row.get("scores") if isinstance(row.get("scores"), Mapping) else {}
            labels = row.get("labels") if isinstance(row.get("labels"), Mapping) else {}
            answer_scores: dict[str, Mapping[str, Any]] = {}
            answer_labels: dict[str, Mapping[str, Any]] = {}
            for answer_label in ("answer_a", "answer_b"):
                if not isinstance(scores.get(answer_label), Mapping) or not isinstance(
                    labels.get(answer_label), Mapping
                ):
                    invalid_rating = True
                    answer_scores[answer_label] = {}
                    answer_labels[answer_label] = {}
                    continue
                answer_scores[answer_label] = scores[answer_label]
                answer_labels[answer_label] = labels[answer_label]
                for dimension in RATING_DIMENSIONS:
                    value = scores[answer_label].get(dimension)
                    if (
                        isinstance(value, bool)
                        or not isinstance(value, (int, float))
                        or not 1 <= value <= 5
                    ):
                        invalid_rating = True
                for label_name in (
                    "critical_factual_or_entity_error",
                    "unique_alias_unnecessary_confirmation",
                    "ambiguity_misresolution",
                    "policy_violation",
                ):
                    if not isinstance(labels[answer_label].get(label_name), bool):
                        invalid_rating = True
            candidate_dimension_scores = answer_scores.get(candidate_label, {})
            for dimension in RATING_DIMENSIONS:
                value = candidate_dimension_scores.get(dimension)
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    continue
                candidate_scores[dimension].append(float(value))
            candidate_labels = answer_labels.get(candidate_label, {})
            stable_labels = answer_labels.get(stable_label, {})
            critical_errors += int(bool(candidate_labels.get("critical_factual_or_entity_error")))
            unique_alias_confirmations += int(
                bool(candidate_labels.get("unique_alias_unnecessary_confirmation"))
            )
            ambiguity_misresolutions += int(bool(candidate_labels.get("ambiguity_misresolution")))
            candidate_policy_violations += int(bool(candidate_labels.get("policy_violation")))
            stable_policy_violations += int(bool(stable_labels.get("policy_violation")))
    if invalid_rating or any(len(candidate_scores[item]) != len(rating_rows) for item in RATING_DIMENSIONS):
        return {
            "gate_version": QUALITY_GATE_SPEC["version"],
            "gate_spec_sha256": QUALITY_GATE_SPEC_SHA256,
            "status": "invalid_human_evidence",
            "passed": False,
            "case_count": len(expected),
            "human_rating_count": len(rating_rows),
            "reason_codes": ["invalid_human_rating_shape_or_score"],
        }

    overall_preference = candidate_preferences / preference_total if preference_total else 0.0
    category_preference = {
        category: category_candidate[category] / total
        for category, total in sorted(category_total.items())
    }
    dimension_medians = {
        dimension: statistics.median(values)
        for dimension, values in sorted(candidate_scores.items())
    }
    checks = {
        "critical_factual_entity_errors": critical_errors == 0,
        "overall_candidate_preference": overall_preference >= 0.70,
        "major_scenario_candidate_preference": all(
            value >= 0.60 for value in category_preference.values()
        ),
        "core_dimension_medians": all(value >= 4.0 for value in dimension_medians.values()),
        "unique_alias_unnecessary_confirmation": unique_alias_confirmations == 0,
        "ambiguity_misresolution": ambiguity_misresolutions == 0,
        "policy_violation_nonincrease": candidate_policy_violations <= stable_policy_violations,
    }
    failed = [name for name, passed in checks.items() if not passed]
    return {
        "gate_version": QUALITY_GATE_SPEC["version"],
        "gate_spec_sha256": QUALITY_GATE_SPEC_SHA256,
        "status": "pass" if not failed else "reject_quality_regression",
        "passed": not failed,
        "case_count": len(expected),
        "human_rating_count": len(rating_rows),
        "overall_candidate_preference": overall_preference,
        "category_candidate_preference": category_preference,
        "candidate_dimension_medians": dimension_medians,
        "critical_factual_or_entity_errors": critical_errors,
        "unique_alias_unnecessary_confirmations": unique_alias_confirmations,
        "ambiguity_misresolutions": ambiguity_misresolutions,
        "candidate_policy_violations": candidate_policy_violations,
        "stable_policy_violations": stable_policy_violations,
        "checks": checks,
        "reason_codes": failed or ["pass"],
    }
