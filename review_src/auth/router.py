from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response, status

from .dependencies import get_client_ip, get_current_user
from .email_sender import email_security_warnings
from .schemas import (
    LoginRequest,
    PasswordResetConfirmRequest,
    PasswordResetRequest,
    PasswordChangeRequest,
    RegisterRequest,
    ResendVerificationRequest,
    VerifyEmailRequest,
    WatchlistAddRequest,
    WatchlistReorderRequest,
)
from .security import (
    ACCESS_TOKEN_MINUTES,
    AUTH_COOKIE_NAME,
    AUTH_COOKIE_SAMESITE,
    AUTH_COOKIE_SECURE,
    SESSION_DAYS,
    auth_security_warnings,
    verify_turnstile,
)
from .service import (
    add_user_watchlist,
    create_user,
    delete_user_watchlist,
    list_user_watchlist,
    login_user,
    reorder_user_watchlist,
    request_password_reset,
    resend_verification,
    reset_password,
    verify_email,
)

router = APIRouter(prefix="/api", tags=["auth"])


def _raise_bad_request(message: str) -> None:
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)


def _set_auth_cookies(response: Response, access_token: str, session_token: str) -> None:
    response.set_cookie(
        AUTH_COOKIE_NAME,
        access_token,
        max_age=ACCESS_TOKEN_MINUTES * 60,
        httponly=True,
        secure=AUTH_COOKIE_SECURE,
        samesite=AUTH_COOKIE_SAMESITE,
        path="/",
    )
    response.set_cookie(
        f"{AUTH_COOKIE_NAME}_session",
        session_token,
        max_age=SESSION_DAYS * 24 * 3600,
        httponly=True,
        secure=AUTH_COOKIE_SECURE,
        samesite=AUTH_COOKIE_SAMESITE,
        path="/",
    )


@router.get("/auth/security-check")
def auth_security_check() -> dict:
    warnings = auth_security_warnings() + email_security_warnings()
    return {
        "ok": not warnings,
        "warnings": warnings,
        "cookie_name": AUTH_COOKIE_NAME,
        "cookie_secure": AUTH_COOKIE_SECURE,
    }


@router.post("/auth/register")
def register(payload: RegisterRequest, request: Request) -> dict:
    from adapter.phone_verification import configured
    from auth.email_sender import smtp_configured
    if not configured() or not smtp_configured():
        raise HTTPException(503,"註冊尚未開放：管理員需先完成 Email 與手機驗證服務設定")
    ip = get_client_ip(request)
    ok, reason = verify_turnstile(payload.turnstile_token, ip)
    if not ok:
        _raise_bad_request("防機器人驗證失敗，請重新操作")
    created, message = create_user(payload.email, payload.password, ip)
    if not created:
        _raise_bad_request(message)
    return {"ok": True, "message": message, "turnstile": reason}


@router.get("/auth/options")
def auth_options():
    import os
    from adapter.phone_verification import configured
    from auth.email_sender import smtp_configured
    from auth.security import TURNSTILE_SECRET_KEY
    site_key=os.getenv("TURNSTILE_SITE_KEY","").strip()
    return {"registration_enabled": configured() and smtp_configured() and (not TURNSTILE_SECRET_KEY or bool(site_key)),
            "email_configured":smtp_configured(),"phone_configured":configured(),
            "phone_required":True,"turnstile_site_key":site_key if TURNSTILE_SECRET_KEY else ""}


@router.post("/auth/verify-email")
def verify_email_endpoint(payload: VerifyEmailRequest) -> dict:
    ok, message = verify_email(payload.email, payload.code)
    if not ok:
        _raise_bad_request(message)
    return {"ok": True, "message": message}


@router.post("/auth/resend-verification")
def resend_verification_endpoint(payload: ResendVerificationRequest, request: Request) -> dict:
    ip = get_client_ip(request)
    ok, _ = verify_turnstile(payload.turnstile_token, ip)
    if not ok:
        _raise_bad_request("防機器人驗證失敗，請重新操作")
    sent, message = resend_verification(payload.email)
    if not sent:
        _raise_bad_request(message)
    return {"ok": True, "message": message}


