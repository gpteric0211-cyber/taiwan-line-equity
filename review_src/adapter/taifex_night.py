from __future__ import annotations

import json
import csv
import io
import math
from datetime import date
from typing import Any

import requests

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


TAIFEX_DAILY_FUTURES_URL = "https://openapi.taifex.com.tw/v1/DailyMarketReportFut"
TRACKED_CONTRACTS = {"TX", "MTX", "TE", "ZEF", "TF", "ZFF", "SOF"}


def _number(value: Any) -> float | None:
    try:
        number = float(str(value or "").replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _date(value: Any) -> str | None:
    text = str(value or "").strip()
    if len(text) != 8 or not text.isdigit():
        return None
    try:
        return date(int(text[:4]), int(text[4:6]), int(text[6:8])).isoformat()
    except ValueError:
        return None


def _report_rows(text: str) -> list[dict[str, Any]]:
    clean=text.lstrip("\ufeff\r\n \t")
    if clean.startswith(("[", "{")):
        payload=json.loads(clean)
        if not isinstance(payload,list):
            raise ValueError("TAIFEX JSON report must be a row list")
        return payload
    # The official endpoint can return a BOM-prefixed CSV despite Accept: JSON.
    fields={"日期":"Date","契約代號":"Contract","到期月份(週別)":"ContractMonth(Week)",
        "最後成交價":"Last","漲跌%":"%","合計成交量":"Volume","交易時段":"TradingSession"}
    reader=csv.DictReader(io.StringIO(clean))
    if not set(fields).issubset(reader.fieldnames or []):
        raise ValueError("TAIFEX report schema is neither the documented JSON nor CSV layout")
    return [{target:row[source] for source,target in fields.items()} for row in reader]
def fetch_taifex_night_snapshot(timeout: float = 20) -> dict[str, Any]:
    try:
        response = requests.get(
            TAIFEX_DAILY_FUTURES_URL,
            headers={**HEADERS, "Accept": "application/json"},
            timeout=timeout,
        )
        response.raise_for_status()
        response.encoding = "utf-8"
        payload = _report_rows(response.text)
    except Exception as exc:
        return {"ok": False, "status": "source_delayed", "items": [], "error": safe_error(exc)}
    rows = payload if isinstance(payload, list) else []
    candidates: list[dict[str, Any]] = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        contract = str(raw.get("Contract") or "").strip().upper()
        session = str(raw.get("TradingSession") or "").strip()
        month = str(raw.get("ContractMonth(Week)") or "").strip()
        trade_date = _date(raw.get("Date"))
        last = _number(raw.get("Last"))
        change_pct = _number(raw.get("%"))
        volume = _number(raw.get("Volume"))
        if (
            contract not in TRACKED_CONTRACTS
            or "盤後" not in session
            or "/" in month
            or not trade_date
            or last is None
            or last <= 0
            or change_pct is None
            or volume is None
            or volume <= 0
        ):
            continue
        candidates.append(
            {
                "trade_date": trade_date,
                "contract": contract,
                "contract_month": month,
                "last": last,
                "change_pct": change_pct,
                "volume": volume,
                "trading_session": session,
                "source": "TAIFEX_DAILY_MARKET_REPORT_FUT",
                "source_quality": "official",
            }
        )
    if not candidates:
        return {"ok": False, "status": "source_delayed", "items": [], "reason": "no_after_hours_rows"}
    latest_date = max(str(row["trade_date"]) for row in candidates)
    latest = [row for row in candidates if row["trade_date"] == latest_date]
    selected: dict[str, dict[str, Any]] = {}
    for row in latest:
        contract = str(row["contract"])
        if contract not in selected or float(row["volume"]) > float(selected[contract]["volume"]):
            selected[contract] = row
    return {
        "ok": bool(selected),
        "status": "ok" if selected else "source_delayed",
        "trade_date": latest_date,
        "items": list(selected.values()),
        "rows": len(selected),
    }
