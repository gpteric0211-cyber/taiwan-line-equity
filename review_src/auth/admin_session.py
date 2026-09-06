"""Independent back-office credentials: role checks, explicit logout, audited access."""

from contextlib import closing
import time
from fastapi import HTTPException, Request
from core.accounts_database import db
from auth.security import create_jwt, decode_jwt, hash_token, verify_password, dummy_verify_password
from auth.service import too_many_failed_logins, record_login_attempt
from auth.local_owner import local_admin_request

COOKIE = "equity_admin"
TTL = 3600


def _user(conn, user_id):
    return conn.execute("SELECT u.*,m.role,COALESCE(s.credential_version,0) credential_version,EXISTS(SELECT 1 FROM local_admin_identity l WHERE l.user_id=u.id) local_admin FROM users u JOIN member_access m ON m.user_id=u.id LEFT JOIN account_security s ON s.user_id=u.id WHERE u.id=?", (user_id,)).fetchone()


def login(email,password,ip,request=None):
    if too_many_failed_logins(ip,email):
        raise HTTPException(429,"嘗試次數過多，請 15 分鐘後再試")
    with closing(db()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
        user = _user(conn,row[0]) if row else None
        correct = verify_password(password,user["hashed_password"]) if user else (dummy_verify_password(password) or False)
        verified = user and (local_admin_request(request) if user["local_admin"] else user["is_verified"])
        if not user or not correct or not user["is_active"] or not verified or user["role"] not in {"owner","manager"}:
            token = None
        else:
            token=create_jwt(user["id"],token_type="admin",expires_seconds=TTL,extra={"cv":user["credential_version"]})
            conn.execute("INSERT INTO admin_session VALUES(?,?,?,NULL)", (hash_token(token),user["id"],time.time()+TTL))
            conn.execute("INSERT INTO admin_event(user_id,action,created_at) VALUES(?,'login',?)", (user["id"],time.time()))
            conn.execute("UPDATE users SET last_login_at=? WHERE id=?", (time.time(),user["id"]))
    record_login_attempt(ip,email,bool(token),"admin_login")
    if not token:
        raise HTTPException(401,"帳號、密碼錯誤或沒有後臺權限")
    return token


def current_admin(request: Request):
    token=request.cookies.get(COOKIE,"")
    claims=decode_jwt(token)
    if not claims or claims.get("type")!="admin":
        raise HTTPException(401,"請登入後臺")
    with closing(db()) as conn:
        user=_user(conn,claims["sub"])
        session=conn.execute("SELECT 1 FROM admin_session WHERE token_hash=? AND revoked_at IS NULL AND expires_at>?", (hash_token(token),time.time())).fetchone()
        verified = user and (local_admin_request(request) if user["local_admin"] else user["is_verified"])
        if (not user or not session or not user["is_active"] or not verified
                or user["role"] not in {"owner","manager"} or user["credential_version"]!=claims.get("cv")):
            raise HTTPException(401,"後臺登入已失效，請重新登入")
        # Admin access intentionally does not depend on customer phone verification.
        return {"id":user["id"],"email":user["email"],"role":user["role"]}


def logout(token):
    with closing(db()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        row=conn.execute("SELECT user_id FROM admin_session WHERE token_hash=? AND revoked_at IS NULL", (hash_token(token),)).fetchone()
        if row:
            conn.execute("UPDATE admin_session SET revoked_at=? WHERE token_hash=?", (time.time(),hash_token(token)))
            conn.execute("INSERT INTO admin_event(user_id,action,created_at) VALUES(?,'logout',?)", (row[0],time.time()))


def events(offset=0):
    with closing(db()) as conn:
        rows=conn.execute("""SELECT e.user_id,u.email,e.action,e.created_at,NULL target_id,NULL before_json,NULL after_json,'' reason
          FROM admin_event e JOIN users u ON u.id=e.user_id
          UNION ALL
          SELECT a.actor_id,u.email,a.action,a.created_at,a.target_id,a.before_json,a.after_json,a.reason
          FROM member_audit a LEFT JOIN users u ON u.id=a.actor_id
          ORDER BY 4 DESC LIMIT 50 OFFSET ?""", (offset,)).fetchall()
        return [dict(row) for row in rows]
