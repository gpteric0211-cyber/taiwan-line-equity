from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

try:
    from core.config import DB_PATH
except Exception as exc:  # pragma: no cover - startup diagnostics
    DB_PATH = None  # type: ignore[assignment]
    CONFIG_ERROR = str(exc)
else:
    CONFIG_ERROR = ""


def _readonly_sqlite_uri(path: Path) -> str:
    normalized = path.resolve().as_posix()
    return f"file:{quote(normalized, safe=':/')}?mode=ro"


def _validate_identifier(value: str, *, kind: str = "identifier") -> str:
    name = (value or "").strip()
    if not name.replace("_", "").isalnum() or not name:
        raise ValueError(f"{kind} must contain only letters, numbers, and underscores")
    return name


def _validate_table_name(value: str) -> str:
    return _validate_identifier(value, kind="table name")


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return bool(row)


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    return {row[1] for row in conn.execute(f"PRAGMA table_info({_validate_table_name(table)})").fetchall()}


def _first_existing_date_column(columns: set[str]) -> str | None:
    for name in ("date", "trade_date", "data_date", "snapshot_date", "created_date"):
        if name in columns:
            return name
    return None


def _latest_count_for_table(conn: sqlite3.Connection, table: str) -> dict[str, Any]:
    safe_table = _validate_table_name(table)
    if not _table_exists(conn, safe_table):
        return {"table": safe_table, "exists": False, "latest_date": None, "row_count": 0}
    columns = _table_columns(conn, safe_table)
    date_col = _first_existing_date_column(columns)
    if not date_col:
        count_row = conn.execute(f"SELECT COUNT(*) FROM {safe_table}").fetchone()
        return {
            "table": safe_table,
            "exists": True,
            "date_column": None,
            "latest_date": None,
            "row_count": int(count_row[0] if count_row else 0),
        }
    latest_row = conn.execute(f"SELECT MAX({date_col}) FROM {safe_table}").fetchone()
    latest_date = latest_row[0] if latest_row else None
    row_count = 0
    if latest_date:
        count_row = conn.execute(
            f"SELECT COUNT(*) FROM {safe_table} WHERE {date_col} = ?",
            (latest_date,),
        ).fetchone()
        row_count = int(count_row[0] if count_row else 0)
    return {
        "table": safe_table,
        "exists": True,
        "date_column": date_col,
        "latest_date": latest_date,
        "row_count": row_count,
    }


def _best_table_status(
    conn: sqlite3.Connection,
    tables: list[str],
    official_latest_date: str | None,
    *,
    min_count: int,
    category: str,
    scope_note: str,
) -> dict[str, Any]:
    checks = [_latest_count_for_table(conn, table) for table in tables]
    existing = [item for item in checks if item.get("exists")]
    if not existing:
        return {
            "category": category,
            "status": "not_created",
            "label": "未建立",
            "tables_checked": tables,
            "table_checks": checks,
            "message": scope_note,
        }
    dated = [item for item in existing if item.get("latest_date")]
    best = max(dated or existing, key=lambda item: int(item.get("row_count") or 0))
    latest_date = best.get("latest_date")
    row_count = int(best.get("row_count") or 0)
    if not latest_date:
        status = "unconfirmed"
        label = "未確認"
    elif official_latest_date and latest_date != official_latest_date:
        status = "not_synced"
        label = "未同步"
    elif row_count < min_count:
        status = "insufficient"
        label = "資料不足"
    else:
        status = "available_unconfirmed"
        label = "可用但未確認本次更新"
    return {
        "category": category,
        "status": status,
        "label": label,
        "tables_checked": tables,
        "table_checks": checks,
        "selected_table": best.get("table"),
        "latest_date": latest_date,
        "row_count": row_count,
        "message": scope_note,
    }


