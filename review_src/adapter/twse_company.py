from __future__ import annotations

import re
from typing import Any

from core.config import HEADERS, safe_error
from core.date_utils import normalize_date
from core.http import request_json
from core.utils import parse_num


TWSE_COMPANY_LIST_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"


def _first(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def normalize_twse_company_row(row: dict[str, Any]) -> dict[str, Any] | None:
    code = str(_first(row, "公司代號", "Code", "code") or "").strip()
    if not re.fullmatch(r"\d{4}", code):
        return None
    return {
        "code": code,
        "name": str(_first(row, "公司簡稱", "公司名稱", "Name", "name") or "").strip(),
        "market": "listed",
        "exchange": "TWSE",
        "security_type": "stock",
        "listing_date": normalize_date(_first(row, "上市日期", "ListingDate", "listing_date")),
        "data_date": normalize_date(_first(row, "出表日期", "Date", "data_date")),
        "paid_in_capital_twd": parse_num(
            _first(row, "實收資本額", "Paidin.Capital.NTDollars", "paid_in_capital_twd")
        ),
        "issued_shares": parse_num(
            _first(
                row,
                "已發行普通股數或TDR原股發行股數",
                "IssueShares",
                "issued_shares",
            )
        ),
        "source": "TWSE_COMPANY_OPENAPI",
        "source_status": "ok",
    }


def fetch_twse_company_list() -> dict[str, Any]:
    out: dict[str, Any] = {
        "ok": False,
        "source": "TWSE_COMPANY_OPENAPI",
        "url": TWSE_COMPANY_LIST_URL,
        "rows": 0,
        "valid_rows": 0,
        "items": [],
        "error": None,
    }
    try:
        payload = request_json(
            TWSE_COMPANY_LIST_URL,
            headers=HEADERS,
            retries=2,
            retry_wait=1.5,
            timeout=25,
        )
        raw_rows = payload if isinstance(payload, list) else []
        items = [
            item
            for item in (
                normalize_twse_company_row(row)
                for row in raw_rows
                if isinstance(row, dict)
            )
            if item
        ]
        out.update(
            {
                "ok": bool(items),
                "rows": len(raw_rows),
                "valid_rows": len(items),
                "items": items,
                "columns": list(raw_rows[0].keys()) if raw_rows else [],
            }
        )
    except Exception as exc:
        out["error"] = safe_error(exc)
    return out
