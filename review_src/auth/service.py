from __future__ import annotations

import time
from contextlib import closing
from typing import Any

from core.components import resolve_stock
from core.accounts_database import db

from .email_sender import send_password_reset_email, send_verification_email
from .security import (
    ACCESS_TOKEN_MINUTES,
    SESSION_DAYS,
    create_jwt,
    dummy_verify_password,
    generate_verification_code,
    hash_password,
    hash_token,
    hash_verification_code,
    password_policy_error,
    verify_password,
    verify_verification_code,
)

WATCHLIST_LIMIT = 5
VERIFICATION_TTL_SECONDS = 15 * 60
LOGIN_FAIL_LIMIT = 5
LOGIN_FAIL_WINDOW_SECONDS = 15 * 60
REGISTER_IP_LIMIT = 5
REGISTER_WINDOW_SECONDS = 60 * 60


def now_ts() -> float:
    return time.time()


def too_many_failed_logins(ip: str, email: str) -> bool:
    cutoff = now_ts() - LOGIN_FAIL_WINDOW_SECONDS
    with closing(db()) as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS c FROM login_attempts
            WHERE success=0 AND attempted_at>=? AND (ip=? OR email=?)
            """,
            (cutoff, ip, email),
        ).fetchone()
        return int(row["c"] if row else 0) >= LOGIN_FAIL_LIMIT


def record_login_attempt(ip: str, email: str, success: bool, reason: str | None = None) -> None:
    with closing(db()) as conn:
        conn.execute(
            "INSERT INTO login_attempts(ip,email,success,reason,attempted_at) VALUES(?,?,?,?,?)",
            (ip, email, 1 if success else 0, reason, now_ts()),
        )
        conn.commit()


def too_many_registrations(ip: str) -> bool:
    cutoff = now_ts() - REGISTER_WINDOW_SECONDS
    with closing(db()) as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS c FROM login_attempts
            WHERE ip=? AND reason='register' AND attempted_at>=?
            """,
            (ip, cutoff),
        ).fetchone()
        return int(row["c"] if row else 0) >= REGISTER_IP_LIMIT