@router.post("/auth/login")
def login(payload: LoginRequest, request: Request, response: Response) -> dict:
    ip = get_client_ip(request)
    ok, _ = verify_turnstile(payload.turnstile_token, ip)
    if not ok:
        _raise_bad_request("防機器人驗證失敗，請重新操作")
    success, message, data = login_user(
        payload.email,
        payload.password,
        ip,
        request.headers.get("User-Agent"),
    )
    if not success or not data:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=message)
    _set_auth_cookies(response, data["access_token"], data["session_token"])
    return {"ok": True, "message": message, **data}


@router.post("/auth/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(AUTH_COOKIE_NAME, path="/")
    response.delete_cookie(f"{AUTH_COOKIE_NAME}_session", path="/")
    return {"ok": True, "message": "已登出"}


@router.get("/auth/me")
def me(user: dict = Depends(get_current_user)) -> dict:
    return {"ok": True, "user": user}


@router.post("/auth/forgot-password")
def forgot_password(payload: PasswordResetRequest, request: Request) -> dict:
    ip = get_client_ip(request)
    ok, _ = verify_turnstile(payload.turnstile_token, ip)
    if not ok:
        _raise_bad_request("防機器人驗證失敗，請重新操作")
    sent, message = request_password_reset(payload.email)
    if not sent:
        _raise_bad_request(message)
    return {"ok": True, "message": message}


@router.post("/auth/reset-password")
def reset_password_endpoint(payload: PasswordResetConfirmRequest) -> dict:
    ok, message = reset_password(payload.email, payload.code, payload.new_password)
    if not ok:
        _raise_bad_request(message)
    return {"ok": True, "message": message}


@router.post("/auth/change-password")
def change_password_endpoint(payload: PasswordChangeRequest, request: Request, response: Response,
                             user: dict = Depends(get_current_user)) -> dict:
    from api.portfolio import _mutation
    from auth.password_change import change_password
    from auth.service import too_many_failed_logins, record_login_attempt
    _mutation(request)
    ip = get_client_ip(request)
    if too_many_failed_logins(ip, user["email"]):
        raise HTTPException(429, "嘗試次數過多，請 15 分鐘後再試")
    ok, message = change_password(user["id"],payload.current_password,payload.new_password)
    if not ok:
        record_login_attempt(ip,user["email"],False,"password_change")
        _raise_bad_request(message)
    response.delete_cookie(AUTH_COOKIE_NAME, path="/")
    response.delete_cookie(f"{AUTH_COOKIE_NAME}_session", path="/")
    return {"ok":True,"message":message}


@router.get("/me/watchlist")
def my_watchlist(user: dict = Depends(get_current_user)) -> dict:
    return {"ok": True, "items": list_user_watchlist(int(user["id"])), "limit": 5}


@router.post("/me/watchlist")
def add_my_watchlist(payload: WatchlistAddRequest, user: dict = Depends(get_current_user)) -> dict:
    ok, message, item = add_user_watchlist(int(user["id"]), payload.query)
    if not ok:
        _raise_bad_request(message)
    return {"ok": True, "message": message, "item": item, "items": list_user_watchlist(int(user["id"]))}


@router.delete("/me/watchlist/{code}")
def delete_my_watchlist(code: str, user: dict = Depends(get_current_user)) -> dict:
    delete_user_watchlist(int(user["id"]), code)
    return {"ok": True, "items": list_user_watchlist(int(user["id"]))}


@router.patch("/me/watchlist/reorder")
def reorder_my_watchlist(payload: WatchlistReorderRequest = Body(...), user: dict = Depends(get_current_user)) -> dict:
    reorder_user_watchlist(int(user["id"]), payload.codes)
    return {"ok": True, "items": list_user_watchlist(int(user["id"]))}
