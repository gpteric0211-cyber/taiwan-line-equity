"""Password rotation revokes every previously issued access/session credential."""

from contextlib import closing
import time
from core.accounts_database import db
from auth.security import hash_password, verify_password, password_policy_error, verify_verification_code


def revoke_credentials(conn, user_id, now):
    conn.execute("INSERT INTO account_security(user_id,credential_version) VALUES(?,1) ON CONFLICT(user_id) DO UPDATE SET credential_version=credential_version+1", (user_id,))
    conn.execute("UPDATE auth_sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL", (now,user_id))


def change_password(user_id, current_password, new_password, code):
    error=password_policy_error(new_password)
    if error:
        return False,error
    with closing(db()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        user=conn.execute("SELECT * FROM users WHERE id=? AND is_active=1 AND is_verified=1", (user_id,)).fetchone()
        if not user or not verify_password(current_password,user["hashed_password"]):
            return False,"目前密碼錯誤"
        if current_password==new_password:
            return False,"新密碼不可與目前密碼相同"
        now=time.time()
        proof = conn.execute(
            "SELECT * FROM email_verifications WHERE email=? AND purpose='change_password' AND used_at IS NULL ORDER BY created_at DESC LIMIT 1",
            (user["email"],),
        ).fetchone()
        if not proof or proof["expires_at"] <= now or proof["attempts"] >= 5:
            return False, "請重新寄送 Email 驗證碼"
        if not verify_verification_code(user["email"], code, "change_password", proof["code_hash"]):
            conn.execute("UPDATE email_verifications SET attempts=attempts+1 WHERE id=?", (proof["id"],))
            return False, "Email 驗證碼錯誤"
        conn.execute("UPDATE email_verifications SET used_at=? WHERE email=? AND used_at IS NULL AND purpose IN ('change_password','reset_password')", (now,user["email"]))
        conn.execute("UPDATE users SET hashed_password=?,updated_at=? WHERE id=?", (hash_password(new_password),now,user_id))
        revoke_credentials(conn,user_id,now)
    return True,"密碼已更新，請重新登入"
