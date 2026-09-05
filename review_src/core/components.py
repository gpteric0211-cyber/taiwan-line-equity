from __future__ import annotations

import csv
import logging
from contextlib import closing

from core.config import COMPONENTS_FILE, safe_error
from core.db import db


def read_components() -> list[dict[str, str]]:
    with COMPONENTS_FILE.open("r", encoding="utf-8-sig", newline="") as f:
        return [{"code": str(r["code"]).zfill(4), "name": r.get("name", "")} for r in csv.DictReader(f)]


def resolve_stock(query: str) -> dict[str, str] | None:
    q = (query or "").strip().replace("，", " ").replace(",", " ")
    if not q:
        return None
    parts = q.split()
    first = parts[0]
    comps = read_components()
    if first.isdigit() and 1 <= len(first) <= 4:
        code = first.zfill(4)
        name = " ".join(parts[1:]).strip()
        if not name:
            for r in comps:
                if r["code"] == code:
                    name = r["name"]
                    break
        if not name:
            try:
                with closing(db()) as conn:
                    row = conn.execute(
                        """
                        SELECT name
                        FROM eod_price
                        WHERE code=? AND name IS NOT NULL AND TRIM(name)!=''
                        ORDER BY date DESC, updated_at DESC
                        LIMIT 1
                        """,
                        (code,),
                    ).fetchone()
                if row:
                    name = str(row["name"] or "").strip()
            except Exception:
                name = ""
        return {"code": code, "name": name}
    for r in comps:
        if q == r["name"] or q in r["name"]:
            return r
    try:
        with closing(db()) as conn:
            row = conn.execute(
                """
                SELECT code,name
                FROM eod_price
                WHERE name=? OR name LIKE ?
                GROUP BY code
                ORDER BY CASE WHEN name=? THEN 0 ELSE 1 END, MAX(date) DESC, code
                LIMIT 1
                """,
                (q, f"%{q}%", q),
            ).fetchone()
        if row:
            return {"code": str(row["code"]).zfill(4), "name": str(row["name"] or "").strip()}
    except Exception as exc:
        logging.warning("stock component lookup failed for %s: %s", q, safe_error(exc))
    return None