def _official_rows_by_source(
    official_source_counts: list[dict[str, Any]],
    official_latest_date: str | None,
) -> tuple[int, int]:
    twse_rows = 0
    tpex_rows = 0
    for item in official_source_counts:
        if official_latest_date and item.get("latest_date") != official_latest_date:
            continue
        source = str(item.get("source") or "").strip()
        rows = int(item.get("row_count") or 0)
        source_kind = _official_source_kind(source)
        if source_kind == "twse":
            twse_rows += rows
        elif source_kind == "tpex":
            tpex_rows += rows
    return twse_rows, tpex_rows


def _official_source_kind(source: str) -> str | None:
    upper = str(source or "").upper()
    if "TWSE" in upper and (
        "OFFICIAL" in upper
        or "OPENAPI" in upper
        or "STOCK_DAY" in upper
        or "MI_INDEX" in upper
    ):
        return "twse"
    if "TPEX" in upper and (
        "OFFICIAL" in upper
        or "OPENAPI" in upper
        or "DAILY_QUOTES" in upper
    ):
        return "tpex"
    return None


def _latest_official_source_date(official_source_counts: list[dict[str, Any]]) -> str | None:
    dates = [
        str(item.get("latest_date"))
        for item in official_source_counts
        if item.get("latest_date") and _official_source_kind(str(item.get("source") or ""))
    ]
    return max(dates) if dates else None


def build_completeness_report(
    conn: sqlite3.Connection,
    *,
    table: str,
    result: dict[str, Any],
    min_count: int,
) -> dict[str, Any]:
    official_latest_date = (
        result.get("official_latest_date")
        or _latest_official_source_date(result.get("official_source_latest_counts") or [])
        or result.get("latest_date")
    )
    twse_rows, tpex_rows = _official_rows_by_source(
        result.get("official_source_latest_counts") or [],
        official_latest_date,
    )
    if not twse_rows or not tpex_rows:
        for item in result.get("market_latest_counts") or []:
            market = str(item.get("market") or "").upper()
            rows = int(item.get("row_count") or 0)
            if not twse_rows and market in {"上市", "TSE", "TWSE", "TWSE_OFFICIAL"}:
                twse_rows += rows
            elif not tpex_rows and market in {"上櫃", "OTC", "TPEX", "TPEX_OFFICIAL"}:
                tpex_rows += rows
    official_total = twse_rows + tpex_rows
    scope_unknown = False
    if not official_total:
        # Keep the global count for diagnostics, but do not treat it as proof that
        # both TWSE and TPEX are complete. Without a market/source split, the
        # scope cannot be verified safely.
        official_total = int(result.get("global_latest_row_count") or result.get("row_count") or 0)
        scope_unknown = official_total > 0

    official_issues: list[str] = []
    if scope_unknown:
        official_status_code = "scope_unknown"
        official_label = "無法確認"
        official_complete = False
        official_scope = "無法確認"
        official_message = "history_price 缺少可確認上市 / 上櫃分布的欄位或 join 來源"
        official_issues.append("market_scope_unknown")
    elif twse_rows > 0 and tpex_rows > 0:
        official_status_code = "completed"
        official_label = "完成"
        official_complete = True
        official_scope = "上市 + 上櫃官方日線 OHLCV"
        official_message = "上市 + 上櫃官方日線 OHLCV 已更新"
    elif twse_rows == 0 and tpex_rows > 0:
        official_status_code = "partial"
        official_label = "部分完成 / 不完整"
        official_complete = False
        official_scope = "僅上櫃官方日線 OHLCV"
        official_message = "只有上櫃資料，上市官方日線缺失"
        official_issues.append("listed_missing")
    elif twse_rows > 0 and tpex_rows == 0:
        official_status_code = "partial"
        official_label = "部分完成 / 不完整"
        official_complete = False
        official_scope = "僅上市官方日線 OHLCV"
        official_message = "只有上市資料，上櫃官方日線缺失"
        official_issues.append("otc_missing")
    else:
        official_status_code = "missing"
        official_label = "未完成 / 缺資料"
        official_complete = False
        official_scope = "無"
        official_message = "最新日期沒有上市或上櫃官方日線 OHLCV"
        official_issues.extend(["listed_missing", "otc_missing"])

    official_status = {
        "category": "official_ohlcv",
        "status": official_status_code,
        "label": official_label,
        "table": table,
        "latest_date": official_latest_date,
        "twse_rows": twse_rows,
        "tpex_rows": tpex_rows,
        "total_rows": official_total,
        "message": official_message,
        "official_daily_complete": official_complete,
        "official_daily_scope": official_scope,
        "official_daily_issues": official_issues,
    }
    price_volume_status = _best_table_status(
        conn,
        ["price_volume_distribution", "price_volume_profile_daily", "price_volume_score_daily"],
        official_latest_date,
        min_count=min_count,
        category="price_volume",
        scope_note="本次 official-only 更新不包含 Yahoo / PChome 分價量資料",
    )
    time_sales_status = _best_table_status(
        conn,
        ["fugle_intraday_trades", "intraday_time_sales_daily", "intraday_quote_1m"],
        official_latest_date,
        min_count=min_count,
        category="time_sales",
        scope_note="本次 official-only 更新不包含 Yahoo time-sales 即時明細",
    )
    inner_outer_status = _best_table_status(
        conn,
        ["daily_inner_outer_volume"],
        official_latest_date,
        min_count=min_count,
        category="inner_outer",
        scope_note="本次 official-only 更新不包含內外盤量",
    )
    yahoo_status = {
        "category": "yahoo",
        "status": "not_enabled",
        "label": "未啟用",
        "message": "本次 official-only 更新未啟用 Yahoo 全市場補充資料",
    }
    pchome_status = {
        "category": "pchome",
        "status": "not_enabled_skeleton",
        "label": "未啟用 / skeleton",
        "message": "PChome 補充資料仍為 skeleton/TODO，未納入本次更新",
    }
    all_data_complete = all(
        item.get("status") == "completed"
        for item in (official_status, price_volume_status, time_sales_status, inner_outer_status)
    )
    return {
        "official_ohlcv_status": official_status,
        "price_volume_status": price_volume_status,
        "time_sales_status": time_sales_status,
        "inner_outer_status": inner_outer_status,
        "yahoo_status": yahoo_status,
        "pchome_status": pchome_status,
        "all_data_complete": all_data_complete,
        "complete_scope": official_status.get("official_daily_scope") or "未達官方日線完整門檻",
    }


