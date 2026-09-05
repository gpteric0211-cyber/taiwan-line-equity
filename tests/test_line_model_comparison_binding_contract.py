"""Design-v1.2 binding contracts: frozen manual expectations, real validator.

Historical/synthetic evidence only. No candidate parser or service imports.
The 181 previously manual controls retain their complete source records; only
explicit old-path precedence overrides below change their integration verdict.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import sqlite3

import pytest

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "logs/line_model_shadow"
PRIOR = LOG / "comparison_binding_v1_2_impact_20260831"
DROOT = LOG / "phase2_semantic_followup_20260831"
CASE_COUNT = 213
CASESET_SHA = "23baa447b5ba5485d401e4d213d0935461b9f465b1502c4f5ea9ab48d0e24f6f"
PASS = ["pass"]
MISSING = "claim_operand_binding_missing"
AMBIG = "claim_operand_binding_ambiguous"
MISMATCH = "claim_field_label_mismatch"
UNKNOWN = "claim_field_label_unverifiable"
FALSE = "comparison_direction_contradicts_evidence"
COMP = "comparison_operands_not_comparable"
STRUCT = "comparison_structure_unverifiable"
PH = re.compile(r"\{\{(F[0-9]{3,})\}\}")

# Design section 6 step 0: these *existing* guards run before the new grammar.
# Manually traced to validate_model_analysis_v2; never copied from parser output.
OLD_PRIORITY = {
    **{key: ["comparison_without_two_numeric_placeholders"] for key in (
        "evidence_only_disallowed", "priority_unknown_left_missing_right",
        "priority_independent_unknown_retained", "priority_missing_unknown_no_p")},
    **{key: ["placeholder_without_matching_evidence", "unresolved_placeholder"] for key in (
        "position_group_missing_before_false", "range_evidence_not_cited", "cost_missing_same_block")},
    # Missing F056 also leaves one numeric placeholder: preserve that old code.
    "cost_missing_field": ["unknown_evidence_id", "comparison_without_two_numeric_placeholders", "placeholder_without_matching_evidence", "unresolved_placeholder"],
    "cost_invalid_scope": ["comparison_without_two_numeric_placeholders", "ineligible_numeric_placeholder", "unresolved_placeholder"],
    **{key: ["ineligible_numeric_placeholder", "unresolved_placeholder"] for key in (
        "cost_invalid_quality", "cost_invalid_authority")},
}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def read_source(path, expected_sha=None):
    raw = path.read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    if expected_sha:
        assert actual == expected_sha, "source drift: " + path.name
    return json.loads(raw), {"path": path.relative_to(ROOT).as_posix(), "sha256": actual}


def expected_render(output, packet):
    """Fixed public rendering contract, no production renderer/semantic oracle."""
    units = {"TWD": " 元", "shares": " 股", "lots": " 張", "percent": "%", "ratio": " 倍",
             "point": " 點", "points": " 點", "index": "", "count": "", "": ""}
    values = {}
    for f in packet["facts"]:
        unit = str(f.get("unit") or "")
        value = re.sub(r"(?<=\d)\.0+$", "", str(f.get("value")))
        values[f["fact_id"]] = ("推估 " if f.get("quality") == "estimated" else "") + value + units.get(unit, " " + unit)
    return [PH.sub(lambda m: values[m[1]], b["text_template"].strip()) for b in output["explanation_blocks"]]


def build_cases():
    cases = []
    base, base_source = read_source(DROOT / "D04_true_comparison_control.json")

    def add(cid, packet, output, reasons, provenance, *, after=None, repairs=None,
            rendered_after=None, original_record=None):
        after = deepcopy(reasons if after is None else after)
        cases.append({"case_id": cid, "packet": deepcopy(packet), "output": deepcopy(output),
                      "expected_raw": list(reasons), "expected_after_repair": after,
                      "expected_repair_codes": repairs or [],
                      "expected_rendered_raw": expected_render(output, packet) if reasons == PASS else [],
                      "expected_rendered_after_repair": (rendered_after if rendered_after is not None else
                                                         expected_render(output, packet) if after == PASS else []),
                      "provenance": provenance, "original_manual_record": original_record})

    for filename, sha, count in (
        ("tool_control_inputs.json", "1d49fa239ed80c505e8f4427259fd93cc1b4e557d63f5476bb08bbb2fe33e0a1", 167),
        ("supplemental_control_inputs.json", "eb86426fcc581a9afa54dea20a97d9b636e9d7bcb23c462c72972253c4567a84", 14),
    ):
        data, source = read_source(PRIOR / filename, sha)
        assert data["count"] == count and len(data["cases"]) == count
        # The older supplemental artifact used JSON's default separators.
        content_sha = (digest(data["cases"]) if count == 167 else hashlib.sha256(
            json.dumps(data["cases"], ensure_ascii=False, sort_keys=True).encode()).hexdigest())
        assert content_sha == data["sha"], "manual source contents drift"
        for old in data["cases"]:
            cid = old["id"]
            provenance = {"manual_source": source, "selector": cid,
                          "basis": "design-v1.2 sections4-7; prior manually frozen expectations; old path first"}
            if cid.startswith("D0"):
                original, original_source = read_source(DROOT / (cid + ".json"))
                packet, output = original["derived_packet"], original["derived_output"]
                assert old["text"] == output["explanation_blocks"][0]["text_template"]
                assert old["facts"] == packet["facts"]
                provenance["original_diagnostic"] = original_source
            else:
                packet = deepcopy(base["derived_packet"])
                packet["request"].update(depth="focused", scopes=[], analysis_cutoff=old.get("analysis_cutoff", packet["request"]["analysis_cutoff"]))
                packet["facts"] = deepcopy(old["facts"])
                output = {"contract_version": "model-analysis-v2", "explanation_blocks": [{
                    "block_type": old.get("block_type", "fact"), "text_template": old["text"],
                    "evidence_ids": deepcopy(old["cited"]), "uncertainty": "high", "conditions": []}],
                    "missing_data": [], "research_limitations": [], "used_event_ids": []}
                provenance["projection"] = "one unchanged manual-control text/facts/cited; focused; no new model output"
            reasons = OLD_PRIORITY.get(cid, old["expected_reasons"] or PASS)
            if cid in OLD_PRIORITY:
                provenance["old_path_override"] = {"prior_grammar_only": old["expected_reasons"], "integration": reasons}
            add(cid, packet, output, reasons, provenance, original_record=old)

    def derivative(cid, template, reasons=PASS, *, facts=None, evidence=None, base_id="D04_true_comparison_control",
                   extra_blocks=(), rename=None):
        original, source = read_source(DROOT / (base_id + ".json"))
        packet, output = deepcopy(original["derived_packet"]), deepcopy(original["derived_output"])
        block = output["explanation_blocks"][0]
        block["text_template"] = template
        block["evidence_ids"] = list(evidence if evidence is not None else dict.fromkeys(PH.findall(template)))
        for f in packet["facts"]:
            f.update((facts or {}).get(f["fact_id"], {}))
        if rename:
            for f in packet["facts"]:
                f["fact_id"] = rename.get(f["fact_id"], f["fact_id"])
            block["text_template"] = PH.sub(lambda m: "{{" + rename.get(m[1], m[1]) + "}}", template)
            block["evidence_ids"] = [rename.get(i, i) for i in block["evidence_ids"]]
        output["explanation_blocks"].extend(deepcopy(extra_blocks))
        add(cid, packet, output, reasons, {"kind": "synthetic_derivative", "source": source,
            "base_id": base_id, "mutations": {"template": template, "facts": facts, "evidence": evidence,
                                              "extra_blocks": list(extra_blocks), "rename": rename},
            "expectation_basis": "manually specified design-v1.2 family controls, before validator execution"})

    d1, _ = read_source(DROOT / "D01_current_comparison_missing_rhs.json")
    d1block = d1["derived_output"]["explanation_blocks"][0]
    d1text, d1ids = d1block["text_template"], d1block["evidence_ids"]
    derivative("D01_rhs_restored", d1text.replace("高於布林中軌", "高於布林中軌{{F010}}"), base_id="D01_current_comparison_missing_rhs")
    derivative("D01_rhs_only_evidence", d1text, [MISSING], evidence=[*d1ids, "F010"], base_id="D01_current_comparison_missing_rhs")
    derivative("D01_rhs_only_other_block", d1text, [MISSING], evidence=d1ids, base_id="D01_current_comparison_missing_rhs",
               extra_blocks=[{"block_type": "fact", "text_template": "布林中軌{{F010}}。", "evidence_ids": ["F010"], "uncertainty": "low", "conditions": []}])
    derivative("D01_unrelated_numbers_no_substitute", d1text + "本益比{{F033}}。", [MISSING], base_id="D01_current_comparison_missing_rhs")
    pair = "收盤價{{F001}}高於布林中軌{{F010}}；DIF{{F019}}高於Signal{{F021}}。"
    derivative("D01_two_complete_pairs", pair)
    derivative("D01_second_pair_missing_rhs", pair.replace("{{F021}}", ""), [MISSING])
    three = "本益比{{F033}}，股價淨值比{{F032}}，殖利率{{F031}}。"
    derivative("D03_three_labels", three)
    derivative("D03_swapped_even_equal_values", three.replace("F033", "TEMP").replace("F032", "F033").replace("TEMP", "F032"),
               [MISMATCH], facts={"F032": {"value": "28.05"}})
    derivative("D03_reindexed_fields_unchanged", three, rename={"F033": "F933", "F032": "F932", "F031": "F931"})
    derivative("D03_unknown_label", "神奇比率{{F033}}。", [UNKNOWN])
    derivative("D03_synonym_control", "市盈率{{F033}}，市帳比{{F032}}，股利殖利率{{F031}}。")
    derivative("D03_parallel_missing_slot", "本益比與股價淨值比分別為{{F033}}。", [MISSING])
    derivative("D05_one_ma_false", base["derived_output"]["explanation_blocks"][0]["text_template"], [FALSE], facts={"F023": {"value": "2500"}})
    for operator, expected in (("高於", [FALSE]), ("低於", [FALSE]), ("等於", PASS), ("不低於", PASS), ("不高於", PASS)):
        derivative("D05_equal_" + operator, "收盤價{{F001}}" + operator + "布林中軌{{F010}}。", expected, facts={"F010": {"value": "2420"}})
    derivative("D05_not_above_false", "收盤價{{F001}}不高於布林中軌{{F010}}。", [FALSE])
    derivative("D05_negative_macd", "DIF{{F019}}低於Signal{{F021}}。", facts={"F019": {"value": "-2"}, "F021": {"value": "-1"}})
    derivative("D05_zero_macd", "DIF{{F019}}等於Signal{{F021}}。", facts={"F019": {"value": "0"}, "F021": {"value": "0"}})
    for name, value in (("nan", "NaN"), ("infinity", "Infinity"), ("bool", True)):
        derivative("D05_nonfinite_" + name, "收盤價{{F001}}高於均線{{F025}}。", [COMP], facts={"F001": {"value": value}})
    derivative("bare_shares_vs_lots", "{{F006}}低於{{F030}}。", [COMP], facts={"F030": {"unit": "lots"}})
    derivative("bare_equal_value_different_domain", "{{F001}}等於{{F033}}。", [COMP], facts={"F033": {"value": "2420"}})

    compatibility, source = read_source(LOG / "c21_delivery_20260831_r2/cases_all_after.json",
        "346685b1eb2d07c645b14bd1b544c991f2a6344a99d06d030972a3f77d24e65b")
    for old in compatibility:
        if old["case_id"] in ("C01-A17", "C02-comparison", "C21-PE-original"):
            add(old["case_id"], old["packet"], old["output"], old["expected_raw"], {"source": source, "original_record": old},
                after=old["expected_after_repair"], repairs=old["expected_repair_codes"], rendered_after=old["expected_rendered_after_repair"])
    s1r, source = read_source(LOG / "c21_delivery_20260831_r2/s1r_cases_after.json",
        "192107280d63b4c2e10169d3e3037ab25b0ea619c83ef1890036b3cea49e05b4")
    old = next(c for c in s1r if c["case_id"] == "grounded_comparison")
    add("S1R_grounded_comparison", old["packet"], old["output"], PASS, {"source": source, "original_record": old})
    valuation, source = read_source(LOG / "comparison_binding_fix_20260901/before_valuation/manifest.json")
    for old in valuation["cases"]:
        if old["case_id"] in ("positive_prompt_text_template", "tail_bullish_text_template"):
            add("valuation_" + old["case_id"], old["packet"], old["output"], old["expected_reasons"], {"source": source, "original_record": old})
    for case in cases:
        case["literal_input_sha256"] = digest({"packet": case["packet"], "output": case["output"]})
    return cases


def frozen_cases(cases):
    ids = [c["case_id"] for c in cases]
    assert len(cases) == CASE_COUNT and len(set(ids)) == CASE_COUNT, "case count/id drift"
    assert digest(cases) == CASESET_SHA, "case input/provenance/expected drift"
    return {c["case_id"]: c for c in cases}


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    attempts = []
    def forbidden(*args, **kwargs):
        attempts.append("DB/network")
        raise AssertionError("offline binding contract forbids DB/network")
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    for name in ("connect", "connect_ex"):
        monkeypatch.setattr(socket.socket, name, forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    yield
    assert not attempts


@pytest.fixture(scope="module")
def evidence_dir(tmp_path_factory):
    value = os.environ.get("COMPARISON_BINDING_EVIDENCE_DIR")
    path = Path(value) if value else tmp_path_factory.mktemp("comparison-binding")
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture(scope="module")
def cases(evidence_dir):
    result = frozen_cases(build_cases())
    with (evidence_dir / "manifest.json").open("x", encoding="utf-8") as stream:
        json.dump({"count": CASE_COUNT, "caseset_sha": CASESET_SHA, "cases": list(result.values())},
                  stream, ensure_ascii=False, indent=2)
    return result


@pytest.mark.parametrize("case_id", [c["case_id"] for c in build_cases()])
def test_comparison_binding_contract(case_id, cases, evidence_dir):
    from core.line_model_validation import validate_model_analysis_v2, deterministic_limitation_placeholder_repair
    case = deepcopy(cases[case_id])
    packet, output = case["packet"], case["output"]
    raw = validate_model_analysis_v2(output, packet)
    repaired, codes = deterministic_limitation_placeholder_repair(output, packet)
    after = validate_model_analysis_v2(repaired, packet)
    observed = {"case": case, "raw": asdict(raw), "after_repair": asdict(after),
                "repair_codes": list(codes), "repaired_output": repaired, "live_calls": 0}
    # Case IDs may contain unicode; only derive a fixed-length filesystem-safe name.
    with (evidence_dir / (hashlib.sha256(case_id.encode()).hexdigest() + ".json")).open("x", encoding="utf-8") as stream:
        json.dump(observed, stream, ensure_ascii=False, indent=2)
    assert list(codes) == case["expected_repair_codes"], observed
    for name, result in (("raw", raw), ("after_repair", after)):
        expected = case["expected_" + name]
        assert list(result.reason_codes) == expected, observed
        assert result.passed is (expected == PASS), observed
        assert list(result.rendered_blocks) == case["expected_rendered_" + name], observed
    assert raw.analysis == output
    assert after.analysis == repaired
    if not codes:
        assert repaired == output
    assert digest({"packet": packet, "output": output}) == case["literal_input_sha256"]


@pytest.mark.parametrize("change", ("missing", "duplicate", "id", "text", "packet", "expected", "provenance"))
def test_frozen_binding_cases_reject_drift(change):
    rows = build_cases()
    if change == "missing":
        rows.pop()
    elif change == "duplicate":
        rows[1] = deepcopy(rows[0])
    elif change == "id":
        rows[0]["case_id"] = "unknown"
    elif change == "text":
        rows[0]["output"]["explanation_blocks"][0]["text_template"] = "changed"
    elif change == "packet":
        rows[0]["packet"]["facts"][0]["value"] = "changed"
    elif change == "expected":
        rows[0]["expected_raw"] = [MISSING]
    else:
        rows[0]["provenance"]["selector"] = "changed"
    with pytest.raises(AssertionError, match="drift"):
        frozen_cases(rows)


# Separate additive boundary contract for the approved date-scalar split.  It
# intentionally does not rewrite the frozen 213 comparison cases above.
DATE_CASE_COUNT = 9
DATE_CASESET_SHA = "a8879c2a8c5e75384cabac0d85192f319ecba436fce3436e53c6f68c747c8c60"


def build_date_boundary_cases():
    def fact(fid="F101", *, field="trade_date", value="2026-08-28", scopes=None, **changes):
        row = {
            "fact_id": fid, "field": field, "value": value, "unit": "", "period": "daily",
            "as_of": "2026-08-28", "authority_tier": "canonical_db", "quality": "ok",
            "use_scope": list(scopes or ["date_claim"]),
        }
        row.update(changes)
        return row

    def case(cid, text, facts, evidence, reasons):
        packet = {"request": {"depth": "focused"}, "facts": deepcopy(facts), "events": []}
        output = {"contract_version": "model-analysis-v2", "explanation_blocks": [{
            "block_type": "fact", "text_template": text, "evidence_ids": list(evidence),
            "uncertainty": "low", "conditions": [],
        }], "missing_data": [], "used_event_ids": [], "research_limitations": []}
        rendered = []
        if reasons == PASS:
            values = {row["fact_id"]: str(row["value"]) for row in facts}
            rendered = [PH.sub(lambda match: values[match[1]], text)]
        return {"case_id": cid, "packet": packet, "output": output,
                "expected_reasons": list(reasons), "expected_rendered": rendered,
                "expectation_basis": "approved finite date-statement/numeric-grammar split"}

    price = {
        "fact_id": "F001", "domain": "official_ohlcv", "field": "close", "value": "2420",
        "unit": "TWD", "currency": "TWD", "period": "daily", "as_of": "2026-08-28",
        "authority_tier": "canonical_db", "quality": "ok", "use_scope": ["numeric_claim"],
    }
    middle = {**price, "fact_id": "F010", "domain": "technical",
              "field": "bollinger.middle", "value": "2389.25"}
    sparse = fact("F002", field=None, unit=None, period=None, as_of=None)
    typed = fact()
    return [
        case("date_sparse_legacy_positive", "資料日期為 {{F002}}。", [sparse], ["F002"], PASS),
        case("date_typed_positive", "資料日期為{{F101}}。", [typed], ["F101"], PASS),
        case("date_wrong_field_not_exempt", "資料日期為{{F101}}。",
             [fact(field="close")], ["F101"], [UNKNOWN]),
        case("date_numeric_scope_not_exempt", "資料日期為{{F101}}。",
             [fact(field="pe_ratio", scopes=["numeric_claim"])], ["F101"], [UNKNOWN]),
        case("financial_label_date_fact_not_exempt", "本益比{{F101}}。",
             [typed], ["F101"], [MISMATCH]),
        case("invalid_date_value_not_exempt", "資料日期為{{F101}}。",
             [fact(value="not-a-date", as_of=None)], ["F101"], [UNKNOWN]),
        case("duplicate_date_fact_not_exempt", "資料日期為{{F101}}。",
             [typed, {**typed}], ["F101"], [AMBIG]),
        case("date_then_false_comparison_still_rejected",
             "資料日期為{{F101}}；收盤價{{F001}}低於布林中軌{{F010}}。",
             [typed, price, middle], ["F101", "F001", "F010"], [FALSE]),
        case("date_then_missing_operand_still_rejected",
             "資料日期為{{F101}}；收盤價{{F001}}高於布林中軌。",
             [typed, price, middle], ["F101", "F001"],
             ["comparison_without_two_numeric_placeholders"]),
    ]


def frozen_date_boundary_cases(rows):
    ids = [row["case_id"] for row in rows]
    assert len(rows) == DATE_CASE_COUNT and len(set(ids)) == DATE_CASE_COUNT, "date case count/id drift"
    assert digest(rows) == DATE_CASESET_SHA, "date case content/expectation drift"
    return {row["case_id"]: row for row in rows}


@pytest.mark.parametrize("case_id", [row["case_id"] for row in build_date_boundary_cases()])
def test_date_statement_numeric_grammar_boundary(case_id):
    from core.line_model_validation import deterministic_limitation_placeholder_repair, validate_model_analysis_v2
    original = frozen_date_boundary_cases(build_date_boundary_cases())[case_id]
    packet, output = deepcopy(original["packet"]), deepcopy(original["output"])
    raw = validate_model_analysis_v2(output, packet)
    repaired, repair_codes = deterministic_limitation_placeholder_repair(output, packet)
    after = validate_model_analysis_v2(repaired, packet)
    observed = {"case_id": case_id, "raw": asdict(raw), "after": asdict(after),
                "repair_codes": list(repair_codes)}
    assert list(repair_codes) == [], observed
    assert repaired == output
    for result in (raw, after):
        assert list(result.reason_codes) == original["expected_reasons"], observed
        assert result.passed is (original["expected_reasons"] == PASS), observed
        assert list(result.rendered_blocks) == original["expected_rendered"], observed
    assert original["packet"] == packet and original["output"] == output


@pytest.mark.parametrize("change", ("missing", "duplicate", "text", "expected"))
def test_frozen_date_boundary_cases_reject_drift(change):
    rows = build_date_boundary_cases()
    if change == "missing":
        rows.pop()
    elif change == "duplicate":
        rows[1] = deepcopy(rows[0])
    elif change == "text":
        rows[0]["output"]["explanation_blocks"][0]["text_template"] = "changed"
    else:
        rows[0]["expected_reasons"] = [UNKNOWN]
    with pytest.raises(AssertionError, match="drift"):
        frozen_date_boundary_cases(rows)
