from __future__ import annotations

"""S1-R red release contracts, not a validator fix or live-model benchmark.

Import the single fixture source; pin full-case semantic content independently.
The required semantic reason below is a NEW contract, not an existing runtime
capability. Never regenerate golden hashes automatically to make failures pass.
"""

import hashlib
import json
import os
import sys
import tempfile
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import audit_line_model_validation_boundaries as audit  # noqa: E402


ABSENCE_REASON = "absence_only_evidence_cannot_support_claim"
# Frozen before test creation; each case matches the prior 53-case audit artifact.
CASE_SHA256 = {
    "absence_bullish:text_template": "0fc581970cdc70e77a2810b272f86ad053e74e0a3f74bd8f9fc740aee4f11cd3",
    "absence_bearish:text_template": "ce001547c9d28fb20dd3f1f701638d25f1d12a99fd47abd6f9776d1e45dde8a0",
    "honest_limitation:text_template": "99be4a9e923490f54392b29df1793af746be7b8438808c49bdacc638d0a046fb",
    "absence_bullish:conditions": "0a9ba7e21c1ffa12db5dc4593482616e3c73cc8bed7769654925f9d56d0891c0",
    "absence_bearish:conditions": "3cc6ddd2ea38671181a0d8923f5ae578b7ba68e8554866b48849118d4a222ba1",
    "honest_limitation:conditions": "c2d2b3c4304a933e17567516a6dc6b332af0973c1d65647dbbbd007d5df96ab5",
    "absence_bullish:missing_data": "6437cf229305f5f8dea7aa74994d7d4f75201c0ea3dae6ef6a6c378c30bf3fcc",
    "absence_bearish:missing_data": "57a8c1026f99fa772d4a95595c4d36412d7223b70c60c9318e823a15b341879c",
    "honest_limitation:missing_data": "0868323376a01a91226a21436f6d12bcdf33d6395861799d5dcc989f79095fa7",
    "absence_bullish:research_limitations": "457871819bb879ea320754d907d0bd42d4902665c1fe9d81a6e74663c3171343",
    "absence_bearish:research_limitations": "50671811debcaeccfef66a722f645246259ae453f342ae5f978303bd28944855",
    "honest_limitation:research_limitations": "1530f4467b2902c502621f9841f6f9a59edc87f4c0be236da6e05129fb27bd86",
    "grounded_comparison": "42c5661c397538e43bad861ce50ecab8b579453512c90bdb505d7794f49c262a",
}