def _group_counts(conn: sqlite3.Connection, table: str, column: str, where_sql: str = "", params: tuple[Any, ...] = ()) -> dict[str, int]:
    safe_table = _validate_table_name(table)
    if not _table_exists(conn, safe_table):
        return {}
    safe_column = _validate_identifier(column, kind="column name")
    clauses = [f"{safe_column} IS NOT NULL", f"TRIM({safe_column}) <> ''"]
    if where_sql:
        clauses.append(f"({where_sql})")
    query = f"SELECT {safe_column} AS key, COUNT(*) FROM {safe_table} WHERE {' AND '.join(clauses)}"
    query += " GROUP BY key ORDER BY key"
    return {str(row[0]): int(row[1]) for row in conn.execute(query, params).fetchall()}


def _column_empty_count(conn: sqlite3.Connection, table: str, column: str, where_sql: str = "", params: tuple[Any, ...] = ()) -> int:
    safe_table = _validate_table_name(table)
    if not _table_exists(conn, safe_table):
        return 0
    safe_column = _validate_identifier(column, kind="column name")
    clauses = [f"({safe_column} IS NULL OR TRIM(COALESCE({safe_column}, '')) = '')"]
    if where_sql:
        clauses.append(f"({where_sql})")
    row = conn.execute(f"SELECT COUNT(*) FROM {safe_table} WHERE {' AND '.join(clauses)}", params).fetchone()
    return int(row[0] if row else 0)


def _column_null_count(conn: sqlite3.Connection, table: str, column: str, where_sql: str = "", params: tuple[Any, ...] = ()) -> int:
    safe_table = _validate_table_name(table)
    if not _table_exists(conn, safe_table):
        return 0
    safe_column = _validate_identifier(column, kind="column name")
    clauses = [f"{safe_column} IS NULL"]
    if where_sql:
        clauses.append(f"({where_sql})")
    row = conn.execute(f"SELECT COUNT(*) FROM {safe_table} WHERE {' AND '.join(clauses)}", params).fetchone()
    return int(row[0] if row else 0)


