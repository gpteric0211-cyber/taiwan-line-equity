from __future__ import annotations

import json
import math
from contextlib import closing
from typing import Any

from core.config import CHIP_SCORE_LOW_VOLUME_THRESHOLD, TDCC_TREND_WEEKS
from core.db import db
from core.utils import fmt, normalize_date, now_tpe, parse_num, today_iso
from repository.daily_chip_momentum_repository import (
    cleanup_daily_chip_momentum_history,
    get_latest_chip_momentum,
    upsert_daily_chip_momentum,
)
from repository.full_market_batch_repository import resolve_full_market_analysis_date


VALID_PHASES = {"intraday_estimate", "closing_correction", "complete", "incomplete"}
VALID_DATA_STATUS = {"ok", "partial", "source_delayed", "source_unavailable", "low_volume_skip"}


def _lots(value: Any) -> int | None:
    num = parse_num(value)
    if num is None:
        return None
    return int(round(float(num) / 1000.0))


def _int_or_none(value: Any) -> int | None:
    num = parse_num(value)
    return int(round(float(num))) if num is not None and math.isfinite(float(num)) else None


def _ratio(num: Any, den: Any) -> float | None:
    n = parse_num(num)
    d = parse_num(den)
    if n is None or d in (None, 0):
        return None
    return round(float(n) / float(d), 4)


def _latest_price_row(conn, code: str, date: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM history_price WHERE code=? AND date<=? ORDER BY date DESC LIMIT 1",
        (code, date),
    ).fetchone()
    return dict(row) if row else None


def _previous_close(conn, code: str, date: str) -> float | None:
    row = conn.execute(
        "SELECT close FROM history_price WHERE code=? AND date<? AND close IS NOT NULL ORDER BY date DESC LIMIT 1",
        (code, date),
    ).fetchone()
    if row:
        return parse_num(row["close"])
    row = conn.execute(
        "SELECT close FROM daily_chip_momentum WHERE stock_id=? AND date<? AND close IS NOT NULL ORDER BY date DESC LIMIT 1",
        (code, date),
    ).fetchone()
    return parse_num(row["close"]) if row else None


def _volume_average(conn, code: str, date: str, days: int) -> float | None:
    rows = conn.execute(
        "SELECT volume FROM history_price WHERE code=? AND date<=? AND volume IS NOT NULL ORDER BY date DESC LIMIT ?",
        (code, date, int(days)),
    ).fetchall()
    lots = [_lots(row["volume"]) for row in rows]
    nums = [float(v) for v in lots if v is not None]
    if not nums:
        return None
    return sum(nums) / len(nums)


def _institution_row(conn, code: str, date: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM institution_daily WHERE code=? AND date<=? ORDER BY date DESC LIMIT 1",
        (code, date),
    ).fetchone()
    return dict(row) if row else None


def _margin_row(conn, code: str, date: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM margin_daily WHERE code=? AND date<=? ORDER BY date DESC LIMIT 1",
        (code, date),
    ).fetchone()
    return dict(row) if row else None


def _intraday_snapshot(conn, code: str) -> dict[str, Any] | None:
    try:
        row = conn.execute("SELECT * FROM intraday_quote_snapshot WHERE stock_id=?", (code,)).fetchone()
    except Exception:
        return None
    return dict(row) if row else None


def calculate_daily_chip_score(row: dict[str, Any]) -> int | None:
    volume = parse_num(row.get("volume"))
    if volume is None or volume < CHIP_SCORE_LOW_VOLUME_THRESHOLD:
        return None
    score = 0
    inst_total_net = parse_num(row.get("inst_total_net"))
    foreign_net = parse_num(row.get("foreign_net"))
    trust_net = parse_num(row.get("trust_net"))
    margin_balance = parse_num(row.get("margin_balance"))
    margin_change = parse_num(row.get("margin_change"))
    close = parse_num(row.get("close"))
    previous_close = parse_num(row.get("previous_close"))
    volume_ratio_5d = parse_num(row.get("volume_ratio_5d"))
    if inst_total_net is not None and inst_total_net > 0:
        score += 1
    if foreign_net is not None and foreign_net > 0 and trust_net is not None and trust_net > 0:
        score += 1
    if inst_total_net is not None and volume > 0 and inst_total_net / volume > 0.05:
        score += 1
    if margin_balance not in (None, 0) and margin_change is not None:
        if margin_change < 0 or abs(margin_change / margin_balance) < 0.01:
            score += 1
    if close is not None and previous_close is not None and close > previous_close and volume_ratio_5d is not None and volume_ratio_5d > 1:
        score += 1
    if inst_total_net is not None and inst_total_net < 0:
        score -= 1
    if margin_balance not in (None, 0) and margin_change is not None and margin_change / margin_balance > 0.03:
        score -= 1
    if close is not None and previous_close is not None and close > previous_close and inst_total_net is not None and inst_total_net < 0 and margin_change is not None and margin_change > 0:
        score -= 2
    return max(-4, min(4, score))


