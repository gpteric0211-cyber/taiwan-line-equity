from __future__ import annotations

"""Offline audit of ALL preserved Phase B attempts; never calls a model or DB.

Token checks start from historical compacted packets, not a fresh DB projection.
They cannot establish current-input quality, model pass rate or serving latency.
"""

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "review_src"))

from core.line_model_contract import CONTEXT_PROFILES, compact_packet_to_token_budget  # noqa: E402
from core.line_model_output_schema import model_analysis_output_schema  # noqa: E402
from core.line_model_validation import validate_model_analysis_v2  # noqa: E402
from services.line_model_shadow_service import (  # noqa: E402
    MODEL_ANALYSIS_FINAL_GUARD,
    MODEL_ANALYSIS_SYSTEM_PROMPT,
)


QUESTIONS = {
    "fundamental_chip_technical_news": "台積電基本面、籌碼、技術面與最新新聞完整分析",
    "technical_valuation_support_risk": "台積電技術面、估值、支撐壓力與風險完整評估",
    "chip_night_us_events": "台積電籌碼、夜盤、美股與重大事件完整分析",
}


def audit_attempts(rows: list[dict]) -> dict:
    attempts = []
    by_scenario = {}
    for row in rows:
        scenario = row["scenario"]
        result = row["body"]["result"]
        packet = result["compacted_packet"]
        raw = validate_model_analysis_v2(result["model_output"], packet)
        accepted = validate_model_analysis_v2(
            result.get("validated_model_output") or result["model_output"], packet,
        )
        schema = model_analysis_output_schema(packet["request"]["depth"])
        schema_text = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
        projected = compact_packet_to_token_budget(
            packet,
            system_prompt=f"{MODEL_ANALYSIS_SYSTEM_PROMPT}\nOUTPUT_JSON_SCHEMA：{schema_text}",
            question=f"{QUESTIONS[scenario]}\n{MODEL_ANALYSIS_FINAL_GUARD}",
            profile=CONTEXT_PROFILES[result["profile"]], actual_context=16384,
            reserved_output_tokens=900,
        )
        counts = by_scenario.setdefault(scenario, {
            "attempts": 0, "pass": 0, "reject": 0,
            "first_pass_global_attempt_index": None, "first_pass_scenario_attempt": None,
        })
        counts["attempts"] += 1
        counts[result["validator_result"]] += 1
        if result["validator_result"] == "pass" and counts["first_pass_global_attempt_index"] is None:
            counts["first_pass_global_attempt_index"] = row["attempt_index"]
            counts["first_pass_scenario_attempt"] = counts["attempts"]
        removed = set(projected.removed_fact_ids)
        attempts.append({
            "global_attempt_index": row["attempt_index"], "scenario": scenario,
            "scenario_attempt": counts["attempts"],
            "historical_validator_result": result["validator_result"],
            "historical_validator_reasons": result["validator_reason_codes"],
            "finish_reason": result["finish_reason"],
            "completion_token_count": result["completion_token_count"],
            "raw_replay_reasons": list(raw.reason_codes),
            "stored_validated_output_replay_pass": accepted.passed,
            "stored_output_decision_unchanged": accepted.passed == (result["validator_result"] == "pass"),
            "new_generation_prompt_preflight": {
                "ready": projected.compacted_preflight.ready,
                "estimate_before_compaction": projected.original_preflight.estimated_prompt_tokens,
                "estimated_prompt_tokens": projected.compacted_preflight.estimated_prompt_tokens,
                "effective_prompt_budget": projected.compacted_preflight.effective_prompt_budget,
                "facts_before": len(packet["facts"]),
                "facts_after": len(projected.packet["facts"]),
                "removed_fields": [
                    {"fact_id": f["fact_id"], "domain": f["domain"], "field": f["field"]}
                    for f in packet["facts"] if f["fact_id"] in removed
                ],
                "events_before": len(packet["events"]),
                "events_after": len(projected.packet["events"]),
                "coverage": projected.packet["coverage"],
            },
        })
    for counts in by_scenario.values():
        counts["reject_rate"] = counts["reject"] / counts["attempts"]
    return {
        "audit_kind": "offline_historical_output_and_prompt_replay_not_live_evidence",
        "model_calls": 0, "canonical_writes": 0, "load_matrix_evidence": False,
        "context_assumption": 16384, "output_reserve_assumption": 900,
        "packet_input": "stored_compacted_packet_not_fresh_db_projection",
        "attempt_count": len(attempts), "by_scenario": by_scenario,
        "historical_reject_reasons": dict(Counter(
            reason for item in attempts if item["historical_validator_result"] == "reject"
            for reason in item["historical_validator_reasons"]
        )),
        "all_stored_output_decisions_unchanged": all(a["stored_output_decision_unchanged"] for a in attempts),
        "all_new_prompt_preflights_ready": all(a["new_generation_prompt_preflight"]["ready"] for a in attempts),
        "attempts": attempts,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Historical Phase B JSONL (relative to current directory)")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.input.resolve() == args.output.resolve() or args.output.exists():
        parser.error("output must be a new file, never overwrite original evidence")
    source = args.input.read_bytes()
    rows = [json.loads(line) for line in source.decode("utf-8-sig").splitlines() if line.strip()]
    if not rows:
        parser.error("input must contain at least one attempt")
    report = audit_attempts(rows)
    report.update(
        created_at=datetime.now().astimezone().isoformat(),
        source_artifact=args.input.name, source_sha256=hashlib.sha256(source).hexdigest(),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({key: value for key, value in report.items() if key != "attempts"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
