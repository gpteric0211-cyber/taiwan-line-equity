#!/usr/bin/env python3
"""Read-only legacy-vs-shadow outlook comparison report.

This script only calls local HTTP endpoints supplied by --base-url and writes a
Markdown report. It does not import production app code, start a server, write
to the database, or fetch external market data.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import traceback
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_CODES = ["2317", "2330", "2382", "2454", "2308"]
DEFAULT_BASE_URL = "http://127.0.0.1:8057"
DEFAULT_OUTPUT = "docs/outlook_shadow_diff_report.md"
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
            body = resp.read().decode("utf-8", errors="replace")
            try:
                data = json.loads(body)
            except json.JSONDecodeError as exc:
                return None, f"non_json_response: {exc}"
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
    except Exception as exc:  # defensive CLI boundary
        return None, f"unexpected_error: {exc}"


def first_present(mapping: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            return mapping[key]
    return None


def find_first_key(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for value in obj.values():
            found = find_first_key(value, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = find_first_key(value, key)
            if found is not None:
                return found
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


def ensure_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def as_text(value: Any) -> str:
    if value is None or value == "":
        return "N/A"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def md_escape(value: Any) -> str:
    text = as_text(value)
    return text.replace("|", "\\|").replace("\n", "<br>")


def summarize_formal(detail: dict[str, Any] | None) -> dict[str, Any]:
    if not detail:
        return {
            "available": False,
            "status": "unavailable",
            "score": "N/A",
            "direction": "N/A",
            "confidence": "N/A",
            "gate_or_notice": "N/A",
            "title_or_summary": "N/A",
            "data_time": "N/A",
            "forbidden_paths": [],
        }

    outlook = find_first_key(detail, "next_day_outlook")
    outlook_dict = ensure_dict(outlook)
    forbidden_paths = collect_keys(detail, set(FORBIDDEN_FORMAL_KEYS))
    if not outlook_dict:
        return {
            "available": True,
            "status": "missing_next_day_outlook",
            "score": "N/A",
            "direction": "N/A",
            "confidence": "N/A",
            "gate_or_notice": "missing next_day_outlook",
            "title_or_summary": "N/A",
            "data_time": "N/A",
            "forbidden_paths": forbidden_paths,
        }

    score = first_present(
        outlook_dict,
        [
            "score",
            "probability",
            "final_probability",
            "prob",
            "bullish_probability",
            "up_probability",
            "total_score",
            "composite_probability",
        ],
    )
    direction = first_present(
        outlook_dict,
        ["direction", "bias", "status", "trend", "label", "signal", "verdict", "summary_status"],
    )
    confidence = first_present(outlook_dict, ["confidence", "confidence_level", "trust_level"])
    gate = first_present(outlook_dict, ["gate", "notice", "warning", "message", "reason"])
    title = first_present(outlook_dict, ["title", "display_title", "summary", "description"])
    data_time = first_present(outlook_dict, ["data_time", "as_of_time", "updated_at", "date"])

    return {
        "available": True,
        "status": "ok",
        "score": as_text(score),
        "direction": as_text(direction),
        "confidence": as_text(confidence),
        "gate_or_notice": as_text(gate),
        "title_or_summary": as_text(title),
        "data_time": as_text(data_time),
        "forbidden_paths": forbidden_paths,
    }


def shadow_enabled_false(payload: dict[str, Any]) -> bool:
    enabled = payload.get("enabled")
    if enabled is False:
        return True
    if payload.get("ok") is False and "disabled" in as_text(payload).lower():
        return True
    return False


def extract_shadow_context(payload: dict[str, Any]) -> dict[str, Any]:
    for key in ("shadow_context", "context", "calendar_context", "data", "shadow"):
        value = payload.get(key)
        if isinstance(value, dict):
            return value
    return payload


def summarize_market(name: str, value: Any) -> str:
    if not isinstance(value, dict):
        return f"{name}: N/A"
    session = first_present(value, ["session", "session_status", "status", "is_open"])
    source = first_present(value, ["source", "calendar_source"])
    confidence = first_present(value, ["confidence", "calendar_confidence"])
    target = first_present(value, ["target_trade_date", "belongs_to_trade_date"])
    parts = [name]
    for label, item in (
        ("session", session),
        ("source", source),
        ("confidence", confidence),
        ("target", target),
    ):
        if item not in (None, ""):
            parts.append(f"{label}={item}")
    return ": ".join([parts[0], ", ".join(parts[1:])]) if len(parts) > 1 else f"{name}: N/A"


def iter_calendar_markets(context: dict[str, Any]) -> dict[str, Any]:
    calendar = (
        context.get("calendar_debug")
        or context.get("calendar_status")
        or context.get("markets")
        or context.get("market_calendar")
        or {}
    )
    if isinstance(calendar, dict) and isinstance(calendar.get("markets"), dict):
        calendar = calendar["markets"]
    if not isinstance(calendar, dict):
        return {}
    return calendar


def summarize_calendar(context: dict[str, Any]) -> str:
    markets = iter_calendar_markets(context)
    if not markets:
        return "N/A"
    segments = []
    for key in ("TWSE", "twse", "US", "us", "TAIFEX", "taifex"):
        if key in markets:
            segments.append(summarize_market(key.upper(), markets[key]))
    if not segments:
        for key, value in markets.items():
            if isinstance(value, dict):
                segments.append(summarize_market(str(key), value))
    return "; ".join(segments) if segments else "N/A"


def summarize_factor_freshness(context: dict[str, Any]) -> list[dict[str, Any]]:
    raw = context.get("factor_freshness") or context.get("factors") or {}
    rows: list[dict[str, Any]] = []
    if isinstance(raw, dict):
        iterable = raw.items()
    elif isinstance(raw, list):
        iterable = [(str(item.get("factor", idx)) if isinstance(item, dict) else str(idx), item) for idx, item in enumerate(raw)]
    else:
        iterable = []
    for factor, value in iterable:
        if isinstance(value, dict):
            rows.append(
                {
                    "factor": factor,
                    "freshness": as_text(first_present(value, ["freshness", "status", "data_quality"])),
                    "usable": as_text(first_present(value, ["usable", "is_usable", "available"])),
                    "confidence": as_text(first_present(value, ["confidence", "confidence_level"])),
                    "note": as_text(first_present(value, ["calendar_context_note", "reason", "note"])),
                }
            )
        else:
            rows.append(
                {
                    "factor": factor,
                    "freshness": as_text(value),
                    "usable": "N/A",
                    "confidence": "N/A",
                    "note": "N/A",
                }
            )
    return rows


def summarize_shadow(payload: dict[str, Any] | None, error: str | None) -> dict[str, Any]:
    if error:
        return {
            "available": False,
            "status": f"WARN: {error}",
            "score": "N/A",
            "direction": "N/A",
            "target_trade_date": "N/A",
            "display_title": "N/A",
            "as_of_time": "N/A",
            "calendar_summary": "N/A",
            "factor_rows": [],
            "context": {},
            "warning": error,
        }
    if not payload:
        return {
            "available": False,
            "status": "SKIP: empty shadow payload",
            "score": "N/A",
            "direction": "N/A",
            "target_trade_date": "N/A",
            "display_title": "N/A",
            "as_of_time": "N/A",
            "calendar_summary": "N/A",
            "factor_rows": [],
            "context": {},
            "warning": "empty shadow payload",
        }
    if shadow_enabled_false(payload):
        reason = as_text(first_present(payload, ["reason", "message", "detail"]) or "shadow endpoint disabled")
        return {
            "available": False,
            "status": f"SKIP: {reason}",
            "score": "N/A",
            "direction": "N/A",
            "target_trade_date": "N/A",
            "display_title": "N/A",
            "as_of_time": "N/A",
            "calendar_summary": "N/A",
            "factor_rows": [],
            "context": payload,
            "warning": reason,
        }

    context = extract_shadow_context(payload)
    target_trade_date = first_present(context, ["target_trade_date", "belongs_to_trade_date"])
    display_title = first_present(context, ["display_title", "title"])
    as_of_time = first_present(context, ["as_of_time", "generated_at", "data_time"])
    factor_rows = summarize_factor_freshness(context)
    return {
        "available": True,
        "status": "ok",
        "score": "N/A",
        "direction": "N/A",
        "target_trade_date": as_text(target_trade_date),
        "display_title": as_text(display_title),
        "as_of_time": as_text(as_of_time),
        "calendar_summary": summarize_calendar(context),
        "factor_rows": factor_rows,
        "context": context,
        "warning": None,
    }


def build_url(base_url: str, path: str) -> str:
    return urllib.parse.urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))


def difference_note(formal: dict[str, Any], shadow: dict[str, Any]) -> str:
    notes = []
    if formal["status"] != "ok":
        notes.append(f"formal={formal['status']}")
    if shadow["status"] != "ok":
        notes.append(str(shadow["status"]))
    if shadow["score"] == "N/A":
        notes.append("shadow score not implemented; calendar/freshness only")
    if shadow["target_trade_date"] != "N/A":
        notes.append(f"shadow targets {shadow['target_trade_date']}")
    if formal["forbidden_paths"]:
        notes.append("formal response contains forbidden shadow keys")
    return "; ".join(notes) if notes else "formal output and shadow context both available"


def compare_code(base_url: str, code: str, timeout: float) -> dict[str, Any]:
    detail_url = build_url(base_url, f"/api/stock/{code}/detail")
    shadow_url = build_url(base_url, f"/api/debug/outlook_shadow/{code}")

    detail_payload, detail_error = fetch_json(detail_url, timeout)
    formal = summarize_formal(detail_payload)
    if detail_error:
        formal["available"] = False
        formal["status"] = f"WARN: {detail_error}"
        formal["gate_or_notice"] = detail_error

    shadow_payload, shadow_error = fetch_json(shadow_url, timeout)
    shadow = summarize_shadow(shadow_payload, shadow_error)

    status = "success"
    if str(formal["status"]).startswith("WARN") or str(shadow["status"]).startswith("WARN"):
        status = "warn"
    if str(shadow["status"]).startswith("SKIP"):
        status = "skip"
    if str(formal["status"]).startswith("WARN") and str(shadow["status"]).startswith("WARN"):
        status = "fail"

    return {
        "code": code,
        "status": status,
        "formal": formal,
        "shadow": shadow,
        "difference_note": difference_note(formal, shadow),
    }


def render_report(base_url: str, results: list[dict[str, Any]]) -> str:
    counts = {"success": 0, "skip": 0, "warn": 0, "fail": 0}
    for result in results:
        counts[result["status"]] = counts.get(result["status"], 0) + 1
    generated_at = now_iso()
    lines = [
        "# Legacy vs Shadow Outlook Diff Report",
        "",
        "## Summary",
        "",
        f"* generated_at: `{generated_at}`",
        f"* base_url: `{base_url}`",
        f"* total codes: `{len(results)}`",
        f"* success: `{counts.get('success', 0)}`",
        f"* skip: `{counts.get('skip', 0)}`",
        f"* warn: `{counts.get('warn', 0)}`",
        f"* fail: `{counts.get('fail', 0)}`",
        "",
        "## Important Note",
        "",
        "This report is read-only. It compares the formal detail API output with the debug shadow calendar/freshness context.",
        "",
        "Shadow context does not take over production `next_day_outlook`. It currently focuses on calendar, freshness, and target trade date context. If `shadow_score` is `N/A`, that is expected and means the shadow adjusted score has not been implemented in this phase.",
        "",
        "## Per-Code Results",
        "",
        "| Code | Formal Status | Shadow Status | Formal Score | Formal Direction | Shadow Score | Shadow Target Trade Date | Calendar Summary | Difference Note |",
        "| ---- | ------------- | ------------- | ------------ | ---------------- | ------------ | ------------------------ | ---------------- | --------------- |",
    ]

    for result in results:
        formal = result["formal"]
        shadow = result["shadow"]
        lines.append(
            "| {code} | {formal_status} | {shadow_status} | {formal_score} | {formal_direction} | {shadow_score} | {target} | {calendar} | {note} |".format(
                code=md_escape(result["code"]),
                formal_status=md_escape(formal["status"]),
                shadow_status=md_escape(shadow["status"]),
                formal_score=md_escape(formal["score"]),
                formal_direction=md_escape(formal["direction"]),
                shadow_score=md_escape(shadow["score"]),
                target=md_escape(shadow["target_trade_date"]),
                calendar=md_escape(shadow["calendar_summary"]),
                note=md_escape(result["difference_note"]),
            )
        )

    lines.extend(["", "## Shadow Calendar Details", ""])
    for result in results:
        shadow = result["shadow"]
        context = shadow["context"]
        markets = iter_calendar_markets(context)
        lines.append(f"### {result['code']}")
        lines.append("")
        if not markets:
            lines.append(f"* status: {shadow['status']}")
            lines.append("* TWSE status: N/A")
            lines.append("* US status: N/A")
            lines.append("* TAIFEX status: N/A")
            lines.append("* override_active: N/A")
            lines.append("* source: N/A")
            lines.append("* confidence: N/A")
            lines.append(f"* target_trade_date: {shadow['target_trade_date']}")
            lines.append("")
            continue
        for market_name, market_info in markets.items():
            if isinstance(market_info, dict):
                lines.append(f"* {market_name}: `{json.dumps(market_info, ensure_ascii=False, sort_keys=True)}`")
        override_active = first_present(context, ["override_active", "has_override"])
        source = first_present(context, ["source", "calendar_source"])
        confidence = first_present(context, ["confidence", "calendar_confidence"])
        lines.append(f"* override_active: {as_text(override_active)}")
        lines.append(f"* source: {as_text(source)}")
        lines.append(f"* confidence: {as_text(confidence)}")
        lines.append(f"* target_trade_date: {shadow['target_trade_date']}")
        lines.append("")

    lines.extend(["## Factor Freshness Details", ""])
    for result in results:
        lines.append(f"### {result['code']}")
        lines.append("")
        factor_rows = result["shadow"]["factor_rows"]
        if not factor_rows:
            lines.append("* N/A")
            lines.append("")
            continue
        lines.append("| factor | freshness | usable | confidence | calendar_context_note |")
        lines.append("| ------ | --------- | ------ | ---------- | --------------------- |")
        for row in factor_rows:
            lines.append(
                f"| {md_escape(row['factor'])} | {md_escape(row['freshness'])} | {md_escape(row['usable'])} | {md_escape(row['confidence'])} | {md_escape(row['note'])} |"
            )
        lines.append("")

    lines.extend(["## Warnings / Skips", ""])
    warning_count = 0
    for result in results:
        formal = result["formal"]
        shadow = result["shadow"]
        if formal["status"] != "ok":
            warning_count += 1
            lines.append(f"* {result['code']} formal: {formal['status']} / {formal['gate_or_notice']}")
        if shadow["status"] != "ok":
            warning_count += 1
            lines.append(f"* {result['code']} shadow: {shadow['status']}")
        if formal["score"] == "N/A":
            warning_count += 1
            lines.append(f"* {result['code']} formal score missing: shown as N/A")
    if warning_count == 0:
        lines.append("* No warnings or skips.")

    lines.extend(["", "## Formal API Protection", ""])
    formal_failures = 0
    for result in results:
        paths = result["formal"]["forbidden_paths"]
        if paths:
            formal_failures += 1
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
                    "counts": counts,
                    "formal_api_protection_failures": formal_failures,
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
    parser = argparse.ArgumentParser(description="Compare formal next_day_outlook with shadow calendar/freshness context.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--codes", nargs="+", default=DEFAULT_CODES)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--timeout", type=float, default=10.0)
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    results: list[dict[str, Any]] = []
    try:
        for code in args.codes:
            results.append(compare_code(args.base_url, code, args.timeout))
        report = render_report(args.base_url, results)
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report, encoding="utf-8")
        counts = {"success": 0, "skip": 0, "warn": 0, "fail": 0}
        for result in results:
            counts[result["status"]] = counts.get(result["status"], 0) + 1
        print(
            "Legacy vs shadow report written to {path} | total={total} success={success} skip={skip} warn={warn} fail={fail}".format(
                path=output_path,
                total=len(results),
                success=counts.get("success", 0),
                skip=counts.get("skip", 0),
                warn=counts.get("warn", 0),
                fail=counts.get("fail", 0),
            )
        )
        return 1 if counts.get("fail", 0) else 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
