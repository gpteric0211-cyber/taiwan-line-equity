from __future__ import annotations

import os
import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

bot_bearer_scheme = HTTPBearer(auto_error=False)


def _clean_token(value: str) -> str:
    token = str(value or "").strip().strip('"').strip("'")
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    return token


def _configured_token() -> str:
    token = _clean_token(os.getenv("BOT_MARKET_DATA_TOKEN", ""))
    placeholders = {"change-me", "changeme", "your-token", "token"}
    if len(token) < 32 or token.lower() in placeholders:
        return ""
    return token


def require_bot_market_data_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(bot_bearer_scheme),
) -> bool:
    """Authenticate machine-to-machine market-data reads with a rotatable token."""

    expected = _configured_token()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Bot market-data API is not configured",
        )
    supplied = _clean_token(credentials.credentials if credentials else "")
    if not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bot token is invalid",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return True
