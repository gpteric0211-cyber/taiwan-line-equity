from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any

from core.line_memory_config import LineMemorySettings, line_memory_settings
from core.line_memory_identity import (
    LineConversationIdentity,
    LineMemoryKeyMaterial,
    event_receipt_key,
    identity_for_event,
    load_or_create_key_material,
    message_receipt_key,
)
from repository.line_conversation_repository import (
    LineConversationRepository,
    LineConversationRepositoryError,
)
from services.conversation_compaction_service import SUMMARY_VERSION, compact_conversation
from services.conversation_context_builder import build_conversation_context
from services.conversation_projection_v1 import (
    CONVERSATION_PROJECTION_VERSION,
    build_conversation_projection_v1,
)


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class EventMemoryContext:
    conversation_key: str
    event_key: str
    message_key: str
    event_timestamp: int
    identity: LineConversationIdentity | None
    persistent_allowed: bool


def _legacy_conversation_key(event: dict[str, Any], channel_namespace: str) -> str:
    source = event.get("source") if isinstance(event.get("source"), dict) else {}
    source_type = str(source.get("type") or "unknown")
    user_id = str(source.get("userId") or "")
    chat_id = str(source.get("groupId") or source.get("roomId") or user_id)
    if not chat_id:
        return ""
    raw = f"{channel_namespace}|{source_type}|{chat_id}|{user_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _event_timestamp(event: dict[str, Any]) -> int:
    try:
        value = int(event.get("timestamp") or 0)
    except (TypeError, ValueError):
        value = 0
    return value if value > 0 else int(time.time() * 1000)


