from __future__ import annotations

from contextlib import closing
from typing import Any

from core.db import db


def set_fetch_status(key: str, status: str, message: str, updated_at: float) -> None:
    """Persist one fetch_status row.

    This repository owns only fetch_status SQL. It must not import app.py,
    FastAPI, core.status, or any analysis modules.
    """
    with closing(db()) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO fetch_status(key,status,message,updated_at) VALUES(?,?,?,?)",
            (key, status, message, updated_at),
        )
        conn.commit()


def get_fetch_status() -> dict[str, Any]:
    """Return all fetch_status rows keyed by status key."""
    out: dict[str, Any] = {}
    with closing(db()) as conn:
        for row in conn.execute("SELECT * FROM fetch_status"):
            out[row["key"]] = {
                "status": row["status"],
                "message": row["message"],
                "updated_at": row["updated_at"],
            }
    return out