def _column_blank_count(conn: sqlite3.Connection, table: str, column: str, where_sql: str = "", params: tuple[Any, ...] = ()) -> int:
    safe_table = _validate_table_name(table)
    if not _table_exists(conn, safe_table):
        return 0
    safe_column = _validate_identifier(column, kind="column name")
    clauses = [f"{safe_column} IS NOT NULL", f"TRIM({safe_column}) = ''"]
    if where_sql:
        clauses.append(f"({where_sql})")
    row = conn.execute(f"SELECT COUNT(*) FROM {safe_table} WHERE {' AND '.join(clauses)}", params).fetchone()
    return int(row[0] if row else 0)


def _column_value_count(conn: sqlite3.Connection, table: str, column: str, value: str, where_sql: str = "", params: tuple[Any, ...] = ()) -> int:
    safe_table = _validate_table_name(table)
    if not _table_exists(conn, safe_table):
        return 0
    safe_column = _validate_identifier(column, kind="column name")
    clauses = [f"UPPER(TRIM(COALESCE({safe_column}, ''))) = ?"]
    final_params: tuple[Any, ...] = (value.upper(),) + tuple(params)
    if where_sql:
        clauses.append(f"({where_sql})")
    row = conn.execute(f"SELECT COUNT(*) FROM {safe_table} WHERE {' AND '.join(clauses)}", final_params).fetchone()
    return int(row[0] if row else 0)


def _fugle_trade_side_status(conn: sqlite3.Connection) -> dict[str, Any]:
    table = "fugle_intraday_trades"
    required_columns = {
        "side_inferred",
        "side_label_zh",
        "side_method",
        "side_confidence",
        "side_reason",
        "prev_price",
        "prev_price_source",
    }
    if not _table_exists(conn, table):
        return {"available": False, "reason": "table_missing", "columns_ok": False}
    columns = _table_columns(conn, table)
    missing = sorted(required_columns - columns)
    if missing:
        return {"available": False, "reason": "columns_missing", "columns_ok": False, "missing_columns": missing}
    latest_row = conn.execute("SELECT MAX(trade_date) FROM fugle_intraday_trades WHERE source='FUGLE'").fetchone()
    latest_date = latest_row[0] if latest_row else None
    if not latest_date:
        return {"available": False, "reason": "no_fugle_rows", "columns_ok": True, "latest_date": None}
    total_row = conn.execute(
        "SELECT COUNT(*), COUNT(DISTINCT code), MIN(trade_time), MAX(trade_time) FROM fugle_intraday_trades WHERE source='FUGLE' AND trade_date=?",
        (latest_date,),
    ).fetchone()
    where = "source='FUGLE' AND trade_date=?"
    where_params = (latest_date,)
    null_side_count = _column_null_count(conn, table, "side_inferred", where, where_params)
    blank_side_count = _column_blank_count(conn, table, "side_inferred", where, where_params)
    unknown_side_count = _column_value_count(conn, table, "side_inferred", "UNKNOWN", where, where_params)
    null_method_count = _column_null_count(conn, table, "side_method", where, where_params)
    blank_method_count = _column_blank_count(conn, table, "side_method", where, where_params)
    unknown_method_count = _column_value_count(conn, table, "side_method", "UNKNOWN", where, where_params)
    return {
        "available": True,
        "columns_ok": True,
        "data_complete": null_side_count == 0 and blank_side_count == 0 and null_method_count == 0 and blank_method_count == 0,
        "data_status": "complete" if null_side_count == 0 and blank_side_count == 0 and null_method_count == 0 and blank_method_count == 0 else "columns_exist_but_rows_have_null_or_blank",
        "latest_date": latest_date,
        "stock_count": int(total_row[1] if total_row else 0),
        "total_trades": int(total_row[0] if total_row else 0),
        "earliest_trade_time": total_row[2] if total_row else None,
        "latest_trade_time": total_row[3] if total_row else None,
        "stats_scope": "latest_trade_date_only",
        "null_side_count": null_side_count,
        "blank_side_count": blank_side_count,
        "unknown_side_count": unknown_side_count,
        "null_method_count": null_method_count,
        "blank_method_count": blank_method_count,
        "unknown_method_count": unknown_method_count,
        "side_counts": _group_counts(conn, table, "side_inferred", "source='FUGLE' AND trade_date=?", (latest_date,)),
        "method_counts": _group_counts(conn, table, "side_method", "source='FUGLE' AND trade_date=?", (latest_date,)),
        "note": "Fugle side inference is supplemental and is not an exchange-original side field.",
    }


