from __future__ import annotations

"""Rebuild inspectable packets from stored A20 raw packets with current source.

This is an offline deployment-preflight aid.  It does not query the canonical
database, call the local model, or represent a live Phase B quality sample.
"""

import argparse
import hashlib
import json
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = PROJECT_ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from core.line_model_contract import CONTEXT_PROFILES, compact_packet_to_token_budget  # noqa: E402
from core.line_model_output_schema import model_analysis_output_schema  # noqa: E402
from services.line_model_shadow_service import (  # noqa: E402
    MODEL_ANALYSIS_FINAL_GUARD,
    MODEL_ANALYSIS_SYSTEM_PROMPT,
)


SOURCE_PATHS = (
    "review_src/core/line_model_contract.py",
    "review_src/core/line_model_output_schema.py",
    "review_src/services/line_model_shadow_service.py",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _packet_ids(packet: dict[str, Any]) -> set[str]:
    return {
        str(item.get(key))
        for collection, key in (("facts", "fact_id"), ("events", "event_id"))
        for item in packet.get(collection, [])
        if isinstance(item, dict) and item.get(key)
    }


def _rule_ids(value: Any, *, key: str = "") -> set[str]:
    if isinstance(value, dict):
        return set().union(*(_rule_ids(item, key=str(name)) for name, item in value.items()), set())
    if isinstance(value, list):
        return set().union(*(_rule_ids(item, key=key) for item in value), set())
    if key in {"fact_id", "event_id"} or key.endswith("_ids"):
        text = str(value or "")
        if text.startswith(("F", "E")):
            return {text}
    return set()


def _selected_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Choose the earliest preserved attempt for each scenario, never a best pass."""

    selected: dict[str, dict[str, Any]] = {}
    for row in sorted(rows, key=lambda item: int(item.get("attempt_index") or 0)):
        scenario = str(row.get("scenario") or "")
        result = (row.get("body") or {}).get("result") or {}
        if scenario and isinstance(result.get("raw_packet"), dict):
            selected.setdefault(scenario, row)
    return list(selected.values())


def audit_current_packets(
    rows: list[dict[str, Any]],
    *,
    actual_context: int = 16_384,
    reserved_output_tokens: int = 900,
) -> dict[str, Any]:
    scenarios = []
    for row in _selected_rows(rows):
        body = row["body"]
        result = body["result"]
        raw_packet = result["raw_packet"]
        depth = str(raw_packet.get("request", {}).get("depth") or result.get("depth") or "focused")
        profile_name = str(result["profile"])
        schema = model_analysis_output_schema(depth)
        schema_json = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
        compacted = compact_packet_to_token_budget(
            raw_packet,
            system_prompt=f"{MODEL_ANALYSIS_SYSTEM_PROMPT}\nOUTPUT_JSON_SCHEMA：{schema_json}",
            question=f"{body['question']}\n{MODEL_ANALYSIS_FINAL_GUARD}",
            profile=CONTEXT_PROFILES[profile_name],
            actual_context=actual_context,
            reserved_output_tokens=reserved_output_tokens,
        )
        current_packet = compacted.packet
        current_ids = _packet_ids(current_packet)
        output_rules = current_packet.get("render_contract", {}).get("output_rules", {})
        referenced_rule_ids = _rule_ids(output_rules)
        removed_fact_ids = set(compacted.removed_fact_ids)
        removed_event_ids = set(compacted.removed_event_ids)
        scenarios.append({
            "scenario": row["scenario"],
            "question": body["question"],
            "selection": {
                "policy": "earliest_preserved_attempt_per_scenario_not_best_sample",
                "global_attempt_index": row.get("attempt_index"),
                "request_id": body.get("request_id") or result.get("request_id"),
            },
            "profile": profile_name,
            "raw_packet_summary": {
                "facts": len(raw_packet.get("facts", [])),
                "events": len(raw_packet.get("events", [])),
                "included_sections": raw_packet.get("coverage", {}).get("included_sections", []),
                "omitted_sections": raw_packet.get("coverage", {}).get("omitted_sections", []),
                "omission_reasons": raw_packet.get("coverage", {}).get("omission_reasons", {}),
            },
            "current_compacted_packet_summary": {
                "facts": len(current_packet.get("facts", [])),
                "events": len(current_packet.get("events", [])),
                "included_sections": current_packet.get("coverage", {}).get("included_sections", []),
                "omitted_sections": current_packet.get("coverage", {}).get("omitted_sections", []),
                "omission_reasons": current_packet.get("coverage", {}).get("omission_reasons", {}),
            },
            "preflight": {
                "before_compaction": asdict(compacted.original_preflight),
                "after_compaction": asdict(compacted.compacted_preflight),
            },
            "removed_facts": [
                {"fact_id": fact.get("fact_id"), "domain": fact.get("domain"), "field": fact.get("field")}
                for fact in raw_packet.get("facts", [])
                if fact.get("fact_id") in removed_fact_ids
            ],
            "removed_events": [
                {"event_id": event.get("event_id"), "verification_state": event.get("verification_state")}
                for event in raw_packet.get("events", [])
                if event.get("event_id") in removed_event_ids
            ],
            "output_rules_integrity": {
                "version": output_rules.get("version"),
                "referenced_ids": sorted(referenced_rule_ids),
                "missing_from_compacted_packet": sorted(referenced_rule_ids - current_ids),
            },
            "raw_packet": raw_packet,
            "current_compacted_packet": current_packet,
        })
    return {
        "audit_kind": "offline_current_source_raw_packet_replay_not_live_phase_b_evidence",
        "packet_input": "stored_raw_packet_not_fresh_db_projection",
        "selection_policy": "earliest_preserved_attempt_per_scenario_not_best_sample",
        "model_calls": 0,
        "line_messages_sent": 0,
        "canonical_db_reads": 0,
        "canonical_db_writes": 0,
        "valid_for_model_quality": False,
        "valid_for_load_matrix": False,
        "actual_context_assumption": actual_context,
        "reserved_output_tokens": reserved_output_tokens,
        "scenario_count": len(scenarios),
        "all_preflights_ready": bool(scenarios) and all(
            item["preflight"]["after_compaction"]["ready"] for item in scenarios
        ),
        "all_output_rule_ids_resolve": bool(scenarios) and all(
            not item["output_rules_integrity"]["missing_from_compacted_packet"] for item in scenarios
        ),
        "scenarios": scenarios,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Preserved A20 JSONL")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    input_path = args.input if args.input.is_absolute() else PROJECT_ROOT / args.input
    output_path = args.output if args.output.is_absolute() else PROJECT_ROOT / args.output
    if input_path.resolve() == output_path.resolve() or output_path.exists():
        parser.error("output must be a new file and must not overwrite source evidence")
    source = input_path.read_bytes()
    rows = [json.loads(line) for line in source.decode("utf-8-sig").splitlines() if line.strip()]
    if not rows:
        parser.error("input must contain at least one A20 row")
    report = audit_current_packets(rows)
    report.update({
        "created_at": datetime.now().astimezone().isoformat(),
        "source_artifact": input_path.name,
        "source_sha256": hashlib.sha256(source).hexdigest(),
        "current_source_sha256": {relative: _sha256(PROJECT_ROOT / relative) for relative in SOURCE_PATHS},
    })
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({key: value for key, value in report.items() if key != "scenarios"}, ensure_ascii=False))
    return 0 if report["all_preflights_ready"] and report["all_output_rule_ids_resolve"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
