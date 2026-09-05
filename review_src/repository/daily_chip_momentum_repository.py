from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import closing
from typing import Any

from core.config import DAILY_CHIP_MOMENTUM_KEEP_ROWS, INTRADAY_1M_KEEP_DAYS
from core.db import db
from core.utils import now_tpe
from repository.full_market_batch_repository import resolve_full_market_analysis_date


DAILY_CHIP_COLUMNS = [
    "date",
    "stock_id",
    "close",
    "previous_close",
    "volume",
    "volume_ratio_5d",
    "volume_ratio_20d",
    "foreign_net",
    "trust_net",
    "dealer_net",
    "inst_total_net",
    "margin_balance",
    "short_balance",
    "margin_change",
    "short_change",
    "intraday_high",
    "intraday_low",
    "intraday_vwap",
    "chip_score",
    "chip_label",
    "chip_summary",
    "tdcc_trend",
    "tdcc_whale_change_pct",
    "tdcc_shareholder_change",
    "last_updated_phase",
    "data_status",
    "completeness_json",
    "source_flags_json",
    "price_source",
    "inst_source",
    "margin_source",
    "intraday_source",
    "updated_at",
]


def _updated_at() -> str:
    return now_tpe().strftime("%Y-%m-%d %H:%M:%S")


def _json_text(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, sort_keys=True)


def upsert_intraday_snapshot(row: dict[str, Any], *, dry_run: bool = False) -> bool:
    stock_id = str(row.get("stock_id") or "").strip().zfill(4)[:4]
    if not stock_id.isdigit():
        return False
    if dry_run:
        return True
    values = {
        "stock_id": stock_id,
        "quote_time": row.get("quote_time"),
        "price": row.get("price"),
        "open": row.get("open"),
        "high": row.get("high"),
        "low": row.get("low"),
        "close": row.get("close"),
        "volume": row.get("volume"),
        "turnover": row.get("turnover"),
        "vwap": row.get("vwap"),
        "change": row.get("change"),
        "change_pct": row.get("change_pct"),
        "source": row.get("source") or "fugle_rest",
        "last_updated_phase": row.get("last_updated_phase") or "intraday",
        "data_status": row.get("data_status") or "partial",
        "raw_json": row.get("raw_json") if isinstance(row.get("raw_json"), str) else _json_text(row.get("raw_json")),
        "updated_at": _updated_at(),
    }
    with closing(db()) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO intraday_quote_snapshot
                (stock_id,quote_time,price,open,high,low,close,volume,turnover,vwap,change,change_pct,source,last_updated_phase,data_status,raw_json,updated_at)
            VALUES
                (:stock_id,:quote_time,:price,:open,:high,:low,:close,:volume,:turnover,:vwap,:change,:change_pct,:source,:last_updated_phase,:data_status,:raw_json,:updated_at)
            """,
            values,
        )
        conn.commit()
    return True


def upsert_intraday_1m(row: dict[str, Any], *, dry_run: bool = False) -> bool:
    stock_id = str(row.get("stock_id") or "").strip().zfill(4)[:4]
    trade_date = str(row.get("trade_date") or "").strip()
    minute_ts = str(row.get("minute_ts") or "").strip()
    if not (stock_id.isdigit() and trade_date and minute_ts):
        return False
    if dry_run:
        return True
    values = {
        "trade_date": trade_date,
        "minute_ts": minute_ts,
        "stock_id": stock_id,
        "open": row.get("open"),
        "high": row.get("high"),
        "low": row.get("low"),
        "close": row.get("close"),
        "volume": row.get("volume"),
        "turnover": row.get("turnover"),
        "vwap": row.get("vwap"),
        "source": row.get("source") or "fugle_rest",
        "data_status": row.get("data_status") or "partial",
        "updated_at": _updated_at(),
    }
    with closing(db()) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO intraday_quote_1m
                (trade_date,minute_ts,stock_id,open,high,low,close,volume,turnover,vwap,source,data_status,updated_at)
            VALUES
                (:trade_date,:minute_ts,:stock_id,:open,:high,:low,:close,:volume,:turnover,:vwap,:source,:data_status,:updated_at)
            """,
            values,
        )
        conn.commit()
    return True


