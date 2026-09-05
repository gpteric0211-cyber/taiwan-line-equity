"""Stable local signing secret; no shared development signing key."""

from __future__ import annotations
import os
from pathlib import Path
import secrets


def jwt_secret() -> str:
    configured = os.getenv("JWT_SECRET_KEY", "").strip()
    if configured:
        if len(configured) < 32 or configured == "dev-change-this-jwt-secret-before-production":
            raise ValueError("JWT_SECRET_KEY must be an unpredictable secret of at least 32 characters")
        return configured
    root = Path(__file__).resolve().parents[2]
    path = Path(os.getenv("EQUITY_JWT_KEY_FILE", "var/private/jwt.key")).expanduser()
    path = path if path.is_absolute() else root / path
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="ascii") as stream:
            stream.write(secrets.token_urlsafe(48))
        if os.name != "nt":
            path.chmod(0o600)
    except FileExistsError:
        pass
    value = path.read_text(encoding="ascii").strip()
    if len(value) < 32:
        raise ValueError("The local JWT signing key is incomplete; restore it from the private backup")
    return value
