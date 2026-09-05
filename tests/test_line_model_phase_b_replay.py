from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit_line_model_phase_b_replay.py"
SPEC = importlib.util.spec_from_file_location("phase_b_replay", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def test_replay_counts_failed_attempts_and_labels_historical_only():
    packet = {
        "request": {"depth": "focused", "scopes": []},
        "facts": [], "events": [],
        "coverage": {"included_sections": [], "omitted_sections": [], "omission_reasons": {}},
    }
    valid = {
        "contract_version": "model-analysis-v2",
        "explanation_blocks": [{"block_type": "limitation", "text_template": "資料不足。",
                                "evidence_ids": [], "uncertainty": "high", "conditions": []}],
        "missing_data": [], "used_event_ids": [], "research_limitations": [],
    }
    rows = [{"scenario": "fundamental_chip_technical_news", "attempt_index": index,
             "body": {"result": {
                 "compacted_packet": packet, "model_output": output,
                 "validator_result": decision, "validator_reason_codes": [reason],
                 "profile": "focused-16k-v1", "finish_reason": finish, "completion_token_count": 900,
             }}} for index, output, decision, reason, finish in [
        (2, "{", "reject", "invalid_json", "length"),
        (5, json.dumps(valid), "pass", "pass", "stop"),
    ]]
    report = runner.audit_attempts(rows)
    counts = report["by_scenario"]["fundamental_chip_technical_news"]
    assert counts == {"attempts": 2, "pass": 1, "reject": 1, "reject_rate": 0.5,
                      "first_pass_global_attempt_index": 5, "first_pass_scenario_attempt": 2}
    assert report["attempt_count"] == 2
    assert report["all_stored_output_decisions_unchanged"] is True
    assert report["model_calls"] == 0
    assert report["load_matrix_evidence"] is False
    assert report["attempts"][0]["finish_reason"] == "length"
    assert report["packet_input"] == "stored_compacted_packet_not_fresh_db_projection"


def test_replay_cli_cannot_overwrite_input_or_existing_evidence(monkeypatch, tmp_path):
    evidence = tmp_path / "evidence.jsonl"
    evidence.write_text("original", encoding="utf-8")
    monkeypatch.setattr(runner.sys, "argv", [str(SCRIPT), str(evidence), "--output", str(evidence)])
    with pytest.raises(SystemExit) as error:
        runner.main()
    assert error.value.code == 2
    assert evidence.read_text(encoding="utf-8") == "original"
