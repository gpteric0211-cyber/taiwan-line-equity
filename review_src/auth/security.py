from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any

import requests

from core.config import HEADERS, mask_secret_text
from core.application_secrets import jwt_secret


JWT_SECRET_KEY = jwt_secret()
JWT_ISSUER = os.getenv("JWT_ISSUER", "taiwan50-dashboard")
ACCESS_TOKEN_MINUTES = int(os.getenv("ACCESS_TOKEN_MINUTES", "30"))
SESSION_DAYS = int(os.getenv("SESSION_DAYS", "14"))
AUTH_COOKIE_NAME = os.getenv("AUTH_COOKIE_NAME", "tw50_access_token")
AUTH_COOKIE_SECURE = os.getenv("AUTH_COOKIE_SECURE", "0").strip().lower() in {"1", "true", "yes", "on"}
AUTH_COOKIE_SAMESITE = os.getenv("AUTH_COOKIE_SAMESITE", "lax")
TURNSTILE_SECRET_KEY = os.getenv("TURNSTILE_SECRET_KEY", "").strip()
TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"

PBKDF2_ITERATIONS = int(os.getenv("AUTH_PBKDF2_ITERATIONS", "210000"))
PASSWORD_DUMMY_HASH = ""


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode((data + padding).encode("ascii"))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${_b64url(salt)}${_b64url(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        method, iter_text, salt_text, digest_text = str(encoded or "").split("$", 3)
        if method != "pbkdf2_sha256":
            return False
        iterations = int(iter_text)
        salt = _b64url_decode(salt_text)
        expected = _b64url_decode(digest_text)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def dummy_verify_password(password: str) -> None:
    global PASSWORD_DUMMY_HASH
    if not PASSWORD_DUMMY_HASH:
        PASSWORD_DUMMY_HASH = hash_password("dummy-password-for-timing")
    verify_password(password or "", PASSWORD_DUMMY_HASH)


def hash_token(token: str) -> str:
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def create_jwt(subject: int | str, *, token_type: str = "access", expires_seconds: int | None = None, extra: dict[str, Any] | None = None) -> str:
    now = int(time.time())
    exp = now + int(expires_seconds if expires_seconds is not None else ACCESS_TOKEN_MINUTES * 60)
    header = {"alg": "HS256", "typ": "JWT"}
    payload: dict[str, Any] = {
        "iss": JWT_ISSUER,
        "sub": str(subject),
        "type": token_type,
        "iat": now,
        "exp": exp,
        "jti": secrets.token_urlsafe(16),
    }
    if extra:
        payload.update(extra)
    signing_input = (
        _b64url(json.dumps(header, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
        + "."
        + _b64url(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    )
    sig = hmac.new(JWT_SECRET_KEY.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256).digest()
    return signing_input + "." + _b64url(sig)


def decode_jwt(token: str) -> dict[str, Any] | None:
    try:
        parts = str(token or "").split(".")
        if len(parts) != 3:
            return None
        signing_input = ".".join(parts[:2])
        expected_sig = hmac.new(JWT_SECRET_KEY.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256).digest()
        actual_sig = _b64url_decode(parts[2])
        if not hmac.compare_digest(expected_sig, actual_sig):
            return None
        header = json.loads(_b64url_decode(parts[0]).decode("utf-8"))
        if header.get("alg") != "HS256":
            return None
        payload = json.loads(_b64url_decode(parts[1]).decode("utf-8"))
        if payload.get("iss") != JWT_ISSUER:
            return None
        if int(payload.get("exp") or 0) < int(time.time()):
            return None
        return payload
    except Exception:
        return None


def generate_verification_code() -> str:
    return f"{secrets.randbelow(1000000):06d}"


def hash_verification_code(email: str, code: str, purpose: str) -> str:
    raw = f"{str(email).lower()}:{purpose}:{code}"
    return hmac.new(JWT_SECRET_KEY.encode("utf-8"), raw.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_verification_code(email: str, code: str, purpose: str, code_hash: str) -> bool:
    actual = hash_verification_code(email, code, purpose)
    return hmac.compare_digest(actual, str(code_hash or ""))


def verify_turnstile(token: str | None, remote_ip: str | None = None) -> tuple[bool, str]:
    if not TURNSTILE_SECRET_KEY:
        return True, "turnstile_not_configured"
    if not token:
        return False, "missing_turnstile_token"
    try:
        data = {"secret": TURNSTILE_SECRET_KEY, "response": token}
        if remote_ip:
            data["remoteip"] = remote_ip
        resp = requests.post(TURNSTILE_VERIFY_URL, data=data, headers=HEADERS, timeout=8)
        payload = resp.json()
        if bool(payload.get("success")):
            return True, "ok"
        return False, mask_secret_text(str(payload.get("error-codes") or "turnstile_failed"))
    except Exception as exc:
        return False, mask_secret_text(f"turnstile_error:{exc}")


def password_policy_error(password: str) -> str | None:
    text = str(password or "")
    if len(text) < 10:
        return "密碼至少需要 10 個字元"
    if len(text) > 128:
        return "密碼過長"
    if text.isdigit() or text.isalpha():
        return "密碼需混合字母、數字或符號，不能只使用單一類型"
    return None


def auth_security_warnings() -> list[str]:
    warnings: list[str] = []
    if JWT_SECRET_KEY == "dev-change-this-jwt-secret-before-production":
        warnings.append("JWT_SECRET_KEY 尚未設定，正式上線前必須更換")
    if not TURNSTILE_SECRET_KEY:
        warnings.append("TURNSTILE_SECRET_KEY 尚未設定，防機器人驗證目前為開發模式")
    if not AUTH_COOKIE_SECURE:
        warnings.append("AUTH_COOKIE_SECURE 未啟用；正式 HTTPS 上線時應設為 1")
    return warnings
