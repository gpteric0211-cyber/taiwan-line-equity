from __future__ import annotations

import json
import re
import sqlite3
import time
from datetime import date
from typing import Any, Iterable

from core.market_foundation_schema import source_rank
from core.data_quality import assess_daily_ohlcv
from core.utils import normalize_date


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS stock_no_trade_dates (
    trade_date TEXT NOT NULL,
    code TEXT NOT NULL,
    market TEXT NOT NULL,
    reason TEXT NOT NULL,
    source TEXT NOT NULL,
    source_url TEXT,
    source_quality TEXT NOT NULL DEFAULT 'official',
    evidence_json TEXT,
    verified_at REAL NOT NULL,
    PRIMARY KEY(trade_date, code)
);
CREATE INDEX IF NOT EXISTS idx_stock_no_trade_dates_code_date
    ON stock_no_trade_dates(code, trade_date DESC);
"""


def ensure_stock_no_trade_schema(conn: sqlite3.Connection) -> None:
    """Create the evidence table from an explicit update/repair path."""

    conn.executescript(SCHEMA_SQL)


def stock_no_trade_table_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='stock_no_trade_dates'"
    ).fetchone()
    return bool(row)


def upsert_verified_no_trade_dates(
    conn: sqlite3.Connection,
    rows: Iterable[dict[str, Any]],
    *,
    ensure_schema: bool = True,
) -> int:
    """Persist source-explicit official evidence that a stock had no daily bar."""

    if ensure_schema:
        ensure_stock_no_trade_schema(conn)
    written = 0
    verified_at = time.time()
    for raw in rows:
        trade_date = normalize_date(raw.get("trade_date") or raw.get("date"))
        raw_code = str(raw.get("code") or "").strip()
        code = raw_code.zfill(4)
        market = str(raw.get("market") or "").strip().lower()
        reason = str(raw.get("reason") or "").strip()
        source = str(raw.get("source") or "").strip()
        source_url = str(raw.get("source_url") or "").strip()
        source_quality = str(raw.get("source_quality") or "official").strip().lower()
        try:
            valid_calendar_date = bool(trade_date and date.fromisoformat(trade_date))
        except ValueError:
            valid_calendar_date = False
        expected_sources = {
            "otc": {"TPEX TRADING_STOCK", "TPEX DAILY_QUOTES"},
            "listed": {"TWSE STOCK_DAY", "TWSE MI_INDEX"},
        }.get(market, set())
        source_url_ok = bool(
            (market == "otc" and source_url.startswith("https://www.tpex.org.tw/"))
            or (
                market == "listed"
                and (
                    source_url.startswith("https://www.twse.com.tw/")
                    or source_url.startswith("https://openapi.twse.com.tw/")
                )
            )
        )
        if (
            not valid_calendar_date
            or not re.fullmatch(r"\d{1,4}", raw_code)
            or code == "0000"
            or market not in {"listed", "otc"}
            or not reason
            or source_quality not in {"official", "ok", "high"}
            or source not in expected_sources
            or not source_url_ok
        ):
            continue
        existing_cursor = conn.execute(
            "SELECT * FROM history_price WHERE date=? AND code=?",
            (trade_date, code),
        )
        existing = existing_cursor.fetchone()
        if existing:
            existing_row = (
                dict(existing)
                if hasattr(existing, "keys")
                else dict(zip((column[0] for column in existing_cursor.description), existing))
            )
            # A valid official K line wins.  An official-labelled zero/missing
            # price row is not a K line and must not block exact no-bar evidence.
            if (
                source_rank(existing_row.get("source")) >= 100
                and assess_daily_ohlcv({**existing_row, "code": code}).get("ready")
            ):
                continue
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS invalid_history_price_quarantine(
                    code TEXT NOT NULL,
                    date TEXT NOT NULL,
                    source TEXT,
                    reason TEXT NOT NULL,
                    row_json TEXT NOT NULL,
                    quarantined_at REAL NOT NULL,
                    PRIMARY KEY(code,date)
                )
                """
            )
            conn.execute(
                """
                INSERT INTO invalid_history_price_quarantine(
                    code,date,source,reason,row_json,quarantined_at
                ) VALUES(?,?,?,?,?,?)
                ON CONFLICT(code,date) DO UPDATE SET
                    source=excluded.source,reason=excluded.reason,
                    row_json=excluded.row_json,quarantined_at=excluded.quarantined_at
                """,
                (
                    code,
                    trade_date,
                    existing_row.get("source"),
                    "official verified no-trade date conflicts with fallback K line",
                    json.dumps(existing_row, ensure_ascii=False, default=str),
                    verified_at,
                ),
            )
            conn.execute(
                "DELETE FROM history_price WHERE date=? AND code=?",
                (trade_date, code),
            )
        evidence = raw.get("evidence")
        evidence = dict(evidence) if isinstance(evidence, dict) else {}
        if existing:
            existing_volume = existing_row.get("volume")
            existing_amount = existing_row.get("amount")
            try:
                residual_volume = float(existing_volume or 0)
            except (TypeError, ValueError):
                residual_volume = 0.0
            try:
                residual_amount = float(existing_amount or 0)
            except (TypeError, ValueError):
                residual_amount = 0.0
            evidence["replaced_invalid_history_row"] = {
                key: existing_row.get(key)
                for key in ("source", "open", "high", "low", "close", "volume", "amount")
            }
            if residual_volume > 0 or residual_amount > 0:
                reason = "official_no_ohlcv_with_residual_activity"
                evidence["reported_volume_shares"] = residual_volume
                evidence["reported_amount_twd"] = residual_amount
        evidence_json = json.dumps(
            evidence if evidence is not None else {},
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
        conn.execute(
            """
            INSERT INTO stock_no_trade_dates(
                trade_date,code,market,reason,source,source_url,
                source_quality,evidence_json,verified_at
            ) VALUES(?,?,?,?,?,?,?,?,?)
            ON CONFLICT(trade_date,code) DO UPDATE SET
                market=excluded.market,
                reason=excluded.reason,
                source=excluded.source,
                source_url=excluded.source_url,
                source_quality=excluded.source_quality,
                evidence_json=excluded.evidence_json,
                verified_at=excluded.verified_at
            """,
            (
                trade_date,
                code,
                market,
                reason,
                source,
                source_url,
                source_quality,
                evidence_json,
                float(raw.get("verified_at") or verified_at),
            ),
        )
        written += 1
    return written


def verified_no_trade_dates(
    conn: sqlite3.Connection,
    code: str,
    candidate_dates: Iterable[str],
) -> set[str]:
    """Read verified exceptions only; this helper never creates schema or writes."""

    if not stock_no_trade_table_exists(conn):
        return set()
    dates = sorted({d for d in (normalize_date(value) for value in candidate_dates) if d})
    if not dates:
        return set()
    placeholders = ",".join("?" for _ in dates)
    rows = conn.execute(
        f"""
        SELECT trade_date
        FROM stock_no_trade_dates
        WHERE code=?
          AND trade_date IN ({placeholders})
          AND LOWER(source_quality) IN ('official','ok','high')
          AND (
              (
                  LOWER(market)='otc'
                  AND source IN ('TPEX TRADING_STOCK','TPEX DAILY_QUOTES')
                  AND source_url LIKE 'https://www.tpex.org.tw/%'
              )
              OR (
                  LOWER(market)='listed'
                  AND source IN ('TWSE STOCK_DAY','TWSE MI_INDEX')
                  AND (
                      source_url LIKE 'https://www.twse.com.tw/%'
                      OR source_url LIKE 'https://openapi.twse.com.tw/%'
                  )
              )
          )
        """,
        (str(code).zfill(4), *dates),
    ).fetchall()
    return {
        str(row["trade_date"] if isinstance(row, sqlite3.Row) else row[0])
        for row in rows
    }
