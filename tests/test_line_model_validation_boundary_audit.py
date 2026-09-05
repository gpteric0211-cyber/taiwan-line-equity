from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import audit_line_model_validation_boundaries as audit  # noqa: E402


def _verdict(passed):
    return SimpleNamespace(passed=passed, reason_codes=("pass" if passed else "rejected",),
                           rendered_blocks=("fixture",) if passed else ())


def test_boundary_audit_has_unique_ids_all_surfaces_and_positive_controls():
    cases = audit.build_cases()
    assert len(cases) == len({case["case_id"] for case in cases}) == 53
    for family in ("unbound_period", "absence_only_semantics"):
        assert {case["surface"] for case in cases if case["family"] == family} == set(audit.SURFACES)
    assert sum(case["expected_pass"] for case in cases) == 5
    assert all(len(case["output"]["explanation_blocks"]) == 3 for case in cases)


@pytest.mark.parametrize("always_pass", [False, True])
def test_audit_rejects_accept_everything_and_silence_everything(monkeypatch, always_pass):
    monkeypatch.setattr(audit, "validate_model_analysis_v2", lambda *_: _verdict(always_pass))
    report = audit.audit_cases(audit.build_cases())
    assert report["status"] == "needs_rework"
    assert report["expectation_mismatch_count"] > 0
    assert report["model_calls"] == 0
    assert report["general_semantic_correctness_proven"] is False


def test_audit_detects_repair_that_wrongly_accepts_negative_case(monkeypatch):
    verdicts = iter([_verdict(False), _verdict(True)])
    monkeypatch.setattr(audit, "validate_model_analysis_v2", lambda *_: next(verdicts))
    report = audit.audit_cases([audit.build_cases()[0]])
    assert report["status"] == "needs_rework"
    assert report["cases"][0]["raw_pass"] is False
    assert report["cases"][0]["after_repair_pass"] is True


def test_empty_audit_never_passes():
    assert audit.audit_cases([])["status"] == "needs_rework"


def test_audit_uses_actual_validator_without_mutating_fixtures():
    cases = audit.build_cases()
    before = json.dumps(cases, ensure_ascii=False, sort_keys=True)
    report = audit.audit_cases(cases)
    assert json.dumps(cases, ensure_ascii=False, sort_keys=True) == before
    assert report["case_count"] == 53
    assert all(row["expectation_met"] for row in report["cases"] if row["family"] == "positive_control")
    # Do not encode a vulnerability as a passing release gate. The CLI must
    # remain red for any known counterexample, even when these tool tests pass.
    assert report["status"] == ("needs_rework" if report["expectation_mismatch_count"] else "pass")


@pytest.mark.parametrize("status,expected_exit", [("needs_rework", 2), ("pass", 0)])
def test_cli_saves_status_and_never_overwrites_evidence(monkeypatch, tmp_path, status, expected_exit):
    monkeypatch.setattr(audit, "audit_cases", lambda _: {"status": status, "cases": []})
    path = tmp_path / "audit.json"
    assert audit.main(["--output", str(path)]) == expected_exit
    assert json.loads(path.read_text(encoding="utf-8"))["status"] == status
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        audit.main(["--output", str(path)])
    assert path.read_bytes() == original
