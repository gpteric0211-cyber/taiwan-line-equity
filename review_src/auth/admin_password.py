"""One-use emailed password links; GET never consumes a credential."""

from contextlib import closing
import json
import os
import secrets
import time
from urllib.parse import urlsplit
from fastapi import HTTPException
from auth import email_sender
from auth.password_change import revoke_credentials
from auth.security import hash_password, hash_token, password_policy_error, verify_password
from core.accounts_database import db
from core.portfolio_storage import configured_path

TTL = 15 * 60


def public_origin():
    """Trust operator configuration or supervisor output, never a request Host."""
    origin = os.getenv("EQUITY_AUTH_PUBLIC_URL", "").strip().rstrip("/")
    if not origin:
        try:
            state = json.loads(configured_path("EQUITY_PUBLIC_ENDPOINT_FILE", "var/services/public-endpoint.json").read_text(encoding="utf-8"))
            origin = state.get("origin", "") if state.get("status") == "ready" else ""
        except (OSError, ValueError):
            origin = ""
    parsed = urlsplit(origin)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.path or parsed.query or parsed.fragment):
        raise HTTPException(503, "請先設定可收取改密碼連結的 HTTPS 網站網址")
    return origin


def request_link(user_id):
    if not email_sender.smtp_configured():
        raise HTTPException(503, "寄信服務尚未設定；目前可以登入後臺，設定寄信服務後才能寄送修改密碼連結。")
    origin = public_origin()
    now = time.time()
    token = secrets.token_urlsafe(32)
    digest = hash_token(token)
    with closing(db()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        user = conn.execute("SELECT u.email,COALESCE(s.credential_version,0) cv FROM users u JOIN member_access m ON m.user_id=u.id LEFT JOIN account_security s ON s.user_id=u.id WHERE u.id=? AND u.is_active=1 AND m.role IN ('owner','manager')", (user_id,)).fetchone()
        if not user:
            raise HTTPException(403, "沒有後臺權限")
        recent = conn.execute("SELECT MAX(created_at),COUNT(*) FROM admin_password_link WHERE user_id=? AND created_at>?", (user_id,now-3600)).fetchone()
        if recent[1] >= 5 or (recent[0] is not None and recent[0] > now-60):
            raise HTTPException(429, "寄送過於頻繁，請稍後再試（每分鐘一次、每小時最多五次）")
        conn.execute("UPDATE admin_password_link SET used_at=? WHERE user_id=? AND used_at IS NULL", (now,user_id))
        conn.execute("INSERT INTO admin_password_link VALUES(?,?,?,?,?,?,NULL)", (digest,user_id,user["email"],user["cv"],now,now+TTL))
    # Fragment is not sent to the server, reverse proxy, access logs or referrers.
    url = origin + "/admin/password#token=" + token
    if not email_sender.send_password_change_link(user["email"], url):
        with closing(db()) as conn, conn:
            conn.execute("UPDATE admin_password_link SET used_at=? WHERE token_hash=?", (time.time(),digest))
        raise HTTPException(503, "修改密碼信寄送失敗，請稍後再試")
    return "修改密碼連結已寄至管理員 Email，15 分鐘內有效；重新寄送會使舊連結失效。"


def complete(token, new_password, confirm_password):
    if new_password != confirm_password:
        raise HTTPException(400, "兩次新密碼輸入不一致")
    error = password_policy_error(new_password)
    if error:
        raise HTTPException(400, error)
    now = time.time()
    with closing(db()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("""SELECT l.*,u.hashed_password FROM admin_password_link l
            JOIN users u ON u.id=l.user_id JOIN member_access m ON m.user_id=u.id
            LEFT JOIN account_security s ON s.user_id=u.id
            WHERE l.token_hash=? AND l.used_at IS NULL AND l.expires_at>?
            AND l.credential_version=COALESCE(s.credential_version,0) AND l.email=u.email
            AND u.is_active=1 AND m.role IN ('owner','manager')""", (hash_token(token),now)).fetchone()
        if not row:
            raise HTTPException(400, "連結已失效或已使用，請在後臺重新寄送")
        if verify_password(new_password,row["hashed_password"]):
            raise HTTPException(400, "新密碼不可與目前密碼相同")
        user_id = row["user_id"]
        conn.execute("UPDATE users SET hashed_password=?,is_verified=1,updated_at=? WHERE id=?", (hash_password(new_password),now,user_id))
        conn.execute("DELETE FROM local_admin_identity WHERE user_id=?", (user_id,))
        conn.execute("UPDATE admin_password_link SET used_at=? WHERE user_id=? AND used_at IS NULL", (now,user_id))
        conn.execute("UPDATE email_verifications SET used_at=? WHERE email=? AND used_at IS NULL", (now,row["email"]))
        revoke_credentials(conn,user_id,now)
        conn.execute("UPDATE admin_session SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL", (now,user_id))
        conn.execute("INSERT INTO admin_event(user_id,action,created_at) VALUES(?,'password_change',?)", (user_id,now))
    return "密碼已更新，請使用新密碼重新登入後臺。"
