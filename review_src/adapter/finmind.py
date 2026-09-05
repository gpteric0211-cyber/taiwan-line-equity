from __future__ import annotations

import logging
import threading
import time
from collections import deque
from datetime import timedelta
from typing import Any

from core.config import (
    FINMIND_API,
    FINMIND_HOURLY_SOFT_LIMIT,
    FINMIND_TOKEN,
    HEADERS,
    mask_secret_text,
    safe_error,
)
from core.http import request_json
from core.status import set_status
from core.utils import now_tpe, today_iso

FINMIND_TOKEN_DISABLED_REASON = ""
_finmind_rate_lock = threading.RLock()
_finmind_request_times: deque[float] = deque()


def get_finmind_token_disabled_reason() -> str:
    return FINMIND_TOKEN_DISABLED_REASON


BROKER_FLOW_DATASET = "TaiwanStockTradingDailyReport"
BROKER_FLOW_COLUMN_ALIASES = {
    "date": {"date", "Date"},
    "stock_code": {"stock_id", "code", "data_id"},
    "broker": {"securities_trader_id", "securities_trader", "broker", "broker_id", "????", "??"},
    "buy": {"buy", "Buy", "??", "????"},
    "sell": {"sell", "Sell", "??", "????"},
}

def finmind_rate_guard() -> None:
    """鍏嶈不椤嶅害淇濊锛氫笉鎵撴豢 600/hr锛屼娇鐢?soft limit 淇濈暀绶╄銆?
    涓嶅湪 _finmind_rate_lock 鍏у懠鍙?set_status()锛岄伩鍏嶆湭渚嗗舰鎴愯法閹栭爢搴忓晱椤屻€?    """
    now = time.time()
    blocked = False
    used = 0
    with _finmind_rate_lock:
        while _finmind_request_times and now - _finmind_request_times[0] > 3600:
            _finmind_request_times.popleft()
        used = len(_finmind_request_times)
        if used >= FINMIND_HOURLY_SOFT_LIMIT:
            blocked = True
        else:
            _finmind_request_times.append(now)

    if blocked:
        msg = f"FinMind rate guard active: {used}/{FINMIND_HOURLY_SOFT_LIMIT} calls used in the last hour"
        set_status("finmind_rate", "stale", msg)
        raise RuntimeError("FinMind rate guard active; background data repair paused")


def _finmind_parse_response(data: Any, dataset: str) -> list[dict[str, Any]]:
    if isinstance(data, dict) and data.get("status") not in (200, "200", None):
        raise RuntimeError(f"FinMind {dataset} status={data.get('status')} msg={mask_secret_text(data.get('msg'))}")
    rows = data.get("data", []) if isinstance(data, dict) else []
    return rows if isinstance(rows, list) else []


def _is_finmind_token_illegal_error(exc_or_text: Any) -> bool:
    text = str(exc_or_text or "").lower()
    return ("token is illegal" in text) or ("token illegal" in text) or ("invalid token" in text)


