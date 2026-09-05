"""Copy an existing project without modifying it; produce a verifiable manifest.

Run from the new project: python tools/migrate_legacy.py SOURCE --destination .
Database snapshots use SQLite's online backup API, never a live file copy.
Excluded archives/runtimes stay at SOURCE until separately reconciled.
"""

from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import time

EXCLUDED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".tmp",
    "node_modules",
    "dist",
    "runtime",
    "models",
    "backups",
    "logs",
    "MitakeGU",
    ".codex",
}
SOURCE_DIRS = {"review_src", "scripts", "tests", "docs", "packaging", ".agents", "維護工具"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_sources(source: Path, destination: Path) -> dict:
    entries = []
    for base, dirs, files in os.walk(source):
        relative = Path(base).relative_to(source)
        dirs[:] = [
            d
            for d in dirs
            if d not in EXCLUDED_DIRS
            and (relative.parts or d in SOURCE_DIRS)
            and not (relative == Path("review_src") and d == "data")
        ]
        for name in files:
            item = Path(base) / name
            rel = item.relative_to(source)
            if item.is_symlink() or name.endswith(
                (".pyc", ".log", ".err", ".db", ".sqlite", ".sqlite3", "-wal", "-shm")
            ):
                continue
            if name.startswith(".env") and not name.endswith("example"):
                continue
            target = destination / rel
            digest = sha256(item)
            if target.exists():
                if sha256(target) != digest:
                    raise ValueError(f"Refusing to overwrite changed file: {rel.as_posix()}")
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, target)
            if sha256(target) != digest:
                raise OSError(f"Copy verification failed: {rel.as_posix()}")
            entries.append({"path": rel.as_posix(), "bytes": item.stat().st_size, "sha256": digest})
    return {
        "files": entries,
        "count": len(entries),
        "excluded_directories": sorted(EXCLUDED_DIRS),
        "source_modified": False,
        "secrets_copied": False,
    }


def snapshot(source: Path, target: Path) -> dict:
    if target.exists():
        raise FileExistsError(f"Snapshot already exists: {target.name}")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".partial")
    if temporary.exists():
        raise FileExistsError(f"Unfinished snapshot exists: {temporary.name}")
    started = time.monotonic()
    last_report = started

    def progress(status, remaining, total):
        nonlocal last_report
        if time.monotonic() - last_report >= 10:
            print(
                json.dumps({"snapshot": target.name, "pages_remaining": remaining, "pages_total": total}),
                flush=True,
            )
            last_report = time.monotonic()

    with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as src:
        with closing(sqlite3.connect(temporary)) as dst:
            src.backup(dst, pages=4096, progress=progress, sleep=0.05)
            integrity = [row[0] for row in dst.execute("PRAGMA integrity_check")]
            if integrity != ["ok"]:
                raise RuntimeError(f"Snapshot integrity check failed; partial retained: {integrity[:5]}")
            foreign_keys = dst.execute("PRAGMA foreign_key_check").fetchall()
            if foreign_keys:
                raise RuntimeError("Snapshot has foreign-key violations; partial retained")
            tables = [
                row[0]
                for row in dst.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            ]
            counts = {
                name: dst.execute('SELECT COUNT(*) FROM "' + name.replace('"', '""') + '"').fetchone()[0]
                for name in tables
            }
    temporary.replace(target)
    return {
        "file": target.name,
        "bytes": target.stat().st_size,
        "sha256": sha256(target),
        "integrity_check": integrity,
        "foreign_key_violations": len(foreign_keys),
        "table_rows": counts,
        "elapsed_seconds": round(time.monotonic() - started, 2),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--destination", type=Path, default=Path("."))
    parser.add_argument("--database", type=Path, help="Database path relative to the source project")
    args = parser.parse_args()
    source, destination = args.source.resolve(), args.destination.resolve()
    if source == destination or source in destination.parents or destination in source.parents:
        parser.error("Source and destination must be separate directory trees")
    if not source.is_dir():
        parser.error("Source directory does not exist")
    reports = destination / "var" / "migration"
    reports.mkdir(parents=True, exist_ok=True)
    if args.database:
        if args.database.is_absolute() or ".." in args.database.parts:
            parser.error("--database must be a path inside the source project")
        result = snapshot(source / args.database, destination / args.database)
        report = reports / (args.database.name + ".snapshot.json")
    else:
        result = copy_sources(source, destination)
        report = reports / "source-manifest.json"
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps({"report": report.relative_to(destination).as_posix(), "status": "verified"}), flush=True
    )


if __name__ == "__main__":
    main()