def verify_market_foundation_update(table: str, min_count: int) -> dict[str, Any]:
    db_path = Path(DB_PATH) if DB_PATH else None
    if not db_path:
        return {
            "ok": False,
            "status": "DB_NOT_CONFIGURED",
            "latest_date": None,
            "row_count": 0,
            "min_count": min_count,
            "db_path": None,
            "error": CONFIG_ERROR or "DB path is not configured.",
        }
    if not db_path.exists():
        return {
            "ok": False,
            "status": "DB_NOT_FOUND",
            "latest_date": None,
            "row_count": 0,
            "min_count": min_count,
            "db_path": str(db_path),
            "error": "SQLite DB file was not found.",
        }

    safe_table = _validate_table_name(table)
    uri = _readonly_sqlite_uri(db_path)
    with sqlite3.connect(uri, uri=True) as conn:
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({safe_table})").fetchall()}
        latest_row = conn.execute(f"SELECT MAX(date) FROM {safe_table}").fetchone()
        latest_date = latest_row[0] if latest_row else None
        global_latest_row_count = 0
        if latest_date:
            count_row = conn.execute(
                f"SELECT COUNT(*) FROM {safe_table} WHERE date = ?",
                (latest_date,),
            ).fetchone()
            global_latest_row_count = int(count_row[0] if count_row else 0)

        market_counts: list[dict[str, Any]] = []
        if "market" in columns:
            market_latest_rows = conn.execute(
                f"""
                SELECT COALESCE(NULLIF(market, ''), 'unknown') AS market_name, MAX(date)
                FROM {safe_table}
                GROUP BY market_name
                """
            ).fetchall()
            for market_name, market_latest_date in market_latest_rows:
                if not market_latest_date:
                    continue
                market_count_row = conn.execute(
                    f"""
                    SELECT COUNT(*)
                    FROM {safe_table}
                    WHERE COALESCE(NULLIF(market, ''), 'unknown') = ?
                      AND date = ?
                    """,
                    (market_name, market_latest_date),
                ).fetchone()
                market_counts.append(
                    {
                        "market": market_name,
                        "latest_date": market_latest_date,
                        "row_count": int(market_count_row[0] if market_count_row else 0),
                    }
                )

        official_source_counts: list[dict[str, Any]] = []
        if "source" in columns:
            source_latest_rows = conn.execute(
                f"""
                SELECT source, MAX(date)
                FROM {safe_table}
                WHERE UPPER(COALESCE(source, '')) LIKE '%TWSE%'
                   OR UPPER(COALESCE(source, '')) LIKE '%TPEX%'
                   OR UPPER(COALESCE(source, '')) LIKE '%OFFICIAL%'
                GROUP BY source
                """
            ).fetchall()
            for source_name, source_latest_date in source_latest_rows:
                if not source_latest_date:
                    continue
                source_count_row = conn.execute(
                    f"""
                    SELECT COUNT(*)
                    FROM {safe_table}
                    WHERE source = ?
                      AND date = ?
                    """,
                    (source_name, source_latest_date),
                ).fetchone()
                official_source_counts.append(
                    {
                        "source": source_name,
                        "latest_date": source_latest_date,
                        "row_count": int(source_count_row[0] if source_count_row else 0),
                    }
                )

        market_split_row_count = sum(int(item["row_count"]) for item in market_counts)
        official_source_row_count = sum(int(item["row_count"]) for item in official_source_counts)
        effective_row_count = max(global_latest_row_count, market_split_row_count, official_source_row_count)
        ok = bool(latest_date) and effective_row_count >= min_count
        result = {
            "ok": ok,
            "status": "PASS" if ok else "FAIL",
            "latest_date": latest_date,
            "official_latest_date": _latest_official_source_date(official_source_counts),
            "row_count": effective_row_count,
            "global_latest_date": latest_date,
            "global_latest_row_count": global_latest_row_count,
            "market_latest_counts": market_counts,
            "official_source_latest_counts": official_source_counts,
            "min_count": min_count,
            "db_path": str(db_path),
        }
        result.update(build_completeness_report(conn, table=safe_table, result=result, min_count=min_count))
        result["fugle_trade_side_status"] = _fugle_trade_side_status(conn)
        return result


