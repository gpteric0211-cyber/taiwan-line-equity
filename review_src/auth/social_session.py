"""Create local credentials only after provider verification and account checks."""

from contextlib import closing
import time
from core.accounts_database import db
from auth.security import ACCESS_TOKEN_MINUTES, SESSION_DAYS, create_jwt, hash_token


def issue_session(user_id):
    with closing(db()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        user = conn.execute("SELECT u.*,COALESCE(s.credential_version,0) cv FROM users u LEFT JOIN account_security s ON s.user_id=u.id WHERE u.id=?", (user_id,)).fetchone()
        if not user or not user["is_active"] or not user["is_verified"]:
            raise ValueError("請先完成 Email 驗證或聯絡管理員")
        claims = {"cv": user["cv"]}
        access = create_jwt(user_id,token_type="access",expires_seconds=ACCESS_TOKEN_MINUTES*60,extra=claims)
        session = create_jwt(user_id,token_type="session",expires_seconds=SESSION_DAYS*86400,extra=claims)
        now = time.time()
        conn.execute("INSERT INTO auth_sessions(user_id,token_hash,expires_at,created_at) VALUES(?,?,?,?)", (user_id,hash_token(session),now+SESSION_DAYS*86400,now))
        conn.execute("UPDATE users SET last_login_at=? WHERE id=?", (now,user_id))
        return access,session