def finmind_get(dataset: str, code: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
    # Fetch FinMind data using query-string token style.
    global FINMIND_TOKEN_DISABLED_REASON
    finmind_rate_guard()
    params = {"dataset": dataset, "data_id": code, "start_date": start_date, "end_date": end_date}

    # First try the configured token only when it has not already been proven bad.
    # If FinMind rejects it with "Token is illegal", retry once without token and
    # remember that state so the next 50-stock batch does not spam 150 HTTP 400 errors.
    if FINMIND_TOKEN and not FINMIND_TOKEN_DISABLED_REASON:
        try:
            data = request_json(FINMIND_API, params={**params, "token": FINMIND_TOKEN}, headers=HEADERS, retries=1, retry_wait=1)
            return _finmind_parse_response(data, dataset)
        except Exception as exc:
            if not _is_finmind_token_illegal_error(exc):
                raise
            FINMIND_TOKEN_DISABLED_REASON = "FinMind token rejected; retrying with public access for this process"
            set_status("finmind_token", "stale", FINMIND_TOKEN_DISABLED_REASON)
            logging.warning("FinMind token rejected; retrying without token for this process")

    data = request_json(FINMIND_API, params=params, headers=HEADERS, retries=2, retry_wait=2)
    return _finmind_parse_response(data, dataset)


def _broker_flow_find_column(columns: list[str], aliases: set[str]) -> str | None:
    normalized = {str(col).strip(): str(col).strip() for col in columns}
    lower_map = {str(col).strip().lower(): str(col).strip() for col in columns}
    for alias in aliases:
        if alias in normalized:
            return normalized[alias]
        found = lower_map.get(alias.lower())
        if found:
            return found
    return None


def _broker_flow_column_map(rows: list[dict[str, Any]]) -> dict[str, str | None]:
    columns: list[str] = []
    seen: set[str] = set()
    for row in rows[:20]:
        for key in row.keys():
            clean = str(key).strip()
            if clean and clean not in seen:
                columns.append(clean)
                seen.add(clean)
    return {
        name: _broker_flow_find_column(columns, aliases)
        for name, aliases in BROKER_FLOW_COLUMN_ALIASES.items()
    }


def _broker_flow_reason_from_error(text: str) -> str:
    lower = str(text or "").lower()
    if any(x in lower for x in ["winerror 10013", "failed to establish a new connection", "connection", "network", "timeout"]):
        return "network_error"
    if "token" in lower and ("missing" in lower or "empty" in lower):
        return "token_missing"
    if any(x in lower for x in ["permission", "sponsor", "not authorized", "permission denied"]):
        return "sponsor_required"
    if any(x in lower for x in ["unauthorized", "401", "403", "token is illegal", "invalid token"]):
        return "permission_denied"
    if any(x in lower for x in ["dataset", "not found", "no such"]):
        return "dataset_unavailable"
    if any(x in lower for x in ["parse", "column"]):
        return "parse_failed"
    return "empty_data"


def _broker_flow_to_number(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).replace(",", "").replace("%", "").strip()
    if text in {"", "--", "-", "None", "null"}:
        return None
    try:
        return float(text)
    except Exception:
        return None


def _broker_flow_sample_rows(rows: list[dict[str, Any]], column_map: dict[str, str | None], limit: int = 3) -> list[dict[str, Any]]:
    sample: list[dict[str, Any]] = []
    for row in rows[:limit]:
        item: dict[str, Any] = {}
        for out_key, source_key in column_map.items():
            if source_key:
                item[out_key] = row.get(source_key)
        sample.append(item)
    return sample


def _broker_flow_parse_finmind_payload(data: Any, dataset: str) -> tuple[list[dict[str, Any]], str | None]:
    if isinstance(data, dict):
        status = data.get("status")
        if status not in (200, "200", None):
            return [], mask_secret_text(str(data.get("msg") or data.get("message") or status))
        rows = data.get("data") or []
        if isinstance(rows, list):
            return [r for r in rows if isinstance(r, dict)], None
    return [], f"{dataset} parse failed"


def _broker_flow_fetch_once(
    *,
    code: str,
    start_date: str,
    end_date: str,
    method: str,
) -> tuple[list[dict[str, Any]], str | None]:
    params = {
        "dataset": BROKER_FLOW_DATASET,
        "data_id": code,
        "start_date": start_date,
        "end_date": end_date,
    }
    headers = dict(HEADERS)
    if method == "query_token":
        params["token"] = FINMIND_TOKEN
    elif method == "authorization_header":
        headers["Authorization"] = f"Bearer {FINMIND_TOKEN}"
    data = request_json(FINMIND_API, params=params, headers=headers, retries=1, retry_wait=1, timeout=20)
    return _broker_flow_parse_finmind_payload(data, BROKER_FLOW_DATASET)


def _debug_broker_flow_source(code: str, days: int) -> dict[str, Any]:
    days = max(1, min(int(days or 5), 10))
    code = str(code).strip().zfill(4)[:4]
    if not FINMIND_TOKEN:
        return {
            "ok": False,
            "dataset": BROKER_FLOW_DATASET,
            "source": "FinMind",
            "code": code,
            "can_implement_broker_flow_concentration": False,
            "reason": "token_missing",
            "writes_db": False,
            "next_step": "Do not implement broker flow concentration until a FinMind token with dataset permission is available.",
        }
    end_date = today_iso()
    start_date = (now_tpe().date() - timedelta(days=days - 1)).isoformat()
    attempts: list[dict[str, Any]] = []

    for method in ("query_token", "authorization_header"):
        try:
            rows, parse_error = _broker_flow_fetch_once(
                code=code,
                start_date=start_date,
                end_date=end_date,
                method=method,
            )
            if parse_error:
                attempts.append({"method": method, "ok": False, "error": mask_secret_text(parse_error)})
                if method == "query_token":
                    continue
                reason = _broker_flow_reason_from_error(parse_error)
                return {
                    "ok": False,
                    "dataset": BROKER_FLOW_DATASET,
                    "source": "FinMind",
                    "code": code,
                    "method": method,
                    "can_implement_broker_flow_concentration": False,
                    "reason": reason,
                    "attempts": attempts,
                    "writes_db": False,
                    "next_step": "Do not implement broker flow concentration until data source is available.",
                }
            if not rows:
                attempts.append({"method": method, "ok": False, "error": "empty data"})
                if method == "query_token":
                    continue
                return {
                    "ok": False,
                    "dataset": BROKER_FLOW_DATASET,
                    "source": "FinMind",
                    "code": code,
                    "method": method,
                    "can_implement_broker_flow_concentration": False,
                    "reason": "empty_data",
                    "attempts": attempts,
                    "writes_db": False,
                    "next_step": "Do not implement broker flow concentration until data source is available.",
                }
            column_map = _broker_flow_column_map(rows)
            missing_columns = [name for name, col in column_map.items() if not col]
            sample_rows = _broker_flow_sample_rows(rows, column_map)
            buy_col = column_map.get("buy")
            sell_col = column_map.get("sell")
            numeric_buy_sell = any(
                (_broker_flow_to_number(row.get(buy_col)) is not None if buy_col else False)
                or (_broker_flow_to_number(row.get(sell_col)) is not None if sell_col else False)
                for row in rows[:20]
            )
            available_dates = sorted({str(row.get(column_map["date"])) for row in rows if column_map.get("date") and row.get(column_map["date"])})
            can_implement = not missing_columns and numeric_buy_sell
            return {
                "ok": can_implement,
                "dataset": BROKER_FLOW_DATASET,
                "source": "FinMind",
                "code": code,
                "method": method,
                "can_implement_broker_flow_concentration": can_implement,
                "reason": None if can_implement else "parse_failed",
                "row_count": len(rows),
                "available_dates": available_dates,
                "latest_date": max(available_dates) if available_dates else None,
                "columns": list(rows[0].keys()) if rows else [],
                "column_map": column_map,
                "missing_columns": missing_columns,
                "sample_rows": sample_rows,
                "unit_hint": "buy/sell appears numeric; unit must be verified before formula implementation" if numeric_buy_sell else "buy/sell unit unavailable; must verify before formula implementation",
                "writes_db": False,
                "attempts": attempts + [{"method": method, "ok": True}],
                "next_step": "Phase 2B-1 can design broker_flow_concentration tables and formula." if can_implement else "Do not implement broker flow concentration until data source is available.",
            }
        except Exception as exc:
            error = safe_error(exc)
            attempts.append({"method": method, "ok": False, "error": mask_secret_text(error)})
            if method == "query_token" and FINMIND_TOKEN:
                continue
            return {
                "ok": False,
                "dataset": BROKER_FLOW_DATASET,
                "source": "FinMind",
                "code": code,
                "method": method,
                "can_implement_broker_flow_concentration": False,
                "reason": _broker_flow_reason_from_error(error),
                "attempts": attempts,
                "writes_db": False,
                "next_step": "Do not implement broker flow concentration until data source is available.",
            }
    return {
        "ok": False,
        "dataset": BROKER_FLOW_DATASET,
        "source": "FinMind",
        "code": code,
        "can_implement_broker_flow_concentration": False,
        "reason": "empty_data",
        "attempts": attempts,
        "writes_db": False,
        "next_step": "Do not implement broker flow concentration until data source is available.",
    }