def _status_line(item: dict[str, Any]) -> str:
    label = item.get("label") or item.get("status") or "--"
    latest_date = item.get("latest_date")
    row_count = item.get("row_count")
    suffix = []
    if latest_date:
        suffix.append(f"最新日期：{latest_date}")
    if row_count is not None:
        suffix.append(f"筆數：{row_count}")
    return f"{label}" + (f"（{'，'.join(suffix)}）" if suffix else "")


def print_zh_summary(result: dict[str, Any], *, details: bool) -> None:
    official = result.get("official_ohlcv_status") or {}
    price_volume = result.get("price_volume_status") or {}
    time_sales = result.get("time_sales_status") or {}
    inner_outer = result.get("inner_outer_status") or {}
    yahoo = result.get("yahoo_status") or {}
    pchome = result.get("pchome_status") or {}

    print("=" * 60)
    print("台股資料更新完整度檢查")
    print("=" * 60)
    print()
    print("官方日線資料：")
    print(f"狀態：{official.get('label') or '--'}")
    print(f"資料表：{official.get('table') or '--'}")
    print(f"最新日期：{official.get('latest_date') or '--'}")
    print(f"上市筆數：{official.get('twse_rows') or 0}")
    print(f"上櫃筆數：{official.get('tpex_rows') or 0}")
    print(f"合計筆數：{official.get('total_rows') or 0}")
    print(f"結論：{official.get('message') or '--'}")
    if official.get("official_daily_issues"):
        print(f"問題：{', '.join(official.get('official_daily_issues') or [])}")
    print()
    print("分價量表：")
    print(f"狀態：{_status_line(price_volume)}")
    print(f"說明：{price_volume.get('message') or '--'}")
    print()
    print("即時明細 / Time Sales：")
    print(f"狀態：{_status_line(time_sales)}")
    print(f"說明：{time_sales.get('message') or '--'}")
    if time_sales.get("selected_table") == "fugle_intraday_trades":
        print("來源：Fugle intraday trades（逐筆成交），不是 Yahoo time-sales。")
    print()
    print("內外盤量：")
    print(f"狀態：{_status_line(inner_outer)}")
    print(f"說明：{inner_outer.get('message') or '--'}")
    print()
    print("Yahoo 補充資料：")
    print(f"狀態：{yahoo.get('label') or '--'}")
    print(f"說明：{yahoo.get('message') or '--'}")
    print()
    print("PChome 補充資料：")
    print(f"狀態：{pchome.get('label') or '--'}")
    print(f"說明：{pchome.get('message') or '--'}")
    print()
    print("整體結論：")
    print(f"官方日線：{official.get('label') or '--'}")
    print(f"分價量表：{price_volume.get('label') or '--'}")
    print(f"即時明細：{time_sales.get('label') or '--'}")
    print(f"內外盤量：{inner_outer.get('label') or '--'}")
    print(f"本次更新完整範圍：{result.get('complete_scope') or '--'}")
    print(f"是否所有資料都完整：{'是' if result.get('all_data_complete') else '否'}")
    fugle_side = result.get("fugle_trade_side_status") or {}
    print()
    print("Fugle 逐筆成交 side 推論：")
    print(f"欄位狀態：{'完整' if fugle_side.get('columns_ok') else '缺欄位'}")
    print(f"可用狀態：{'可用' if fugle_side.get('available') else '不可用'}")
    print(f"最新日期：{fugle_side.get('latest_date') or '--'}")
    print(f"股票數：{fugle_side.get('stock_count') or 0}")
    print(f"逐筆筆數：{fugle_side.get('total_trades') or 0}")
    if fugle_side.get("stats_scope") == "latest_trade_date_only":
        print("統計範圍：僅最新交易日")
    print(f"side NULL 筆數：{fugle_side.get('null_side_count') or 0}")
    print(f"side 空字串筆數：{fugle_side.get('blank_side_count') or 0}")
    print(f"side UNKNOWN 筆數：{fugle_side.get('unknown_side_count') or 0}")
    print(f"method NULL 筆數：{fugle_side.get('null_method_count') or 0}")
    print(f"method 空字串筆數：{fugle_side.get('blank_method_count') or 0}")
    print(f"method UNKNOWN 筆數：{fugle_side.get('unknown_method_count') or 0}")
    print(f"side 欄位資料狀態：{fugle_side.get('data_status') or '--'}")
    print(f"side 分布：{json.dumps(fugle_side.get('side_counts') or {}, ensure_ascii=False, sort_keys=True)}")
    print(f"method 分布：{json.dumps(fugle_side.get('method_counts') or {}, ensure_ascii=False, sort_keys=True)}")
    print("提醒：這是補充推論欄位，不是交易所原始內外盤。")
    if details:
        print()
        print("詳細檢查：")
        for key in ("price_volume_status", "time_sales_status", "inner_outer_status"):
            item = result.get(key) or {}
            print(f"- {item.get('category') or key}:")
            for check in item.get("table_checks") or []:
                exists = "存在" if check.get("exists") else "未建立"
                print(
                    f"  - {check.get('table')}: {exists}"
                    f"，最新日期：{check.get('latest_date') or '--'}"
                    f"，筆數：{check.get('row_count') or 0}"
                )


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify latest market foundation rows in SQLite.")
    parser.add_argument("--min-count", type=int, default=1000)
    parser.add_argument("--table", default="history_price")
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--zh", action="store_true", help="Print a Chinese human-readable completeness summary.")
    parser.add_argument("--details", action="store_true", help="Include table-level completeness details in Chinese output.")
    args = parser.parse_args()

    try:
        result = verify_market_foundation_update(args.table, args.min_count)
    except Exception as exc:
        result = {
            "ok": False,
            "status": "ERROR",
            "latest_date": None,
            "row_count": 0,
            "min_count": args.min_count,
            "db_path": str(DB_PATH) if DB_PATH else None,
            "error": str(exc),
        }

    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    elif args.zh:
        print_zh_summary(result, details=args.details)
    else:
        print(f"SQLite DB: {result.get('db_path') or '--'}")
        print(f"Latest data date: {result.get('latest_date') or '--'}")
        print(f"{args.table} row count on global latest date: {result.get('global_latest_row_count')}")
        if result.get("market_latest_counts"):
            print("Market latest row counts:")
            for item in result["market_latest_counts"]:
                print(f"  - {item['market']}: {item['latest_date']} / {item['row_count']}")
        if result.get("official_source_latest_counts"):
            print("Official source latest row counts:")
            for item in result["official_source_latest_counts"]:
                print(f"  - {item['source']}: {item['latest_date']} / {item['row_count']}")
        print(f"Effective row count: {result.get('row_count')}")
        print(f"Minimum expected count: {result.get('min_count')}")
        print(f"Verification result: {result.get('status')}")
        if result.get("error"):
            print(f"Error: {result.get('error')}")

    if result.get("ok"):
        return 0
    if result.get("status") in {"DB_NOT_CONFIGURED", "DB_NOT_FOUND"}:
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
