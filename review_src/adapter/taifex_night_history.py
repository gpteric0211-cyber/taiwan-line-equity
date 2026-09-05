from __future__ import annotations

"""Official TAIFEX after-hours history download and deterministic parser."""

import csv
import io
import math
from datetime import date
from typing import Any

import requests

from adapter.taifex_night import TRACKED_CONTRACTS
from core.config import HEADERS, safe_error

try:
    import truststore  # type: ignore
except Exception:
    truststore = None
else:
    try:
        truststore.inject_into_ssl()
    except Exception:
        pass


TAIFEX_FUT_DATA_DOWN_URL = "https://www.taifex.com.tw/cht/3/futDataDown"
TAIFEX_FUT_DOWNLOAD_REFERER = "https://www.taifex.com.tw/cht/3/futDailyMarketView"


def _number(value: Any) -> float | None:
    text = str(value or "").replace(",", "").replace("%", "").strip()
    if not text or text in {"-", "--"}:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _trade_date(value: Any) -> str | None:
    text = str(value or "").strip().replace("/", "-")
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def parse_taifex_futures_csv(content: bytes) -> list[dict[str, Any]]:
    """Select the highest-volume outright month for each tracked contract/day."""

    decoded = content.decode("big5", errors="strict")
    reader = csv.reader(io.StringIO(decoded))
    next(reader, None)
    selected: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in reader:
        if len(raw) < 18:
            continue
        trade_date = _trade_date(raw[0])
        contract = str(raw[1] or "").strip().upper()
        contract_month = str(raw[2] or "").strip()
        last = _number(raw[6])
        change_pct = _number(raw[8])
        volume = _number(raw[9])
        trading_session = str(raw[17] or "").strip()
        if (
            trade_date is None
            or contract not in TRACKED_CONTRACTS
            or trading_session != "盤後"
            or "/" in contract_month
            or last is None
            or last <= 0
            or change_pct is None
            or volume is None
            or volume <= 0
        ):
            continue
        row = {
            "trade_date": trade_date,
            "contract": contract,
            "contract_month": contract_month,
            "last": last,
            "change_pct": change_pct,
            "volume": volume,
            "trading_session": trading_session,
            "source": "TAIFEX_FUT_DATA_DOWN",
            "source_quality": "official",
        }
        key = (trade_date, contract)
        previous = selected.get(key)
        if previous is None or volume > float(previous["volume"]):
            selected[key] = row
    return [selected[key] for key in sorted(selected)]


def fetch_taifex_futures_range(
    *,
    start_date: date,
    end_date: date,
    timeout: float = 90,
) -> dict[str, Any]:
    if end_date < start_date:
        raise ValueError("end_date cannot precede start_date")
    if (end_date - start_date).days > 31:
        raise ValueError("TAIFEX range download cannot exceed one month")
    try:
        response = requests.post(
            TAIFEX_FUT_DATA_DOWN_URL,
            data={
                "down_type": "1",
                "queryStartDate": start_date.strftime("%Y/%m/%d"),
                "queryEndDate": end_date.strftime("%Y/%m/%d"),
                "commodity_id": "all",
                "commodity_id2": "",
            },
            headers={**HEADERS, "Referer": TAIFEX_FUT_DOWNLOAD_REFERER},
            timeout=timeout,
        )
        response.raise_for_status()
        rows = parse_taifex_futures_csv(response.content)
    except Exception as exc:
        return {
            "ok": False,
            "status": "source_delayed",
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "rows": [],
            "error": safe_error(exc),
        }
    return {
        "ok": bool(rows),
        "status": "ok" if rows else "source_delayed",
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "rows": rows,
        "row_count": len(rows),
    }