def _label_for_score(score: int | None) -> str:
    if score is None:
        return "量能不足，暫不判斷"
    if score >= 3:
        return "籌碼偏多"
    if score >= 1:
        return "籌碼略偏多"
    if score == 0:
        return "籌碼中性"
    if score >= -2:
        return "籌碼略偏空"
    return "籌碼偏空"


def generate_chip_summary(row: dict[str, Any]) -> str:
    label = str(row.get("chip_label") or _label_for_score(row.get("chip_score")))
    phase = str(row.get("last_updated_phase") or "")
    data_status = str(row.get("data_status") or "")
    parts: list[str] = []
    if phase == "intraday_estimate":
        parts.append("盤中暫估，收盤後校正")
    elif phase == "closing_correction":
        parts.append("收盤校正中")
    elif phase == "incomplete":
        parts.append("資料暫不完整")
    elif data_status in {"partial", "source_delayed"}:
        parts.append("資料暫不完整")
    else:
        parts.append("完整")
    parts.append(label)
    inst = parse_num(row.get("inst_total_net"))
    margin_change = parse_num(row.get("margin_change"))
    tdcc = str(row.get("tdcc_trend") or "")
    if inst is not None:
        parts.append("法人偏買" if inst > 0 else ("法人偏賣" if inst < 0 else "法人中性"))
    if margin_change is not None:
        parts.append("融資增加" if margin_change > 0 else ("融資減少" if margin_change < 0 else "融資持平"))
    if tdcc and tdcc != "資料不足":
        parts.append(tdcc)
    return "；".join(parts)[:120]


def calc_tdcc_trend(stock_id: str, weeks: int = TDCC_TREND_WEEKS) -> dict[str, Any]:
    code = str(stock_id or "").strip().zfill(4)[:4]
    if not code.isdigit():
        return {"trend": "資料不足", "weeks_observed": 0}
    try:
        with closing(db()) as conn:
            rows = conn.execute(
                """
                SELECT date,total_holders,big_400_share_pct,big_1000_share_pct
                FROM tdcc_equity_summary
                WHERE code=?
                ORDER BY date DESC
                LIMIT ?
                """,
                (code, int(weeks)),
            ).fetchall()
    except Exception:
        return {"trend": "資料不足", "weeks_observed": 0}
    if len(rows) < 2:
        return {"trend": "資料不足", "weeks_observed": len(rows)}
    latest = dict(rows[0])
    oldest = dict(rows[-1])
    latest_whale = parse_num(latest.get("big_400_share_pct"))
    oldest_whale = parse_num(oldest.get("big_400_share_pct"))
    fallback_level = "400"
    if latest_whale is None or oldest_whale is None:
        latest_whale = parse_num(latest.get("big_1000_share_pct"))
        oldest_whale = parse_num(oldest.get("big_1000_share_pct"))
        fallback_level = "1000"
    holders_latest = parse_num(latest.get("total_holders"))
    holders_oldest = parse_num(oldest.get("total_holders"))
    whale_change = None if latest_whale is None or oldest_whale is None else round(latest_whale - oldest_whale, 4)
    holder_change = None if holders_latest is None or holders_oldest is None else int(round(holders_latest - holders_oldest))
    trend = "籌碼持平"
    if whale_change is None:
        trend = "資料不足"
    elif abs(whale_change) < 0.5:
        trend = "籌碼持平"
    elif whale_change > 0 and holder_change is not None and holder_change < 0:
        trend = "籌碼集中"
    elif whale_change < 0 and holder_change is not None and holder_change > 0:
        trend = "籌碼發散"
    return {
        "trend": trend,
        "weeks_observed": len(rows),
        "whale_change_pct": whale_change,
        "shareholder_change": holder_change,
        "fallback_level": fallback_level,
    }


