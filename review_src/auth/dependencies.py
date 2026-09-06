from __future__ import annotations

from contextlib import closing
from typing import Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from core.accounts_database import db

from .security import AUTH_COOKIE_NAME, decode_jwt

bearer_scheme = HTTPBearer(auto_error=False)


def get_client_ip(request: Request) -> str:
    cf_ip = request.headers.get("CF-Connecting-IP")
    if cf_ip:
        return cf_ip.strip()
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _token_from_request(request: Request, credentials: HTTPAuthorizationCredentials | None) -> str | None:
    if credentials and credentials.credentials:
        return credentials.credentials
    cookie_token = request.cookies.get(AUTH_COOKIE_NAME)
    return cookie_token or None


def _load_user(user_id: int) -> dict[str, Any] | None:
    with closing(db()) as conn:
        row = conn.execute(
            """SELECT u.id,u.email,u.is_verified,u.is_active,u.created_at,u.last_login_at,
            COALESCE(s.credential_version,0) credential_version,
            COALESCE(s.phone_required,0) phone_required,
            EXISTS(SELECT 1 FROM account_phone p WHERE p.user_id=u.id) phone_verified FROM users u
            LEFT JOIN account_security s ON s.user_id=u.id WHERE u.id=?""",
            (user_id,),
        ).fetchone()
        return dict(row) if row else None


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> dict[str, Any]:
    token = _token_from_request(request, credentials)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="請先登入",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = decode_jwt(token)
    if not payload or payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登入已過期，請重新登入")
    try:
        user_id = int(payload.get("sub"))
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token 無效")
    user = _load_user(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="帳號不存在")
    if payload.get("cv", 0) != user.get("credential_version", 0):
        raise HTTPException(status_code=401, detail="密碼已更新，請重新登入")
    if not user.get("is_active"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="帳號已停用")
    if not user.get("is_verified"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="請先完成 Email 驗證")
    allowed = request.url.path.startswith("/api/auth/phone") or request.url.path in {
        "/api/auth/me", "/api/auth/change-password", "/api/auth/change-password/code", "/api/auth/logout"}
    if user.get("phone_required") and not user.get("phone_verified") and not allowed:
        raise HTTPException(403,"請先完成手機驗證")
    return user


def get_optional_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> dict[str, Any] | None:
    token = _token_from_request(request, credentials)
    if not token:
        return None
    payload = decode_jwt(token)
    if not payload or payload.get("type") != "access":
        return None
    try:
        user_id = int(payload.get("sub"))
    except Exception:
        return None
    user = _load_user(user_id)
    if not user or not user.get("is_active") or not user.get("is_verified"):
        return None
    if payload.get("cv", 0) != user.get("credential_version", 0):
        return None
    if user.get("phone_required") and not user.get("phone_verified"):
        return None
    return user
