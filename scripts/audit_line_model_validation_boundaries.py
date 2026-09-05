from __future__ import annotations

"""Offline negative/positive contract probes; exit 2 when a known gap remains.

Synthetic fixtures call the real validator and deterministic repair only. No
model, DB, network, environment-file access, or claim of general semantic proof.
Relative output paths are resolved against the project root, not the shell cwd.
"""

import argparse
import hashlib
import json
import os
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = PROJECT_ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from core.line_model_validation import (  # noqa: E402
    deterministic_limitation_placeholder_repair,
    validate_model_analysis_v2,
)

SURFACES = ("text_template", "conditions", "missing_data", "research_limitations")
RAW_PERIODS = (
    ("quarter_ascii", "Q2"),
    ("quarter_lowercase", "q2"),
    ("quarter_fullwidth", "Ｑ２"),
    ("quarter_spaced", "Q 2"),
    ("year_quarter", "2026年Q2"),
    ("iso_date", "2026-08-28"),
    ("roc_year", "民國一一五年"),
    ("chinese_quarter", "第二季"),
    ("chinese_year_spaced", "二〇二六 年"),
    ("chinese_month_spaced", "八 月"),
)


def _fixture() -> tuple[dict[str, Any], dict[str, Any]]:
    packet = {
        "contract_version": "model-fact-packet-v2",
        "request": {"depth": "focused", "scopes": []},
        "facts": [{
            "fact_id": "F101", "field": "support_resistance.status",
            "value": "unavailable", "unit": "", "period": "daily",
            "as_of": "2026-08-28", "quality": "unavailable",
            "authority_tier": "canonical_db", "use_scope": ["limitation"],
        }],
        "events": [],
    }
    block = {
        "block_type": "limitation", "text_template": "缺少可核對的支撐壓力資料。",
        "evidence_ids": ["F101"], "uncertainty": "high", "conditions": [],
    }
    output = {
        "contract_version": "model-analysis-v2",
        # Match the focused generation schema's minimum shape as well.
        "explanation_blocks": [deepcopy(block) for _ in range(3)],
        "missing_data": [], "used_event_ids": [], "research_limitations": [],
    }
    return packet, output


def _case(case_id: str, family: str, surface: str, text: str, expected: bool) -> dict:
    packet, output = _fixture()
    if surface == "text_template":
        output["explanation_blocks"][0][surface] = text
    elif surface == "conditions":
        output["explanation_blocks"][0][surface] = [text]
    else:
        output[surface] = [text]
    return {"case_id": case_id, "family": family, "surface": surface,
            "expected_pass": expected, "packet": packet, "output": output}


def build_cases() -> list[dict]:
    cases = []
    for surface in SURFACES:
        for name, period in RAW_PERIODS:
            cases.append(_case(f"{name}:{surface}", "unbound_period", surface,
                               f"缺少{period}的支撐壓力資料。", False))
        cases.append(_case(f"absence_bullish:{surface}", "absence_only_semantics", surface,
                           "支撐壓力區間資料不可用；均線結構偏多。", False))
        cases.append(_case(f"absence_bearish:{surface}", "absence_only_semantics", surface,
                           "支撐壓力資料不足，但價格趨勢已轉弱。", False))
        cases.append(_case(f"honest_limitation:{surface}", "positive_control", surface,
                           "資料不足，無法判斷支撐壓力。", True))
    packet, output = _fixture()
    for fact_id, field, value in (("F102", "close", 100), ("F103", "moving_averages.ma20", 90)):
        packet["facts"].append({
            "fact_id": fact_id, "field": field, "value": value, "unit": "TWD",
            "period": "daily", "as_of": "2026-08-28", "quality": "ok",
            "authority_tier": "canonical_db", "use_scope": ["explanation", "numeric_claim"],
        })
    output["explanation_blocks"][0].update(
        block_type="inference", text_template="收盤{{F102}}高於均線{{F103}}，僅反映價格相對位置。",
        evidence_ids=["F102", "F103"],
    )
    cases.append({"case_id": "grounded_comparison", "family": "positive_control",
                  "surface": "text_template", "expected_pass": True, "packet": packet, "output": output})
    return cases


def audit_cases(cases: list[dict]) -> dict:
    rows = []
    for case in cases:
        packet, output = deepcopy(case["packet"]), deepcopy(case["output"])
        raw = validate_model_analysis_v2(output, packet)
        repaired, repair_codes = deterministic_limitation_placeholder_repair(output, packet)
        final = validate_model_analysis_v2(repaired if repaired is not None else output, packet)
        expectation_met = raw.passed == case["expected_pass"] and final.passed == case["expected_pass"]
        rows.append({
            **case, "raw_pass": raw.passed, "raw_reasons": list(raw.reason_codes),
            "after_repair_pass": final.passed, "after_repair_reasons": list(final.reason_codes),
            "repair_codes": list(repair_codes), "after_repair_output": repaired,
            "rendered_blocks": list(final.rendered_blocks), "expectation_met": expectation_met,
        })
    mismatches = [row["case_id"] for row in rows if not row["expectation_met"]]
    return {
        "contract_version": "line-model-boundary-audit-v1",
        "evidence_kind": "offline_synthetic_validator_and_repair_not_live_model",
        "status": "pass" if rows and not mismatches else "needs_rework",
        "model_calls": 0, "canonical_db_writes": 0, "valid_for_load_matrix": False,
        "general_semantic_correctness_proven": False,
        "case_count": len(rows), "expectation_mismatch_count": len(mismatches),
        "mismatched_case_ids": mismatches, "cases": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", help="New JSON evidence file, relative to project root")
    args = parser.parse_args(argv)
    report = audit_cases(build_cases())
    report["captured_at"] = datetime.now(timezone.utc).isoformat()
    report["source_sha256"] = {
        relative: hashlib.sha256((PROJECT_ROOT / relative).read_bytes()).hexdigest()
        for relative in ("scripts/audit_line_model_validation_boundaries.py",
                         "review_src/core/line_model_validation.py")
    }
    if args.output:
        output = Path(args.output)
        if not output.is_absolute():
            output = PROJECT_ROOT / output
        output.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation prevents overwriting either old evidence or source.
        with output.open("x", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    print(json.dumps({key: value for key, value in report.items() if key != "cases"}, ensure_ascii=True))
    return 0 if report["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
