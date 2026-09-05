from __future__ import annotations

from contextlib import closing
from pathlib import Path
from typing import Any

import pandas as pd

from core.db import read_only_db


def read_strategy_backtest_inputs(
    *,
    database_path: Path | None = None,
    date_to: str | None = None,
    codes: list[str] | None = None,
) -> dict[str, Any]:
    """Read point-in-time OHLCV/technical inputs without creating or writing tables."""

    filters = [
        "sm.security_type='stock'",
        "hp.close>0",
        "hp.volume>0",
        "UPPER(COALESCE(hp.source_quality,'')) IN ('OFFICIAL','OK','HIGH')",
        "(UPPER(COALESCE(hp.source,'')) LIKE 'TWSE%' OR UPPER(COALESCE(hp.source,'')) LIKE 'TPEX%')",
        "(sm.first_seen_date IS NULL OR hp.date>=sm.first_seen_date)",
    ]
    params: list[Any] = []
    if date_to:
        filters.append("hp.date<=?")
        params.append(date_to)
    normalized_codes = [str(code).zfill(4) for code in list(codes or []) if str(code).isdigit()]
    if normalized_codes:
        placeholders = ",".join("?" for _ in normalized_codes)
        filters.append(f"hp.code IN ({placeholders})")
        params.extend(normalized_codes)
    where_sql = " AND ".join(filters)
    with closing(read_only_db(database_path)) as conn:
        conn.execute("BEGIN")
        rows = pd.read_sql_query(
            f"""
            SELECT
                hp.date AS trade_date,
                hp.code,
                sm.name,
                COALESCE(hp.market,sm.market) AS market,
                sm.first_seen_date,
                sm.last_seen_date,
                hp.open,hp.high,hp.low,hp.close,hp.volume,hp.volume_unit,hp.amount,
                hp.source,hp.source_quality,
                tech.formula_version,tech.input_row_count,tech.input_start_date,
                tech.input_end_date,tech.adjustment_event_count,
                tech.ma20,tech.ma60,tech.rsi14,tech.macd_osc,tech.atr14,
                tech.volume_ma20,tech.previous_10d_low,
                tech.data_quality AS technical_data_quality,
                tech.decision_ready AS technical_decision_ready
            FROM history_price AS hp
            INNER JOIN stock_master AS sm ON sm.code=hp.code
            LEFT JOIN daily_technical_snapshot AS tech
              ON tech.code=hp.code AND tech.trade_date=hp.date
            WHERE {where_sql}
            ORDER BY hp.code,hp.date
            """,
            conn,
            params=params,
        )
        actions = (
            pd.read_sql_query(
                """
                SELECT code,date AS action_date,action_type,is_confirmed
                FROM corporate_actions
                ORDER BY code,date
                """,
                conn,
            )
            if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='corporate_actions'"
            ).fetchone()
            else pd.DataFrame(columns=["code", "action_date", "action_type", "is_confirmed"])
        )
    return {"rows": rows, "corporate_actions": actions}