def _base_completeness() -> dict[str, str]:
    return {
        "price": "missing",
        "institution": "missing",
        "margin": "missing",
        "tdcc": "missing",
        "intraday": "disabled",
    }


def _source_flags() -> dict[str, str]:
    return {
        "price_source": "none",
        "inst_source": "none",
        "margin_source": "none",
        "intraday_source": "none",
    }


def _row_for_code(code: str, *, date: str | None, mode: str) -> dict[str, Any]:
    code = str(code or "").strip().zfill(4)[:4]
    target = normalize_date(date)
    with closing(db()) as conn:
        analysis_date = resolve_full_market_analysis_date(conn, target)
        price_row = (
            _latest_price_row(conn, code, analysis_date)
            if analysis_date
            else None
        )
        if not price_row:
            return {
                "date": analysis_date or target or today_iso(),
                "stock_id": code,
                "last_updated_phase": "incomplete",
                "data_status": "source_unavailable",
                "chip_score": None,
                "chip_label": "量能不足，暫不判斷",
                "chip_summary": "資料暫不完整；尚無可用價量資料",
                "completeness": _base_completeness(),
                "source_flags": _source_flags(),
                "price_source": "none",
                "inst_source": "none",
                "margin_source": "none",
                "intraday_source": "none",
            }
        row_date = str(price_row.get("date") or target or today_iso())
        volume_lots = _lots(price_row.get("volume"))
        inst = _institution_row(conn, code, row_date)
        margin = _margin_row(conn, code, row_date)
        snapshot = _intraday_snapshot(conn, code) if mode == "watchlist" else None
        close = parse_num(snapshot.get("price")) if snapshot and mode == "watchlist" else parse_num(price_row.get("close"))
        previous_close = _previous_close(conn, code, row_date)
        vol_avg5 = _volume_average(conn, code, row_date, 5)
        vol_avg20 = _volume_average(conn, code, row_date, 20)
        tdcc = calc_tdcc_trend(code)
    foreign_net = _lots(inst.get("foreign_net")) if inst else None
    trust_net = _lots(inst.get("trust_net")) if inst else None
    dealer_net = _lots(inst.get("dealer_net")) if inst else None
    inst_total = None
    if any(x is not None for x in (foreign_net, trust_net, dealer_net)):
        inst_total = int(sum(x or 0 for x in (foreign_net, trust_net, dealer_net)))
    margin_change = _int_or_none(margin.get("margin_delta")) if margin else None
    margin_balance = _int_or_none(margin.get("margin_balance")) if margin else None
    short_change = _int_or_none(margin.get("short_delta")) if margin else None
    short_balance = _int_or_none(margin.get("short_balance")) if margin else None
    phase = "intraday_estimate" if snapshot and mode == "watchlist" else "complete"
    completeness = _base_completeness()
    source_flags = _source_flags()
    completeness["price"] = "available" if close is not None else "missing"
    source_flags["price_source"] = "fugle_snapshot" if snapshot and mode == "watchlist" else "twse_official"
    completeness["institution"] = "available" if inst else "missing"
    source_flags["inst_source"] = "finmind" if inst else "none"
    completeness["margin"] = "available" if margin else "missing"
    source_flags["margin_source"] = "finmind" if margin else "none"
    completeness["tdcc"] = "available" if tdcc.get("trend") != "資料不足" else "missing"
    completeness["intraday"] = "available" if snapshot and mode == "watchlist" else "disabled"
    source_flags["intraday_source"] = "fugle_rest" if snapshot and mode == "watchlist" else "none"
    if phase == "intraday_estimate":
        data_status = "partial"
    elif not inst:
        data_status = "source_delayed"
    elif not margin:
        data_status = "partial"
        phase = "closing_correction"
    else:
        data_status = "ok"
    result = {
        "date": row_date,
        "stock_id": code,
        "close": close,
        "previous_close": previous_close,
        "volume": volume_lots,
        "volume_ratio_5d": _ratio(volume_lots, vol_avg5),
        "volume_ratio_20d": _ratio(volume_lots, vol_avg20),
        "foreign_net": foreign_net,
        "trust_net": trust_net,
        "dealer_net": dealer_net,
        "inst_total_net": inst_total,
        "margin_balance": margin_balance,
        "short_balance": short_balance,
        "margin_change": margin_change,
        "short_change": short_change,
        "intraday_high": parse_num(snapshot.get("high")) if snapshot else None,
        "intraday_low": parse_num(snapshot.get("low")) if snapshot else None,
        "intraday_vwap": parse_num(snapshot.get("vwap")) if snapshot else None,
        "tdcc_trend": tdcc.get("trend"),
        "tdcc_whale_change_pct": tdcc.get("whale_change_pct"),
        "tdcc_shareholder_change": tdcc.get("shareholder_change"),
        "last_updated_phase": phase,
        "data_status": data_status,
        "completeness": completeness,
        "source_flags": source_flags,
        "price_source": source_flags["price_source"],
        "inst_source": source_flags["inst_source"],
        "margin_source": source_flags["margin_source"],
        "intraday_source": source_flags["intraday_source"],
    }
    score = calculate_daily_chip_score(result)
    if score is None:
        result["data_status"] = "low_volume_skip" if volume_lots is not None else data_status
    if phase == "closing_correction" and score is not None:
        score = max(-2, min(2, score))
    result["chip_score"] = score
    result["chip_label"] = _label_for_score(score)
    result["chip_summary"] = generate_chip_summary(result)
    result["updated_at"] = now_tpe().strftime("%Y-%m-%d %H:%M:%S")
    return result


