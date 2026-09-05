from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path
from typing import Any, Iterable


SALVAGED_TABLE = "price_volume_distribution"
OMITTED_TABLES = {
    "fugle_intraday_trades": (
        "The source table is corrupt; raw supplemental trades must be collected again."
    ),
    "fugle_intraday_capture_runs": (
        "Capture claims are intentionally reset because the related raw trades are not copied."
    ),
    "technical_indicator_component": (
        "Derived technical components are intentionally rebuilt from trusted OHLCV history."
    ),
    "technical_indicator_state": (
        "Derived technical state is intentionally rebuilt with the technical components."
    ),
    "daily_technical_snapshot": (
        "Derived daily technical snapshots are intentionally rebuilt from trusted OHLCV history."
    ),
    "daily_inner_outer_volume": (
        "Derived Fugle inner/outer summaries are intentionally rebuilt from persisted source payloads."
    ),
}
REQUIRED_REBUILT_TABLES = {
    SALVAGED_TABLE,
    "fugle_intraday_trades",
    "fugle_intraday_capture_runs",
}
REBUILT_TABLES = {SALVAGED_TABLE, *OMITTED_TABLES}


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _quote_pragma_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _read_only_uri(path: Path) -> str:
    return f"{path.as_uri()}?mode=ro"


def _database_objects(conn: sqlite3.Connection, object_type: str) -> list[tuple[str, str]]:
    return [
        (str(name), str(sql))
        for name, sql in conn.execute(
            """
            SELECT name, sql
            FROM sqlite_master
            WHERE type = ?
              AND name NOT LIKE 'sqlite_%'
              AND sql IS NOT NULL
            ORDER BY name
            """,
            (object_type,),
        )
    ]


def _table_names(conn: sqlite3.Connection) -> list[str]:
    return [name for name, _ in _database_objects(conn, "table")]


def _table_integrity(conn: sqlite3.Connection, table: str) -> str:
    pragma = f"PRAGMA integrity_check({_quote_pragma_string(table)})"
    rows = [str(row[0]) for row in conn.execute(pragma)]
    return "ok" if rows == ["ok"] else "\n".join(rows)


def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    pragma = f"PRAGMA table_info({_quote_identifier(table)})"
    return [str(row[1]) for row in conn.execute(pragma)]


def _copy_table(
    destination_conn: sqlite3.Connection,
    table: str,
) -> tuple[int, int]:
    quoted = _quote_identifier(table)
    source_count = int(
        destination_conn.execute(f"SELECT COUNT(*) FROM source_db.{quoted}").fetchone()[0]
    )
    destination_conn.execute("BEGIN")
    try:
        destination_conn.execute(
            f"INSERT INTO main.{quoted} SELECT * FROM source_db.{quoted}"
        )
        destination_conn.commit()
    except Exception:
        destination_conn.rollback()
        raise
    destination_count = int(
        destination_conn.execute(f"SELECT COUNT(*) FROM main.{quoted}").fetchone()[0]
    )
    if destination_count != source_count:
        raise RuntimeError(
            f"Row-count mismatch for {table}: source={source_count}, "
            f"destination={destination_count}"
        )
    return source_count, destination_count


def _latest_history_dates(
    conn: sqlite3.Connection,
    limit: int,
) -> list[str]:
    if "history_price" not in _table_names(conn):
        return []
    return [
        str(row[0])
        for row in conn.execute(
            """
            SELECT DISTINCT date
            FROM history_price
            WHERE date IS NOT NULL AND TRIM(date) <> ''
            ORDER BY date DESC
            LIMIT ?
            """,
            (limit,),
        )
    ]


