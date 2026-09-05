"""Verify backup hashes and reopened databases; optionally rehearse restore into a NEW directory."""

from __future__ import annotations
import argparse
from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.migrate_legacy import sha256


def verify(backup: Path, restore_to: Path | None = None) -> dict:
    backup = backup.resolve()
    manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or not manifest:
        raise ValueError("Backup manifest is empty or invalid")
    if restore_to is not None:
        restore_to = restore_to.resolve()
        restore_to.mkdir(parents=True, exist_ok=False)
    results = {}
    for name, record in manifest.items():
        filename = record["file"]
        if Path(filename).name != filename or not filename.endswith(".sqlite3"):
            raise ValueError("Backup manifest filename must be a SQLite basename")
        source = (backup / filename).resolve()
        if source.parent != backup or not source.is_file():
            raise ValueError("Backup file is missing or outside the backup directory")
        target = source
        if restore_to is not None:
            target = restore_to / (filename + ".partial")
            with source.open("rb") as src, target.open("xb") as dst:
                shutil.copyfileobj(src, dst, length=4 * 1024 * 1024)
        if target.stat().st_size != record["bytes"] or sha256(target) != record["sha256"]:
            raise ValueError(f"Backup size or hash mismatch: {name}")
        with closing(sqlite3.connect(target.as_uri() + "?mode=ro", uri=True)) as conn:
            if [r[0] for r in conn.execute("PRAGMA integrity_check")] != ["ok"]:
                raise ValueError(f"Database integrity check failed: {name}")
            if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise ValueError(f"Database foreign-key check failed: {name}")
            tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            counts = {
                table: conn.execute('SELECT COUNT(*) FROM "' + table.replace('"', '""') + '"').fetchone()[0]
                for table in tables
            }
            if counts != record["table_rows"]:
                raise ValueError(f"Database row counts differ from manifest: {name}")
        if restore_to is not None:
            target.replace(restore_to / filename)
        results[name] = {
            "ok": True,
            "bytes": record["bytes"],
            "tables": len(counts),
            "rows": sum(counts.values()),
        }
        print(json.dumps({"verified": name, **results[name]}), flush=True)
    report = {
        "ok": True,
        "restore_rehearsed": restore_to is not None,
        "production_modified": False,
        "databases": results,
        "keys_restored": False,
        "note": "Encryption keys are separate; this validates database recovery only.",
    }
    if restore_to is not None:
        (restore_to / "verified-restore.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup", type=Path)
    parser.add_argument("--restore-to", type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.backup, args.restore_to), indent=2))


if __name__ == "__main__":
    main()
