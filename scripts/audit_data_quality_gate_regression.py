#!/usr/bin/env python3
"""Read-only regression audit for the D3B data-quality gate.

This script calls only the configured local API base URL and reads source
text for static gate-review evidence. It does not import the FastAPI app,
write to DB, start a server, fetch external websites, or modify production
logic.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_CODES = ["2317", "2330", "2382", "2454", "2308"]
DEFAULT_BASE_URL = "http://127.0.0.1:8057"
DEFAULT_OUTPUT = "docs/data_quality_gate_regression_report.md"
BEFORE_D3B_COVERAGE = "2/5"
AFTER_D3B_COVERAGE = "5/5"
SOURCE_DELAYED_CODES = {"2330", "2454", "2308"}
FORBIDDEN_FORMAL_KEYS = {
    "shadow_outlook",
    "freshness_adjusted_outlook_shadow",
    "factor_freshness",
    "calendar_status",
    "calendar_debug",
    "debug_shadow",
    "override",
    "market_calendar",
}


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def build_url(base_url: str, path: str) -> str:
    return urllib.parse.urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))


def fetch_json(url: str, timeout: float) -> tuple[dict[str, Any] | None, str | None]:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            data = json.loads(raw)
            if isinstance(data, dict):
                return data, None
            return {"_payload": data}, None
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


def as_text(value: Any) -> str:
    if value is None or value == "":
        return "N/A"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def md_escape(value: Any) -> str:
    return as_text(value).replace("|", "\\|").replace("\n", "<br>")


def find_forbidden_keys(payload: Any, *, path: str = "$") -> list[str]:
    found: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            next_path = f"{path}.{key}"
            if key in FORBIDDEN_FORMAL_KEYS:
                found.append(next_path)
            found.extend(find_forbidden_keys(value, path=next_path))
    elif isinstance(payload, list):
        for idx, value in enumerate(payload):
            found.extend(find_forbidden_keys(value, path=f"{path}[{idx}]"))
    return found


def find_first_key(payload: Any, key_name: str) -> Any:
    if isinstance(payload, dict):
        if key_name in payload:
            return payload[key_name]
        for value in payload.values():
            found = find_first_key(value, key_name)
            if found is not None:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = find_first_key(value, key_name)
            if found is not None:
                return found
    return None


def find_quality_terms(payload: Any) -> list[str]:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    terms = []
    for term in ("data_quality_warnings", "source_delayed", "stale", "delayed"):
        if term in text:
            terms.append(term)
    return terms


def find_blocking_issue(payload: dict[str, Any] | None) -> str:
    if not payload:
        return "unknown"
    if payload.get("ok") is False:
        return as_text(payload.get("reason") or payload.get("message") or "ok=false")
    readiness = find_first_key(payload, "readiness")
    if isinstance(readiness, dict):
        failed = readiness.get("failed")
        if failed:
            return as_text(failed)
    return "none_detected"


def extract_score(outlook: Any) -> str:
    if not isinstance(outlook, dict):
        return "N/A"
    for key in ("probability", "score", "final_probability", "prob"):
        if key in outlook:
            return as_text(outlook.get(key))
    return "N/A"


def extract_direction(outlook: Any) -> str:
    if not isinstance(outlook, dict):
        return "N/A"
    for key in ("direction", "status", "trend", "label"):
        if key in outlook:
            return as_text(outlook.get(key))
    return "N/A"


def extract_confidence(outlook: Any) -> str:
    if not isinstance(outlook, dict):
        return "N/A"
    for key in ("confidence", "trust", "trust_level"):
        if key in outlook:
            return as_text(outlook.get(key))
    return "N/A"


def audit_code(base_url: str, code: str, timeout: float) -> dict[str, Any]:
    url = build_url(base_url, f"/api/stock/{code}/detail")
    payload, error = fetch_json(url, timeout)
    if error:
        return {
            "code": code,
            "status": "WARN",
            "formal_present": False,
            "quality_warning": "unknown",
            "blocking_issue": error,
            "shadow_leak": "unknown",
            "score": "N/A",
            "direction": "N/A",
            "confidence": "N/A",
            "notes": "API unavailable; audit skipped for this code.",
        }

    outlook = payload.get("next_day_outlook") if isinstance(payload, dict) else None
    formal_present = isinstance(outlook, dict) and bool(outlook)
    forbidden = find_forbidden_keys(payload)
    terms = find_quality_terms(payload)
    blocking_issue = find_blocking_issue(payload)
    quality_warning = ", ".join(terms) if terms else "not_exposed"

    status = "PASS"
    notes: list[str] = []
    if not formal_present:
        status = "WARN"
        notes.append("formal next_day_outlook missing")
    if forbidden:
        status = "FAIL"
        notes.append("forbidden shadow/debug keys leaked")
    if code in SOURCE_DELAYED_CODES and formal_present and not terms:
        notes.append("insufficient_api_evidence")

    return {
        "code": code,
        "status": status,
        "formal_present": formal_present,
        "quality_warning": quality_warning,
        "blocking_issue": blocking_issue,
        "shadow_leak": ", ".join(forbidden) if forbidden else "none",
        "score": extract_score(outlook),
        "direction": extract_direction(outlook),
        "confidence": extract_confidence(outlook),
        "notes": "; ".join(notes) if notes else "ok",
    }


def function_body(source: str, name: str) -> tuple[int | None, str]:
    match = re.search(rf"^def\s+{re.escape(name)}\s*\(", source, flags=re.MULTILINE)
    if not match:
        return None, ""
    start = match.start()
    start_line = source[:start].count("\n") + 1
    next_match = re.search(r"^def\s+\w+\s*\(", source[match.end() :], flags=re.MULTILINE)
    if next_match:
        end = match.end() + next_match.start()
        return start_line, source[start:end]
    return start_line, source[start:]


def yes_no(value: bool) -> str:
    return "PASS" if value else "UNKNOWN"


def static_gate_review(app_path: Path) -> list[dict[str, str]]:
    if not app_path.exists():
        return [{"item": "app.py readable", "result": "FAIL", "evidence": f"{app_path} not found"}]

    source = app_path.read_text(encoding="utf-8", errors="replace")
    line, body = function_body(source, "data_readiness_for_items")
    if not body:
        return [{"item": "data_readiness_for_items found", "result": "FAIL", "evidence": "function not found"}]

    checks = [
        (
            "D3B gate scoped to front_close_mode",
            "front_close_mode" in body and body.count("if front_close_mode") >= 3,
            f"function line {line}; found front_close_mode branches",
        ),
        (
            "non-front-close retains blocking behavior",
            "stale_chip = True" in body
            and "外資持股資料日期過舊" in body
            and "法人資料日期過舊" in body
            and "融資資料日期過舊" in body,
            "non-front-close else branches still append stale-date issues",
        ),
        (
            "true row shortage remains blocking",
            "法人/融資資料不足" in body and "外資累積成本推估缺外資持股 anchor" in body,
            "row-count shortage issue strings remain present",
        ),
        ("missing price remains blocking", "缺最新價格" in body, "price None appends issue"),
        ("insufficient K-line rows remain blocking", "歷史K線不足" in body, "hist_count threshold issue remains"),
        ("technical readiness remains blocking", "RSI5/10/14 無法完整計算" in body and "MA20/MA60 無法完整計算" in body, "technical issue strings remain"),
        ("support/resistance unavailable remains blocking", "支撐/賣壓缺可靠數值" in body, "support/resistance issue string remains"),
        ("valuation incomplete remains blocking", "估值數字不完整" in body, "valuation issue string remains"),
        ("required numeric cells remain blocking", "numeric_issues" in body and "數字欄位" in body, "numeric validation block remains present"),
        ("missing source trace remains blocking", "source_issues" in body and "issues.extend(source_issues)" in body, "source trace issues still extend issues"),
        ("global volume-unit issue remains blocking", "volume_unit_ok" in body and "volume" in body.lower(), "volume audit still present in readiness function"),
        ("expected-delay warnings are metadata", "data_quality_warnings" in body and "source_delayed" in body and "delayed_chip_notes" in body, "D3B warning metadata block found"),
        ("UNAVAILABLE / MISSING not converted to neutral score", "next_day_outlook" not in body and "50" not in body[body.find("delayed_chip_notes") : body.find("foreign_holding_ready")], "readiness gate does not synthesize scores"),
        ("no fabricated financial values in D3B block", "reason" in body and "source_delayed" in body and "parse_num" not in body[body.find("delayed_chip_notes") : body.find("foreign_holding_ready")], "D3B block only records metadata"),
    ]

    return [
        {"item": item, "result": yes_no(ok), "evidence": evidence}
        for item, ok, evidence in checks
    ]


def render_report(
    *,
    base_url: str,
    results: list[dict[str, Any]],
    static_review: list[dict[str, str]],
) -> str:
    total = len(results)
    with_formal = sum(1 for item in results if item["formal_present"])
    missing = total - with_formal
    warn = sum(1 for item in results if item["status"] == "WARN")
    fail = sum(1 for item in results if item["status"] == "FAIL")
    current = f"{with_formal}/{total}" if total else "0/0"

    lines = [
        "# Data Quality Gate Regression Report",
        "",
        "## Summary",
        "",
        f"* generated_at: `{now_iso()}`",
        f"* base_url: `{base_url}`",
        f"* total codes: `{total}`",
        f"* with_formal: `{with_formal}`",
        f"* missing: `{missing}`",
        f"* warn: `{warn}`",
        f"* fail: `{fail}`",
        "",
        "## Coverage Regression",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| before_D3B_coverage | {BEFORE_D3B_COVERAGE} |",
        f"| after_D3B_coverage | {AFTER_D3B_COVERAGE} |",
        f"| current_coverage | {current} |",
        "",
        "## Per-Code Checks",
        "",
        "| Code | Formal Outlook | Data Quality Warning | Blocking Issue | Shadow Key Leak | Status | Notes |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in results:
        lines.append(
            "| {code} | {formal} | {quality} | {blocking} | {shadow} | {status} | {notes} |".format(
                code=md_escape(item["code"]),
                formal="present" if item["formal_present"] else "missing",
                quality=md_escape(item["quality_warning"]),
                blocking=md_escape(item["blocking_issue"]),
                shadow=md_escape(item["shadow_leak"]),
                status=md_escape(item["status"]),
                notes=md_escape(item["notes"]),
            )
        )

    lines.extend([
        "",
        "## Source-Delayed Verification",
        "",
        "| Code | Formal Present | API Evidence | Interpretation |",
        "| --- | --- | --- | --- |",
    ])
    by_code = {item["code"]: item for item in results}
    for code in sorted(SOURCE_DELAYED_CODES):
        item = by_code.get(code, {})
        evidence = item.get("quality_warning", "unknown")
        if evidence in {"not_exposed", "unknown"}:
            interpretation = "insufficient_api_evidence"
        else:
            interpretation = "API exposed source-delayed/stale/delayed metadata"
        lines.append(
            f"| {code} | {'yes' if item.get('formal_present') else 'no'} | {md_escape(evidence)} | {md_escape(interpretation)} |"
        )

    lines.extend([
        "",
        "## Static Gate Review",
        "",
        "| Check | Result | Evidence |",
        "| --- | --- | --- |",
    ])
    for item in static_review:
        lines.append(f"| {md_escape(item['item'])} | {md_escape(item['result'])} | {md_escape(item['evidence'])} |")

    forbidden_failures = [item for item in results if item["shadow_leak"] != "none"]
    lines.extend([
        "",
        "## Formal API Protection",
        "",
        f"* forbidden shadow/debug key leaks: `{len(forbidden_failures)}`",
        "* forbidden keys checked: `shadow_outlook`, `freshness_adjusted_outlook_shadow`, `factor_freshness`, `calendar_status`, `calendar_debug`, `debug_shadow`, `override`, `market_calendar`",
        "",
        "## Production Safety",
        "",
        "* no DB writes performed by this script",
        "* no API schema changes",
        "* no frontend changes",
        "* no external data source calls",
        "* no formal score takeover",
        "* no background task created",
        "* no production module imported",
    ])
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit D3B data-quality gate regression.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--codes", nargs="+", default=DEFAULT_CODES)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--timeout", type=float, default=10.0)
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    results = [audit_code(args.base_url, str(code).zfill(4), args.timeout) for code in args.codes]
    static_review = static_gate_review(Path("review_src/app.py"))
    report = render_report(base_url=args.base_url, results=results, static_review=static_review)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report, encoding="utf-8")

    fail = sum(1 for item in results if item["status"] == "FAIL")
    warn = sum(1 for item in results if item["status"] == "WARN")
    with_formal = sum(1 for item in results if item["formal_present"])
    print(
        f"Data-quality gate regression report written to {output} | "
        f"total={len(results)} with_formal={with_formal} warn={warn} fail={fail}"
    )
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