def _salvage_price_volume_distribution(
    source_conn: sqlite3.Connection,
    destination_conn: sqlite3.Connection,
    dates: Iterable[str],
) -> dict[str, Any]:
    tables = set(_table_names(source_conn))
    if SALVAGED_TABLE not in tables:
        return {
            "status": "source_table_absent",
            "rows": 0,
            "dates": [],
            "skipped_dates": [],
        }

    columns = _table_columns(source_conn, SALVAGED_TABLE)
    if not columns:
        raise RuntimeError(f"Could not read columns for {SALVAGED_TABLE}")
    quoted_columns = ", ".join(_quote_identifier(column) for column in columns)
    placeholders = ", ".join("?" for _ in columns)
    source_sql = (
        f"SELECT {quoted_columns} FROM {_quote_identifier(SALVAGED_TABLE)} "
        "INDEXED BY idx_price_volume_distribution_date "
        "WHERE trade_date = ? ORDER BY id"
    )
    insert_sql = (
        f"INSERT INTO {_quote_identifier(SALVAGED_TABLE)} ({quoted_columns}) "
        f"VALUES ({placeholders})"
    )

    recovered_dates: list[dict[str, Any]] = []
    skipped_dates: list[dict[str, str]] = []
    recovered_rows = 0
    for trade_date in dates:
        try:
            # Fetch a whole date before inserting it. A corrupt source read can therefore
            # never leave a partially copied date in the destination.
            rows = source_conn.execute(source_sql, (trade_date,)).fetchall()
        except sqlite3.DatabaseError as exc:
            skipped_dates.append({"date": trade_date, "reason": str(exc)})
            continue
        if not rows:
            continue
        destination_conn.execute("BEGIN")
        try:
            destination_conn.executemany(insert_sql, rows)
            destination_conn.commit()
        except Exception:
            destination_conn.rollback()
            raise
        recovered_rows += len(rows)
        recovered_dates.append({"date": trade_date, "rows": len(rows)})

    destination_rows = int(
        destination_conn.execute(
            f"SELECT COUNT(*) FROM {_quote_identifier(SALVAGED_TABLE)}"
        ).fetchone()[0]
    )
    if destination_rows != recovered_rows:
        raise RuntimeError(
            f"Salvage row-count mismatch: inserted={recovered_rows}, "
            f"destination={destination_rows}"
        )
    return {
        "status": "salvaged_by_trade_date",
        "rows": recovered_rows,
        "dates": recovered_dates,
        "skipped_dates": skipped_dates,
    }


def _copy_sequences(
    source_conn: sqlite3.Connection,
    destination_conn: sqlite3.Connection,
) -> None:
    has_source_sequence = source_conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sqlite_sequence'"
    ).fetchone()
    has_destination_sequence = destination_conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sqlite_sequence'"
    ).fetchone()
    if not has_source_sequence or not has_destination_sequence:
        return
    for name, sequence in source_conn.execute("SELECT name, seq FROM sqlite_sequence"):
        if name in OMITTED_TABLES:
            continue
        current_row = destination_conn.execute(
            "SELECT seq FROM sqlite_sequence WHERE name = ?", (name,)
        ).fetchone()
        current = int(current_row[0]) if current_row else 0
        target = max(current, int(sequence or 0))
        updated = destination_conn.execute(
            "UPDATE sqlite_sequence SET seq = ? WHERE name = ?",
            (target, name),
        )
        if updated.rowcount == 0:
            destination_conn.execute(
                "INSERT INTO sqlite_sequence(name, seq) VALUES (?, ?)",
                (name, target),
            )
    destination_conn.commit()


def _verify_database(
    destination: Path,
    copied_tables: dict[str, dict[str, int]],
) -> dict[str, Any]:
    with closing(
        sqlite3.connect(_read_only_uri(destination), uri=True, timeout=60)
    ) as conn:
        quick_rows = [str(row[0]) for row in conn.execute("PRAGMA quick_check")]
        integrity_rows = [str(row[0]) for row in conn.execute("PRAGMA integrity_check")]
        quick_check = "ok" if quick_rows == ["ok"] else "\n".join(quick_rows)
        integrity_check = (
            "ok" if integrity_rows == ["ok"] else "\n".join(integrity_rows)
        )
        if quick_check != "ok" or integrity_check != "ok":
            raise RuntimeError(
                "Recovered database failed integrity verification: "
                f"quick_check={quick_check}; integrity_check={integrity_check}"
            )
        for table, counts in copied_tables.items():
            actual = int(
                conn.execute(
                    f"SELECT COUNT(*) FROM {_quote_identifier(table)}"
                ).fetchone()[0]
            )
            if actual != counts["source_rows"]:
                raise RuntimeError(
                    f"Final row-count mismatch for {table}: "
                    f"expected={counts['source_rows']}, actual={actual}"
                )
        history_count = None
        history_latest = None
        if "history_price" in _table_names(conn):
            history_count = int(conn.execute("SELECT COUNT(*) FROM history_price").fetchone()[0])
            history_latest = conn.execute("SELECT MAX(date) FROM history_price").fetchone()[0]
        return {
            "quick_check": quick_check,
            "integrity_check": integrity_check,
            "journal_mode": str(conn.execute("PRAGMA journal_mode").fetchone()[0]),
            "history_count": history_count,
            "history_latest": history_latest,
        }