class ConversationMemoryService:
    """Coordinates encrypted LINE history without becoming a financial data source."""

    def __init__(self, settings: LineMemorySettings) -> None:
        self.settings = settings
        self._lock = threading.RLock()
        self._repository: LineConversationRepository | None = None
        self._key_material: LineMemoryKeyMaterial | None = None
        self._initialization_error = ""
        self._volatile_sessions: dict[str, dict[str, Any]] = {}
        self._volatile_exchanges: dict[str, list[dict[str, Any]]] = {}
        self._volatile_events: dict[str, str] = {}
        self._owner_id = f"{os.getpid()}-{uuid.uuid4().hex}"
        self.initialize()

    @property
    def repository(self) -> LineConversationRepository | None:
        return self._repository

    def initialize(self) -> None:
        if not self.settings.persistent_enabled or self._repository is not None:
            return
        try:
            key_material = load_or_create_key_material(self.settings.key_file)
            repository = LineConversationRepository(self.settings, key_material)
            repository.initialize()
            migrated = repository.migrate_active_retention_policy()
            repository.purge_expired()
            self._key_material = key_material
            self._repository = repository
            self._initialization_error = ""
            if any(migrated.values()):
                LOGGER.info(
                    "LINE memory retention policy migrated subjects=%d sessions=%d summaries=%d answers=%d",
                    migrated["subjects"],
                    migrated["sessions"],
                    migrated["summaries"],
                    migrated["answers"],
                )
        except Exception as exc:
            self._repository = None
            self._key_material = None
            self._initialization_error = type(exc).__name__
            LOGGER.error("Persistent LINE memory unavailable error_class=%s", type(exc).__name__)

    def event_context(self, event: dict[str, Any]) -> EventMemoryContext:
        conversation_key = _legacy_conversation_key(event, self.settings.channel_namespace)
        event_id = str(event.get("webhookEventId") or "")
        message = event.get("message") if isinstance(event.get("message"), dict) else {}
        message_id = str(message.get("id") or "")
        timestamp = _event_timestamp(event)
        identity: LineConversationIdentity | None = None
        event_key = ""
        message_key = ""
        if self._key_material is not None:
            identity = identity_for_event(
                event,
                channel_namespace=self.settings.channel_namespace,
                key_material=self._key_material,
            )
            stable_event_id = event_id or f"{conversation_key}|{timestamp}|{message_id}"
            event_key = event_receipt_key(
                stable_event_id,
                channel_namespace=self.settings.channel_namespace,
                key_material=self._key_material,
            )
            message_key = message_receipt_key(
                message_id,
                channel_namespace=self.settings.channel_namespace,
                key_material=self._key_material,
            )
        else:
            stable_event_id = event_id or f"{conversation_key}|{timestamp}|{message_id}"
            event_key = hashlib.sha256(stable_event_id.encode("utf-8")).hexdigest()
            message_key = (
                hashlib.sha256(message_id.encode("utf-8")).hexdigest() if message_id else ""
            )
        return EventMemoryContext(
            conversation_key=conversation_key,
            event_key=event_key,
            message_key=message_key,
            event_timestamp=timestamp,
            identity=identity,
            persistent_allowed=bool(
                identity and identity.persistent_allowed and self._repository is not None
            ),
        )

    def context_for_event(self, event: dict[str, Any]) -> dict[str, Any]:
        memory = self.event_context(event)
        if (
            self.settings.persistent_enabled
            and memory.identity is not None
            and not memory.identity.persistent_allowed
        ):
            return {}
        if memory.persistent_allowed and memory.identity and self._repository:
            try:
                state = self._repository.load_session(memory.identity.scope_key)
                recent = self._repository.recent_exchanges(
                    memory.identity.scope_key,
                    limit=self.settings.recent_exchange_limit,
                )
                global_summary = self._repository.active_summary(memory.identity.scope_key)
                code = str(state.get("code") or "")
                stock_summary = (
                    self._repository.active_summary(memory.identity.scope_key, stock_code=code)
                    if code
                    else {}
                )
                context = build_conversation_context(
                    state,
                    recent_exchanges=recent,
                    rolling_summary=global_summary,
                    stock_summary=stock_summary,
                    character_budget=self.settings.prompt_character_budget,
                )
                projection = self._repository.active_projection(memory.identity.scope_key)
                if projection:
                    context["conversation_projection"] = projection
                return context
            except LineConversationRepositoryError as exc:
                LOGGER.error("LINE memory read failed error_class=%s", type(exc).__name__)
        with self._lock:
            state = dict(self._volatile_sessions.get(memory.conversation_key) or {})
            recent = list(self._volatile_exchanges.get(memory.conversation_key) or [])[
                -self.settings.recent_exchange_limit :
            ]
        context = build_conversation_context(
            state,
            recent_exchanges=recent,
            character_budget=self.settings.prompt_character_budget,
        )
        if state or recent:
            context["conversation_projection"] = build_conversation_projection_v1(
                state,
                recent_exchanges=recent,
            )
        return context

    def begin_event(self, event: dict[str, Any]) -> bool:
        memory = self.event_context(event)
        if memory.persistent_allowed and memory.identity and self._repository:
            try:
                return self._repository.reserve_event(
                    memory.event_key,
                    memory.identity.scope_key,
                    memory.event_timestamp,
                )
            except LineConversationRepositoryError as exc:
                LOGGER.error("LINE event dedupe degraded error_class=%s", type(exc).__name__)
        with self._lock:
            if self._volatile_events.get(memory.event_key) in {"processing", "delivered"}:
                return False
            self._volatile_events[memory.event_key] = "processing"
        return True

    def finish_event(self, event: dict[str, Any], *, delivered: bool) -> None:
        memory = self.event_context(event)
        if memory.persistent_allowed and self._repository:
            try:
                self._repository.finish_event(memory.event_key, delivered=delivered)
            except LineConversationRepositoryError as exc:
                LOGGER.error("LINE event completion degraded error_class=%s", type(exc).__name__)
        with self._lock:
            if delivered:
                self._volatile_events[memory.event_key] = "delivered"
            else:
                self._volatile_events.pop(memory.event_key, None)

    def commit_after_delivery(
        self,
        event: dict[str, Any],
        *,
        user_text: str,
        assistant_text: str,
        state: dict[str, Any] | None,
        message_type: str,
    ) -> bool:
        memory = self.event_context(event)
        clean_state = dict(state or {})
        # Raw exchanges intentionally expire sooner than the compacted memory.
        # Keep only the latest delivered answer in the encrypted session so a
        # low-frequency user can still continue the last topic after raw text
        # has expired, without retaining an unbounded transcript.
        clean_state["last_assistant_answer"] = " ".join(
            str(assistant_text or "").split()
        )[:1100]
        code = str(clean_state.get("code") or "")
        trade_date = str(clean_state.get("trade_date") or "")
        fingerprint = str(clean_state.pop("_facts_fingerprint", "") or "")
        if not fingerprint and (code or trade_date):
            fingerprint = hashlib.sha256(
                json.dumps(
                    {
                        "code": code,
                        "trade_date": trade_date,
                        "last_focus": str(clean_state.get("last_focus") or ""),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
        committed = False
        if memory.persistent_allowed and memory.identity and self._repository:
            try:
                committed = self._repository.commit_exchange(
                    memory.identity,
                    event_key=memory.event_key,
                    message_key=memory.message_key,
                    event_timestamp=memory.event_timestamp,
                    message_type=message_type,
                    user_text=user_text,
                    assistant_text=assistant_text,
                    state=clean_state,
                    stock_code=code,
                    trade_date=trade_date,
                    facts_fingerprint=fingerprint,
                )
                if committed:
                    self._compact_if_needed(memory.identity.scope_key)
                    self._store_projection(memory.identity.scope_key, clean_state)
            except LineConversationRepositoryError as exc:
                LOGGER.error("Persistent LINE memory commit failed error_class=%s", type(exc).__name__)
        allow_volatile_history = not (
            self.settings.persistent_enabled
            and memory.identity is not None
            and not memory.identity.persistent_allowed
        )
        if not allow_volatile_history:
            return committed
        with self._lock:
            current = self._volatile_sessions.get(memory.conversation_key) or {}
            if clean_state:
                old_order = (
                    int(current.get("_event_timestamp") or 0),
                    str(current.get("_event_key") or ""),
                )
                new_order = (memory.event_timestamp, memory.event_key)
                if new_order >= old_order:
                    self._volatile_sessions[memory.conversation_key] = {
                        **clean_state,
                        "_event_timestamp": memory.event_timestamp,
                        "_event_key": memory.event_key,
                    }
            exchanges = self._volatile_exchanges.setdefault(memory.conversation_key, [])
            if not any(row.get("event_key") == memory.event_key for row in exchanges):
                exchanges.append(
                    {
                        "event_key": memory.event_key,
                        "event_timestamp": memory.event_timestamp,
                        "message_type": message_type,
                        "stock_code": code,
                        "trade_date": trade_date,
                        "user": str(user_text or "")[:4800],
                        "assistant": str(assistant_text or "")[:6000],
                    }
                )
                del exchanges[: -max(self.settings.recent_exchange_limit, 2)]
        return committed or bool(memory.conversation_key)

    def _store_projection(self, scope_key: str, state: dict[str, Any]) -> None:
        repository = self._repository
        if repository is None:
            return
        recent = repository.recent_exchanges(
            scope_key,
            limit=min(self.settings.recent_exchange_limit, 6),
        )
        summary = repository.active_summary(scope_key)
        projection = build_conversation_projection_v1(
            state,
            recent_exchanges=recent,
            rolling_summary=summary,
            last_analysis_id=str(state.get("last_analysis_id") or "") or None,
            last_analysis_cutoff=str(state.get("last_analysis_cutoff") or "") or None,
        )
        repository.store_projection(
            scope_key,
            projection=projection,
            projection_version=CONVERSATION_PROJECTION_VERSION,
            turn_count=int(projection["turn_count"]),
            token_count=int(projection["token_count_upper_bound"]),
            last_analysis_id=str(projection.get("last_analysis_id") or ""),
            last_analysis_cutoff=str(projection.get("last_analysis_cutoff") or ""),
        )

    def _compact_if_needed(self, scope_key: str) -> None:
        repository = self._repository
        if repository is None:
            return
        compaction_limit = max(
            self.settings.compaction_trigger,
            self.settings.compaction_batch_size,
        )
        rows = repository.oldest_unsummarized_exchanges(
            scope_key,
            limit=compaction_limit,
        )
        if len(rows) < self.settings.compaction_trigger:
            return
        if not repository.claim_compaction(scope_key, self._owner_id):
            return
        try:
            rows = repository.oldest_unsummarized_exchanges(
                scope_key,
                limit=compaction_limit,
            )
            if len(rows) < self.settings.compaction_trigger:
                return
            existing = repository.active_summary(scope_key)
            result = compact_conversation(
                existing_summary=existing,
                exchanges=rows,
                timeout_seconds=self.settings.compaction_timeout_seconds,
            )
            if not result.ok:
                LOGGER.warning("LINE memory compaction skipped reason=%s", result.reason)
                return
            source_ids = [int(row["exchange_id"]) for row in rows]
            repository.store_summary(
                scope_key,
                stock_code="",
                summary=result.summary,
                source_exchange_ids=source_ids,
                model_id=result.model_id,
                summary_version=SUMMARY_VERSION,
            )
            for code, summary in result.stock_summaries.items():
                code_ids = [
                    int(row["exchange_id"])
                    for row in rows
                    if str(row.get("stock_code") or "") == code
                ]
                if code_ids:
                    repository.store_summary(
                        scope_key,
                        stock_code=code,
                        summary=summary,
                        source_exchange_ids=code_ids,
                        model_id=result.model_id,
                        summary_version=SUMMARY_VERSION,
                    )
        finally:
            repository.release_compaction(scope_key, self._owner_id)

    def clear_scope(self, event: dict[str, Any]) -> int:
        memory = self.event_context(event)
        deleted = 0
        if memory.persistent_allowed and memory.identity and self._repository:
            deleted = self._repository.clear_scope(memory.identity.scope_key)
        with self._lock:
            self._volatile_sessions.pop(memory.conversation_key, None)
            self._volatile_exchanges.pop(memory.conversation_key, None)
        return deleted

    def clear_principal(self, event: dict[str, Any]) -> int:
        memory = self.event_context(event)
        deleted = 0
        if memory.persistent_allowed and memory.identity and self._repository:
            deleted = self._repository.clear_principal(memory.identity.principal_key)
        self.clear_scope(event)
        return deleted

    def suppress_unsent(self, event: dict[str, Any]) -> int:
        memory = self.event_context(event)
        unsend = event.get("unsend") if isinstance(event.get("unsend"), dict) else {}
        raw_message_id = str(unsend.get("messageId") or "")
        if not raw_message_id or not memory.persistent_allowed or not self._repository:
            return 0
        key = message_receipt_key(
            raw_message_id,
            channel_namespace=self.settings.channel_namespace,
            key_material=self._key_material,  # type: ignore[arg-type]
        )
        return self._repository.suppress_message(memory.identity.scope_key, key)  # type: ignore[union-attr]

    def readiness(self) -> dict[str, Any]:
        if not self.settings.persistent_enabled:
            return {
                "ready": True,
                "storage": "memory",
                "restart_persistence": False,
                "privacy_retention_seconds": self.settings.summary_retention_seconds,
            }
        health = self._repository.health() if self._repository else {"ready": False}
        return {
            **health,
            "ready": bool(health.get("ready")),
            "storage": "encrypted_sqlite",
            "restart_persistence": bool(health.get("ready")),
            "privacy_retention_seconds": self.settings.summary_retention_seconds,
            "long_term_retention_approved": self.settings.long_term_approved,
            "initialization_error": self._initialization_error,
        }


_SERVICE_LOCK = threading.Lock()
_SERVICE: ConversationMemoryService | None = None
_SERVICE_SIGNATURE: tuple[object, ...] | None = None


def conversation_memory_service() -> ConversationMemoryService:
    global _SERVICE, _SERVICE_SIGNATURE
    settings = line_memory_settings()
    with _SERVICE_LOCK:
        if _SERVICE is None or _SERVICE_SIGNATURE != settings.signature:
            _SERVICE = ConversationMemoryService(settings)
            _SERVICE_SIGNATURE = settings.signature
        return _SERVICE


def reset_conversation_memory_service_for_tests() -> None:
    global _SERVICE, _SERVICE_SIGNATURE
    with _SERVICE_LOCK:
        _SERVICE = None
        _SERVICE_SIGNATURE = None
