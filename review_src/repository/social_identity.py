"""Minimal keyed identity links; never store provider tokens, emails or profiles."""

from contextlib import closing
import hashlib
import hmac
import time
from core.accounts_database import db
from auth.security import JWT_SECRET_KEY, hash_token, verify_password


def identity_hash(provider, client, subject):
    return hmac.new(JWT_SECRET_KEY.encode(),
                    (provider + "\0" + client + "\0" + subject).encode(), hashlib.sha256).hexdigest()


def remember_state(state):
    with closing(db()) as conn, conn:
        conn.execute("DELETE FROM social_state WHERE expires_at<=?", (time.time(),))
        conn.execute("INSERT INTO social_state VALUES(?,?)", (hash_token(state),time.time()+600))


def consume_state(state):
    with closing(db()) as conn, conn:
        return conn.execute("DELETE FROM social_state WHERE state_hash=? AND expires_at>?",
                            (hash_token(state),time.time())).rowcount == 1


def password_matches(user_id, password):
    with closing(db()) as conn:
        row = conn.execute("SELECT hashed_password FROM users WHERE id=? AND is_active=1 AND is_verified=1", (user_id,)).fetchone()
        return bool(row and verify_password(password,row[0]))


def linked(user_id):
    with closing(db()) as conn:
        return [row[0] for row in conn.execute("SELECT provider FROM social_identity WHERE user_id=?", (user_id,))]


def resolve(provider, subject_hash, user_id=None, version=None):
    with closing(db()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute("SELECT user_id FROM social_identity WHERE provider=? AND subject_hash=?", (provider,subject_hash)).fetchone()
        if user_id is not None:
            user = conn.execute("SELECT u.is_active,u.is_verified,COALESCE(s.credential_version,0) cv FROM users u LEFT JOIN account_security s ON s.user_id=u.id WHERE u.id=?", (user_id,)).fetchone()
            if not user or not user["is_active"] or not user["is_verified"] or user["cv"] != version:
                raise ValueError("帳號狀態已變更，請重新登入")
            if existing and existing[0] != user_id:
                raise ValueError("此第三方帳號已綁定其他會員")
            other = conn.execute("SELECT subject_hash FROM social_identity WHERE provider=? AND user_id=?", (provider,user_id)).fetchone()
            if other and other[0] != subject_hash:
                raise ValueError("請先解除原帳號綁定")
            conn.execute("INSERT OR IGNORE INTO social_identity VALUES(?,?,?,?)", (provider,subject_hash,user_id,time.time()))
            return user_id
        if not existing:
            raise ValueError("尚未綁定：請先註冊並以 Email 登入，再到帳號安全綁定")
        return existing[0]


def unlink(user_id, provider):
    with closing(db()) as conn, conn:
        conn.execute("DELETE FROM social_identity WHERE user_id=? AND provider=?", (user_id,provider))
