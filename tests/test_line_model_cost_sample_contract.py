"""Offline RED contract: research_limitations cost-sample prose only.

No candidate guard or replacement validator lives here. A10 facts are read from
the immutable corpus; the small surrounding packet/output comes from the existing
audit builder. Every case is a SYNTHETIC DERIVATIVE, never a repaired historical
answer. Golden expectations describe the approved narrow contract, not today's
blanket rejection. Green negative cases alone do not prove the future matcher.

No service/model/DB imports. ``service_final_projection`` only reproduces the
existing selection rule; it is not a service integration or live quality test.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import socket
import sqlite3
import tempfile
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path

import pytest

from scripts import audit_line_model_validation_boundaries as audit

ROOT = Path(__file__).resolve().parents[1]
CORPUS = "logs/line_model_shadow/final_phase_a_current_source_v2_20260830.jsonl"
CORPUS_SHA = "ab86c7e371a506f9d0f46084da04ac6f36f0c40d1c6bfd53136e5bedfe563125"
AUDIT_SOURCE = "scripts/audit_line_model_validation_boundaries.py"
AUDIT_SHA = "774ffdb59fc0b30721c76aeb07c761460a75440fd34e588f422bfb4a000bbde0"
BASE_ID = "honest_limitation:text_template"
BASE_SHA = "99be4a9e923490f54392b29df1793af746be7b8438808c49bdacc638d0a046fb"
COST_TEXT = "本包外資近期增量成本估算樣本不足。"
U = "unverifiable_limitation_claim"
A = "absence_only_evidence_cannot_support_claim"
N = "ungrounded_numeric_or_date_claim"
S = "invalid_limitation_schema"
PASS = ("pass",)

# Frozen before the first validator/test execution. No regeneration mode.
CASE_COUNT = 76
CASESET_SHA = "3d174e4331325f10c2ec50edfad0f11867dfc275ab3b749982d385d1a0b905bb"


def json_safe(value):
    """Lossless tagged evidence for in-memory nonfinite robustness cases.

    Tags are used ONLY for hashing/evidence, never passed to the validator. These
    three floats are not valid JSON wire packets and prove no transport behavior.
    """
    if isinstance(value, float) and not math.isfinite(value):
        return {"__python_nonfinite_float__": repr(value)}
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_safe(item) for item in value]
    return value


def digest(value):
    return hashlib.sha256(json.dumps(
        json_safe(value), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


def source_fixture():
    corpus_bytes = (ROOT / CORPUS).read_bytes()
    assert hashlib.sha256(corpus_bytes).hexdigest() == CORPUS_SHA, "corpus drift"
    assert hashlib.sha256((ROOT / AUDIT_SOURCE).read_bytes()).hexdigest() == AUDIT_SHA, "audit drift"
    records = [json.loads(line) for line in corpus_bytes.splitlines() if line.strip()]
    assert len(records) == 20, "A20 record count drift"
    original = records[9]
    assert original["attempt_index"] == 10, "A10 provenance drift"
    original_packet = original["body"]["result"]["compacted_packet"]
    pair = [deepcopy(f) for f in original_packet["facts"] if f["fact_id"] in {"F056", "F057"}]
    assert [f["fact_id"] for f in pair] == ["F056", "F057"]
    assert [f["field"] for f in pair] == [
        "canonical_costs.foreign_estimated.required_days",
        "canonical_costs.foreign_estimated.sample_days",
    ]
    assert [f["value"] for f in pair] == [60, 18]
    bases = [c for c in audit.build_cases() if c["case_id"] == BASE_ID]
    assert len(bases) == 1 and digest(bases[0]) == BASE_SHA, "base case drift"
    base = deepcopy(bases[0])
    packet, output = base["packet"], base["output"]
    packet["request"] = {"depth": "focused", "scopes": ["institutional"],
                         "analysis_cutoff": original_packet["request"]["analysis_cutoff"]}
    packet["facts"].extend(pair)
    output["research_limitations"] = [COST_TEXT]
    return packet, output, {
        "kind": "synthetic_derivative_not_historical_output",
        "corpus": {"path": CORPUS, "sha256": CORPUS_SHA, "line": 10, "attempt": 10},
        "base": {"path": AUDIT_SOURCE, "sha256": AUDIT_SHA,
                 "case_id": BASE_ID, "case_sha256": BASE_SHA},
        "base_transformations": [
            "Retain audit base F101 and three safe limitation blocks; discard no A10 facts in-place.",
            "Append byte-content-equivalent A10 F056/F057 into an independent synthetic packet.",
            "Set focused/institutional request, retaining A10 analysis_cutoff.",
            "Set top-level research_limitations to the approved nonnumeric cost sentence.",
        ],
        "historical_pair": pair,
        "historical_request": original_packet["request"],
        "scope": "single-stock; research_limitations only; no numeric rendering capability",
    }


def build_cases():
    packet, output, provenance = source_fixture()
    cases = []

    def add(case_id, edits=(), reasons=(U,)):
        p, o = deepcopy(packet), deepcopy(output)
        # Declarative exact-path edits are provenance, not an implementation of
        # evidence eligibility. No predicate here chooses the expected verdict.
        for operation, path, value in edits:
            target = {"packet": p, "output": o}
            for key in path[:-1]:
                target = target[key]
            if operation == "set":
                target[path[-1]] = deepcopy(value)
            elif operation == "delete":
                del target[path[-1]]
            elif operation == "append":
                target[path[-1]].append(deepcopy(value))
            else:
                raise AssertionError(f"unknown fixture edit {operation}")
        rendered = [b["text_template"] for b in o["explanation_blocks"]] if reasons == PASS else []
        verdict = {"passed": reasons == PASS, "reason_codes": list(reasons), "rendered_blocks": rendered}
        cases.append({
            "case_id": case_id, "packet": p, "output": o,
            "provenance": {**deepcopy(provenance), "case_edits": deepcopy(edits)},
            "expected_raw": deepcopy(verdict), "expected_after_repair": deepcopy(verdict),
            "expected_service_final_projection": deepcopy(verdict),
            "expected_repair_codes": [], "expected_after_repair_output": deepcopy(o),
            "expected_service_repair_attempted": False, "expected_service_used_repair": False,
            "literal_input_sha256": digest({"packet": p, "output": o}),
        })

    def fact(index, key, value):
        return ("set", ("packet", "facts", index, key), value)

    def request(key, value):
        return ("set", ("packet", "request", key), value)

    def text(value):
        return ("set", ("output", "research_limitations"), [value])

    add("positive_a10_18_lt_60", reasons=PASS)
    add("positive_other_values_and_ids", (fact(1, "value", 45), fact(2, "value", 17),
        fact(1, "fact_id", "F201"), fact(2, "fact_id", "F202")), PASS)
    add("positive_zero_lt_60", (fact(2, "value", 0),), PASS)
    add("control_honest_no_cost_claim", (text("資料不足，無法判斷支撐壓力。"),), PASS)

    for name, index in (("required_only", 2), ("sample_only", 1)):
        add(name, (("delete", ("packet", "facts", index), None),))
    for index, side in ((1, "required"), (2, "sample")):
        for key in ("field", "domain", "trade_date", "as_of"):
            add(f"missing_{side}_{key}", (("delete", ("packet", "facts", index, key), None),))
    add("duplicate_fact_id", (fact(2, "fact_id", "F056"),))
    add("duplicate_identical_row", (("append", ("packet", "facts"), packet["facts"][2]),))
    duplicate = {**packet["facts"][2], "fact_id": "F202"}
    add("duplicate_field_different_id", (("append", ("packet", "facts"), duplicate),))
    add("conflicting_same_field", (("append", ("packet", "facts"), {**duplicate, "value": 70}),))
    other_pair = [{**f, "fact_id": f["fact_id"] + "X", "trade_date": "2026-08-27", "as_of": "2026-08-27"}
                  for f in packet["facts"][1:]]
    add("multiple_dated_pairs", tuple(("append", ("packet", "facts"), f) for f in other_pair))

    for name, index, value in (
        ("sample_equal_required", 2, 60), ("sample_exceeds_required", 2, 61),
        ("required_zero", 1, 0), ("required_negative", 1, -1), ("sample_negative", 2, -1),
    ):
        add(name, (fact(index, "value", value),))
    for index, side in ((1, "required"), (2, "sample")):
        for name, value in (("bool", True), ("fractional", 18.5), ("null", None),
                            ("nan", float("nan")), ("infinity", float("inf")),
                            ("negative_infinity", float("-inf"))):
            add(f"{side}_{name}", (fact(index, "value", value),))
        for quality in ("stale", "unavailable", "estimated"):
            add(f"{side}_{quality}", (fact(index, "quality", quality),))
        add(f"{side}_noncanonical", (fact(index, "authority_tier", "external_news"),))
        for name, scope in (("limitation_only", ["limitation"]), ("no_scope", []),
                            ("no_numeric", ["explanation"]), ("no_explanation", ["numeric_claim"])):
            add(f"{side}_{name}", (fact(index, "use_scope", scope),))

    for key, value in (("trade_date", "2026-08-27"), ("as_of", "2026-08-27"),
                       ("period", "weekly"), ("unit", "shares")):
        add(f"mismatched_{key}", (fact(2, key, value),))
    add("both_wrong_unit", (fact(1, "unit", "days"), fact(2, "unit", "days")))
    add("both_wrong_domain", (fact(1, "domain", "technical"), fact(2, "domain", "technical")))
    add("institutional_not_requested", (request("scopes", ["technical"]),))
    add("missing_cutoff", (("delete", ("packet", "request", "analysis_cutoff"), None),))
    add("pair_later_than_cutoff", (request("analysis_cutoff", "2026-08-27"),))
    for key in ("trade_date", "as_of"):
        add(f"malformed_{key}", (fact(1, key, "not-a-date"), fact(2, key, "not-a-date")))
    for name, prefix in (("investmenttrust_pair", "canonical_costs.trust_estimated"),
                         ("unrelated_metric_pair", "unrelated_metric")):
        add(name, (fact(1, "field", prefix + ".required_days"), fact(2, "field", prefix + ".sample_days")))
    for name, phrase in (("investmenttrust_claim", "本包投信近期增量成本估算樣本不足。"),
                         ("total_holdings_claim", "本包外資總持股資料不足。"),
                         ("net_flows_claim", "本包外資淨買賣超資料不足。")):
        add(name, (text(phrase),))
    # Once the legal prefix is recognized, only the unsafe tail should remain
    # in reasons. Today's extra U is a diagnostic-contract red, NOT an unsafe pass.
    add("tail_bullish", (text(COST_TEXT + "均線偏多。"),), (A,))
    add("tail_bearish", (text(COST_TEXT + "價格趨勢已轉弱。"),), (A,))
    add("tail_certainty", (text(COST_TEXT + "必定轉弱。"),))
    add("conditions_cannot_supply_sample_fact", (
        ("delete", ("packet", "facts", 2), None),
        ("set", ("output", "explanation_blocks", 0, "conditions"), [COST_TEXT]),
    ))
    for name, phrase in (("raw_number", "本包外資近期增量成本估算樣本只有18日。"),
                         ("raw_date", "本包外資近期增量成本估算截至2026-08-28樣本不足。"),
                         ("raw_quarter", "本包外資近期增量成本估算Q2樣本不足。")):
        add(name, (text(phrase),), (N,))
    add("schema_priority", (text({"not": "a string"}),), (S,))
    return cases


def frozen_cases(cases):
    ids = [case["case_id"] for case in cases]
    assert len(ids) == len(set(ids)) == CASE_COUNT, "case count/identity drift"
    assert digest(cases) == CASESET_SHA, "case content/expectation drift"
    return {case["case_id"]: case for case in cases}


@pytest.fixture(autouse=True)
def forbid_external_io(monkeypatch):
    attempts = []

    def forbidden(*args, **kwargs):
        attempts.append("external_io_attempt")
        raise AssertionError("cost-sample contract must not access network or DB")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    yield
    assert not attempts


@pytest.fixture(scope="module")
def evidence_directory(tmp_path_factory):
    configured = os.environ.get("COST_SAMPLE_EVIDENCE_DIR")
    if not configured:
        return tmp_path_factory.mktemp("cost-sample")
    parent = Path(configured)
    if not parent.is_absolute():
        parent = ROOT / parent
    parent.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="cost-", dir=parent))


def write_evidence(directory, name, row):
    with (directory / (name + ".json")).open("x", encoding="utf-8") as stream:
        json.dump(json_safe(row), stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


@pytest.fixture(scope="module")
def pinned_cases(evidence_directory):
    cases = frozen_cases(build_cases())
    write_evidence(evidence_directory, "manifest", {
        "cases": list(cases.values()), "caseset_sha256": CASESET_SHA,
        "individual_sha256": {key: digest(value) for key, value in cases.items()},
        "validator_sha256": hashlib.sha256((ROOT / "review_src/core/line_model_validation.py").read_bytes()).hexdigest(),
        "test_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    })
    return cases


def evaluate(packet, output):
    before = digest({"packet": packet, "output": output})
    observed = {}
    try:
        raw = audit.validate_model_analysis_v2(output, packet)
        observed["raw"] = asdict(raw)
        repaired, codes = audit.deterministic_limitation_placeholder_repair(output, packet)
        observed["repair_codes"] = list(codes)
        observed["after_repair_output"] = repaired
        after = audit.validate_model_analysis_v2(repaired if repaired is not None else output, packet)
        observed["after_repair"] = asdict(after)
        attempted = not raw.passed and repaired is not None and bool(codes)
        used = attempted and after.passed
        observed["service_repair_attempted"] = attempted
        observed["service_used_repair"] = used
        observed["service_final_projection"] = asdict(after if used else raw)
    except Exception as exc:
        observed["exception"] = {"type": type(exc).__name__, "message": str(exc)}
    observed["input_unchanged"] = before == digest({"packet": packet, "output": output})
    return observed


def verdict_errors(observed, expected):
    errors = []
    for key in ("passed", "reason_codes", "rendered_blocks"):
        actual, target = json_safe(observed[key]), expected[key]
        if type(actual) is not type(target) or actual != target:
            errors.append(f"{key}: observed={actual!r}; expected={target!r}")
    return errors


@pytest.mark.parametrize("case_id", [case["case_id"] for case in build_cases()])
def test_cost_sample_contract(case_id, pinned_cases, evidence_directory):
    case = deepcopy(pinned_cases[case_id])
    original_digest = digest(case)
    observed = evaluate(case["packet"], case["output"])
    errors = []
    if "exception" in observed:
        errors.append(f"exception is NOT semantic rejection: {observed['exception']}")
    else:
        for stage in ("raw", "after_repair", "service_final_projection"):
            errors.extend(f"{stage}: {error}" for error in verdict_errors(observed[stage], case["expected_" + stage]))
            if observed[stage]["passed"] and observed[stage]["analysis"] != case["output"]:
                errors.append(f"{stage}: passed analysis lost or changed requested text")
        for key in ("repair_codes", "after_repair_output", "service_repair_attempted", "service_used_repair"):
            if digest(observed[key]) != digest(case["expected_" + key]):
                errors.append(f"{key} drift: {observed[key]!r}")
    if not observed["input_unchanged"] or original_digest != digest(case):
        errors.append("validator/repair mutated input")
    write_evidence(evidence_directory, case_id, {
        "case": case, "observed": observed, "contract_errors": errors,
        "contract_passed": not errors, "real_model_calls": 0,
        "live_service_invoked": False, "load_or_quality_evidence": False,
    })
    assert not errors, f"{case_id}; evidence={evidence_directory / (case_id + '.json')}\n" + "\n".join(errors)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "id", "text", "packet", "expected", "provenance"])
def test_cost_sample_manifest_rejects_drift(mutation):
    cases = build_cases()
    if mutation == "missing":
        cases.pop()
    elif mutation == "duplicate":
        cases[1] = deepcopy(cases[0])
    elif mutation == "id":
        cases[0]["case_id"] = "not-the-frozen-case"
    elif mutation == "text":
        cases[0]["output"]["research_limitations"] = ["資料不足。"]
    elif mutation == "packet":
        cases[0]["packet"]["facts"][2]["value"] = 60
    elif mutation == "expected":
        cases[0]["expected_raw"]["reason_codes"] = [U]
    else:
        cases[0]["provenance"]["corpus"]["line"] = 11
    with pytest.raises(AssertionError, match="drift"):
        frozen_cases(cases)


@pytest.mark.parametrize("observed", [
    {"passed": False, "reason_codes": ["invalid_json"], "rendered_blocks": []},
    {"passed": False, "reason_codes": [U + "_typo"], "rendered_blocks": []},
    {"passed": False, "reason_codes": [U, "invalid_block_schema"], "rendered_blocks": []},
    {"passed": True, "reason_codes": [U], "rendered_blocks": []},
    {"passed": False, "reason_codes": [U], "rendered_blocks": ["未驗證的分析"]},
])
def test_cost_sample_verdict_rejects_false_green(observed):
    assert verdict_errors(observed, {"passed": False, "reason_codes": [U], "rendered_blocks": []})
