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

TWSE_INSTITUTION_T86_URL = "https://www.twse.com.tw/rwd/zh/fund/T86"


def normalize_twse_institution_code(value: Any) -> str | None:
    text = str(value or "").strip()
    return text if re.fullmatch(r"\d{4}", text) else None


def _int_value(value: Any) -> int | None:
    num = parse_num(value)
    return int(num) if num is not None else None


def normalize_twse_institution_row(fields: list[str], row: list[Any], data_date: str | None) -> dict[str, Any] | None:
    if len(row) < 18:
        return None
    code = normalize_twse_institution_code(row[0])
    if not code:
        return None
    return {
        "date": data_date,
        "code": code,
        "name": str(row[1] or "").strip(),
        "foreign_net": _int_value(row[4]),
        "foreign_buy": _int_value(row[2]),
        "foreign_sell": _int_value(row[3]),
        "trust_net": _int_value(row[10]),
        "trust_buy": _int_value(row[8]),
        "trust_sell": _int_value(row[9]),
        "dealer_net": _int_value(row[11]),
        "dealer_buy": (_int_value(row[12]) or 0) + (_int_value(row[15]) or 0),
        "dealer_sell": (_int_value(row[13]) or 0) + (_int_value(row[16]) or 0),
        "source": "TWSE_T86",
        "market": "listed",
        "raw": dict(zip(fields, row)),
    }


def fetch_twse_institution_dry_run(
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
    params = {
        "response": "json",
        "date": (requested or "").replace("-", ""),
        "selectType": "ALLBUT0999",
    }
    if not requested:
        params.pop("date", None)
    url = TWSE_INSTITUTION_T86_URL
    try:
        resp = budgeted_get_response(
            url,
            budget=budget,
            purpose="twse_institution_dry_run",
            params=params,
            headers={**HEADERS, "Referer": "https://www.twse.com.tw/", "Accept": "application/json, text/javascript, */*; q=0.01"},
            timeout=timeout,
            http_get=http_get,
        )
        status = resp.status_code
        raw_text = resp.text or ""
        if status >= 400:
            return {
                **budget.summary(),
                "ok": False,
                "market": "listed",
                "source": "TWSE_T86",
                "official_url": resp.url,
                "http_status": status,
                "response_format": "error",
                "raw_response_snippet": mask_secret_text(raw_text[:500]),
                "writes_db": False,
                "error": f"HTTP {status}",
            }
        payload = resp.json()
        fields = payload.get("fields") if isinstance(payload, dict) else None
        rows = payload.get("data") if isinstance(payload, dict) else None
        fields = fields if isinstance(fields, list) else []
        rows = rows if isinstance(rows, list) else []
        data_date = normalize_date(payload.get("date")) if isinstance(payload, dict) else None
        parsed = [
            item
            for item in (normalize_twse_institution_row(fields, row, data_date) for row in rows if isinstance(row, list))
            if item
        ]
        keys = [(r["date"], r["code"], r["source"], r["market"]) for r in parsed]
        duplicate_count = len(keys) - len(set(keys))
        result = {
            **budget.summary(),
            "ok": bool(parsed),
            "market": "listed",
            "source": "TWSE_T86",
            "official_url": resp.url,
            "http_status": status,
            "response_format": "json",
            "encoding": resp.encoding,
            "data_date": data_date,
            "title": payload.get("title") if isinstance(payload, dict) else None,
            "csv_header_fields": fields,
            "raw_response_snippet": mask_secret_text(raw_text[:700]),
            "raw_rows_count": len(rows),
            "parsed_rows_count": len(parsed),
            "valid_rows_count": len(parsed),
            "invalid_rows_count": len(rows) - len(parsed),
            "distinct_stock_count": len({r["code"] for r in parsed}),
            "first_5_parsed_rows": [{k: v for k, v in r.items() if k != "raw"} for r in parsed[:5]],
            "field_mapping": {
                "code": "fields[0] 證券代號",
                "name": "fields[1] 證券名稱",
                "foreign_net": "fields[4] 外陸資買賣超股數(不含外資自營商)",
                "foreign_buy": "fields[2] 外陸資買進股數(不含外資自營商)",
                "foreign_sell": "fields[3] 外陸資賣出股數(不含外資自營商)",
                "trust_net": "fields[10] 投信買賣超股數",
                "trust_buy": "fields[8] 投信買進股數",
                "trust_sell": "fields[9] 投信賣出股數",
                "dealer_net": "fields[11] 自營商買賣超股數",
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
            "market": "listed",
            "source": "TWSE_T86",
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
            "market": "listed",
            "source": "TWSE_T86",
            "official_url": url,
            "response_format": "json",
            "writes_db": False,
            "error": safe_error(exc),
        }
