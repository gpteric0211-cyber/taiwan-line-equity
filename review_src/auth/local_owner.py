"""Explicit local operator provisioning; never exposed by an HTTP route."""

from contextlib import closing
from ipaddress import ip_address
import json
import time
from auth.schemas import EMAIL_RE, normalize_email
from auth.security import hash_password, password_policy_error
from core.accounts_database import db


def local_admin_request(request):
    """Unverified local owners cannot use tunnels, forwarded hosts or public clients."""
    if not request or not request.client:
        return False
    if any(name in request.headers for name in (
        "forwarded", "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto",
        "x-real-ip", "cf-connecting-ip", "cf-ray",
    )):
        return False
    try:
        return (ip_address(request.client.host).is_loopback
                and request.url.hostname in {"localhost", "127.0.0.1", "::1"})
    except ValueError:
        return False


def create_owner(email, password, *, allow_temporary_password=False):
    email = normalize_email(email)
    if not EMAIL_RE.fullmatch(email):
        raise ValueError("Email 格式不正確")
    if not password or len(password) > 128:
        raise ValueError("請輸入 1 至 128 字的密碼")
    error = password_policy_error(password)
    if error and not allow_temporary_password:
        raise ValueError(error)
    hashed = hash_password(password)
    now = time.time()
    with closing(db()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT 1 FROM member_access WHERE role='owner'").fetchone():
            raise ValueError("已有擁有者，請登入後臺授權其他管理員。")
        if conn.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            raise ValueError("帳號已存在；不會覆寫密碼或自動提升權限。")
        user_id = conn.execute("INSERT INTO users(email,hashed_password,is_verified,is_active,created_at,updated_at) VALUES(?,?,0,1,?,?)", (email,hashed,now,now)).lastrowid
        conn.execute("INSERT INTO account_security(user_id,phone_required) VALUES(?,1)", (user_id,))
        conn.execute("INSERT INTO member_access(user_id,role) VALUES(?,'owner')", (user_id,))
        conn.execute("INSERT INTO local_admin_identity VALUES(?)", (user_id,))
        conn.execute("INSERT INTO member_audit(actor_id,target_id,action,before_json,after_json,reason,request_id,created_at) VALUES(NULL,?,'bootstrap_owner','{}',?,?,'bootstrap_owner',?)", (user_id,json.dumps({"role":"owner","plan":"free"}),"Local operator initialization; local access until email password change",now))
    return user_id