def _store_verification_code(email: str, purpose: str, code: str) -> bool:
    ts = now_ts()
    with closing(db()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        recent = conn.execute("SELECT MAX(created_at),COUNT(*) FROM email_verifications WHERE email=? AND purpose=? AND created_at>?", (email,purpose,ts-3600)).fetchone()
        if recent[1] >= 5 or (recent[0] is not None and recent[0] > ts-60):
            return False
        conn.execute(
            "UPDATE email_verifications SET used_at=? WHERE email=? AND purpose=? AND used_at IS NULL",
            (ts, email, purpose),
        )
        conn.execute(
            "INSERT INTO email_verifications(email,code_hash,purpose,expires_at,created_at) VALUES(?,?,?,?,?)",
            (email, hash_verification_code(email, code, purpose), purpose, ts + VERIFICATION_TTL_SECONDS, ts),
        )
        conn.commit()
    return True


def create_user(email: str, password: str, ip: str) -> tuple[bool, str]:
    policy_error = password_policy_error(password)
    if policy_error:
        return False, policy_error
    if too_many_registrations(ip):
        return False, "註冊次數過多，請稍後再試"
    ts = now_ts()
    with closing(db()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute("SELECT id,is_verified FROM users WHERE email=?", (email,)).fetchone()
        if existing and existing["is_verified"]:
            return False, "此 Email 已註冊，請直接登入"
        hashed = hash_password(password)
        if existing:
            conn.execute(
                "UPDATE users SET hashed_password=?, updated_at=?, is_active=1 WHERE email=?",
                (hashed, ts, email),
            )
        else:
            conn.execute(
                "INSERT INTO users(email,hashed_password,is_verified,is_active,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (email, hashed, 0, 1, ts, ts),
            )
        conn.execute("INSERT INTO account_security(user_id,phone_required) SELECT id,1 FROM users WHERE email=? ON CONFLICT(user_id) DO UPDATE SET phone_required=1",(email,))
        conn.execute(
            "INSERT INTO login_attempts(ip,email,success,reason,attempted_at) VALUES(?,?,?,?,?)",
            (ip, email, 1, "register", ts),
        )
        conn.commit()
    code = generate_verification_code()
    if not _store_verification_code(email, "register", code):
        return False, "驗證碼寄送過於頻繁，請稍後再試"
    if not send_verification_email(email, code):
        return False, "驗證信寄送失敗，請稍後再試"
    return True, "註冊成功，請至 Email 收取驗證碼"


def verify_email(email: str, code: str) -> tuple[bool, str]:
    ts = now_ts()
    with closing(db()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT * FROM email_verifications
            WHERE email=? AND purpose='register' AND used_at IS NULL
            ORDER BY created_at DESC LIMIT 1
            """,
            (email,),
        ).fetchone()
        if not row or float(row["expires_at"]) <= ts or row["attempts"] >= 5:
            return False, "驗證碼已過期，請重新發送"
        if not verify_verification_code(email, code, "register", row["code_hash"]):
            conn.execute("UPDATE email_verifications SET attempts=attempts+1 WHERE id=?", (row["id"],))
            conn.commit()
            return False, "驗證碼錯誤"
        conn.execute("UPDATE email_verifications SET used_at=? WHERE id=?", (ts, row["id"]))
        conn.execute("UPDATE users SET is_verified=1, updated_at=? WHERE email=?", (ts, email))
        conn.commit()
    return True, "Email 驗證完成"


def resend_verification(email: str) -> tuple[bool, str]:
    with closing(db()) as conn:
        user = conn.execute("SELECT id,is_verified FROM users WHERE email=?", (email,)).fetchone()
    if not user:
        return True, "如果此 Email 可驗證，系統會寄出驗證碼"
    if user["is_verified"]:
        return False, "此帳號已完成驗證"
    code = generate_verification_code()
    if not _store_verification_code(email, "register", code):
        return False, "驗證碼寄送過於頻繁，請稍後再試"
    if not send_verification_email(email, code):
        return False, "驗證信寄送失敗，請稍後再試"
    return True, "驗證碼已重新寄出"


def login_user(email: str, password: str, ip: str, user_agent: str | None = None) -> tuple[bool, str, dict[str, Any] | None]:
    if too_many_failed_logins(ip, email):
        return False, "登入失敗次數過多，請 15 分鐘後再試", None
    with closing(db()) as conn:
        user = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if not user:
            dummy_verify_password(password)
            record_login_attempt(ip, email, False, "invalid_credentials")
            return False, "Email 或密碼錯誤", None
        if not verify_password(password, user["hashed_password"]):
            record_login_attempt(ip, email, False, "invalid_credentials")
            return False, "Email 或密碼錯誤", None
        if not user["is_active"]:
            record_login_attempt(ip, email, False, "inactive")
            return False, "帳號已停用", None
        if not user["is_verified"]:
            record_login_attempt(ip, email, False, "unverified")
            return False, "請先完成 Email 驗證", None
        security = conn.execute("SELECT u.hashed_password,COALESCE(s.credential_version,0) cv FROM users u LEFT JOIN account_security s ON s.user_id=u.id WHERE u.id=?", (user["id"],)).fetchone()
        if not security or security["hashed_password"] != user["hashed_password"]:
            return False, "密碼已更新，請重新登入", None
        claims = {"cv": security["cv"]}
        access_token = create_jwt(user["id"], token_type="access", expires_seconds=ACCESS_TOKEN_MINUTES * 60, extra=claims)
        session_token = create_jwt(user["id"], token_type="session", expires_seconds=SESSION_DAYS * 24 * 3600, extra=claims)
        ts = now_ts()
        conn.execute("UPDATE users SET last_login_at=?, updated_at=? WHERE id=?", (ts, ts, user["id"]))
        conn.execute(
            "INSERT INTO auth_sessions(user_id,token_hash,user_agent,ip,expires_at,created_at) VALUES(?,?,?,?,?,?)",
            (user["id"], hash_token(session_token), user_agent, ip, ts + SESSION_DAYS * 24 * 3600, ts),
        )
        conn.commit()
    record_login_attempt(ip, email, True, "login")
    return True, "登入成功", {
        "access_token": access_token,
        "session_token": session_token,
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_MINUTES * 60,
        "user": {"id": user["id"], "email": user["email"]},
    }


def request_password_reset(email: str) -> tuple[bool, str]:
    with closing(db()) as conn:
        user = conn.execute("SELECT id,is_active FROM users WHERE email=?", (email,)).fetchone()
    if not user or not user["is_active"]:
        return True, "如果此 Email 存在，系統會寄出重設碼"
    code = generate_verification_code()
    if not _store_verification_code(email, "reset_password", code):
        return False, "重設碼寄送過於頻繁，請稍後再試"
    if not send_password_reset_email(email, code):
        return False, "重設信寄送失敗，請稍後再試"
    return True, "密碼重設驗證碼已寄出"


def reset_password(email: str, code: str, new_password: str) -> tuple[bool, str]:
    policy_error = password_policy_error(new_password)
    if policy_error:
        return False, policy_error
    ts = now_ts()
    with closing(db()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT * FROM email_verifications
            WHERE email=? AND purpose='reset_password' AND used_at IS NULL
            ORDER BY created_at DESC LIMIT 1
            """,
            (email,),
        ).fetchone()
        if not row or float(row["expires_at"]) <= ts or row["attempts"] >= 5:
            return False, "驗證碼已過期，請重新申請"
        if not verify_verification_code(email, code, "reset_password", row["code_hash"]):
            conn.execute("UPDATE email_verifications SET attempts=attempts+1 WHERE id=?", (row["id"],))
            conn.commit()
            return False, "驗證碼錯誤"
        conn.execute("UPDATE users SET hashed_password=?, updated_at=? WHERE email=?", (hash_password(new_password), ts, email))
        conn.execute("UPDATE email_verifications SET used_at=? WHERE id=?", (ts, row["id"]))
        from auth.password_change import revoke_credentials
        user = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
        if user:
            revoke_credentials(conn, user[0], ts)
        conn.commit()
    return True, "密碼已更新，請重新登入"


def list_user_watchlist(user_id: int) -> list[dict[str, Any]]:
    with closing(db()) as conn:
        rows = conn.execute(
            "SELECT stock_code AS code, stock_name AS name, sort_order FROM user_watchlist WHERE user_id=? ORDER BY sort_order, stock_code",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def add_user_watchlist(user_id: int, query: str) -> tuple[bool, str, dict[str, Any] | None]:
    item = resolve_stock(query)
    if not item:
        return False, "找不到股票，請輸入代號或名稱", None
    ts = now_ts()
    with closing(db()) as conn:
        exists = conn.execute(
            "SELECT id,sort_order FROM user_watchlist WHERE user_id=? AND stock_code=?",
            (user_id, item["code"]),
        ).fetchone()
        count = conn.execute("SELECT COUNT(*) AS c FROM user_watchlist WHERE user_id=?", (user_id,)).fetchone()["c"]
        if not exists and int(count) >= WATCHLIST_LIMIT:
            return False, f"自選股最多 {WATCHLIST_LIMIT} 檔，請先移除一檔", None
        if exists:
            conn.execute(
                "UPDATE user_watchlist SET stock_name=?, updated_at=? WHERE id=?",
                (item.get("name", ""), ts, exists["id"]),
            )
            sort_order = exists["sort_order"]
        else:
            row = conn.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 AS n FROM user_watchlist WHERE user_id=?", (user_id,)).fetchone()
            sort_order = int(row["n"] if row else 0)
            conn.execute(
                "INSERT INTO user_watchlist(user_id,stock_code,stock_name,sort_order,added_at,updated_at) VALUES(?,?,?,?,?,?)",
                (user_id, item["code"], item.get("name", ""), sort_order, ts, ts),
            )
        conn.commit()
    return True, "已加入自選股", {"code": item["code"], "name": item.get("name", ""), "sort_order": sort_order}


def delete_user_watchlist(user_id: int, code: str) -> None:
    code = str(code or "").strip().zfill(4)
    with closing(db()) as conn:
        conn.execute("DELETE FROM user_watchlist WHERE user_id=? AND stock_code=?", (user_id, code))
        conn.commit()


def reorder_user_watchlist(user_id: int, codes: list[str]) -> None:
    clean = [str(c or "").strip().zfill(4) for c in codes if str(c or "").strip()]
    ts = now_ts()
    with closing(db()) as conn:
        for idx, code in enumerate(clean[:WATCHLIST_LIMIT]):
            conn.execute(
                "UPDATE user_watchlist SET sort_order=?, updated_at=? WHERE user_id=? AND stock_code=?",
                (idx, ts, user_id, code),
            )
        conn.commit()
