"""Explicit TWSE corporate-action halt intervals; never infer prices or dates."""

from __future__ import annotations
import re
from datetime import date, timedelta
from typing import Any
import requests
from core.config import HEADERS, safe_error

BASE_URL = "https://www.twse.com.tw/rwd/zh"
SOURCES = (
    ("twse_capital_reduction", "/reducation/TWTAVU", "減資停止買賣"),
    ("twse_par_value_change", "/change/TWTB7U", "變更股票面額停止買賣"),
    ("twse_capital_resumption", "/reducation/TWTAUU", "減資恢復買賣公告"),
)
DETAIL_PATH = "/reducation/TWTAVUDetail"


def _rows(payload: Any, required: set[str]) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or str(payload.get("stat", "")).upper() != "OK":
        raise ValueError("corporate-action report is unavailable")
    fields, data = payload.get("fields"), payload.get("data")
    if not isinstance(fields, list) or not isinstance(data, list):
        raise ValueError("corporate-action report has no field/row schema")
    fields = [str(value).strip().rstrip("：:") for value in fields]
    if len(fields) != len(set(fields)) or not required.issubset(fields):
        raise ValueError("corporate-action report fields changed")
    if any(not isinstance(row, list) or len(row) != len(fields) for row in data):
        raise ValueError("corporate-action report has malformed rows")
    return [dict(zip(fields, row)) for row in data]


def _get(path: str, timeout: float, **params: str) -> Any:
    response = requests.get(
        BASE_URL + path,
        params={"response": "json", **params},
        headers={**HEADERS, "Accept": "application/json"},
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()


def _normalize(row: dict[str, Any], source_id: str, reason: str, observed_date: str) -> dict[str, Any]:
    # Import here so the existing adapter may aggregate this module without a cycle.
    from adapter.official_trading_restrictions import _event, parse_roc_date

    code = str(row.get("股票代號", "")).strip()
    start = parse_roc_date(row.get("停止買賣日期"))
    resume = parse_roc_date(row.get("恢復買賣日期"))
    if not re.fullmatch(r"[0-9]{4}", code) or not start or not resume or resume <= start:
        raise ValueError("corporate-action halt requires an explicit valid interval")
    end = (date.fromisoformat(resume) - timedelta(days=1)).isoformat()
    return _event(
        code=code,
        company_name=row.get("名稱") or row.get("股票名稱"),
        market="listed",
        restriction_type="trading_halt",
        announcement_date=observed_date,
        effective_from=start,
        effective_to=end,
        reason=f"{reason}；停止買賣 {start}，恢復買賣 {resume}",
        source_id=source_id,
    )


def fetch_corporate_halts(*, as_of_date: str, timeout: float = 20) -> dict[str, Any]:
    date.fromisoformat(as_of_date)
    items, sources = [], []
    for source_id, path, reason in SOURCES:
        result = dict(
            market="listed", source_id=source_id, url=BASE_URL + path, data_date=as_of_date, rows_received=0
        )
        try:
            required = {"股票代號", "名稱", "恢復買賣日期"}
            is_resumption = source_id == "twse_capital_resumption"
            required.add("詳細資料" if is_resumption else "停止買賣日期")
            rows = _rows(_get(path, timeout), required)
            parsed = []
            if is_resumption and len(rows) > 64:
                raise ValueError("corporate-action detail request limit exceeded")
            for row in rows:
                if is_resumption:
                    # The opaque detail date is ONLY a lookup key. The detail report
                    # supplies the actual stop date; it must never be guessed from it.
                    match = re.fullmatch(r"\s*([0-9]{4})\s*,\s*([0-9]{8})\s*", str(row["詳細資料"]))
                    if not match or match[1] != str(row["股票代號"]).strip():
                        raise ValueError("corporate-action detail identifier mismatch")
                    details = _rows(
                        _get(DETAIL_PATH, timeout, STK_NO=match[1], FILE_DATE=match[2]),
                        {"股票代號", "股票名稱", "停止買賣日期"},
                    )
                    if len(details) != 1 or str(details[0]["股票代號"]).strip() != match[1]:
                        raise ValueError("corporate-action detail stock mismatch")
                    row = {**row, "停止買賣日期": details[0]["停止買賣日期"]}
                parsed.append(_normalize(row, source_id, reason, as_of_date))
            items.extend(parsed)
            result.update(ok=True, status="ok", rows_received=len(parsed), raw_rows_received=len(rows))
        except Exception as exc:
            result.update(ok=False, status="failed", error=safe_error(exc))
        sources.append(result)
    return {"items": items, "sources": sources, "ok": all(row["ok"] for row in sources)}