def refresh_daily_chip_momentum_for_codes(
    codes: list[str],
    date: str | None = None,
    mode: str = "watchlist",
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    mode = mode if mode in {"watchlist", "tw50"} else "watchlist"
    stats = {
        "ok": True,
        "mode": mode,
        "dry_run": dry_run,
        "writes_db": bool(not dry_run),
        "total": 0,
        "write_count": 0,
        "failed": [],
    }
    seen: set[str] = set()
    for raw in codes:
        code = str(raw or "").strip().zfill(4)[:4]
        if not code.isdigit() or code in seen:
            continue
        seen.add(code)
        stats["total"] += 1
        try:
            row = _row_for_code(code, date=date, mode=mode)
            if upsert_daily_chip_momentum(row, dry_run=dry_run):
                stats["write_count"] += 1
                if not dry_run:
                    cleanup_daily_chip_momentum_history(code)
            else:
                stats["failed"].append({"code": code, "reason": "upsert_skipped"})
        except Exception as exc:
            stats["failed"].append({"code": code, "reason": str(exc)})
    stats["failed_count"] = len(stats["failed"])
    return stats


def build_chip_momentum_payload(stock_id: str, date: str | None = None) -> dict[str, Any]:
    data = get_latest_chip_momentum(stock_id, date)
    if not data:
        return {
            "available": False,
            "chip_score": None,
            "chip_label": "量能不足，暫不判斷",
            "chip_summary": "資料暫不完整；尚未建立每日籌碼動能",
            "last_updated_phase": "incomplete",
            "phase": "incomplete",
            "data_status": "source_unavailable",
            "completeness": _base_completeness(),
            "tdcc_trend": "資料不足",
            "source_flags": _source_flags(),
        }
    return {
        "available": True,
        "chip_score": data.get("chip_score"),
        "chip_label": data.get("chip_label") or _label_for_score(data.get("chip_score")),
        "chip_summary": data.get("chip_summary") or generate_chip_summary(data),
        "last_updated_phase": data.get("last_updated_phase"),
        "phase": data.get("last_updated_phase"),
        "data_status": data.get("data_status"),
        "completeness": data.get("completeness") or {},
        "tdcc_trend": data.get("tdcc_trend") or "資料不足",
        "source_flags": data.get("source_flags") or {},
    }


def summarize_chip_momentum_for_quote(stock_id: str) -> str | None:
    payload = build_chip_momentum_payload(stock_id)
    text = payload.get("chip_summary")
    return str(text)[:60] if text else None
