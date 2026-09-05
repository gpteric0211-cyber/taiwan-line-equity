from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

from core.config import TAIFEX_OPENAPI_BASE, safe_error
from core.http import request_json
from core.utils import fmt, parse_num

_taifex_cache: dict[str, tuple[float, Any]] = {}
_taifex_lock = threading.RLock()
TAIFEX_CACHE_TTL_SECONDS = int(os.getenv("TAIFEX_CACHE_TTL_SECONDS", "900"))

FINANCE_CODES = {"2880", "2881", "2882", "2883", "2884", "2885", "2886", "2887", "2888", "2890", "2891", "2892"}
SEMICONDUCTOR_CODES = {"2330", "2303", "2454", "3034", "3711", "2449", "6669", "3661"}
ELECTRONICS_CODES = {
    "2308", "2317", "2327", "2357", "2382", "2395", "2408", "2412", "2454", "3008",
    "3017", "3034", "3231", "3653", "3711", "4938", "6669", "7769",
}


def fetch_taifex_openapi(endpoint: str) -> Any:
    key = endpoint.strip("/")
    now = time.time()
    with _taifex_lock:
        cached = _taifex_cache.get(key)
        if cached and now - cached[0] < TAIFEX_CACHE_TTL_SECONDS:
            return cached[1]
    data = request_json(f"{TAIFEX_OPENAPI_BASE}/{key}", retries=1, timeout=10)
    with _taifex_lock:
        _taifex_cache[key] = (time.time(), data)
    return data


def _futures_num(v: Any) -> float | None:
    return parse_num(v)


def _futures_pct(v: Any) -> float | None:
    return parse_num(v)


def _is_after_hours_session(value: Any) -> bool:
    text = str(value or "").strip()
    return text in {"盤後", "after-hours", "after_hours", "AH"} or "盤後" in text or "after" in text.lower()


def _is_regular_session(value: Any) -> bool:
    text = str(value or "").strip()
    return text in {"一般", "regular", "regular-hours", "regular_hours"} or "一般" in text or "regular" in text.lower()


def _front_futures_row(rows: list[dict[str, Any]], contract: str, *, after_hours: bool = True) -> dict[str, Any] | None:
    contract = str(contract or "").strip().upper()
    candidates = []
    for r in rows:
        if str(r.get("Contract") or "").strip().upper() != contract:
            continue
        month = str(r.get("ContractMonth(Week)") or "")
        if "/" in month:
            continue
        session = r.get("TradingSession")
        if after_hours and not _is_after_hours_session(session):
            continue
        if not after_hours and not _is_regular_session(session):
            continue
        last = _futures_num(r.get("Last"))
        volume = _futures_num(r.get("Volume")) or 0
        if last is None or volume <= 0:
            continue
        candidates.append(r)
    if not candidates:
        return None
    # For practical next-day reference, prefer the liquid front active contract
    # rather than a thin weekly or far-month contract.
    return max(candidates, key=lambda r: (_futures_num(r.get("Volume")) or 0))


def _stock_futures_contracts(code: str) -> list[dict[str, Any]]:
    code = str(code).zfill(4)
    out: list[dict[str, Any]] = []
    try:
        rows = fetch_taifex_openapi("SingleStockFuturesMargining")
        if isinstance(rows, list):
            for r in rows:
                if str(r.get("UnderlyingSecurityCode") or "").zfill(4) == code:
                    out.append({
                        "contract": str(r.get("Contract") or "").strip().upper(),
                        "name": r.get("ContractName") or "",
                        "source": "TAIFEX SingleStockFuturesMargining",
                    })
    except Exception as exc:
        logging.warning("TAIFEX single stock futures contract lookup failed for %s: %s", code, safe_error(exc))
    return out


