from __future__ import annotations

import threading
from contextlib import closing
from typing import Any

from core.db import db


_watchlist_lock = threading.RLock()


def list_watchlist_items() -> list[dict[str, Any]]:
    """Return global watchlist rows preserving existing sort order."""
    with closing(db()) as conn:
        rows = conn.execute(
            "SELECT code,name,sort_order FROM watchlist ORDER BY sort_order, code"
        ).fetchall()
        return [dict(row) for row in rows]


def list_watchlist_code_name_items() -> list[dict[str, Any]]:
    """Return global watchlist code/name rows preserving existing sort order."""
    with closing(db()) as conn:
        rows = conn.execute(
            "SELECT code,name FROM watchlist ORDER BY sort_order, code"
        ).fetchall()
        return [dict(row) for row in rows]


def get_watchlist_codes() -> list[str]:
    """Return global watchlist codes preserving existing sort order."""
    with closing(db()) as conn:
        rows = conn.execute("SELECT code FROM watchlist ORDER BY sort_order, code").fetchall()
        return [str(row["code"]) for row in rows]


def get_watchlist_codes_unordered() -> list[str]:
    """Return global watchlist codes without imposing a display order."""
    with closing(db()) as conn:
        rows = conn.execute("SELECT code FROM watchlist").fetchall()
        return [str(row["code"]) for row in rows]


def get_watchlist_item(code: str) -> dict[str, Any] | None:
    """Return one global watchlist item by code."""
    clean_code = str(code or "").strip().zfill(4)[:4]
    with closing(db()) as conn:
        row = conn.execute("SELECT code,name FROM watchlist WHERE code=?", (clean_code,)).fetchone()
        return dict(row) if row else None


def watchlist_contains(code: str) -> bool:
    """Return whether the global watchlist contains the code."""
    clean_code = str(code or "").strip().zfill(4)[:4]
    with closing(db()) as conn:
        return conn.execute("SELECT 1 FROM watchlist WHERE code=?", (clean_code,)).fetchone() is not None


def insert_watchlist_if_absent(code: str, name: str, sort_order: int, updated_at: float) -> None:
    """Insert a legacy global watchlist item only when absent."""
    clean_code = str(code or "").strip().zfill(4)[:4]
    with _watchlist_lock, closing(db()) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO watchlist VALUES(?,?,?,?)",
            (clean_code, name or "", int(sort_order), updated_at),
        )
        conn.commit()


def upsert_watchlist_item_with_limit(
    code: str,
    name: str,
    limit: int,
    updated_at: float,
) -> tuple[bool, str | None, dict[str, Any] | None]:
    """Insert or update a global watchlist item while preserving limit behavior."""
    clean_code = str(code or "").strip().zfill(4)[:4]
    with _watchlist_lock, closing(db()) as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM watchlist").fetchone()["c"]
        exists = conn.execute("SELECT 1 FROM watchlist WHERE code=?", (clean_code,)).fetchone()
        if not exists and count >= limit:
            return False, "自選股最多 5 檔，請先移除一檔。", None
        if exists:
            row = conn.execute("SELECT sort_order FROM watchlist WHERE code=?", (clean_code,)).fetchone()
            sort_order = row["sort_order"] if row else count
        else:
            sort_order = count
        conn.execute(
            "INSERT OR REPLACE INTO watchlist VALUES(?,?,?,?)",
            (clean_code, name or "", sort_order, updated_at),
        )
        conn.commit()
    return True, None, {"code": clean_code, "name": name or ""}


def delete_watchlist_item(code: str) -> None:
    """Delete a global watchlist item by code."""
    clean_code = str(code or "").strip().zfill(4)[:4]
    with _watchlist_lock, closing(db()) as conn:
        conn.execute("DELETE FROM watchlist WHERE code=?", (clean_code,))
        conn.commit()
