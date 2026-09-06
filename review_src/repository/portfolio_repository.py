"""Encrypted, owner-scoped portfolios and expiring import/link tokens."""

from __future__ import annotations
from contextlib import closing, contextmanager
import hashlib
import hmac
import json
import secrets
import sqlite3
import time
from pathlib import Path
from cryptography.fernet import Fernet
from core.portfolio_storage import database_path, initialize_key, load_key

SCHEMA = """
CREATE TABLE IF NOT EXISTS portfolio_schema(version INTEGER PRIMARY KEY, applied_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS portfolios(owner TEXT PRIMARY KEY, encrypted BLOB NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS portfolio_drafts(id TEXT PRIMARY KEY, owner TEXT NOT NULL, encrypted BLOB NOT NULL,
 expires_at REAL NOT NULL, confirmed_at REAL, origin TEXT);
CREATE INDEX IF NOT EXISTS idx_portfolio_draft_owner ON portfolio_drafts(owner, expires_at);
CREATE TABLE IF NOT EXISTS portfolio_links(token_hash TEXT PRIMARY KEY, owner TEXT NOT NULL, expires_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS portfolio_aliases(alias TEXT PRIMARY KEY, owner TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS portfolio_intents(owner TEXT PRIMARY KEY, expires_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS portfolio_consent(owner TEXT PRIMARY KEY, version TEXT NOT NULL, accepted_at REAL NOT NULL);
"""


def initialize() -> None:
    initialize_key()
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        conn.execute("INSERT OR IGNORE INTO portfolio_schema VALUES(1,?)", (time.time(),))


