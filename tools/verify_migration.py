"""Compare every inherited business table in two read-only SQLite snapshots."""

from __future__ import annotations
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import pickle
import sqlite3
import time


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def signature(conn, table):
    columns = conn.execute("PRAGMA table_info(" + quote(table) + ")").fetchall()
    primary = [row[1] for row in sorted(columns, key=lambda row: row[5]) if row[5]]
    order = ",".join(quote(name) for name in primary) if primary else "rowid"
    digest = hashlib.sha256()
    digest.update(json.dumps([(row[1], row[2]) for row in columns]).encode())
    count = 0
    cursor = conn.execute("SELECT * FROM " + quote(table) + " ORDER BY " + order)
    while rows := cursor.fetchmany(5000):
        for row in rows:
            digest.update(pickle.dumps(tuple(row), protocol=4))
        count += len(rows)
    return {"rows": count, "sha256": digest.hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    result = {"source_read_only": True, "target_read_only": True, "tables": {}}
    started = time.monotonic()
    with (
        closing(sqlite3.connect(args.source.resolve().as_uri() + "?mode=ro", uri=True)) as src,
        closing(sqlite3.connect(args.destination.resolve().as_uri() + "?mode=ro", uri=True)) as dst,
    ):
        src.execute("BEGIN")
        dst.execute("BEGIN")
        src.execute("PRAGMA query_only=ON")
        dst.execute("PRAGMA query_only=ON")
        tables = [
            row[0]
            for row in src.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        for index, table in enumerate(tables):
            before, after = signature(src, table), signature(dst, table)
            result["tables"][table] = {"source": before, "target": after, "identical": before == after}
            if index % 10 == 0:
                print(json.dumps({"checked_tables": index + 1, "total_tables": len(tables)}), flush=True)
        result["additional_tables"] = [
            row[0]
            for row in dst.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
            if row[0] not in tables
        ]
    result["all_inherited_rows_identical"] = all(item["identical"] for item in result["tables"].values())
    result["inherited_table_count"] = len(result["tables"])
    result["inherited_row_count"] = sum(item["source"]["rows"] for item in result["tables"].values())
    result["elapsed_seconds"] = round(time.monotonic() - started, 2)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "tables"}), flush=True)
    return 0 if result["all_inherited_rows_identical"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
