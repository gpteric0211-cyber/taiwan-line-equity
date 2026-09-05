"""Independent account DB, migrated once from the legacy market database."""

from __future__ import annotations
from contextlib import closing
import sqlite3
from pathlib import Path
from auth.models import CREATE_TABLES_SQL
from core.portfolio_storage import configured_path

TABLES = ("users", "email_verifications", "user_watchlist", "login_attempts", "auth_sessions")


def path() -> Path:
    return configured_path("EQUITY_AUTH_DB", "var/private/accounts.sqlite3")


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(path().as_uri() + "?mode=rw", uri=True, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=FULL")
    return conn


def initialize(source: Path | None = None) -> dict[str, int]:
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        with closing(db()) as conn:
            if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='account_migration'"
            ).fetchone():
                return {name: conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0] for name in TABLES}
        raise RuntimeError("Existing account DB has no migration marker; inspect it before initialization")
    candidate = target.with_suffix(".sqlite3.partial")
    if candidate.exists():
        raise RuntimeError("An unfinished account migration exists; inspect it before retrying")
    counts = {}
    with closing(sqlite3.connect(candidate)) as dst:
        dst.execute("PRAGMA foreign_keys=ON")
        dst.executescript(CREATE_TABLES_SQL)
        with dst:
            if source and source.exists():
                with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as src:
                    src.execute("BEGIN")
                    for table in TABLES:
                        if not src.execute(
                            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
                        ).fetchone():
                            continue
                        columns = [row[1] for row in dst.execute(f"PRAGMA table_info({table})")]
                        names = ",".join('"' + name + '"' for name in columns)
                        rows = src.execute(f"SELECT {names} FROM {table}").fetchall()
                        dst.executemany(
                            f"INSERT INTO {table}({names}) VALUES({','.join('?' for _ in columns)})", rows
                        )
                        counts[table] = len(rows)
                        if dst.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] != len(rows):
                            raise RuntimeError("Account migration row count mismatch")
            if dst.execute("PRAGMA foreign_key_check").fetchall():
                raise RuntimeError("Account migration has broken foreign keys")
            dst.execute(
                "CREATE TABLE account_migration(version INTEGER PRIMARY KEY,applied_at TEXT DEFAULT CURRENT_TIMESTAMP)"
            )
            dst.execute("INSERT INTO account_migration(version) VALUES(1)")
        if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("Account database integrity check failed")
    candidate.replace(target)
    return counts
