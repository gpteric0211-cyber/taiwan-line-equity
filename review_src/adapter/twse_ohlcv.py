from __future__ import annotations

from typing import Any, Callable

from adapter.twse import TWSE_STOCK_DAY_ALL, normalize_twse_stock_day_all_row
from core.request_budget import RequestBudget, RequestCapExceeded, budget_summary, budgeted_get_json
from core.utils import normalize_date


def fetch_twse_ohlcv_dry_run(
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
            TWSE_STOCK_DAY_ALL,
            budget=budget,
            purpose="twse_ohlcv_dry_run",
            retries=2,
            retry_wait=2,
            timeout=20,
            http_get=http_get,
        )
        rows = data if isinstance(data, list) else []
        inferred_dates = [normalize_date(r.get("Date")) for r in rows if isinstance(r, dict)]
        inferred_dates = [d for d in inferred_dates if d]
        data_date = max(inferred_dates) if inferred_dates else None
        parsed = [
            item
            for item in (normalize_twse_stock_day_all_row(row, data_date) for row in rows if isinstance(row, dict))
            if item
        ]
        items = [row for row in parsed if not requested_date or row.get("date") == requested_date]
        keys = [(row.get("date"), row.get("code"), row.get("source"), row.get("market")) for row in items]
        duplicate_count = len(keys) - len(set(keys))
        return {
            **budget.summary(),
            "ok": bool(items),
            "market": "listed",
            "source": "TWSE_OFFICIAL",
            "official_url": TWSE_STOCK_DAY_ALL,
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
                "code": "Code",
                "date": "Date",
                "open": "OpeningPrice",
                "high": "HighestPrice",
                "low": "LowestPrice",
                "close": "ClosingPrice",
                "volume": "TradeVolume",
                "amount": "TradeValue",
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
            "market": "listed",
            "source": "TWSE_OFFICIAL",
            "official_url": TWSE_STOCK_DAY_ALL,
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
            "source": "TWSE_OFFICIAL",
            "official_url": TWSE_STOCK_DAY_ALL,
            "response_format": "json",
            "writes_db": False,
            "error": str(exc),
        }
