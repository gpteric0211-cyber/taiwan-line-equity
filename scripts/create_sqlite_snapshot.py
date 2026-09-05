from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


def create_snapshot(source: Path, destination: Path) -> dict[str, object]:
    source = source.resolve()
    destination = destination.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Source database not found: {source}")
    if source == destination:
        raise ValueError("Source and destination database paths must differ")

    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()

    source_uri = f"{source.as_uri()}?mode=ro"
    with sqlite3.connect(source_uri, uri=True, timeout=30) as source_conn:
        with sqlite3.connect(destination, timeout=30) as destination_conn:
            source_conn.backup(destination_conn, pages=4096, sleep=0.05)
            destination_conn.execute("PRAGMA optimize")

    with sqlite3.connect(f"{destination.as_uri()}?mode=ro", uri=True) as verify_conn:
        quick_check = str(verify_conn.execute("PRAGMA quick_check").fetchone()[0])
        table_count = int(
            verify_conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'"
            ).fetchone()[0]
        )
        history_latest = None
        if verify_conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'history_price'"
        ).fetchone():
            history_latest = verify_conn.execute(
                "SELECT MAX(date) FROM history_price"
            ).fetchone()[0]

    if quick_check.lower() != "ok":
        destination.unlink(missing_ok=True)
        raise RuntimeError(f"SQLite snapshot integrity check failed: {quick_check}")

    return {
        "source": str(source),
        "destination": str(destination),
        "bytes": destination.stat().st_size,
        "quick_check": quick_check,
        "table_count": table_count,
        "history_latest": history_latest,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a consistent portable SQLite snapshot.")
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    result = create_snapshot(args.source, args.destination)
    for key, value in result.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
