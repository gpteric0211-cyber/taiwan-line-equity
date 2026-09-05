from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from core.line_memory_config import LineMemorySettings
from core.line_memory_identity import LineConversationIdentity, LineMemoryKeyMaterial
from core.line_memory_schema import LINE_MEMORY_SCHEMA_VERSION, ensure_line_memory_schema


class LineConversationRepositoryError(RuntimeError):
    pass


class LineConversationRepository:
    def __init__(
        self,
        settings: LineMemorySettings,
        key_material: LineMemoryKeyMaterial,
    ) -> None:
        try:
            from cryptography.fernet import Fernet
        except ImportError as exc:
            raise LineConversationRepositoryError(
                "cryptography is required for encrypted LINE memory"
            ) from exc
        self.settings = settings
        self.key_material = key_material
        self._fernet = Fernet(key_material.encryption_key)

    @property
    def path(self) -> Path:
        return self.settings.database_path

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA secure_delete=ON")
        try:
            yield conn
        finally:
            conn.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and self.path.stat().st_size:
            try:
                with self._connection() as conn:
                    messages = [str(row[0]) for row in conn.execute("PRAGMA quick_check")]
                if messages != ["ok"]:
                    raise LineConversationRepositoryError(
                        "LINE memory SQLite integrity check failed"
                    )
            except sqlite3.DatabaseError as exc:
                raise LineConversationRepositoryError(
                    "LINE memory SQLite cannot be opened safely"
                ) from exc
        try:
            with self._connection() as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=FULL")
                ensure_line_memory_schema(conn)
                conn.commit()
        except sqlite3.DatabaseError as exc:
            raise LineConversationRepositoryError(
                "LINE memory schema initialization failed"
            ) from exc

    def health(self) -> dict[str, Any]:
        try:
            with self._connection() as conn:
                row = conn.execute(
                    "SELECT schema_version FROM line_memory_schema_state WHERE singleton_id=1"
                ).fetchone()
                quick = [str(item[0]) for item in conn.execute("PRAGMA quick_check")]
            return {
                "ready": bool(
                    row
                    and str(row[0]) == LINE_MEMORY_SCHEMA_VERSION
                    and quick == ["ok"]
                ),
                "schema_version": str(row[0]) if row else "",
                "integrity": quick[0] if len(quick) == 1 else "failed",
            }
        except sqlite3.DatabaseError:
            return {"ready": False, "schema_version": "", "integrity": "unavailable"}

    def migrate_active_retention_policy(self, *, now: float | None = None) -> dict[str, int]:
        """Apply a newly versioned long-term policy once to still-active subjects.

        Changing the environment alone would otherwise leave sessions created
        under the previous 24-hour policy with their old expiry.  The subject's
        notice version is the idempotency marker, so ordinary restarts do not
        keep extending inactive memory indefinitely.
        """

        notice_version = str(self.settings.privacy_notice_version or "")
        if not self.settings.long_term_approved or not notice_version:
            return {
                "subjects": 0,
                "sessions": 0,
                "summaries": 0,
                "projections": 0,
                "answers": 0,
            }
        current = float(now if now is not None else time.time())
        expires_at = current + self.settings.summary_retention_seconds
        counts = {
            "subjects": 0,
            "sessions": 0,
            "summaries": 0,
            "projections": 0,
            "answers": 0,
        }
        try:
            with self._connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                rows = conn.execute(
                    """
                    SELECT scope_key FROM line_memory_subject
                    WHERE expires_at>? AND COALESCE(privacy_notice_version, '')<>?
                    """,
                    (current, notice_version),
                ).fetchall()
                for row in rows:
                    scope_key = str(row[0])
                    session = conn.execute(
                        """
                        SELECT state_ciphertext FROM line_memory_session
                        WHERE scope_key=? AND expires_at>?
                        """,
                        (scope_key, current),
                    ).fetchone()
                    if session:
                        state = self._decrypt_json(session[0], {})
                        state = dict(state) if isinstance(state, dict) else {}
                        latest = conn.execute(
                            """
                            SELECT assistant_ciphertext FROM line_memory_exchange
                            WHERE scope_key=? AND expires_at>? AND deleted_at IS NULL
                            ORDER BY event_timestamp DESC, exchange_id DESC LIMIT 1
                            """,
                            (scope_key, current),
                        ).fetchone()
                        if latest and not state.get("last_assistant_answer"):
                            assistant = self._decrypt_json(latest[0], {})
                            answer = " ".join(str((assistant or {}).get("text") or "").split())[:1100]
                            if answer:
                                state["last_assistant_answer"] = answer
                                counts["answers"] += 1
                        conn.execute(
                            """
                            UPDATE line_memory_session
                            SET state_ciphertext=?, expires_at=? WHERE scope_key=?
                            """,
                            (self._encrypt_json(state), expires_at, scope_key),
                        )
                        counts["sessions"] += 1
                    summary_count = conn.execute(
                        """
                        UPDATE line_memory_summary SET expires_at=?
                        WHERE scope_key=? AND active=1 AND expires_at>?
                        """,
                        (expires_at, scope_key, current),
                    ).rowcount
                    counts["summaries"] += max(0, int(summary_count or 0))
                    projection_count = conn.execute(
                        """
                        UPDATE line_memory_projection SET expires_at=?
                        WHERE scope_key=? AND active=1 AND expires_at>?
                        """,
                        (expires_at, scope_key, current),
                    ).rowcount
                    counts["projections"] += max(0, int(projection_count or 0))
                    conn.execute(
                        """
                        UPDATE line_memory_subject
                        SET privacy_notice_version=?, updated_at=?, expires_at=?
                        WHERE scope_key=?
                        """,
                        (notice_version, current, expires_at, scope_key),
                    )
                    counts["subjects"] += 1
                conn.commit()
            return counts
        except sqlite3.DatabaseError as exc:
            raise LineConversationRepositoryError(
                "LINE memory retention migration failed"
            ) from exc

    def _encrypt_json(self, value: Any) -> bytes:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return self._fernet.encrypt(payload)

    def _decrypt_json(self, value: bytes | str | None, fallback: Any) -> Any:
        if value is None:
            return fallback
        token = value.encode("ascii") if isinstance(value, str) else bytes(value)
        try:
            decoded = self._fernet.decrypt(token)
            return json.loads(decoded.decode("utf-8"))
        except Exception as exc:
            raise LineConversationRepositoryError(
                "LINE memory ciphertext failed authentication"
            ) from exc

    def _upsert_subject(
        self,
        conn: sqlite3.Connection,
        identity: LineConversationIdentity,
        *,
        now: float,
        expires_at: float,
    ) -> None:
        conn.execute(
            """
            INSERT INTO line_memory_subject(
                scope_key, principal_key, source_type, key_version,
                privacy_notice_version, created_at, updated_at, expires_at
            ) VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(scope_key) DO UPDATE SET
                principal_key=excluded.principal_key,
                source_type=excluded.source_type,
                key_version=excluded.key_version,
                privacy_notice_version=excluded.privacy_notice_version,
                updated_at=excluded.updated_at,
                expires_at=MAX(line_memory_subject.expires_at, excluded.expires_at)
            """,
            (
                identity.scope_key,
                identity.principal_key,
                identity.source_type,
                identity.key_version,
                self.settings.privacy_notice_version,
                now,
                now,
                expires_at,
            ),
        )

    def load_session(self, scope_key: str, *, now: float | None = None) -> dict[str, Any]:
        if not scope_key:
            return {}
        current = float(now if now is not None else time.time())
        try:
            with self._connection() as conn:
                row = conn.execute(
                    """
                    SELECT state_ciphertext
                    FROM line_memory_session
                    WHERE scope_key=? AND expires_at>?
                    """,
                    (scope_key, current),
                ).fetchone()
            state = self._decrypt_json(row[0], {}) if row else {}
            return dict(state) if isinstance(state, dict) else {}
        except sqlite3.DatabaseError as exc:
            raise LineConversationRepositoryError("LINE memory session read failed") from exc

    def save_session(
        self,
        identity: LineConversationIdentity,
        state: dict[str, Any],
        *,
        event_timestamp: int = 0,
        event_key: str = "",
        now: float | None = None,
    ) -> bool:
        if not identity.persistent_allowed or not identity.scope_key or not state:
            return False
        current = float(now if now is not None else time.time())
        expires_at = current + self.settings.summary_retention_seconds
        event_timestamp = int(event_timestamp or current * 1000)
        try:
            with self._connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                self._upsert_subject(conn, identity, now=current, expires_at=expires_at)
                existing = conn.execute(
                    """
                    SELECT revision,last_event_timestamp,last_event_key
                    FROM line_memory_session WHERE scope_key=?
                    """,
                    (identity.scope_key,),
                ).fetchone()
                if existing:
                    existing_order = (int(existing[1] or 0), str(existing[2] or ""))
                    incoming_order = (event_timestamp, event_key)
                    if incoming_order < existing_order:
                        conn.rollback()
                        return False
                    revision = int(existing[0] or 0) + 1
                else:
                    revision = 1
                conn.execute(
                    """
                    INSERT INTO line_memory_session(
                        scope_key,state_ciphertext,revision,last_event_timestamp,
                        last_event_key,updated_at,expires_at
                    ) VALUES(?,?,?,?,?,?,?)
                    ON CONFLICT(scope_key) DO UPDATE SET
                        state_ciphertext=excluded.state_ciphertext,
                        revision=excluded.revision,
                        last_event_timestamp=excluded.last_event_timestamp,
                        last_event_key=excluded.last_event_key,
                        updated_at=excluded.updated_at,
                        expires_at=excluded.expires_at
                    """,
                    (
                        identity.scope_key,
                        self._encrypt_json(state),
                        revision,
                        event_timestamp,
                        event_key,
                        current,
                        expires_at,
                    ),
                )
                conn.commit()
            return True
        except sqlite3.DatabaseError as exc:
            raise LineConversationRepositoryError("LINE memory session write failed") from exc

    def commit_exchange(
        self,
        identity: LineConversationIdentity,
        *,
        event_key: str,
        message_key: str,
        event_timestamp: int,
        message_type: str,
        user_text: str,
        assistant_text: str,
        state: dict[str, Any],
        stock_code: str = "",
        trade_date: str = "",
        facts_fingerprint: str = "",
        now: float | None = None,
    ) -> bool:
        if not identity.persistent_allowed or not event_key:
            return False
        current = float(now if now is not None else time.time())
        raw_expires_at = current + self.settings.raw_retention_seconds
        state_expires_at = current + self.settings.summary_retention_seconds
        event_timestamp = int(event_timestamp or current * 1000)
        try:
            with self._connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                self._upsert_subject(conn, identity, now=current, expires_at=state_expires_at)
                existing_exchange = conn.execute(
                    "SELECT 1 FROM line_memory_exchange WHERE event_key=?",
                    (event_key,),
                ).fetchone()
                if existing_exchange:
                    conn.rollback()
                    return False
                conn.execute(
                    """
                    INSERT INTO line_memory_exchange(
                        scope_key,event_key,message_key,event_timestamp,message_type,
                        stock_code,trade_date,facts_fingerprint,user_ciphertext,
                        assistant_ciphertext,delivered_at,expires_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        identity.scope_key,
                        event_key,
                        message_key or None,
                        event_timestamp,
                        str(message_type or "text"),
                        str(stock_code or "") or None,
                        str(trade_date or "") or None,
                        str(facts_fingerprint or "") or None,
                        self._encrypt_json({"text": str(user_text or "")[:4800]}),
                        self._encrypt_json({"text": str(assistant_text or "")[:6000]}),
                        current,
                        raw_expires_at,
                    ),
                )
                existing = conn.execute(
                    """
                    SELECT revision,last_event_timestamp,last_event_key
                    FROM line_memory_session WHERE scope_key=?
                    """,
                    (identity.scope_key,),
                ).fetchone()
                incoming_order = (event_timestamp, event_key)
                existing_order = (
                    (int(existing[1] or 0), str(existing[2] or ""))
                    if existing
                    else (0, "")
                )
                if state and incoming_order >= existing_order:
                    revision = int(existing[0] or 0) + 1 if existing else 1
                    conn.execute(
                        """
                        INSERT INTO line_memory_session(
                            scope_key,state_ciphertext,revision,last_event_timestamp,
                            last_event_key,updated_at,expires_at
                        ) VALUES(?,?,?,?,?,?,?)
                        ON CONFLICT(scope_key) DO UPDATE SET
                            state_ciphertext=excluded.state_ciphertext,
                            revision=excluded.revision,
                            last_event_timestamp=excluded.last_event_timestamp,
                            last_event_key=excluded.last_event_key,
                            updated_at=excluded.updated_at,
                            expires_at=excluded.expires_at
                        """,
                        (
                            identity.scope_key,
                            self._encrypt_json(state),
                            revision,
                            event_timestamp,
                            event_key,
                            current,
                            state_expires_at,
                        ),
                    )
                conn.commit()
            return True
        except sqlite3.DatabaseError as exc:
            raise LineConversationRepositoryError("LINE memory exchange write failed") from exc

    def recent_exchanges(
        self,
        scope_key: str,
        *,
        limit: int,
        stock_code: str = "",
        unsummarized_only: bool = False,
        now: float | None = None,
    ) -> list[dict[str, Any]]:
        if not scope_key:
            return []
        current = float(now if now is not None else time.time())
        clauses = ["scope_key=?", "expires_at>?", "deleted_at IS NULL"]
        parameters: list[Any] = [scope_key, current]
        if stock_code:
            clauses.append("stock_code=?")
            parameters.append(stock_code)
        if unsummarized_only:
            clauses.append("summarized_at IS NULL")
        parameters.append(max(1, int(limit)))
        query = f"""
            SELECT exchange_id,event_key,event_timestamp,message_type,stock_code,
                   trade_date,facts_fingerprint,user_ciphertext,assistant_ciphertext,
                   delivered_at
            FROM line_memory_exchange
            WHERE {' AND '.join(clauses)}
            ORDER BY event_timestamp DESC, exchange_id DESC
            LIMIT ?
        """
        try:
            with self._connection() as conn:
                rows = conn.execute(query, parameters).fetchall()
            exchanges: list[dict[str, Any]] = []
            for row in reversed(rows):
                user = self._decrypt_json(row[7], {})
                assistant = self._decrypt_json(row[8], {})
                exchanges.append(
                    {
                        "exchange_id": int(row[0]),
                        "event_key": str(row[1]),
                        "event_timestamp": int(row[2]),
                        "message_type": str(row[3]),
                        "stock_code": str(row[4] or ""),
                        "trade_date": str(row[5] or ""),
                        "facts_fingerprint": str(row[6] or ""),
                        "user": str((user or {}).get("text") or ""),
                        "assistant": str((assistant or {}).get("text") or ""),
                        "delivered_at": float(row[9]),
                    }
                )
            return exchanges
        except sqlite3.DatabaseError as exc:
            raise LineConversationRepositoryError("LINE memory exchanges read failed") from exc

    def oldest_unsummarized_exchanges(
        self,
        scope_key: str,
        *,
        limit: int,
        now: float | None = None,
    ) -> list[dict[str, Any]]:
        """Return the oldest raw turns so rolling summaries never skip history."""

        if not scope_key:
            return []
        current = float(now if now is not None else time.time())
        try:
            with self._connection() as conn:
                rows = conn.execute(
                    """
                    SELECT exchange_id,event_key,event_timestamp,message_type,stock_code,
                           trade_date,facts_fingerprint,user_ciphertext,assistant_ciphertext,
                           delivered_at
                    FROM line_memory_exchange
                    WHERE scope_key=? AND expires_at>? AND deleted_at IS NULL
                          AND summarized_at IS NULL
                    ORDER BY event_timestamp ASC, exchange_id ASC
                    LIMIT ?
                    """,
                    (scope_key, current, max(1, int(limit))),
                ).fetchall()
            exchanges: list[dict[str, Any]] = []
            for row in rows:
                user = self._decrypt_json(row[7], {})
                assistant = self._decrypt_json(row[8], {})
                exchanges.append(
                    {
                        "exchange_id": int(row[0]),
                        "event_key": str(row[1]),
                        "event_timestamp": int(row[2]),
                        "message_type": str(row[3]),
                        "stock_code": str(row[4] or ""),
                        "trade_date": str(row[5] or ""),
                        "facts_fingerprint": str(row[6] or ""),
                        "user": str((user or {}).get("text") or ""),
                        "assistant": str((assistant or {}).get("text") or ""),
                        "delivered_at": float(row[9]),
                    }
                )
            return exchanges
        except sqlite3.DatabaseError as exc:
            raise LineConversationRepositoryError(
                "LINE memory unsummarized exchange read failed"
            ) from exc

    def claim_compaction(
        self,
        scope_key: str,
        owner_id: str,
        *,
        lease_seconds: float = 90,
        now: float | None = None,
    ) -> bool:
        """Obtain a cross-process SQLite lease for one scope's compaction."""

        if not scope_key or not owner_id:
            return False
        current = float(now if now is not None else time.time())
        try:
            with self._connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "DELETE FROM line_memory_compaction_lease WHERE leased_until<=?",
                    (current,),
                )
                row = conn.execute(
                    "SELECT owner_id FROM line_memory_compaction_lease WHERE scope_key=?",
                    (scope_key,),
                ).fetchone()
                if row:
                    conn.rollback()
                    return False
                conn.execute(
                    """
                    INSERT INTO line_memory_compaction_lease(scope_key,owner_id,leased_until)
                    VALUES(?,?,?)
                    """,
                    (scope_key, owner_id, current + max(10.0, float(lease_seconds))),
                )
                conn.commit()
            return True
        except sqlite3.DatabaseError as exc:
            raise LineConversationRepositoryError("LINE compaction lease failed") from exc

    def release_compaction(self, scope_key: str, owner_id: str) -> None:
        if not scope_key or not owner_id:
            return
        try:
            with self._connection() as conn:
                conn.execute(
                    "DELETE FROM line_memory_compaction_lease WHERE scope_key=? AND owner_id=?",
                    (scope_key, owner_id),
                )
                conn.commit()
        except sqlite3.DatabaseError as exc:
            raise LineConversationRepositoryError(
                "LINE compaction lease release failed"
            ) from exc

    def store_projection(
        self,
        scope_key: str,
        *,
        projection: dict[str, Any],
        projection_version: str,
        turn_count: int,
        token_count: int,
        last_analysis_id: str = "",
        last_analysis_cutoff: str = "",
        now: float | None = None,
    ) -> int:
        """Persist a bounded encrypted projection without extending configured retention."""

        if not scope_key:
            return 0
        turns = int(turn_count)
        tokens = int(token_count)
        if turns < 0 or turns > 12:
            raise ValueError("conversation projection turn_count must be between 0 and 12")
        if tokens < 0 or tokens > 4000:
            raise ValueError("conversation projection token_count must be between 0 and 4000")
        current = float(now if now is not None else time.time())
        expires_at = current + self.settings.summary_retention_seconds
        encrypted_projection = {
            **projection,
            "last_analysis_id": str(last_analysis_id or ""),
            "last_analysis_cutoff": str(last_analysis_cutoff or ""),
        }
        try:
            with self._connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "UPDATE line_memory_projection SET active=0 WHERE scope_key=? AND active=1",
                    (scope_key,),
                )
                cursor = conn.execute(
                    """
                    INSERT INTO line_memory_projection(
                        scope_key,projection_ciphertext,projection_version,turn_count,
                        token_count,active,created_at,expires_at
                    ) VALUES(?,?,?,?,?,1,?,?)
                    """,
                    (
                        scope_key,
                        self._encrypt_json(encrypted_projection),
                        str(projection_version),
                        turns,
                        tokens,
                        current,
                        expires_at,
                    ),
                )
                conn.commit()
            return int(cursor.lastrowid)
        except sqlite3.DatabaseError as exc:
            raise LineConversationRepositoryError(
                "LINE conversation projection write failed"
            ) from exc

    def active_projection(
        self,
        scope_key: str,
        *,
        now: float | None = None,
    ) -> dict[str, Any]:
        if not scope_key:
            return {}
        current = float(now if now is not None else time.time())
        try:
            with self._connection() as conn:
                row = conn.execute(
                    """
                    SELECT projection_id,projection_ciphertext,projection_version,
                           turn_count,token_count,created_at,expires_at
                    FROM line_memory_projection
                    WHERE scope_key=? AND active=1 AND expires_at>?
                    ORDER BY projection_id DESC LIMIT 1
                    """,
                    (scope_key, current),
                ).fetchone()
            if not row:
                return {}
            value = self._decrypt_json(row[1], {})
            if not isinstance(value, dict):
                return {}
            return {
                **value,
                "projection_id": int(row[0]),
                "projection_version": str(row[2]),
                "turn_count": int(row[3]),
                "token_count": int(row[4]),
                "created_at": float(row[5]),
                "expires_at": float(row[6]),
            }
        except sqlite3.DatabaseError as exc:
            raise LineConversationRepositoryError(
                "LINE conversation projection read failed"
            ) from exc

    def active_summary(
        self,
        scope_key: str,
        *,
        stock_code: str = "",
        now: float | None = None,
    ) -> dict[str, Any]:
        if not scope_key:
            return {}
        current = float(now if now is not None else time.time())
        try:
            with self._connection() as conn:
                row = conn.execute(
                    """
                    SELECT summary_id,summary_ciphertext,source_exchange_max_id,
                           source_exchange_count,model_id,summary_version,created_at
                    FROM line_memory_summary
                    WHERE scope_key=? AND stock_code=? AND active=1 AND expires_at>?
                    ORDER BY summary_id DESC LIMIT 1
                    """,
                    (scope_key, stock_code, current),
                ).fetchone()
            if not row:
                return {}
            value = self._decrypt_json(row[1], {})
            if not isinstance(value, dict):
                return {}
            return {
                **value,
                "summary_id": int(row[0]),
                "source_exchange_max_id": int(row[2]),
                "source_exchange_count": int(row[3]),
                "model_id": str(row[4]),
                "summary_version": str(row[5]),
                "created_at": float(row[6]),
            }
        except sqlite3.DatabaseError as exc:
            raise LineConversationRepositoryError("LINE memory summary read failed") from exc

    def store_summary(
        self,
        scope_key: str,
        *,
        stock_code: str,
        summary: dict[str, Any],
        source_exchange_ids: list[int],
        model_id: str,
        summary_version: str,
        now: float | None = None,
    ) -> int:
        if not scope_key or not source_exchange_ids:
            return 0
        current = float(now if now is not None else time.time())
        expires_at = current + self.settings.summary_retention_seconds
        unique_ids = sorted({int(item) for item in source_exchange_ids if int(item) > 0})
        if not unique_ids:
            return 0
        try:
            with self._connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "UPDATE line_memory_summary SET active=0 WHERE scope_key=? AND stock_code=? AND active=1",
                    (scope_key, stock_code),
                )
                cursor = conn.execute(
                    """
                    INSERT INTO line_memory_summary(
                        scope_key,stock_code,summary_ciphertext,source_exchange_max_id,
                        source_exchange_count,model_id,summary_version,active,created_at,expires_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        scope_key,
                        stock_code,
                        self._encrypt_json(summary),
                        max(unique_ids),
                        len(unique_ids),
                        model_id,
                        summary_version,
                        1,
                        current,
                        expires_at,
                    ),
                )
                summary_id = int(cursor.lastrowid)
                conn.executemany(
                    "INSERT INTO line_memory_summary_source(summary_id,exchange_id) VALUES(?,?)",
                    [(summary_id, item) for item in unique_ids],
                )
                placeholders = ",".join("?" for _ in unique_ids)
                conn.execute(
                    f"UPDATE line_memory_exchange SET summarized_at=? WHERE exchange_id IN ({placeholders})",
                    [current, *unique_ids],
                )
                conn.commit()
            return summary_id
        except sqlite3.DatabaseError as exc:
            raise LineConversationRepositoryError("LINE memory summary write failed") from exc

    def reserve_event(
        self,
        event_key: str,
        scope_key: str,
        event_timestamp: int,
        *,
        now: float | None = None,
    ) -> bool:
        if not event_key:
            return True
        current = float(now if now is not None else time.time())
        expires_at = current + max(self.settings.raw_retention_seconds, 3600)
        try:
            with self._connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("DELETE FROM line_memory_event_receipt WHERE expires_at<=?", (current,))
                row = conn.execute(
                    "SELECT status,started_at,attempts FROM line_memory_event_receipt WHERE event_key=?",
                    (event_key,),
                ).fetchone()
                if row and str(row[0]) == "delivered":
                    conn.rollback()
                    return False
                if row and str(row[0]) == "processing" and current - float(row[1]) < 60:
                    conn.rollback()
                    return False
                if row:
                    conn.execute(
                        """
                        UPDATE line_memory_event_receipt
                        SET scope_key=?,event_timestamp=?,status='processing',attempts=?,
                            started_at=?,completed_at=NULL,expires_at=?
                        WHERE event_key=?
                        """,
                        (
                            scope_key,
                            int(event_timestamp),
                            int(row[2] or 0) + 1,
                            current,
                            expires_at,
                            event_key,
                        ),
                    )
                else:
                    conn.execute(
                        """
                        INSERT INTO line_memory_event_receipt(
                            event_key,scope_key,event_timestamp,status,attempts,
                            started_at,completed_at,expires_at
                        ) VALUES(?,?,?,'processing',1,?,NULL,?)
                        """,
                        (event_key, scope_key, int(event_timestamp), current, expires_at),
                    )
                conn.commit()
            return True
        except sqlite3.DatabaseError as exc:
            raise LineConversationRepositoryError("LINE event reservation failed") from exc

    def finish_event(
        self,
        event_key: str,
        *,
        delivered: bool,
        now: float | None = None,
    ) -> None:
        if not event_key:
            return
        current = float(now if now is not None else time.time())
        try:
            with self._connection() as conn:
                conn.execute(
                    """
                    UPDATE line_memory_event_receipt
                    SET status=?,completed_at=? WHERE event_key=?
                    """,
                    ("delivered" if delivered else "failed", current, event_key),
                )
                conn.commit()
        except sqlite3.DatabaseError as exc:
            raise LineConversationRepositoryError("LINE event completion write failed") from exc

    def suppress_message(self, scope_key: str, message_key: str, *, now: float | None = None) -> int:
        if not scope_key or not message_key:
            return 0
        current = float(now if now is not None else time.time())
        try:
            with self._connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                rows = conn.execute(
                    """
                    SELECT exchange_id,event_timestamp FROM line_memory_exchange
                    WHERE scope_key=? AND message_key=? AND deleted_at IS NULL
                    """,
                    (scope_key, message_key),
                ).fetchall()
                if not rows:
                    conn.rollback()
                    return 0
                exchange_ids = [int(row[0]) for row in rows]
                conn.execute(
                    "UPDATE line_memory_exchange SET deleted_at=? WHERE scope_key=? AND message_key=?",
                    (current, scope_key, message_key),
                )
                placeholders = ",".join("?" for _ in exchange_ids)
                conn.execute(
                    f"""
                    UPDATE line_memory_summary SET active=0
                    WHERE summary_id IN (
                        SELECT summary_id FROM line_memory_summary_source
                        WHERE exchange_id IN ({placeholders})
                    )
                    """,
                    exchange_ids,
                )
                session = conn.execute(
                    "SELECT last_event_timestamp FROM line_memory_session WHERE scope_key=?",
                    (scope_key,),
                ).fetchone()
                if session and max(int(row[1]) for row in rows) >= int(session[0] or 0):
                    conn.execute("DELETE FROM line_memory_session WHERE scope_key=?", (scope_key,))
                # A projection is derived from recent exchanges and summaries.
                # Once a source message is unsent, retaining that derived state
                # would violate the same privacy deletion boundary.
                conn.execute(
                    "DELETE FROM line_memory_projection WHERE scope_key=?",
                    (scope_key,),
                )
                conn.commit()
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            return len(exchange_ids)
        except sqlite3.DatabaseError as exc:
            raise LineConversationRepositoryError("LINE unsend memory suppression failed") from exc

    def clear_scope(self, scope_key: str) -> int:
        if not scope_key:
            return 0
        try:
            with self._connection() as conn:
                conn.execute(
                    "DELETE FROM line_memory_event_receipt WHERE scope_key=?",
                    (scope_key,),
                )
                conn.execute(
                    "DELETE FROM line_memory_compaction_lease WHERE scope_key=?",
                    (scope_key,),
                )
                cursor = conn.execute("DELETE FROM line_memory_subject WHERE scope_key=?", (scope_key,))
                conn.commit()
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            return int(cursor.rowcount or 0)
        except sqlite3.DatabaseError as exc:
            raise LineConversationRepositoryError("LINE scope memory deletion failed") from exc

    def clear_principal(self, principal_key: str) -> int:
        if not principal_key:
            return 0
        try:
            with self._connection() as conn:
                scope_rows = conn.execute(
                    "SELECT scope_key FROM line_memory_subject WHERE principal_key=?",
                    (principal_key,),
                ).fetchall()
                scope_keys = [str(row[0]) for row in scope_rows]
                if scope_keys:
                    placeholders = ",".join("?" for _ in scope_keys)
                    conn.execute(
                        f"DELETE FROM line_memory_event_receipt WHERE scope_key IN ({placeholders})",
                        scope_keys,
                    )
                    conn.execute(
                        f"DELETE FROM line_memory_compaction_lease WHERE scope_key IN ({placeholders})",
                        scope_keys,
                    )
                cursor = conn.execute(
                    "DELETE FROM line_memory_subject WHERE principal_key=?",
                    (principal_key,),
                )
                conn.commit()
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            return int(cursor.rowcount or 0)
        except sqlite3.DatabaseError as exc:
            raise LineConversationRepositoryError("LINE principal memory deletion failed") from exc

    def purge_expired(self, *, now: float | None = None) -> dict[str, int]:
        current = float(now if now is not None else time.time())
        try:
            with self._connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                exchanges = conn.execute(
                    "DELETE FROM line_memory_exchange WHERE expires_at<=?",
                    (current,),
                ).rowcount
                summaries = conn.execute(
                    "DELETE FROM line_memory_summary WHERE expires_at<=?",
                    (current,),
                ).rowcount
                projections = conn.execute(
                    "DELETE FROM line_memory_projection WHERE expires_at<=?",
                    (current,),
                ).rowcount
                subjects = conn.execute(
                    "DELETE FROM line_memory_subject WHERE expires_at<=?",
                    (current,),
                ).rowcount
                events = conn.execute(
                    "DELETE FROM line_memory_event_receipt WHERE expires_at<=?",
                    (current,),
                ).rowcount
                conn.commit()
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            return {
                "subjects": max(0, int(subjects or 0)),
                "exchanges": max(0, int(exchanges or 0)),
                "summaries": max(0, int(summaries or 0)),
                "projections": max(0, int(projections or 0)),
                "events": max(0, int(events or 0)),
            }
        except sqlite3.DatabaseError as exc:
            raise LineConversationRepositoryError("LINE memory expiry purge failed") from exc

    def counts(self) -> dict[str, int]:
        try:
            with self._connection() as conn:
                return {
                    "subjects": int(conn.execute("SELECT COUNT(*) FROM line_memory_subject").fetchone()[0]),
                    "sessions": int(conn.execute("SELECT COUNT(*) FROM line_memory_session").fetchone()[0]),
                    "exchanges": int(conn.execute("SELECT COUNT(*) FROM line_memory_exchange WHERE deleted_at IS NULL").fetchone()[0]),
                    "summaries": int(conn.execute("SELECT COUNT(*) FROM line_memory_summary WHERE active=1").fetchone()[0]),
                    "projections": int(conn.execute("SELECT COUNT(*) FROM line_memory_projection WHERE active=1").fetchone()[0]),
                    "event_receipts": int(conn.execute("SELECT COUNT(*) FROM line_memory_event_receipt").fetchone()[0]),
                }
        except sqlite3.DatabaseError as exc:
            raise LineConversationRepositoryError("LINE memory diagnostics failed") from exc
