from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


KEY_FILE_VERSION = 1


class LineMemoryKeyError(RuntimeError):
    pass


@dataclass(frozen=True)
class LineMemoryKeyMaterial:
    version: int
    hmac_secret: bytes
    encryption_key: bytes


@dataclass(frozen=True)
class LineConversationIdentity:
    principal_key: str
    scope_key: str
    source_type: str
    persistent_allowed: bool
    key_version: int


def _new_key_document() -> dict[str, Any]:
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:
        raise LineMemoryKeyError(
            "cryptography is required before persistent LINE memory can be enabled"
        ) from exc
    return {
        "version": KEY_FILE_VERSION,
        "hmac_secret": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii"),
        "encryption_key": Fernet.generate_key().decode("ascii"),
    }


def _atomic_create_key_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


def load_or_create_key_material(path: Path) -> LineMemoryKeyMaterial:
    last_error: Exception | None = None
    for attempt in range(20):
        if not path.exists():
            _atomic_create_key_file(path, _new_key_document())
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            version = int(payload.get("version"))
            hmac_secret = base64.urlsafe_b64decode(str(payload.get("hmac_secret") or ""))
            encryption_key = str(payload.get("encryption_key") or "").encode("ascii")
            break
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt == 19:
                raise LineMemoryKeyError("LINE memory key file is invalid") from exc
            time.sleep(0.05)
    else:  # pragma: no cover - defensive; loop either breaks or raises
        raise LineMemoryKeyError("LINE memory key file is invalid") from last_error
    if version != KEY_FILE_VERSION or len(hmac_secret) < 32 or not encryption_key:
        raise LineMemoryKeyError("LINE memory key material is incomplete")
    try:
        from cryptography.fernet import Fernet

        Fernet(encryption_key)
    except (ImportError, ValueError) as exc:
        raise LineMemoryKeyError("LINE memory encryption key is invalid") from exc
    return LineMemoryKeyMaterial(version, hmac_secret, encryption_key)


def pseudonymous_key(secret: bytes, *parts: str) -> str:
    normalized = "|".join(str(part or "") for part in parts)
    return hmac.new(secret, normalized.encode("utf-8"), hashlib.sha256).hexdigest()


def identity_for_event(
    event: dict[str, Any],
    *,
    channel_namespace: str,
    key_material: LineMemoryKeyMaterial,
) -> LineConversationIdentity:
    source = event.get("source") if isinstance(event.get("source"), dict) else {}
    source_type = str(source.get("type") or "unknown").lower()
    user_id = str(source.get("userId") or "")
    group_id = str(source.get("groupId") or "")
    room_id = str(source.get("roomId") or "")
    if source_type == "user":
        chat_id = user_id
    elif source_type == "group":
        chat_id = group_id
    elif source_type == "room":
        chat_id = room_id
    else:
        chat_id = group_id or room_id or user_id
    persistent_allowed = bool(user_id and chat_id and source_type in {"user", "group", "room"})
    if not persistent_allowed:
        return LineConversationIdentity("", "", source_type, False, key_material.version)
    principal_key = pseudonymous_key(
        key_material.hmac_secret,
        "principal",
        channel_namespace,
        user_id,
    )
    scope_key = pseudonymous_key(
        key_material.hmac_secret,
        "scope",
        channel_namespace,
        source_type,
        chat_id,
        user_id,
    )
    return LineConversationIdentity(
        principal_key=principal_key,
        scope_key=scope_key,
        source_type=source_type,
        persistent_allowed=True,
        key_version=key_material.version,
    )


def event_receipt_key(
    event_id: str,
    *,
    channel_namespace: str,
    key_material: LineMemoryKeyMaterial,
) -> str:
    if not event_id:
        return ""
    return pseudonymous_key(
        key_material.hmac_secret,
        "event",
        channel_namespace,
        event_id,
    )


def message_receipt_key(
    message_id: str,
    *,
    channel_namespace: str,
    key_material: LineMemoryKeyMaterial,
) -> str:
    if not message_id:
        return ""
    return pseudonymous_key(
        key_material.hmac_secret,
        "message",
        channel_namespace,
        message_id,
    )
