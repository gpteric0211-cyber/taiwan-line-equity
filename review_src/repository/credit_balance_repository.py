from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from core.credit_balance_schema import ensure_credit_balance_schema
from core.provenance_schema import record_validated_credit_balance_versions


SCHEMA_VERSION = "official-credit-balance-v1"


def upsert_official_credit_balances(
    conn: sqlite3.Connection,
    rows: Iterable[dict[str, Any]],
) -> int:
    ensure_credit_balance_schema(conn)
    normalized = [dict(row) for row in rows]
    if not normalized:
        return 0
    observed = datetime.now(timezone.utc)
    first_seen = observed.isoformat(timespec="seconds")
    usable_from = (observed + timedelta(minutes=5)).isoformat(timespec="seconds")
    epoch = time.time()
    for row in normalized:
        margin_balance = row.get("margin_balance_lots")
        margin_prev = row.get("margin_prev_balance_lots")
        short_balance = row.get("short_balance_lots")
        short_prev = row.get("short_prev_balance_lots")
        lending_balance = row.get("sbl_balance_shares")
        lending_prev = row.get("sbl_prev_balance_shares")
        row["margin_delta_lots"] = (
            int(margin_balance) - int(margin_prev)
            if None not in (margin_balance, margin_prev) else None
        )
        row["short_delta_lots"] = (
            int(short_balance) - int(short_prev)
            if None not in (short_balance, short_prev) else None
        )
        row["sbl_delta_shares"] = (
            int(lending_balance) - int(lending_prev)
            if None not in (lending_balance, lending_prev) else None
        )
        record_validated_credit_balance_versions(conn, row, observed_at=observed)
    conn.executemany(
        """
        INSERT INTO credit_balance_daily(
            trade_date,code,market,
            margin_prev_balance_lots,margin_buy_lots,margin_sell_lots,
            margin_cash_repayment_lots,margin_balance_lots,margin_delta_lots,
            margin_utilization_pct,margin_utilization_method,margin_limit_lots,
            short_prev_balance_lots,short_sell_lots,short_buy_lots,
            short_stock_repayment_lots,short_balance_lots,short_delta_lots,
            short_utilization_pct,short_utilization_method,short_limit_lots,
            sbl_prev_balance_shares,sbl_sell_shares,sbl_return_shares,
            sbl_adjust_shares,sbl_balance_shares,sbl_delta_shares,
            margin_source,lending_source,source_quality,
            first_seen_at,validation_passed_at,usable_from,schema_version,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(trade_date,code) DO UPDATE SET
            market=excluded.market,
            margin_prev_balance_lots=excluded.margin_prev_balance_lots,
            margin_buy_lots=excluded.margin_buy_lots,
            margin_sell_lots=excluded.margin_sell_lots,
            margin_cash_repayment_lots=excluded.margin_cash_repayment_lots,
            margin_balance_lots=excluded.margin_balance_lots,
            margin_delta_lots=excluded.margin_delta_lots,
            margin_utilization_pct=excluded.margin_utilization_pct,
            margin_utilization_method=excluded.margin_utilization_method,
            margin_limit_lots=excluded.margin_limit_lots,
            short_prev_balance_lots=excluded.short_prev_balance_lots,
            short_sell_lots=excluded.short_sell_lots,
            short_buy_lots=excluded.short_buy_lots,
            short_stock_repayment_lots=excluded.short_stock_repayment_lots,
            short_balance_lots=excluded.short_balance_lots,
            short_delta_lots=excluded.short_delta_lots,
            short_utilization_pct=excluded.short_utilization_pct,
            short_utilization_method=excluded.short_utilization_method,
            short_limit_lots=excluded.short_limit_lots,
            sbl_prev_balance_shares=excluded.sbl_prev_balance_shares,
            sbl_sell_shares=excluded.sbl_sell_shares,
            sbl_return_shares=excluded.sbl_return_shares,
            sbl_adjust_shares=excluded.sbl_adjust_shares,
            sbl_balance_shares=excluded.sbl_balance_shares,
            sbl_delta_shares=excluded.sbl_delta_shares,
            margin_source=excluded.margin_source,
            lending_source=excluded.lending_source,
            source_quality=excluded.source_quality,
            validation_passed_at=excluded.validation_passed_at,
            usable_from=excluded.usable_from,
            schema_version=excluded.schema_version,
            updated_at=excluded.updated_at
        """,
        [
            (
                row.get("trade_date"), row.get("code"), row.get("market"),
                row.get("margin_prev_balance_lots"), row.get("margin_buy_lots"), row.get("margin_sell_lots"),
                row.get("margin_cash_repayment_lots"), row.get("margin_balance_lots"), row.get("margin_delta_lots"),
                row.get("margin_utilization_pct"), row.get("margin_utilization_method"), row.get("margin_limit_lots"),
                row.get("short_prev_balance_lots"), row.get("short_sell_lots"), row.get("short_buy_lots"),
                row.get("short_stock_repayment_lots"), row.get("short_balance_lots"), row.get("short_delta_lots"),
                row.get("short_utilization_pct"), row.get("short_utilization_method"), row.get("short_limit_lots"),
                row.get("sbl_prev_balance_shares"), row.get("sbl_sell_shares"), row.get("sbl_return_shares"),
                row.get("sbl_adjust_shares"), row.get("sbl_balance_shares"), row.get("sbl_delta_shares"),
                row.get("margin_source"), row.get("lending_source"), "official",
                first_seen, first_seen, usable_from, SCHEMA_VERSION, first_seen,
            )
            for row in normalized
        ],
    )
    conn.executemany(
        """
        INSERT INTO margin_daily(
            date,code,margin_delta,margin_balance,short_delta,short_balance,
            source,updated_at,source_quality,fetched_at,margin_unit,short_unit
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(date,code) DO UPDATE SET
            margin_delta=excluded.margin_delta,
            margin_balance=excluded.margin_balance,
            short_delta=excluded.short_delta,
            short_balance=excluded.short_balance,
            source=excluded.source,
            updated_at=excluded.updated_at,
            source_quality=excluded.source_quality,
            fetched_at=excluded.fetched_at,
            margin_unit=excluded.margin_unit,
            short_unit=excluded.short_unit
        """,
        [
            (
                row.get("trade_date"), row.get("code"), row.get("margin_delta_lots"),
                row.get("margin_balance_lots"), row.get("short_delta_lots"),
                row.get("short_balance_lots"), row.get("margin_source"), epoch,
                "official", epoch, "lots", "lots",
            )
            for row in normalized
        ],
    )
    conn.executemany(
        """
        INSERT INTO lending_daily(
            date,code,lending_delta,lending_balance,source,updated_at,
            source_quality,fetched_at,lending_unit
        ) VALUES(?,?,?,?,?,?,?,?,?)
        ON CONFLICT(date,code) DO UPDATE SET
            lending_delta=excluded.lending_delta,
            lending_balance=excluded.lending_balance,
            source=excluded.source,
            updated_at=excluded.updated_at,
            source_quality=excluded.source_quality,
            fetched_at=excluded.fetched_at,
            lending_unit=excluded.lending_unit
        """,
        [
            (
                row.get("trade_date"), row.get("code"), row.get("sbl_delta_shares"),
                row.get("sbl_balance_shares"), row.get("lending_source"), epoch,
                "official", epoch, "shares",
            )
            for row in normalized
        ],
    )
    return len(normalized)


def prune_credit_balances(conn: sqlite3.Connection, retain_days: int = 900) -> int:
    cutoff = conn.execute(
        """
        SELECT trade_date FROM credit_balance_daily
        GROUP BY trade_date ORDER BY trade_date DESC LIMIT 1 OFFSET ?
        """,
        (max(0, retain_days - 1),),
    ).fetchone()
    if not cutoff:
        return 0
    cursor = conn.execute(
        "DELETE FROM credit_balance_daily WHERE trade_date < ?",
        (cutoff[0],),
    )
    return max(0, int(cursor.rowcount or 0))
