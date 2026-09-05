from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit_line_model_current_packet_replay.py"
SPEC = importlib.util.spec_from_file_location("current_packet_replay", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def _row(attempt: int, scenario: str = "case-a") -> dict:
    packet = {
        "contract_version": "model-fact-packet-v2",
        "request": {"depth": "focused", "scopes": []},
        "referee": {"immutable": True},
        "facts": [],
        "events": [],
        "conflicts": [],
        "coverage": {"included_sections": [], "omitted_sections": [], "omission_reasons": {}},
        "render_contract": {},
    }
    return {
        "attempt_index": attempt,
        "scenario": scenario,
        "body": {
            "question": "請分析目前資料限制",
            "request_id": f"request-{attempt}",
            "result": {"profile": "focused-16k-v1", "raw_packet": packet},
        },
    }


def test_current_packet_replay_uses_earliest_raw_packet_and_current_rules():
    report = runner.audit_current_packets([_row(8), _row(2), _row(5, "case-b")])
    assert report["scenario_count"] == 2
    assert report["model_calls"] == report["line_messages_sent"] == 0
    assert report["canonical_db_reads"] == report["canonical_db_writes"] == 0
    assert report["packet_input"] == "stored_raw_packet_not_fresh_db_projection"
    assert report["valid_for_model_quality"] is False
    assert report["valid_for_load_matrix"] is False
    assert report["all_preflights_ready"] is True
    assert report["all_output_rule_ids_resolve"] is True
    first = next(item for item in report["scenarios"] if item["scenario"] == "case-a")
    assert first["selection"]["global_attempt_index"] == 2
    assert first["selection"]["policy"] == "earliest_preserved_attempt_per_scenario_not_best_sample"
    assert first["current_compacted_packet"]["render_contract"]["output_rules"]["version"] == (
        "model-analysis-output-rules-v1"
    )


def test_current_packet_replay_cli_never_overwrites(monkeypatch, tmp_path):
    source = tmp_path / "source.jsonl"
    source.write_text(json.dumps(_row(1), ensure_ascii=False) + "\n", encoding="utf-8")
    with pytest.raises(SystemExit) as error:
        runner.main([str(source), "--output", str(source)])
    assert error.value.code == 2

    output = tmp_path / "evidence.json"
    assert runner.main([str(source), "--output", str(output)]) == 0
    original = output.read_bytes()
    with pytest.raises(SystemExit) as error:
        runner.main([str(source), "--output", str(output)])
    assert error.value.code == 2
    assert output.read_bytes() == original
