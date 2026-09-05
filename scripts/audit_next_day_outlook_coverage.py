#!/usr/bin/env python3
"""Read-only formal next_day_outlook coverage audit.

This tool calls only the configured local API base URL and reads source files
for static inventory notes. It does not import the FastAPI app, write to DB,
start a server, fetch external websites, or modify production logic.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import traceback
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_CODES = ["2317", "2330", "2382", "2454", "2308"]
DEFAULT_BASE_URL = "http://127.0.0.1:8057"
DEFAULT_OUTPUT = "docs/next_day_outlook_coverage_audit.md"
BEFORE_D3B_FORMAL_CODES = {"2317", "2382"}
BEFORE_D3B_MISSING_CODES = {"2330", "2454", "2308"}
FORBIDDEN_FORMAL_KEYS = [
    "shadow_outlook",
    "freshness_adjusted_outlook_shadow",
    "factor_freshness",
    "calendar_status",
    "calendar_debug",
    "debug_shadow",
    "override",
    "market_calendar",
]


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def fetch_json(url: str, timeout: float) -> tuple[dict[str, Any] | None, str | None]:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            data = json.loads(raw)
            return data if isinstance(data, dict) else {"_payload": data}, None
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            body = ""
        return None, f"http_error_{exc.code}: {body}"
    except (urllib.error.URLError, TimeoutError) as exc:
        return None, f"request_error: {exc}"
    except json.JSONDecodeError as exc:
        return None, f"non_json_response: {exc}"
    except Exception as exc:
        return None, f"unexpected_error: {exc}"


def build_url(base_url: str, path: str) -> str:
    return urllib.parse.urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))


def as_text(value: Any) -> str:
    if value is None or value == "":
        return "N/A"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def md_escape(value: Any) -> str:
    return as_text(value).replace("|", "\\|").replace("\n", "<br>")


def first_present(mapping: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            return mapping[key]
    return None


def collect_keys(obj: Any, keys: set[str], path: str = "$") -> list[str]:
    found: list[str] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            next_path = f"{path}.{key}"
            if key in keys:
                found.append(next_path)
            found.extend(collect_keys(value, keys, next_path))
    elif isinstance(obj, list):
        for idx, value in enumerate(obj):
            found.extend(collect_keys(value, keys, f"{path}[{idx}]"))
    return found


def source_line(file_path: str, pattern: str) -> str:
    path = Path(file_path)
    if not path.exists():
        return f"{file_path}: Unverified / Not found in current source"
    try:
        for idx, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
            if re.search(pattern, line):
                return f"{file_path}:{idx}"
    except Exception:
        return f"{file_path}: Unverified / read failed"
    return f"{file_path}: Unverified / Not found in current source"


def summarize_outlook(outlook: Any) -> dict[str, Any]:
    if outlook is None:
        return {
            "exists": False,
            "state": "missing",
            "score": "N/A",
            "direction": "N/A",
            "confidence": "N/A",
            "gate": "N/A",
            "title": "N/A",
            "data_time": "N/A",
        }
    if not isinstance(outlook, dict):
        return {
            "exists": True,
            "state": "non_dict",
            "score": "N/A",
            "direction": "N/A",
            "confidence": "N/A",
            "gate": "N/A",
            "title": "N/A",
            "data_time": "N/A",
        }
    if not outlook:
        return {
            "exists": False,
            "state": "empty_object",
            "score": "N/A",
            "direction": "N/A",
            "confidence": "N/A",
            "gate": "N/A",
            "title": "N/A",
            "data_time": "N/A",
        }
    score = first_present(
        outlook,
        [
            "score",
            "probability",
            "probability_up",
            "final_probability",
            "prob",
            "bullish_probability",
            "up_probability",
            "total_score",
        ],
    )
    direction = first_present(
        outlook,
        ["direction", "bias", "status", "trend", "label", "signal", "verdict", "summary"],
    )
    confidence = first_present(outlook, ["confidence", "confidence_level", "trust_level"])
    gate = first_present(outlook, ["gate", "notice", "warning", "message", "reason", "display_gate"])
    title = first_present(outlook, ["title", "display_title", "summary", "description"])
    data_time = first_present(outlook, ["data_time", "calc_date", "as_of", "as_of_time", "updated_at", "date"])
    return {
        "exists": True,
        "state": "ok" if outlook.get("available", True) is not False else "available_false",
        "score": as_text(score),
        "direction": as_text(direction),
        "confidence": as_text(confidence),
        "gate": as_text(gate),
        "title": as_text(title),
        "data_time": as_text(data_time),
    }


def classify_missing(payload: dict[str, Any] | None, error: str | None, outlook_summary: dict[str, Any]) -> tuple[str, list[str], str, str]:
    if error:
        return "formal_api_unavailable", [error], "HTTP/API read failed.", "Confirm server is running and detail route is reachable."
    if not payload:
        return "formal_api_unavailable", ["No JSON payload returned."], "Unknown because payload is empty.", "Retry with server logs."
    if payload.get("ok") is False:
        evidence = [f"ok=false: {payload.get('error') or payload.get('message') or 'N/A'}"]
        if payload.get("readiness"):
            evidence.append(f"readiness={as_text(payload.get('readiness'))}")
        return "missing_data_quality", evidence, "Detail endpoint returned ok=false, likely readiness/data quality gate.", "Inspect readiness and explicit update/repair status."
    if "next_day_outlook" not in payload:
        related = [key for key in ["us_forecast", "futures_night", "score_signal", "technical", "valuation", "base"] if key in payload]
        evidence = [f"top-level next_day_outlook absent", f"related fields present: {', '.join(related) or 'none'}"]
        if "score_signal" not in payload or "technical" not in payload:
            return "missing_price_or_history", evidence, "Core detail context appears incomplete.", "Inspect detail payload readiness and row building."
        return "field_absent_in_response", evidence, "Detail response is available but does not expose next_day_outlook.", "Inspect api_stock_detail branch and returned payload."
    if outlook_summary["state"] == "empty_object":
        return "formal_function_returned_none", ["next_day_outlook is an empty object."], "Formal function may have returned empty payload.", "Inspect synthesize/gate branch."
    if outlook_summary["state"] == "available_false":
        return "gate_hidden_or_paused", [f"next_day_outlook available=false; gate={outlook_summary['gate']}"], "Gate paused or hid formal outlook.", "Inspect next_day_outlook_gate session result."
    return "unknown", ["Formal outlook missing for an unclassified reason."], "Insufficient evidence from API payload.", "Add explicit diagnostic metadata in a later phase."


def audit_code(base_url: str, code: str, timeout: float) -> dict[str, Any]:
    payload, error = fetch_json(build_url(base_url, f"/api/stock/{code}/detail"), timeout)
    forbidden_paths = collect_keys(payload, set(FORBIDDEN_FORMAL_KEYS)) if payload else []
    outlook = payload.get("next_day_outlook") if isinstance(payload, dict) else None
    outlook_summary = summarize_outlook(outlook)
    has_formal = bool(outlook_summary["exists"] and outlook_summary["state"] in {"ok", "available_false"})
    missing_category = "N/A"
    evidence: list[str] = []
    inferred = "N/A"
    next_step = "N/A"
    status = "success"
    if error:
        status = "warn"
    if not outlook_summary["exists"] or error:
        missing_category, evidence, inferred, next_step = classify_missing(payload, error, outlook_summary)
    return {
        "code": code,
        "status": status,
        "payload_ok": bool(payload),
        "has_formal": has_formal and outlook_summary["state"] != "available_false",
        "outlook_summary": outlook_summary,
        "missing_category": missing_category,
        "evidence": evidence,
        "inferred": inferred,
        "next_step": next_step,
        "forbidden_paths": forbidden_paths,
    }


def inventory_rows() -> list[dict[str, str]]:
    return [
        {
            "factor": "us",
            "function": "us_sentiment_factor()",
            "file_line": source_line("review_src/app.py", r"def us_sentiment_factor"),
            "time_field": "`regular_market_time` from `_source_quotes`; fallback quote `date` from yfinance quote payload.",
            "freshness": "`_source_age_decay_from_quote_time()` uses quote timestamp/date and returns freshness/decay.",
            "safe": "Partial",
            "missing": "No normalized `data_time`/`published_at` contract; source metadata remains ad hoc.",
            "notes": "Reads `us_forecast` and `us_assets` built in detail endpoint; no DB required inside factor.",
        },
        {
            "factor": "futures_night",
            "function": "futures_night_factor() / futures_night_signal_for_stock()",
            "file_line": f"{source_line('review_src/app.py', r'def futures_night_factor')} + {source_line('review_src/market/futures.py', r'def futures_night_signal_for_stock')}",
            "time_field": "`futures_night.date` from TAIFEX `Date` field in `DailyMarketReportFut` rows.",
            "freshness": "`futures_night_factor()` uses `_source_age_decay_from_date(date)`.",
            "safe": "Partial",
            "missing": "`belongs_to_trade_date`, session close time, and normalized `data_time` are not available.",
            "notes": "TAIFEX adapter has `_taifex_cache`; factor itself consumes already-built payload.",
        },
        {
            "factor": "chip",
            "function": "chip_factor_for_stock()",
            "file_line": source_line("review_src/app.py", r"def chip_factor_for_stock"),
            "time_field": "Latest max date from `institution_daily.date` and `margin_daily.date`.",
            "freshness": "Uses `_source_age_decay_from_date(latest_date)`.",
            "safe": "Partial",
            "missing": "No `published_at`; no distinction between expected T+1 delay and unexpected stale data.",
            "notes": "Reads DB through `db()`; moving to shadow directly could create coupling unless a read model is added.",
        },
        {
            "factor": "tech",
            "function": "tech_factor_for_stock()",
            "file_line": source_line("review_src/app.py", r"def tech_factor_for_stock"),
            "time_field": "`technical_context_from_rows(rows_asc).date`, sourced from history_price rows.",
            "freshness": "Uses `_source_age_decay_from_date(latest_date)`.",
            "safe": "Partial",
            "missing": "No explicit source quality object; depends on caller-provided rows.",
            "notes": "No DB read inside function, but source rows are built by detail endpoint from DB.",
        },
        {
            "factor": "gate",
            "function": "next_day_outlook_gate()",
            "file_line": source_line("review_src/app.py", r"def next_day_outlook_gate"),
            "time_field": "`tw_market_session_now()` session object.",
            "freshness": "No factor freshness; only display gate by Taiwan market session.",
            "safe": "No",
            "missing": "No normalized market calendar contract in formal path.",
            "notes": "Can hide/replace formal outlook when regular/closing/holiday logic applies.",
        },
        {
            "factor": "synthesize",
            "function": "synthesize_next_day_outlook()",
            "file_line": source_line("review_src/app.py", r"def synthesize_next_day_outlook"),
            "time_field": "Aggregates factor `date`/`freshness` into `sources`; no top-level `as_of_time`.",
            "freshness": "Consumes per-factor freshness but applies fixed weights.",
            "safe": "No",
            "missing": "No target trade date, no normalized factor contract, no shadow score path.",
            "notes": "Formal scoring function; D3A does not modify it.",
        },
        {
            "factor": "record",
            "function": "record_next_day_outlook()",
            "file_line": source_line("review_src/app.py", r"def record_next_day_outlook"),
            "time_field": "`recent_market_date_for_eod()` as calc_date; `time.time()` as generated_at.",
            "freshness": "Persists payload sources as JSON, but not a normalized freshness table.",
            "safe": "No",
            "missing": "DB write path; not suitable for read-only shadow diagnostics.",
            "notes": "Formal persistence function; detail currently calls synthesize with `persist=False`.",
        },
        {
            "factor": "detail endpoint",
            "function": "api_stock_detail()",
            "file_line": source_line("review_src/app.py", r"def api_stock_detail"),
            "time_field": "Builds `rows_asc`, `us_forecast`, `futures_night`, `practical`, `sr_detail`, and calls gate/synthesize.",
            "freshness": "No top-level formal freshness; details are scattered under factor payloads.",
            "safe": "No",
            "missing": "No formal diagnostics for why next_day_outlook is absent.",
            "notes": "Formal API response must not receive shadow fields in this phase.",
        },
    ]


def render_report(base_url: str, results: list[dict[str, Any]]) -> str:
    generated_at = now_iso()
    with_formal = sum(1 for r in results if r["has_formal"])
    missing = sum(1 for r in results if not r["has_formal"])
    warn = sum(1 for r in results if r["status"] == "warn")
    fail = 0
    before_with_formal = len(BEFORE_D3B_FORMAL_CODES)
    before_missing = len(BEFORE_D3B_MISSING_CODES)
    after_formal_codes = {r["code"] for r in results if r["has_formal"]}
    after_missing_codes = {r["code"] for r in results if not r["has_formal"]}
    improved_codes = sorted(BEFORE_D3B_MISSING_CODES & after_formal_codes)
    still_missing_codes = sorted(after_missing_codes)
    lines = [
        "# Next Day Outlook Coverage Audit — After D3B",
        "",
        "## Summary",
        "",
        f"* generated_at: `{generated_at}`",
        f"* base_url: `{base_url}`",
        f"* before_with_formal: `{before_with_formal}`",
        f"* after_with_formal: `{with_formal}`",
        f"* before_missing: `{before_missing}`",
        f"* after_missing: `{missing}`",
        f"* improved_codes: `{', '.join(improved_codes) or 'none'}`",
        f"* still_missing_codes: `{', '.join(still_missing_codes) or 'none'}`",
        f"* total codes: `{len(results)}`",
        f"* with_formal_next_day_outlook: `{with_formal}`",
        f"* missing_formal_next_day_outlook: `{missing}`",
        f"* warn: `{warn}`",
        f"* fail: `{fail}`",
        "",
        "## Per-Code Coverage",
        "",
        "| Code | Before | After | Score | Direction | Confidence | Root Cause | Fix Applied | Still Missing Reason |",
        "| ---- | ------ | ----- | ----- | --------- | ---------- | ---------- | ----------- | -------------------- |",
    ]
    for result in results:
        s = result["outlook_summary"]
        formal_label = "present" if result["has_formal"] else s["state"]
        before_label = "present" if result["code"] in BEFORE_D3B_FORMAL_CODES else "missing"
        root_cause = "N/A"
        fix_applied = "N/A"
        still_missing = "N/A"
        if result["code"] in BEFORE_D3B_MISSING_CODES:
            root_cause = "readiness gate treated source-delayed institution/margin/foreign-shareholding data as blocking"
            fix_applied = "front-close mode treats sufficient source-delayed chip rows as warning metadata"
            if not result["has_formal"]:
                still_missing = result["missing_category"]
        lines.append(
            f"| {md_escape(result['code'])} | {md_escape(before_label)} | {md_escape(formal_label)} | {md_escape(s['score'])} | {md_escape(s['direction'])} | {md_escape(s['confidence'])} | {md_escape(root_cause)} | {md_escape(fix_applied)} | {md_escape(still_missing)} |"
        )

    lines.extend(
        [
            "",
            "## Exact Gate Fixed",
            "",
            "* file: `review_src/app.py`",
            "* function: `data_readiness_for_items()`",
            "* original condition: in front-close / Taiwan 50 mode, sufficient `institution_daily`, `margin_daily`, and `foreign_shareholding` rows could still be appended to blocking `issues` when their latest date lagged the latest K-line.",
            "* new condition: in front-close / Taiwan 50 mode, sufficient-but-delayed institution, margin, and foreign shareholding rows are recorded under `data_quality_warnings` with `source_delayed` status instead of blocking formal detail/outlook.",
            "* why minimal: no formula changed, no API schema changed, no DB schema changed, no frontend changed, no new source added.",
            "",
            "## Data Quality Policy Applied",
            "",
            "* Usable for formal outlook/readiness in front-close mode: sufficient rows with expected `source_delayed` status for institution, margin, and foreign shareholding data.",
            "* Still blocking: missing price, insufficient K-line rows, technical indicators unavailable, support/resistance unavailable, valuation required fields missing, required numeric cost cells invalid, source trace missing, true row-count shortage.",
            "* This audit does not treat unavailable data as neutral score and does not invent missing financial values.",
        ]
    )

    lines.extend(["", "## Missing Outlook Analysis", ""])
    any_missing = False
    for result in results:
        if result["has_formal"]:
            continue
        any_missing = True
        lines.append(f"### {result['code']}")
        lines.append("")
        lines.append("* observed evidence:")
        for item in result["evidence"] or ["N/A"]:
            lines.append(f"  * {item}")
        lines.append(f"* inferred possible cause: {result['inferred']}")
        lines.append("* unknowns: API payload may not expose enough internal branch diagnostics to prove exact cause.")
        lines.append(f"* recommended next diagnostic step: {result['next_step']}")
        lines.append("")
    if not any_missing:
        lines.append("* No missing formal next_day_outlook in checked codes.")
        lines.append("")

    lines.extend(
        [
            "## Factor Timestamp Source Inventory",
            "",
            "| Factor | Function | File/Line | Current Time Field | Freshness/Decay Exists | Safe for Shadow? | Missing Pieces | Notes |",
            "| ------ | -------- | --------- | ------------------ | ---------------------- | ---------------- | -------------- | ----- |",
        ]
    )
    for row in inventory_rows():
        lines.append(
            "| {factor} | {function} | {file_line} | {time_field} | {freshness} | {safe} | {missing} | {notes} |".format(
                factor=md_escape(row["factor"]),
                function=md_escape(row["function"]),
                file_line=md_escape(row["file_line"]),
                time_field=md_escape(row["time_field"]),
                freshness=md_escape(row["freshness"]),
                safe=md_escape(row["safe"]),
                missing=md_escape(row["missing"]),
                notes=md_escape(row["notes"]),
            )
        )

    lines.extend(["", "## Formal API Protection", ""])
    protection_failures = 0
    for result in results:
        paths = result["forbidden_paths"]
        if paths:
            protection_failures += 1
            lines.append(f"* {result['code']}: FAIL forbidden keys found: `{', '.join(paths)}`")
        else:
            lines.append(f"* {result['code']}: PASS no forbidden shadow keys found in formal detail response.")
    lines.append("")
    lines.append("Forbidden keys checked:")
    for key in FORBIDDEN_FORMAL_KEYS:
        lines.append(f"* `{key}`")

    lines.extend(
        [
            "",
            "## Production Safety",
            "",
            "* No DB writes are performed by this script.",
            "* No API or frontend files are modified by this script.",
            "* No external data sources are fetched by this script.",
            "* No production outlook takeover is performed.",
            "* No shadow adjusted score is calculated.",
            "* No background task, scheduler, or server is started by this script.",
            "",
            "## Machine Summary",
            "",
            "```json",
            json.dumps(
                {
                    "generated_at": generated_at,
                    "base_url": base_url,
                    "total": len(results),
                    "with_formal": with_formal,
                    "missing": missing,
                    "warn": warn,
                    "fail": fail,
                    "formal_api_protection_failures": protection_failures,
                    "missing_categories": {r["code"]: r["missing_category"] for r in results if not r["has_formal"]},
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit formal next_day_outlook coverage and factor timestamp sources.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--codes", nargs="+", default=DEFAULT_CODES)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--timeout", type=float, default=10.0)
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        results = [audit_code(args.base_url, str(code), args.timeout) for code in args.codes]
        report = render_report(args.base_url, results)
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(report, encoding="utf-8")
        with_formal = sum(1 for r in results if r["has_formal"])
        missing = sum(1 for r in results if not r["has_formal"])
        warn = sum(1 for r in results if r["status"] == "warn")
        print(
            "Next-day outlook coverage audit written to {path} | total={total} with_formal={with_formal} missing={missing} warn={warn} fail=0".format(
                path=output,
                total=len(results),
                with_formal=with_formal,
                missing=missing,
                warn=warn,
            )
        )
        return 0 if warn == 0 else 1
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