def _digest(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _frozen_cases(cases):
    ids = [case["case_id"] for case in cases]
    assert len(ids) == len(set(ids)) == 53, "audit case count/duplicate ID drift"
    selected = [case for case in cases if case["family"] in {
        "absence_only_semantics", "positive_control",
    }]
    assert len(selected) == 13, "selected case count drift"
    assert {case["case_id"] for case in selected} == set(CASE_SHA256), "selected ID drift"
    for case in selected:
        assert _digest(case) == CASE_SHA256[case["case_id"]], (
            f"frozen content drift: {case['case_id']}"
        )
    assert sum(case["expected_pass"] is False for case in selected) == 8
    assert sum(case["expected_pass"] is True for case in selected) == 5
    return {case["case_id"]: case for case in selected}


def _verdict_errors(result, expected_pass):
    expected_reasons = ["pass"] if expected_pass else [ABSENCE_REASON]
    errors = []
    if result["passed"] is not expected_pass:
        errors.append(f"passed={result['passed']!r}, expected={expected_pass}")
    if list(result["reason_codes"]) != expected_reasons:
        errors.append(f"reason_codes={result['reason_codes']!r}, expected={expected_reasons!r}")
    if bool(result["rendered_blocks"]) is not expected_pass:
        errors.append("rendered_blocks must be nonempty only for accepted positive controls")
    return errors


@pytest.fixture(scope="module")
def frozen_cases():
    return _frozen_cases(audit.build_cases())


@pytest.fixture(scope="module")
def evidence_directory(tmp_path_factory):
    configured = os.environ.get("S1R_EVIDENCE_DIR")
    if not configured:
        return tmp_path_factory.mktemp("s1r-evidence")
    parent = Path(configured)
    if not parent.is_absolute():
        parent = PROJECT_ROOT / parent
    parent.mkdir(parents=True, exist_ok=True)
    # A unique run directory plus exclusive files never overwrites prior samples.
    return Path(tempfile.mkdtemp(prefix="s1r-", dir=parent))


@pytest.mark.parametrize("case_id", tuple(CASE_SHA256))
def test_absence_only_release_contract(case_id, frozen_cases, evidence_directory):
    case = deepcopy(frozen_cases[case_id])
    path = evidence_directory / (case_id.replace(":", "__") + ".json")
    row = {
        "contract_version": "absence-only-release-s1r-v1",
        "evidence_kind": "synthetic_packet_real_validator_and_repair_not_model_output",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "case": deepcopy(case),
        "case_sha256": CASE_SHA256[case_id],
        "expected_reason_codes": ["pass"] if case["expected_pass"] else [ABSENCE_REASON],
        "model_calls": 0, "canonical_db_writes": 0, "valid_for_load_matrix": False,
        "source_sha256": {
            relative: hashlib.sha256((PROJECT_ROOT / relative).read_bytes()).hexdigest()
            for relative in (
                "scripts/audit_line_model_validation_boundaries.py",
                "review_src/core/line_model_validation.py",
                "tests/test_line_model_absence_only_release_contract.py",
            )
        },
    }
    errors = []
    try:
        row["raw"] = asdict(audit.validate_model_analysis_v2(case["output"], case["packet"]))
        repaired, repair_codes = audit.deterministic_limitation_placeholder_repair(
            case["output"], case["packet"],
        )
        row["repair_codes"] = list(repair_codes)
        row["after_repair_output"] = deepcopy(repaired)
        row["after_repair"] = asdict(audit.validate_model_analysis_v2(
            repaired if repaired is not None else case["output"], case["packet"],
        ))
        for stage in ("raw", "after_repair"):
            errors.extend(f"{stage}: {error}" for error in
                          _verdict_errors(row[stage], case["expected_pass"]))
            if case_id == "grounded_comparison":
                expected_text = "收盤100 元高於均線90 元，僅反映價格相對位置。"
                if row[stage]["rendered_blocks"][0:1] not in ([expected_text], (expected_text,)):
                    errors.append(f"{stage}: grounded comparison lost either bound value")
    except Exception as exc:
        # Preserve unexpected failure too; an exception is NEVER semantic rejection.
        row["exception"] = {"type": type(exc).__name__, "message": str(exc)}
        errors.append(f"unexpected exception: {type(exc).__name__}: {exc}")
    row["input_unchanged"] = _digest(case) == CASE_SHA256[case_id]
    if not row["input_unchanged"]:
        errors.append("validator/repair mutated original fixture")
    row["contract_errors"] = errors
    row["contract_passed"] = not errors
    with path.open("x", encoding="utf-8") as handle:
        json.dump(row, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    assert not errors, f"{case_id}; evidence={path}\n" + "\n".join(errors)


@pytest.mark.parametrize("mutation", ["text", "packet", "expectation", "missing", "unknown", "duplicate"])
def test_fixture_guard_rejects_content_or_identity_drift(mutation):
    cases = audit.build_cases()
    target = next(case for case in cases if case["case_id"] == "absence_bullish:text_template")
    if mutation == "text":
        target["output"]["explanation_blocks"][0]["text_template"] = "資料不足，無法判斷。"
    elif mutation == "packet":
        target["packet"]["facts"][0]["quality"] = "ok"
    elif mutation == "expectation":
        target["expected_pass"] = True
    elif mutation == "missing":
        cases.remove(target)
    elif mutation == "unknown":
        target["case_id"] = "unknown_same_count"
    else:
        target["case_id"] = "absence_bearish:text_template"
    with pytest.raises(AssertionError, match="drift"):
        _frozen_cases(cases)


@pytest.mark.parametrize("passed,reasons,rendered", [
    (False, ["invalid_block_schema"], []),
    (False, ["missing_fact_used_outside_limitation"], []),
    (False, ["absence_only_evidence_cannot_support_claim_typo"], []),
    (False, [ABSENCE_REASON, "invalid_json"], []),
    (False, ["pass", ABSENCE_REASON], []),
    (True, [ABSENCE_REASON], []),
    (False, [ABSENCE_REASON], ["unsupported claim"]),
])
def test_verdict_guard_rejects_false_green(passed, reasons, rendered):
    # Helper-only self-test; the 13 release cases above never mock the validator.
    result = {"passed": passed, "reason_codes": reasons, "rendered_blocks": rendered}
    assert _verdict_errors(result, False)


def test_verdict_guard_accepts_exact_reason_and_positive_control():
    assert not _verdict_errors({
        "passed": False, "reason_codes": [ABSENCE_REASON], "rendered_blocks": [],
    }, False)
    assert not _verdict_errors({
        "passed": True, "reason_codes": ["pass"], "rendered_blocks": ["honest limitation"],
    }, True)
