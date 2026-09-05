from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any


TRADE_TABLE = "fugle_intraday_trades"
CAPTURE_TABLE = "fugle_intraday_capture_runs"
INNER_TABLE = "daily_inner_outer_volume"


def _read_only_uri(path: Path) -> str:
    return f"{path.resolve().as_uri()}?mode=ro"


def _read_write_uri(path: Path) -> str:
    return f"{path.resolve().as_uri()}?mode=rw"


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")')]


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
    )


def _row_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def _validate_scoped_rows(
    code: str,
    trade_date: str,
    trades: list[dict[str, Any]],
    capture: dict[str, Any],
) -> None:
    expected_rows = int(capture.get("normalized_row_count") or 0)
    stored_rows = int(capture.get("stored_row_count") or 0)
    quality = str(capture.get("data_quality") or "").upper()
    complete = int(capture.get("capture_complete") or 0)
    if expected_rows != stored_rows or stored_rows != len(trades):
        raise RuntimeError(
            f"{code} capture row-count mismatch: normalized={expected_rows}, "
            f"stored={stored_rows}, readable={len(trades)}"
        )
    if trades:
        if not complete or quality != "SESSION_COMPLETE":
            raise RuntimeError(
                f"{code} readable rows do not have SESSION_COMPLETE evidence"
            )
        sizes = []
        trade_times = []
        for row in trades:
            if (
                str(row.get("code") or "") != code
                or str(row.get("trade_date") or "") != trade_date
                or str(row.get("source") or "").upper() != "FUGLE"
            ):
                raise RuntimeError(f"{code} scoped source row identity mismatch")
            json.loads(str(row.get("raw_json") or "{}"))
            sizes.append(int(row.get("size") or 0))
            trade_times.append(str(row.get("trade_time") or ""))
        captured_lots = capture.get("captured_volume_lots")
        if captured_lots is None or sum(sizes) != int(captured_lots):
            raise RuntimeError(
                f"{code} captured volume mismatch: rows={sum(sizes)}, "
                f"capture={captured_lots}"
            )
        if max(trade_times, default="") != str(
            capture.get("latest_trade_time") or ""
        ):
            raise RuntimeError(f"{code} latest trade-time evidence mismatch")
    elif complete or quality != "UNAVAILABLE":
        raise RuntimeError(
            f"{code} empty scoped rows require an UNAVAILABLE capture record"
        )