def futures_relation_candidates(code: str) -> list[dict[str, Any]]:
    code = str(code).zfill(4)
    rels: list[dict[str, Any]] = []
    # Individual stock futures are verified from TAIFEX official margin table.
    # Most single-stock futures do not have an after-hours session; they are shown
    # as liquidity context only when no after-hours row exists.
    for r in _stock_futures_contracts(code)[:2]:
        rels.append({
            "contract": r["contract"],
            "name": r["name"],
            "relation": "individual_stock_future",
            "weight": 1.0,
            "min_volume_threshold": 50,
            "requires_after_hours": True,
            "source": r["source"],
        })
    if code in FINANCE_CODES:
        rels += [
            {"contract": "TF", "name": "閲戣瀺鏈熻波", "relation": "finance_sector_future", "weight": 0.85, "min_volume_threshold": 80, "requires_after_hours": True, "source": "TAIFEX DailyMarketReportFut"},
            {"contract": "ZFF", "name": "灏忓瀷閲戣瀺鏈熻波", "relation": "finance_sector_future", "weight": 0.55, "min_volume_threshold": 50, "requires_after_hours": True, "source": "TAIFEX DailyMarketReportFut"},
        ]
    elif code == "2330":
        rels += [
            {"contract": "SOF", "name": "鍗婂皫楂?0鏈熻波", "relation": "semiconductor_30_future_high_component", "weight": 0.9, "min_volume_threshold": 100, "requires_after_hours": True, "source": "TAIFEX DailyMarketReportFut"},
            {"contract": "TE", "name": "闆诲瓙鏈熻波", "relation": "electronics_sector_future", "weight": 0.45, "min_volume_threshold": 80, "requires_after_hours": True, "source": "TAIFEX DailyMarketReportFut"},
            {"contract": "ZEF", "name": "灏忓瀷闆诲瓙鏈熻波", "relation": "electronics_sector_future", "weight": 0.35, "min_volume_threshold": 80, "requires_after_hours": True, "source": "TAIFEX DailyMarketReportFut"},
        ]
    elif code in SEMICONDUCTOR_CODES:
        rels += [
            {"contract": "TE", "name": "闆诲瓙鏈熻波", "relation": "electronics_sector_future", "weight": 0.55, "min_volume_threshold": 80, "requires_after_hours": True, "source": "TAIFEX DailyMarketReportFut"},
            {"contract": "ZEF", "name": "灏忓瀷闆诲瓙鏈熻波", "relation": "electronics_sector_future", "weight": 0.45, "min_volume_threshold": 80, "requires_after_hours": True, "source": "TAIFEX DailyMarketReportFut"},
        ]
    elif code in ELECTRONICS_CODES:
        rels += [
            {"contract": "TE", "name": "闆诲瓙鏈熻波", "relation": "electronics_sector_future", "weight": 0.6, "min_volume_threshold": 80, "requires_after_hours": True, "source": "TAIFEX DailyMarketReportFut"},
            {"contract": "ZEF", "name": "灏忓瀷闆诲瓙鏈熻波", "relation": "electronics_sector_future", "weight": 0.45, "min_volume_threshold": 80, "requires_after_hours": True, "source": "TAIFEX DailyMarketReportFut"},
        ]
    rels += [
        {"contract": "TX", "name": "鑷鸿偂鏈熻波", "relation": "broad_market_future", "weight": 0.2, "min_volume_threshold": 5000, "requires_after_hours": True, "source": "TAIFEX DailyMarketReportFut"},
        {"contract": "MTX", "name": "灏忓瀷鑷烘寚鏈熻波", "relation": "broad_market_future", "weight": 0.15, "min_volume_threshold": 5000, "requires_after_hours": True, "source": "TAIFEX DailyMarketReportFut"},
    ]
    dedup: dict[str, dict[str, Any]] = {}
    for r in rels:
        key = r["contract"]
        if key not in dedup or float(r["weight"]) > float(dedup[key]["weight"]):
            dedup[key] = r
    return list(dedup.values())


