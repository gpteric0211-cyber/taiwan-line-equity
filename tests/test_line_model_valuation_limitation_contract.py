"""4.1 only: canonical valuation-baseline absence, not general claim binding.

Synthetic derivatives of frozen A20 facts; never live-model quality evidence.
No service/DB imports. Expectations are fixed before running the validator. The
original packet/output remains immutable; changing an ID cannot grant permission.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3

import pytest

ROOT = Path(__file__).resolve().parents[1]
CORPUS = "logs/line_model_shadow/phase2_acceptance_20260831_1641/phase_a.jsonl"
CORPUS_SHA = "3d257483941107519632a1c7d3b94fdc7ec39ae1b94157a1633401f814d2e036"
PROMPT_TEXT = "缺少比較基準，無法進行相對估值判斷"
DB_TEXT = "缺少同業或歷史估值基準，只能列示估值數值"
SURFACES = ("text_template", "conditions", "missing_data", "research_limitations")
U = "unverifiable_limitation_claim"
A = "absence_only_evidence_cannot_support_claim"
N = "ungrounded_numeric_or_date_claim"
PASS = ("pass",)
CASE_COUNT = 153  # Frozen from inputs below before the first validator execution.
CASESET_SHA = "152b4b6305659fe37f254f15c91bfb464ab7108e3cac095a512c91e3e6c21c62"


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def source_packet():
    original = (ROOT / CORPUS).read_bytes()
    assert hashlib.sha256(original).hexdigest() == CORPUS_SHA, "corpus drift"
    rows = [json.loads(line) for line in original.splitlines() if line.strip()]
    assert [r["attempt_index"] for r in rows] == list(range(1, 21)), "corpus count/order drift"
    packet = deepcopy(rows[0]["body"]["result"]["compacted_packet"])
    packet["request"]["depth"] = "focused"  # Diagnostic fixture, never a live scope reduction.
    return packet


def build_cases():
    base = source_packet()
    cases = []

    def make(case_id, surface="text_template", text=PROMPT_TEXT, reasons=PASS):
        packet = deepcopy(base)
        block = {"block_type": "limitation", "text_template": "資料不足。",
                 "evidence_ids": ["F034", "F045"], "conditions": [], "uncertainty": "high"}
        output = {"contract_version": "model-analysis-v2", "explanation_blocks": [
            {"block_type": "fact", "text_template": "本益比為{{F033}}，股價淨值比為{{F032}}，殖利率為{{F031}}。",
             "evidence_ids": ["F031", "F032", "F033"], "conditions": [], "uncertainty": "low"}, block],
            "missing_data": [], "research_limitations": [], "used_event_ids": []}
        if surface == "text_template":
            block["text_template"] = text
        elif surface == "conditions":
            block["conditions"] = [text]
        else:
            output[surface] = [text]
        case = {"case_id": case_id, "surface": surface, "packet": packet, "output": output,
                "expected_reasons": list(reasons), "expected_repair_codes": [],
                "provenance": {"kind": "synthetic_derivative_not_live_answer", "corpus": CORPUS,
                               "corpus_sha256": CORPUS_SHA, "attempt": 1,
                               "changes": ["depth=focused; same 53 facts; new two-block output"]}}
        cases.append(case)
        return case

    def marker(case):
        return next(f for f in case["packet"]["facts"] if f["fact_id"] == "F034")

    for surface in SURFACES:
        make("positive_prompt_" + surface, surface)
        make("positive_db_" + surface, surface, DB_TEXT)
        for name, key, value in (
            ("wrong_domain", "domain", "fundamentals"),
            ("wrong_field", "field", "valuation_percentile"),
            ("wrong_status", "value", "available"),
            ("near_status", "value", "unavailable_without_peer_or_historical_baseline_extra"),
            ("prose_not_status", "value", DB_TEXT),
            ("external", "authority_tier", "official_external"),
            ("stale", "quality", "stale"), ("missing", "quality", "missing"),
            ("unavailable", "quality", "unavailable"), ("estimated", "quality", "estimated"),
            ("delayed", "quality", "source_delayed"),
            ("empty_scope", "use_scope", []), ("numeric_only", "use_scope", ["numeric_claim"]),
            ("limitation_only", "use_scope", ["limitation"]),
            ("string_scope", "use_scope", "explanation"),
            ("wrong_period", "period", "intraday"), ("wrong_unit", "unit", "ratio"),
            ("future_asof", "as_of", "2026-08-29"),
            ("future_trade_date", "trade_date", "2026-08-29"),
            ("invalid_asof", "as_of", "2026-02-30"),
        ):
            case = make("negative_" + name + "_" + surface, surface, reasons=(U,))
            marker(case)[key] = value
        for key in ("as_of", "trade_date", "use_scope"):
            case = make("negative_deleted_" + key + "_" + surface, surface, reasons=(U,))
            marker(case).pop(key)
        case = make("negative_no_marker_" + surface, surface, reasons=(U,))
        case["packet"]["facts"] = [f for f in case["packet"]["facts"] if f["fact_id"] != "F034"]
        case["output"]["explanation_blocks"][1]["evidence_ids"] = ["F045"]
        case = make("negative_duplicate_field_" + surface, surface, reasons=(U,))
        duplicate = deepcopy(marker(case))
        duplicate["fact_id"] = "F934"
        case["packet"]["facts"].append(duplicate)
        case = make("negative_duplicate_id_" + surface, surface, reasons=(U,))
        duplicate = deepcopy(marker(case))
        duplicate["field"] = "other_field"
        case["packet"]["facts"].append(duplicate)
        case = make("negative_scope_not_requested_" + surface, surface, reasons=(U,))
        case["packet"]["request"]["scopes"].remove("valuation")
        case = make("negative_invalid_cutoff_" + surface, surface, reasons=(U,))
        case["packet"]["request"]["analysis_cutoff"] = "unknown"
        for suffix, text in (("bullish", "均線偏多。"), ("bearish", "價格趨勢已轉弱。")):
            make("tail_" + suffix + "_" + surface, surface, PROMPT_TEXT + "。" + text, (A,))
        make("negative_numeric_" + surface, surface, PROMPT_TEXT + "；本益比30倍。", (N,))
        make("negative_date_" + surface, surface, PROMPT_TEXT + "；資料截至2026-08-28。", (N,))
        make("negative_relative_claim_" + surface, surface, PROMPT_TEXT + "；估值偏低。", (U,))
        make("negative_sr_causation_" + surface, surface,
             PROMPT_TEXT + "；因分價量不足，所以沒有支撐區間。", (U,))

    # The valid marker must be cited by the same block, not just present elsewhere.
    for surface in ("text_template", "conditions"):
        case = make("negative_marker_not_cited_" + surface, surface, reasons=(U,))
        case["output"]["explanation_blocks"][1]["evidence_ids"] = ["F045"]
    case = make("positive_marker_only")
    case["output"]["explanation_blocks"][1]["evidence_ids"] = ["F034"]
    case = make("tail_marker_only", text=PROMPT_TEXT + "；均線偏多。", reasons=(A,))
    case["output"]["explanation_blocks"][1]["evidence_ids"] = ["F034"]
    case = make("negative_marker_only_wrong_status", reasons=(U,))
    case["output"]["explanation_blocks"][1]["evidence_ids"] = ["F034"]
    marker(case)["value"] = "available"
    case = make("positive_renamed_id")
    marker(case)["fact_id"] = "F934"
    case["output"]["explanation_blocks"][1]["evidence_ids"] = ["F934", "F045"]
    make("positive_normalized_spacing", text="缺少 比較基準， 無法進行相對估值判斷。")
    case = make("positive_existing_bound_pe", text="本益比為{{F033}}；" + PROMPT_TEXT)
    case["output"]["explanation_blocks"][1]["evidence_ids"].append("F033")
    case = make("negative_status_as_number", text=PROMPT_TEXT + "；{{F034}}",
                reasons=("ineligible_numeric_placeholder", "unresolved_placeholder"))
    # Never derive expectations from validator/renderer output.
    for case in cases:
        block_text = case["output"]["explanation_blocks"][1]["text_template"].replace("{{F033}}", "28.05 倍")
        case["expected_rendered"] = [
            "本益比為28.05 倍，股價淨值比為9.76 倍，殖利率為0.91%。", block_text
        ] if case["expected_reasons"] == list(PASS) else []
        case["literal_input_sha256"] = digest({key: case[key] for key in ("packet", "output")})
    return cases


def frozen_cases(cases):
    ids = [case["case_id"] for case in cases]
    assert len(cases) == CASE_COUNT and len(set(ids)) == CASE_COUNT, "case count/id drift"
    assert digest(cases) == CASESET_SHA, "case input/provenance/expectation drift"
    return {case["case_id"]: case for case in cases}


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    attempts = []

    def forbidden(*args, **kwargs):
        attempts.append("external_io")
        raise AssertionError("valuation contract forbids network and DB")

    for name in ("connect", "connect_ex"):
        monkeypatch.setattr(socket.socket, name, forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    yield
    assert not attempts


@pytest.fixture(scope="module")
def evidence_dir(tmp_path_factory):
    configured = os.environ.get("VALUATION_LIMITATION_EVIDENCE_DIR")
    path = Path(configured) if configured else tmp_path_factory.mktemp("valuation-limitation")
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture(scope="module")
def cases(evidence_dir):
    cases = frozen_cases(build_cases())
    with (evidence_dir / "manifest.json").open("x", encoding="utf-8") as stream:
        json.dump({"caseset_sha256": CASESET_SHA, "case_count": CASE_COUNT,
                   "validator_sha256": hashlib.sha256((ROOT / "review_src/core/line_model_validation.py").read_bytes()).hexdigest(),
                   "cases": list(cases.values())}, stream, ensure_ascii=False, indent=2)
    return cases


@pytest.mark.parametrize("case_id", [case["case_id"] for case in build_cases()])
def test_valuation_limitation_contract(case_id, cases, evidence_dir):
    # Import only the pure validator, after tripwires are active.
    from core.line_model_validation import deterministic_limitation_placeholder_repair, validate_model_analysis_v2
    case = deepcopy(cases[case_id])
    packet, output = case["packet"], case["output"]
    raw = validate_model_analysis_v2(output, packet)
    repaired, codes = deterministic_limitation_placeholder_repair(output, packet)
    after = validate_model_analysis_v2(repaired, packet)
    observed = {"case": case, "raw": asdict(raw), "after_repair": asdict(after),
                "repair_codes": list(codes), "repaired_output": repaired, "live_model_calls": 0}
    with (evidence_dir / (case_id + ".json")).open("x", encoding="utf-8") as stream:
        json.dump(observed, stream, ensure_ascii=False, indent=2)
    for result in (raw, after):
        assert list(result.reason_codes) == case["expected_reasons"], observed
        assert result.passed is (case["expected_reasons"] == list(PASS))
        assert list(result.rendered_blocks) == case["expected_rendered"]
        assert result.analysis == output
    assert list(codes) == case["expected_repair_codes"]
    assert repaired == output
    assert digest({"packet": packet, "output": output}) == case["literal_input_sha256"]


@pytest.mark.parametrize("change", ("missing", "duplicate", "id", "text", "packet", "expected", "provenance"))
def test_frozen_valuation_cases_reject_drift(change):
    cases = build_cases()
    if change == "missing":
        cases.pop()
    elif change == "duplicate":
        cases[1] = deepcopy(cases[0])
    elif change == "id":
        cases[0]["case_id"] = "unknown"
    elif change == "text":
        cases[0]["output"]["missing_data"] = ["different"]
    elif change == "packet":
        cases[0]["packet"]["facts"][0]["value"] = "different"
    elif change == "expected":
        cases[0]["expected_reasons"] = [U]
    else:
        cases[0]["provenance"]["attempt"] = 2
    with pytest.raises(AssertionError, match="drift"):
        frozen_cases(cases)
