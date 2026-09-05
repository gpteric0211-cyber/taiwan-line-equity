from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "review_src" / "data" / "taiwan50.db"


DDL = """
CREATE TABLE IF NOT EXISTS price_volume_distribution (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_id TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    price REAL NOT NULL,
    volume_lots INTEGER NOT NULL DEFAULT 0,
    volume_shares INTEGER,
    total_volume_lots INTEGER,
    snapshot_time TEXT,
    created_at REAL,
    updated_at REAL,
    UNIQUE(stock_id, trade_date, price)
);
CREATE INDEX IF NOT EXISTS idx_price_volume_distribution_stock
    ON price_volume_distribution(stock_id);
CREATE INDEX IF NOT EXISTS idx_price_volume_distribution_date
    ON price_volume_distribution(trade_date);
CREATE INDEX IF NOT EXISTS idx_price_volume_distribution_stock_date
    ON price_volume_distribution(stock_id, trade_date);
"""


def parse_number(value: object) -> float | None:
    text = str(value or "").strip().replace(",", "").replace("%", "")
    if not text or text in {"--", "-"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def get_field(row: dict[str, str], *names: str) -> str:
    for name in names:
        if name in row and str(row[name]).strip():
            return row[name]
    return ""


def cleanup_distribution(conn: sqlite3.Connection, stock_id: str, keep_days: int = 200) -> int:
    cutoff = conn.execute(
        """
        SELECT trade_date
        FROM (
            SELECT DISTINCT trade_date
            FROM price_volume_distribution
            WHERE stock_id=?
            ORDER BY trade_date DESC
            LIMIT 1 OFFSET ?
        )
        """,
        (stock_id, max(0, keep_days - 1)),
    ).fetchone()
    if not cutoff:
        return 0
    cur = conn.execute(
        "DELETE FROM price_volume_distribution WHERE stock_id=? AND trade_date < ?",
        (stock_id, cutoff[0]),
    )
    return int(cur.rowcount or 0)


def import_csv(path: Path, db_path: Path) -> int:
    now = time.time()
    rows_to_import: list[dict[str, object]] = []
    skipped = 0
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for raw in reader:
            stock_id = get_field(raw, "stock_id", "code", "證券代號", "股票代號").strip().zfill(4)
            trade_date = get_field(raw, "trade_date", "date", "資料日期", "日期").strip().replace("/", "-")
            price = parse_number(get_field(raw, "price", "成交價", "成交價(元)", "價格"))
            volume_lots = parse_number(get_field(raw, "volume_lots", "成交張數", "成交量(張)", "volume"))
            snapshot_time = get_field(raw, "snapshot_time", "成交時間", "time", "時間").strip() or None
            if not stock_id or not trade_date or price is None or volume_lots is None:
                skipped += 1
                continue
            rows_to_import.append({
                "stock_id": stock_id,
                "trade_date": trade_date,
                "price": float(price),
                "volume_lots": int(round(float(volume_lots))),
                "snapshot_time": snapshot_time,
            })
    totals: dict[tuple[str, str], int] = {}
    for row in rows_to_import:
        key = (str(row["stock_id"]), str(row["trade_date"]))
        totals[key] = totals.get(key, 0) + int(row["volume_lots"])

    inserted = 0
    updated = 0
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("PRAGMA busy_timeout=5000;")
        conn.executescript(DDL)
        for row in rows_to_import:
            existing = conn.execute(
                "SELECT id FROM price_volume_distribution WHERE stock_id=? AND trade_date=? AND price=?",
                (row["stock_id"], row["trade_date"], row["price"]),
            ).fetchone()
            total_lots = totals[(str(row["stock_id"]), str(row["trade_date"]))]
            conn.execute(
                """
                INSERT INTO price_volume_distribution(
                    stock_id, trade_date, price, volume_lots, volume_shares,
                    total_volume_lots, snapshot_time, created_at, updated_at
                )
                VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(stock_id, trade_date, price) DO UPDATE SET
                    volume_lots=excluded.volume_lots,
                    volume_shares=excluded.volume_shares,
                    total_volume_lots=excluded.total_volume_lots,
                    snapshot_time=excluded.snapshot_time,
                    updated_at=excluded.updated_at
                """,
                (
                    row["stock_id"],
                    row["trade_date"],
                    row["price"],
                    row["volume_lots"],
                    int(row["volume_lots"]) * 1000,
                    total_lots,
                    row["snapshot_time"],
                    now,
                    now,
                ),
            )
            if existing:
                updated += 1
            else:
                inserted += 1
        removed = 0
        for stock_id in sorted({str(r["stock_id"]) for r in rows_to_import}):
            removed += cleanup_distribution(conn, stock_id, keep_days=200)
        conn.commit()
    print(f"imported={inserted} updated={updated} skipped={skipped} cleanup_removed={removed} db={db_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Import true daily price-volume distribution CSV.")
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--db", type=Path, default=DB_PATH)
    args = parser.parse_args()
    if not args.csv_path.exists():
        print(f"CSV not found: {args.csv_path}", file=sys.stderr)
        return 2
    return import_csv(args.csv_path, args.db)


if __name__ == "__main__":
    raise SystemExit(main())