class PortfolioStore:
    def __init__(self, path: Path | None = None, key: bytes | None = None):
        self.path = path or database_path()
        self.key = key or load_key()
        self.cipher = Fernet(self.key)

    def subject(self, namespace: str, identifier: str) -> str:
        if not identifier:
            raise ValueError("缺少使用者識別")
        return hmac.new(self.key, (namespace + ":" + identifier).encode(), hashlib.sha256).hexdigest()

    @contextmanager
    def connect(self, *, write: bool = False):
        mode = "rw" if write else "ro"
        with closing(
            sqlite3.connect(self.path.resolve().as_uri() + "?mode=" + mode, uri=True, timeout=10)
        ) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=10000")
            if write:
                conn.execute("PRAGMA synchronous=FULL")
                conn.execute("BEGIN IMMEDIATE")
            else:
                conn.execute("PRAGMA query_only=ON")
            try:
                yield conn
                if write:
                    conn.commit()
            except Exception:
                if write:
                    conn.rollback()
                raise

    def _owner(self, conn, subject: str) -> str:
        row = conn.execute("SELECT owner FROM portfolio_aliases WHERE alias=?", (subject,)).fetchone()
        return row["owner"] if row else subject

    def resolved_owner(self, subject: str) -> str:
        with self.connect() as conn:
            return self._owner(conn,subject)

    def _encode(self, value):
        return self.cipher.encrypt(json.dumps(value, ensure_ascii=False, allow_nan=False).encode())

    def _decode(self, value):
        return json.loads(self.cipher.decrypt(value))

    def consent(self, subject: str, version: str) -> None:
        with self.connect(write=True) as conn:
            owner = self._owner(conn, subject)
            conn.execute(
                "INSERT INTO portfolio_consent VALUES(?,?,?) ON CONFLICT(owner) DO UPDATE SET version=excluded.version, accepted_at=excluded.accepted_at",
                (owner, version, time.time()),
            )

    def has_consent(self, subject: str, version: str) -> bool:
        with self.connect() as conn:
            return bool(
                conn.execute(
                    "SELECT 1 FROM portfolio_consent WHERE owner=? AND version=?",
                    (self._owner(conn, subject), version),
                ).fetchone()
            )

    def read(self, subject: str) -> dict:
        with self.connect() as conn:
            owner = self._owner(conn, subject)
            row = conn.execute(
                "SELECT encrypted,updated_at FROM portfolios WHERE owner=?", (owner,)
            ).fetchone()
            return {
                "holdings": self._decode(row["encrypted"]) if row else [],
                "updated_at": row["updated_at"] if row else None,
            }

    def create_draft(
        self, subject: str, holdings: list[dict], *, ttl_seconds: int = 3600, origin: str | None = None
    ) -> str:
        draft_id = secrets.token_urlsafe(18)
        with self.connect(write=True) as conn:
            owner = self._owner(conn, subject)
            conn.execute("DELETE FROM portfolio_drafts WHERE expires_at < ?", (time.time(),))
            conn.execute(
                "INSERT INTO portfolio_drafts VALUES(?,?,?,?,NULL,?)",
                (
                    draft_id,
                    owner,
                    self._encode(holdings),
                    time.time() + ttl_seconds,
                    self.subject("message", origin) if origin else None,
                ),
            )
        return draft_id

    def confirm(self, subject: str, draft_id: str, holdings: list[dict] | None = None) -> list[dict]:
        with self.connect(write=True) as conn:
            owner = self._owner(conn, subject)
            draft = conn.execute(
                "SELECT * FROM portfolio_drafts WHERE id=? AND owner=?", (draft_id, owner)
            ).fetchone()
            if not draft or draft["expires_at"] < time.time():
                raise ValueError("持股草稿不存在或已過期，請重新辨識或輸入")
            row = conn.execute("SELECT encrypted FROM portfolios WHERE owner=?", (owner,)).fetchone()
            current = self._decode(row["encrypted"]) if row else []
            if draft["confirmed_at"] is not None:
                return current
            imported = holdings if holdings is not None else self._decode(draft["encrypted"])
            merged = {item["code"]: item for item in current}
            merged.update({item["code"]: item for item in imported})
            if len(merged) > 100:
                raise ValueError("最多保存 100 檔持股")
            result = sorted(merged.values(), key=lambda item: item["code"])
            conn.execute(
                "INSERT INTO portfolios VALUES(?,?,?) ON CONFLICT(owner) DO UPDATE SET encrypted=excluded.encrypted,updated_at=excluded.updated_at",
                (owner, self._encode(result), time.time()),
            )
            conn.execute("UPDATE portfolio_drafts SET confirmed_at=? WHERE id=?", (time.time(), draft_id))
            return result

    def remove(self, subject: str, code: str) -> None:
        with self.connect(write=True) as conn:
            owner = self._owner(conn, subject)
            row = conn.execute("SELECT encrypted FROM portfolios WHERE owner=?", (owner,)).fetchone()
            if row:
                remaining = [item for item in self._decode(row["encrypted"]) if item["code"] != code]
                conn.execute(
                    "UPDATE portfolios SET encrypted=?,updated_at=? WHERE owner=?",
                    (self._encode(remaining), time.time(), owner),
                )

    def issue_link(self, subject: str) -> str:
        token = secrets.token_urlsafe(18)
        with self.connect(write=True) as conn:
            owner = self._owner(conn, subject)
            conn.execute("DELETE FROM portfolio_links WHERE owner=? OR expires_at<?", (owner, time.time()))
            conn.execute(
                "INSERT INTO portfolio_links VALUES(?,?,?)",
                (hashlib.sha256(token.encode()).hexdigest(), owner, time.time() + 600),
            )
        return token

    def consume_link(self, subject: str, token: str) -> None:
        with self.connect(write=True) as conn:
            row = conn.execute(
                "SELECT * FROM portfolio_links WHERE token_hash=? AND expires_at>=?",
                (hashlib.sha256(token.encode()).hexdigest(), time.time()),
            ).fetchone()
            if not row:
                raise ValueError("綁定碼不存在或已過期")
            old_owner = self._owner(conn, subject)
            if old_owner != subject and old_owner != row["owner"]:
                raise ValueError("此 LINE 帳號已綁定其他帳號，請先解除")
            own = conn.execute("SELECT encrypted FROM portfolios WHERE owner=?", (subject,)).fetchone()
            if own and self._decode(own["encrypted"]) and subject != row["owner"]:
                raise ValueError("LINE 已有持股，請先匯出並清除後再綁定，以免覆蓋紀錄")
            if subject != row["owner"]:
                conn.execute(
                    "INSERT INTO portfolio_aliases VALUES(?,?) ON CONFLICT(alias) DO UPDATE SET owner=excluded.owner",
                    (subject, row["owner"]),
                )
            conn.execute("DELETE FROM portfolio_links WHERE token_hash=?", (row["token_hash"],))

    def forget(self, subject: str, *, unlink_only: bool = False) -> None:
        with self.connect(write=True) as conn:
            owner = self._owner(conn, subject)
            if unlink_only and owner != subject:
                conn.execute("DELETE FROM portfolio_aliases WHERE alias=?", (subject,))
                return
            for table in (
                "portfolios",
                "portfolio_drafts",
                "portfolio_links",
                "portfolio_consent",
                "portfolio_intents",
            ):
                conn.execute(f"DELETE FROM {table} WHERE owner=?", (owner,))
            conn.execute("DELETE FROM portfolio_aliases WHERE alias=? OR owner=?", (subject, owner))

    def arm_upload(self, subject: str) -> None:
        with self.connect(write=True) as conn:
            conn.execute(
                "INSERT INTO portfolio_intents VALUES(?,?) ON CONFLICT(owner) DO UPDATE SET expires_at=excluded.expires_at",
                (self._owner(conn, subject), time.time() + 600),
            )

    def take_upload(self, subject: str) -> bool:
        with self.connect(write=True) as conn:
            owner = self._owner(conn, subject)
            row = conn.execute("SELECT expires_at FROM portfolio_intents WHERE owner=?", (owner,)).fetchone()
            conn.execute("DELETE FROM portfolio_intents WHERE owner=?", (owner,))
            return bool(row and row["expires_at"] >= time.time())

    def suppress_message(self, message_id: str) -> None:
        if not message_id:
            return
        with self.connect(write=True) as conn:
            conn.execute(
                "DELETE FROM portfolio_drafts WHERE origin=?", (self.subject("message", message_id),)
            )

    def cleanup_expired(self) -> int:
        """Remove expired temporary data independently of new user activity."""
        removed = 0
        with self.connect(write=True) as conn:
            for table in ("portfolio_drafts", "portfolio_links", "portfolio_intents"):
                removed += conn.execute(f"DELETE FROM {table} WHERE expires_at < ?", (time.time(),)).rowcount
        return removed