def recover_database(
    source: Path,
    destination: Path,
    *,
    price_volume_history_days: int = 60,
) -> dict[str, Any]:
    started = time.time()
    source = source.resolve()
    destination = destination.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Source database not found: {source}")
    if source == destination:
        raise ValueError("Source and destination database paths must differ")
    if destination.exists():
        raise FileExistsError(f"Destination already exists: {destination}")
    if price_volume_history_days < 1:
        raise ValueError("price_volume_history_days must be at least 1")
    destination.parent.mkdir(parents=True, exist_ok=True)

    copied_tables: dict[str, dict[str, int]] = {}
    omitted: dict[str, dict[str, Any]] = {}
    salvage: dict[str, Any] = {}
    try:
        with closing(
            sqlite3.connect(_read_only_uri(source), uri=True, timeout=60)
        ) as source_conn:
            source_conn.execute("PRAGMA query_only=ON")
            tables = _table_names(source_conn)
            missing = REQUIRED_REBUILT_TABLES.difference(tables)
            if missing:
                raise RuntimeError(
                    "Required recovery tables are missing from source schema: "
                    + ", ".join(sorted(missing))
                )

            integrity: dict[str, str] = {}
            for table in tables:
                if table in REBUILT_TABLES:
                    continue
                result = _table_integrity(source_conn, table)
                integrity[table] = result
                if result != "ok":
                    raise RuntimeError(
                        f"Unexpected corruption outside recovery scope in {table}: {result}"
                    )

            table_sql = dict(_database_objects(source_conn, "table"))
            index_sql = _database_objects(source_conn, "index")
            view_sql = _database_objects(source_conn, "view")
            trigger_sql = _database_objects(source_conn, "trigger")
            history_dates = _latest_history_dates(
                source_conn, price_volume_history_days
            )
            user_version = int(source_conn.execute("PRAGMA user_version").fetchone()[0])
            application_id = int(source_conn.execute("PRAGMA application_id").fetchone()[0])

            destination_uri = f"{destination.as_uri()}?mode=rwc"
            with closing(
                sqlite3.connect(
                    destination_uri,
                    uri=True,
                    timeout=60,
                )
            ) as destination_conn:
                destination_conn.execute("PRAGMA journal_mode=OFF")
                destination_conn.execute("PRAGMA synchronous=OFF")
                destination_conn.execute("PRAGMA foreign_keys=OFF")
                for table in tables:
                    destination_conn.execute(table_sql[table])
                destination_conn.commit()

                source_uri = _read_only_uri(source).replace("'", "''")
                destination_conn.execute(f"ATTACH DATABASE '{source_uri}' AS source_db")
                for table in tables:
                    if table in REBUILT_TABLES:
                        continue
                    source_rows, destination_rows = _copy_table(
                        destination_conn, table
                    )
                    copied_tables[table] = {
                        "source_rows": source_rows,
                        "destination_rows": destination_rows,
                    }

                salvage = _salvage_price_volume_distribution(
                    source_conn,
                    destination_conn,
                    history_dates,
                )
                for table, reason in OMITTED_TABLES.items():
                    source_rows = None
                    try:
                        source_rows = int(
                            source_conn.execute(
                                f"SELECT COUNT(*) FROM {_quote_identifier(table)}"
                            ).fetchone()[0]
                        )
                    except sqlite3.DatabaseError:
                        pass
                    omitted[table] = {
                        "source_rows": source_rows,
                        "destination_rows": 0,
                        "reason": reason,
                    }

                destination_conn.execute("DETACH DATABASE source_db")
                _copy_sequences(source_conn, destination_conn)
                for _name, sql in index_sql:
                    destination_conn.execute(sql)
                for _name, sql in view_sql:
                    destination_conn.execute(sql)
                for _name, sql in trigger_sql:
                    destination_conn.execute(sql)
                destination_conn.execute(f"PRAGMA user_version={user_version}")
                destination_conn.execute(f"PRAGMA application_id={application_id}")
                destination_conn.commit()
                journal_mode = str(
                    destination_conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
                ).lower()
                if journal_mode != "wal":
                    raise RuntimeError(
                        f"Recovered database could not enable WAL journal mode: {journal_mode}"
                    )
                destination_conn.execute("PRAGMA synchronous=FULL")
                destination_conn.execute("PRAGMA optimize")

        verification = _verify_database(destination, copied_tables)
    except Exception:
        destination.unlink(missing_ok=True)
        raise

    return {
        "status": "ok",
        "source": str(source),
        "destination": str(destination),
        "source_preserved": True,
        "destination_bytes": destination.stat().st_size,
        "elapsed_seconds": round(time.time() - started, 3),
        "copied_tables": copied_tables,
        "salvage": {SALVAGED_TABLE: salvage},
        "omitted_tables": omitted,
        "verification": verification,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Rebuild a market SQLite database from a read-only source while "
            "salvaging readable recent price-volume dates."
        )
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument(
        "--price-volume-history-days",
        type=int,
        default=60,
        help="Number of latest history_price dates to attempt for price-volume salvage.",
    )
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = recover_database(
        args.source,
        args.destination,
        price_volume_history_days=args.price_volume_history_days,
    )
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.report:
        report = args.report.resolve()
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"recovery_failed={type(exc).__name__}: {exc}", file=sys.stderr)
        raise