def salvage_scoped_fugle_rows(
    source: Path,
    destination: Path,
    trade_date: str,
    codes: list[str],
) -> dict[str, Any]:
    source = source.resolve()
    destination = destination.resolve()
    if not source.is_file() or not destination.is_file():
        raise FileNotFoundError("source and destination databases must exist")
    if source == destination:
        raise ValueError("source and destination databases must differ")
    selected_codes = sorted(
        {
            str(code).strip().zfill(4)
            for code in codes
            if str(code).strip().isdigit()
        }
    )
    results: list[dict[str, Any]] = []
    with closing(
        sqlite3.connect(_read_only_uri(source), uri=True, timeout=60)
    ) as source_conn, closing(
        sqlite3.connect(_read_write_uri(destination), uri=True, timeout=60)
    ) as destination_conn:
        source_conn.row_factory = sqlite3.Row
        destination_conn.row_factory = sqlite3.Row
        destination_conn.execute("PRAGMA busy_timeout=30000")
        destination_conn.execute("PRAGMA synchronous=FULL")
        for table in (TRADE_TABLE, CAPTURE_TABLE):
            if _columns(source_conn, table) != _columns(destination_conn, table):
                raise RuntimeError(f"source/destination schema mismatch for {table}")
        copy_inner = bool(
            _table_exists(source_conn, INNER_TABLE)
            and _table_exists(destination_conn, INNER_TABLE)
        )
        if copy_inner and _columns(source_conn, INNER_TABLE) != _columns(
            destination_conn, INNER_TABLE
        ):
            raise RuntimeError(f"source/destination schema mismatch for {INNER_TABLE}")
        trade_columns = _columns(destination_conn, TRADE_TABLE)
        capture_columns = _columns(destination_conn, CAPTURE_TABLE)
        trade_insert = (
            f'INSERT INTO "{TRADE_TABLE}" ({", ".join(trade_columns)}) '
            f'VALUES ({", ".join("?" for _ in trade_columns)})'
        )
        capture_insert = (
            f'INSERT INTO "{CAPTURE_TABLE}" ({", ".join(capture_columns)}) '
            f'VALUES ({", ".join("?" for _ in capture_columns)})'
        )
        inner_columns = _columns(destination_conn, INNER_TABLE) if copy_inner else []
        inner_insert = (
            f'INSERT INTO "{INNER_TABLE}" ({", ".join(inner_columns)}) '
            f'VALUES ({", ".join("?" for _ in inner_columns)})'
            if inner_columns
            else ""
        )
        for code in selected_codes:
            try:
                trades = _row_dicts(
                    source_conn.execute(
                        f"""
                        SELECT * FROM {TRADE_TABLE}
                        WHERE code=? AND trade_date=? AND source='FUGLE'
                        ORDER BY trade_time,serial
                        """,
                        (code, trade_date),
                    ).fetchall()
                )
                capture_row = source_conn.execute(
                    f"""
                    SELECT * FROM {CAPTURE_TABLE}
                    WHERE code=? AND trade_date=?
                      AND endpoint='trades' AND source='FUGLE'
                    LIMIT 1
                    """,
                    (code, trade_date),
                ).fetchone()
                if capture_row is None:
                    raise RuntimeError(f"{code} has no readable capture record")
                capture = dict(capture_row)
                _validate_scoped_rows(code, trade_date, trades, capture)
                inner_rows: list[dict[str, Any]] = []
                inner_error = None
                if copy_inner:
                    try:
                        inner_rows = _row_dicts(
                            source_conn.execute(
                                f"""
                                SELECT * FROM {INNER_TABLE}
                                WHERE stock_code=? AND trade_date=?
                                  AND UPPER(COALESCE(source,''))='FUGLE'
                                """,
                                (code, trade_date),
                            ).fetchall()
                        )
                    except sqlite3.DatabaseError as exc:
                        inner_error = str(exc)
                if trades:
                    quality_row = destination_conn.execute(
                        """
                        SELECT COUNT(*) FROM price_volume_distribution
                        WHERE stock_id=? AND trade_date=?
                          AND UPPER(COALESCE(data_quality,source_quality,''))
                              IN ('VALIDATED','SCOPED_VALIDATED')
                        """,
                        (code, trade_date),
                    ).fetchone()
                    if not quality_row or int(quality_row[0] or 0) <= 0:
                        raise RuntimeError(
                            f"{code} has no validated destination distribution"
                        )
                destination_conn.execute("BEGIN IMMEDIATE")
                try:
                    destination_conn.execute(
                        f"DELETE FROM {TRADE_TABLE} WHERE code=? AND trade_date=? AND source='FUGLE'",
                        (code, trade_date),
                    )
                    destination_conn.execute(
                        f"DELETE FROM {CAPTURE_TABLE} WHERE code=? AND trade_date=? AND endpoint='trades' AND source='FUGLE'",
                        (code, trade_date),
                    )
                    if copy_inner and not inner_error:
                        destination_conn.execute(
                            f"DELETE FROM {INNER_TABLE} WHERE stock_code=? AND trade_date=? AND UPPER(COALESCE(source,''))='FUGLE'",
                            (code, trade_date),
                        )
                    if trades:
                        destination_conn.executemany(
                            trade_insert,
                            [tuple(row[column] for column in trade_columns) for row in trades],
                        )
                    destination_conn.execute(
                        capture_insert,
                        tuple(capture[column] for column in capture_columns),
                    )
                    if inner_rows:
                        destination_conn.executemany(
                            inner_insert,
                            [
                                tuple(row[column] for column in inner_columns)
                                for row in inner_rows
                            ],
                        )
                    destination_conn.commit()
                except Exception:
                    destination_conn.rollback()
                    raise
                results.append(
                    {
                        "code": code,
                        "status": "ok",
                        "trade_rows": len(trades),
                        "capture_quality": capture.get("data_quality"),
                        "inner_outer_rows": len(inner_rows),
                        "inner_outer_error": inner_error,
                    }
                )
            except Exception as exc:
                results.append(
                    {"code": code, "status": "failed", "error": str(exc)}
                )
        quick_check = [
            str(row[0]) for row in destination_conn.execute("PRAGMA quick_check")
        ]
    failed = [row for row in results if row["status"] == "failed"]
    return {
        "ok": not failed and quick_check == ["ok"],
        "status": "ok" if not failed and quick_check == ["ok"] else "partial",
        "source": str(source),
        "destination": str(destination),
        "trade_date": trade_date,
        "selected_code_count": len(selected_codes),
        "successful_code_count": len(results) - len(failed),
        "failed_code_count": len(failed),
        "failed_results": failed,
        "copied_trade_rows": sum(int(row.get("trade_rows") or 0) for row in results),
        "copied_inner_outer_rows": sum(
            int(row.get("inner_outer_rows") or 0) for row in results
        ),
        "quick_check": quick_check,
        "results": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Salvage explicitly scoped, fully validated Fugle rows from a preserved database."
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--codes", required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    result = salvage_scoped_fugle_rows(
        args.source,
        args.destination,
        args.date,
        args.codes.replace(",", " ").split(),
    )
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.report:
        report = args.report.resolve()
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0 if result["ok"] else 4


if __name__ == "__main__":
    raise SystemExit(main())
