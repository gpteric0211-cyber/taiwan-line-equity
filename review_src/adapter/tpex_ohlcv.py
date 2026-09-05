from __future__ import annotations

from typing import Any, Callable

from adapter.tpex import TPEX_DAILY_CLOSE_URL, normalize_tpex_daily_close_row
from core.config import HEADERS
from core.request_budget import RequestBudget, RequestCapExceeded, budget_summary, budgeted_get_json


def fetch_tpex_ohlcv_dry_run(
    date: str | None = None,
    *,
    max_requests: int = 10,
    budget: RequestBudget | None = None,
    http_get: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    budget = budget or RequestBudget(max_requests=max_requests)
    requested_date = str(date or "").strip() or None
    try:
        data = budgeted_get_json(
            TPEX_DAILY_CLOSE_URL,
            budget=budget,
            purpose="tpex_ohlcv_dry_run",
            headers=HEADERS,
            retries=2,
            retry_wait=1.5,
            timeout=20,
            http_get=http_get,
        )
        rows = data if isinstance(data, list) else []
        parsed = [
            item
            for item in (normalize_tpex_daily_close_row(row) for row in rows if isinstance(row, dict))
            if item
        ]
        dates = [str(item.get("date")) for item in parsed if item.get("date")]
        data_date = max(dates) if dates else None
        items = [row for row in parsed if not requested_date or row.get("date") == requested_date]
        keys = [(row.get("date"), row.get("code"), row.get("source"), row.get("market") or "otc") for row in items]
        duplicate_count = len(keys) - len(set(keys))
        return {
            **budget.summary(),
            "ok": bool(items),
            "market": "otc",
            "source": "TPEX_OFFICIAL",
            "official_url": TPEX_DAILY_CLOSE_URL,
            "response_format": "json",
            "encoding": "utf-8",
            "requested_date": requested_date,
            "data_date": data_date,
            "columns": list(rows[0].keys()) if rows else [],
            "raw_response_snippet": "Fetched through budgeted official dry-run adapter; raw full payload is not emitted to avoid large reports.",
            "raw_rows_count": len(rows),
            "parsed_rows_count": len(parsed),
            "valid_rows_count": len(items),
            "invalid_rows_count": len(rows) - len(parsed),
            "distinct_stock_count": len({r.get("code") for r in items}),
            "first_5_parsed_rows": [{k: v for k, v in r.items() if k != "raw"} for r in items[:5]],
            "field_mapping": {
                "code": "SecuritiesCompanyCode / Code",
                "date": "Date",
                "open": "Open / OpeningPrice",
                "high": "High / HighestPrice",
                "low": "Low / LowestPrice",
                "close": "Close / ClosingPrice",
                "volume": "TradingShares / TradeVolume",
                "amount": "TransactionAmount / TradeValue",
            },
            "would_upsert_count": len(items),
            "upsert_key_design": ["date", "code"],
            "duplicate_key_count": duplicate_count,
            "warnings": [],
            "errors": [],
            "writes_db": False,
        }
    except RequestCapExceeded as exc:
        return {
            **budget.summary(),
            "ok": False,
            "market": "otc",
            "source": "TPEX_OFFICIAL",
            "official_url": TPEX_DAILY_CLOSE_URL,
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
            "source": "TPEX_OFFICIAL",
            "official_url": TPEX_DAILY_CLOSE_URL,
            "response_format": "json",
            "writes_db": False,
            "error": str(exc),
        }