def futures_night_signal_for_stock(code: str) -> dict[str, Any]:
    code = str(code).zfill(4)
    base = {
        "available": False,
        "source": "TAIFEX OpenAPI DailyMarketReportFut",
        "source_url": f"{TAIFEX_OPENAPI_BASE}/DailyMarketReportFut",
        "can_override_main_status": False,
        "confidence": "none",
        "direction": "unavailable",
        "label": "尚未取得期交所官方盤後期貨資料",
        "items": [],
        "weighted_change_pct": None,
        "quality_reason": "",
    }
    try:
        rows_raw = fetch_taifex_openapi("DailyMarketReportFut")
    except Exception as exc:
        base["quality_reason"] = f"TAIFEX OpenAPI 讀取失敗：{safe_error(exc)}"
        return base
    if not isinstance(rows_raw, list) or not rows_raw:
        base["quality_reason"] = "TAIFEX OpenAPI 回傳空資料"
        return base
    rows = [r for r in rows_raw if isinstance(r, dict)]
    items: list[dict[str, Any]] = []
    weighted_sum = 0.0
    weight_sum = 0.0
    for rel in futures_relation_candidates(code):
        row = _front_futures_row(rows, rel["contract"], after_hours=True)
        regular_row = None
        used = False
        reason = ""
        if not row:
            regular_row = _front_futures_row(rows, rel["contract"], after_hours=False)
            if regular_row:
                reason = "有官方日盤成交，但缺官方盤後成交列；不納入夜盤訊號"
            else:
                reason = "無官方盤後成交列"
        else:
            vol = _futures_num(row.get("Volume")) or 0.0
            pct_val = _futures_pct(row.get("%"))
            if vol < float(rel["min_volume_threshold"]):
                reason = f"盤後量 {fmt(vol)} 低於門檻 {rel['min_volume_threshold']}"
            elif pct_val is None:
                reason = "官方漲跌幅缺值"
            else:
                used = True
                weighted_sum += pct_val * float(rel["weight"])
                weight_sum += float(rel["weight"])
        display_row = row or regular_row
        item = {
            "contract": rel["contract"],
            "name": rel["name"],
            "relation": rel["relation"],
            "weight": rel["weight"],
            "min_volume_threshold": rel["min_volume_threshold"],
            "used": used,
            "reason": reason,
            "date": display_row.get("Date") if display_row else None,
            "month": display_row.get("ContractMonth(Week)") if display_row else None,
            "session": display_row.get("TradingSession") if display_row else "盤後",
            "last": _futures_num(display_row.get("Last")) if display_row else None,
            "change_pct": _futures_pct(display_row.get("%")) if display_row else None,
            "volume": _futures_num(display_row.get("Volume")) if display_row else None,
            "settlement_price": _futures_num(display_row.get("SettlementPrice")) if display_row else None,
            "source": rel["source"],
        }
        items.append(item)
    used_items = [x for x in items if x.get("used")]
    base["items"] = items
    if not used_items or weight_sum <= 0:
        base["quality_reason"] = "沒有通過流動性門檻的高相關盤後期貨商品"
        base["label"] = "無足夠流動性的官方盤後期貨訊號"
        return base
    weighted = weighted_sum / weight_sum
    direction = "bullish" if weighted >= 0.8 else "bearish" if weighted <= -0.8 else "neutral"
    max_weight = max(float(x.get("weight") or 0) for x in used_items)
    confidence = "high" if max_weight >= 0.8 and len(used_items) >= 2 else "medium" if max_weight >= 0.45 else "low"
    latest_dates = sorted({str(x.get("date") or "") for x in used_items if x.get("date")})
    base.update({
        "available": True,
        "confidence": confidence,
        "direction": direction,
        "label": "期貨盤後偏多" if direction == "bullish" else "期貨盤後偏空" if direction == "bearish" else "期貨盤後中性",
        "weighted_change_pct": round(weighted, 2),
        "quality_reason": "官方期交所盤後資料；使用官方 % 欄位，不以現貨收盤價自行換算",
        "date": latest_dates[-1] if latest_dates else None,
        "used_count": len(used_items),
    })
    return base
