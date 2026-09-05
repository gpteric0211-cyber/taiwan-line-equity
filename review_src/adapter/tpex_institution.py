from __future__ import annotations

import re
from typing import Any, Callable

from core.config import HEADERS, mask_secret_text, safe_error
from core.date_utils import normalize_date
from core.request_budget import RequestBudget, RequestCapExceeded, budget_summary, budgeted_get_response
from core.utils import parse_num

try:
    import truststore  # type: ignore
except Exception:
    truststore = None
else:
    try:
        truststore.inject_into_ssl()
    except Exception:
        pass

TPEX_INSTITUTION_DAILY_TRADE_URL = "https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade"


def normalize_tpex_institution_code(value: Any) -> str | None:
    text = str(value or "").strip()
    return text if re.fullmatch(r"\d{4}", text) else None


def _int_value(value: Any) -> int | None:
    num = parse_num(value)
    return int(num) if num is not None else None


def normalize_tpex_institution_date(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    parts = re.split(r"[/-]", text)
    if len(parts) == 3 and parts[0].isdigit() and len(parts[0]) <= 3:
        year = int(parts[0]) + 1911
        return f"{year:04d}-{int(parts[1]):02d}-{int(parts[2]):02d}"
    return normalize_date(text)


def normalize_tpex_institution_row(fields: list[str], row: list[Any], data_date: str | None) -> dict[str, Any] | None:
    if len(row) < 24:
        return None
    code = normalize_tpex_institution_code(row[0])
    if not code:
        return None
    return {
        "date": data_date,
        "code": code,
        "name": str(row[1] or "").strip(),
        "foreign_net": _int_value(row[10]),
        "foreign_buy": _int_value(row[8]),
        "foreign_sell": _int_value(row[9]),
        "trust_net": _int_value(row[13]),
        "trust_buy": _int_value(row[11]),
        "trust_sell": _int_value(row[12]),
        "dealer_net": _int_value(row[22]),
        "dealer_buy": _int_value(row[20]),
        "dealer_sell": _int_value(row[21]),
        "source": "TPEX_INSTITUTION_DAILY_TRADE",
        "market": "otc",
        "raw": dict(zip(fields, row)),
    }


def fetch_tpex_institution_dry_run(
    date: str | None = None,
    *,
    timeout: float = 20,
    max_requests: int = 10,
    budget: RequestBudget | None = None,
    http_get: Callable[..., Any] | None = None,
    include_items: bool = False,
) -> dict[str, Any]:
    requested = normalize_date(date)
    budget = budget or RequestBudget(max_requests=max_requests)
    params = {"response": "json", "type": "Daily"}
    if requested:
        params["date"] = requested.replace("-", "/")
    url = TPEX_INSTITUTION_DAILY_TRADE_URL
    try:
        resp = budgeted_get_response(
            url,
            budget=budget,
            purpose="tpex_institution_dry_run",
            params=params,
            headers={**HEADERS, "Referer": "https://www.tpex.org.tw/", "Accept": "application/json, text/javascript, */*; q=0.01"},
            timeout=timeout,
            http_get=http_get,
        )
        status = resp.status_code
        raw_text = resp.text or ""
        if status >= 400:
            return {
                **budget.summary(),
                "ok": False,
                "market": "otc",
                "source": "TPEX_INSTITUTION_DAILY_TRADE",
                "official_url": resp.url,
                "http_status": status,
                "response_format": "error",
                "raw_response_snippet": mask_secret_text(raw_text[:500]),
                "writes_db": False,
                "error": f"HTTP {status}",
            }
        payload = resp.json()
        tables = payload.get("tables") if isinstance(payload, dict) else None
        table = tables[0] if isinstance(tables, list) and tables and isinstance(tables[0], dict) else {}
        fields = table.get("fields") if isinstance(table, dict) else []
        rows = table.get("data") if isinstance(table, dict) else []
        fields = fields if isinstance(fields, list) else []
        rows = rows if isinstance(rows, list) else []
        data_date = normalize_tpex_institution_date(table.get("date")) if isinstance(table, dict) else None
        parsed = [
            item
            for item in (normalize_tpex_institution_row(fields, row, data_date) for row in rows if isinstance(row, list))
            if item
        ]
        keys = [(r["date"], r["code"], r["source"], r["market"]) for r in parsed]
        duplicate_count = len(keys) - len(set(keys))
        result = {
            **budget.summary(),
            "ok": bool(parsed),
            "market": "otc",
            "source": "TPEX_INSTITUTION_DAILY_TRADE",
            "official_url": resp.url,
            "http_status": status,
            "response_format": "json",
            "encoding": resp.encoding,
            "data_date": data_date,
            "title": table.get("title") if isinstance(table, dict) else None,
            "csv_header_fields": fields,
            "raw_response_snippet": mask_secret_text(raw_text[:700]),
            "raw_rows_count": len(rows),
            "parsed_rows_count": len(parsed),
            "valid_rows_count": len(parsed),
            "invalid_rows_count": len(rows) - len(parsed),
            "distinct_stock_count": len({r["code"] for r in parsed}),
            "first_5_parsed_rows": [{k: v for k, v in r.items() if k != "raw"} for r in parsed[:5]],
            "field_mapping": {
                "code": "data[0] 代號",
                "name": "data[1] 名稱",
                "foreign_net": "data[10] 外資及陸資合計買賣超股數",
                "foreign_buy": "data[8] 外資及陸資合計買進股數",
                "foreign_sell": "data[9] 外資及陸資合計賣出股數",
                "trust_net": "data[13] 投信買賣超股數",
                "trust_buy": "data[11] 投信買進股數",
                "trust_sell": "data[12] 投信賣出股數",
                "dealer_net": "data[22] 自營商合計買賣超股數",
            },
            "would_upsert_count": len(parsed),
            "upsert_key_design": ["date", "code", "source", "market"],
            "current_table_key_warning": "institution_daily currently has PRIMARY KEY(date, code), so schema cannot distinguish source/market without a later approved migration.",
            "duplicate_key_count": duplicate_count,
            "warnings": [],
            "errors": [],
            "writes_db": False,
        }
        if include_items:
            result["items"] = parsed
        return result
    except RequestCapExceeded as exc:
        return {
            **budget.summary(),
            "ok": False,
            "market": "otc",
            "source": "TPEX_INSTITUTION_DAILY_TRADE",
            "official_url": url,
            "response_format": "json",
            "writes_db": False,
            "error": "request_cap_exceeded",
            "attempted_url": exc.attempted_url,
            "purpose": exc.purpose,
        }
    except Exception as exc:
        return {
            **budget_summary(budget, default_max_requests=max_requests),
            "ok": False,
            "market": "otc",
            "source": "TPEX_INSTITUTION_DAILY_TRADE",
            "official_url": url,
            "response_format": "json",
            "writes_db": False,
            "error": safe_error(exc),
        }