def upsert_daily_chip_momentum(row: dict[str, Any], *, dry_run: bool = False) -> bool:
    stock_id = str(row.get("stock_id") or "").strip().zfill(4)[:4]
    date = str(row.get("date") or "").strip()
    if not (stock_id.isdigit() and date):
        return False
    if dry_run:
        return True
    normalized = {key: row.get(key) for key in DAILY_CHIP_COLUMNS}
    normalized["stock_id"] = stock_id
    normalized["date"] = date
    normalized["completeness_json"] = _json_text(row.get("completeness") or row.get("completeness_json"))
    normalized["source_flags_json"] = _json_text(row.get("source_flags") or row.get("source_flags_json"))
    normalized["updated_at"] = row.get("updated_at") or _updated_at()
    placeholders = ",".join("?" for _ in DAILY_CHIP_COLUMNS)
    columns = ",".join(DAILY_CHIP_COLUMNS)
    with closing(db()) as conn:
        conn.execute(
            f"INSERT OR REPLACE INTO daily_chip_momentum ({columns}) VALUES ({placeholders})",
            [normalized.get(key) for key in DAILY_CHIP_COLUMNS],
        )
        conn.commit()
    return True


def get_latest_chip_momentum(stock_id: str, date: str | None = None) -> dict[str, Any] | None:
    code = str(stock_id or "").strip().zfill(4)[:4]
    if not code.isdigit():
        return None
    with closing(db()) as conn:
        analysis_as_of = resolve_full_market_analysis_date(conn, date)
        if analysis_as_of:
            row = conn.execute(
                "SELECT * FROM daily_chip_momentum WHERE stock_id=? AND date<=? ORDER BY date DESC LIMIT 1",
                (code, analysis_as_of),
            ).fetchone()
        else:
            row = None
    if not row:
        return None
    data = dict(row)
    for key, target in (("completeness_json", "completeness"), ("source_flags_json", "source_flags")):
        try:
            data[target] = json.loads(data.get(key) or "{}")
        except Exception:
            data[target] = {}
    return data


def cleanup_daily_chip_momentum_history(stock_id: str, *, keep_rows: int = DAILY_CHIP_MOMENTUM_KEEP_ROWS) -> int:
    code = str(stock_id or "").strip().zfill(4)[:4]
    if not code.isdigit():
        return 0
    try:
        with closing(db()) as conn:
            kept = [
                row["date"]
                for row in conn.execute(
                    "SELECT date FROM daily_chip_momentum WHERE stock_id=? ORDER BY date DESC LIMIT ?",
                    (code, int(keep_rows)),
                ).fetchall()
            ]
            if not kept:
                return 0
            deleted = conn.execute(
                "DELETE FROM daily_chip_momentum WHERE stock_id=? AND date NOT IN (%s)" % ",".join("?" for _ in kept),
                [code, *kept],
            ).rowcount
            conn.commit()
            return int(deleted or 0)
    except sqlite3.Error:
        logging.warning("daily_chip_momentum cleanup failed for %s", code, exc_info=True)
        return 0


def cleanup_intraday_1m_history(*, keep_days: int = INTRADAY_1M_KEEP_DAYS) -> int:
    try:
        cutoff = now_tpe().date().toordinal() - int(keep_days)
        with closing(db()) as conn:
            rows = conn.execute("SELECT DISTINCT trade_date FROM intraday_quote_1m").fetchall()
            old_dates = []
            for row in rows:
                try:
                    if str(row["trade_date"]):
                        from datetime import date as _date
                        if _date.fromisoformat(str(row["trade_date"])).toordinal() < cutoff:
                            old_dates.append(str(row["trade_date"]))
                except Exception:
                    continue
            if not old_dates:
                return 0
            deleted = conn.execute(
                "DELETE FROM intraday_quote_1m WHERE trade_date IN (%s)" % ",".join("?" for _ in old_dates),
                old_dates,
            ).rowcount
            conn.commit()
            return int(deleted or 0)
    except sqlite3.Error:
        logging.warning("intraday_quote_1m cleanup failed", exc_info=True)
        return 0
