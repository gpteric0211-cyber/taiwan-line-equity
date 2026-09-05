"""Account-only membership state with serialized authorization and audit writes."""

from contextlib import closing
import json
import time
from core.accounts_database import db


class MemberConflict(ValueError):
    pass


def _access(conn, user_id):
    row = conn.execute("SELECT * FROM member_access WHERE user_id=?", (user_id,)).fetchone()
    return dict(row) if row else dict(user_id=user_id, role="member", plan="free", expires_at=None, version=0)


def access(user_id):
    with closing(db()) as conn:
        return _access(conn, user_id)


def record_principal(user_id, subject):
    with closing(db()) as conn, conn:
        conn.execute("INSERT INTO member_principal VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET portfolio_subject=excluded.portfolio_subject",(user_id,subject))


def linked_account(subject):
    with closing(db()) as conn:
        row=conn.execute("""SELECT u.id,u.is_active,u.is_verified,COALESCE(s.phone_required,0) phone_required,
            EXISTS(SELECT 1 FROM account_phone p WHERE p.user_id=u.id) phone_verified
            FROM member_principal m JOIN users u ON u.id=m.user_id
            LEFT JOIN account_security s ON s.user_id=u.id WHERE m.portfolio_subject=?""",(subject,)).fetchone()
        return dict(row) if row else None


def list_members(query="", offset=0, limit=30):
    with closing(db()) as conn:
        # Literal substring search: wildcard characters have no special privileges.
        rows = conn.execute(
            """SELECT u.id,u.email,u.is_verified,u.is_active,u.created_at,u.last_login_at,
            COALESCE(m.role,'member') role,COALESCE(m.plan,'free') plan,m.expires_at,
            COALESCE(m.version,0) version FROM users u LEFT JOIN member_access m ON m.user_id=u.id
            WHERE instr(lower(u.email),lower(?))>0 ORDER BY u.id LIMIT ? OFFSET ?""",
            (query, limit, offset),
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM users WHERE instr(lower(email),lower(?))>0", (query,)).fetchone()[0]
        return {"items": [dict(row) for row in rows], "total": total}


def audit(user_id, limit=50):
    with closing(db()) as conn:
        rows = conn.execute("SELECT * FROM member_audit WHERE target_id=? ORDER BY id DESC LIMIT ?", (user_id, limit)).fetchall()
        return [dict(row) for row in rows]


def change(actor_id, target_id, *, action, value, version, reason, request_id):
    """Recheck role inside write lock; a revoked manager cannot race an update."""
    now = time.time()
    with closing(db()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        actor = conn.execute("SELECT is_active,is_verified FROM users WHERE id=?", (actor_id,)).fetchone()
        role = _access(conn, actor_id)["role"]
        if not actor or not actor["is_active"] or not actor["is_verified"] or role not in {"owner", "manager"}:
            raise PermissionError("需要會員管理權限")
        if not conn.execute("SELECT 1 FROM users WHERE id=?", (target_id,)).fetchone():
            raise LookupError("找不到會員")
        before = _access(conn, target_id)
        if action == "role" and role != "owner":
            raise PermissionError("只有擁有者可以調整管理權限")
        if role == "manager" and (target_id == actor_id or before["role"] != "member"):
            raise PermissionError("管理員只能調整一般會員")
        if before["role"] == "owner" and action == "role":
            raise PermissionError("擁有者權限不可由網頁移除")
        requested = dict(value)
        request_json = json.dumps({"action": action, "value": requested, "version": version}, sort_keys=True)
        previous = conn.execute("SELECT * FROM member_audit WHERE request_id=?", (request_id,)).fetchone()
        if previous:
            prior = json.loads(previous["after_json"])
            if previous["actor_id"] != actor_id or previous["target_id"] != target_id or prior.get("request") != request_json or previous["reason"] != reason:
                raise MemberConflict("操作識別碼已被其他操作使用")
            return _access(conn, target_id)
        if before["version"] != version:
            raise MemberConflict("會員資料已更新，請重新整理後再操作")
        after = {**before, **requested, "version": version + 1}
        conn.execute(
            """INSERT INTO member_access(user_id,role,plan,expires_at,version) VALUES(?,?,?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET role=excluded.role,plan=excluded.plan,
            expires_at=excluded.expires_at,version=excluded.version""",
            (target_id, after["role"], after["plan"], after["expires_at"], after["version"]),
        )
        conn.execute("INSERT INTO member_audit(actor_id,target_id,action,before_json,after_json,reason,request_id,created_at) VALUES(?,?,?,?,?,?,?,?)",
                     (actor_id,target_id,action,json.dumps(before),json.dumps({**after,"request":request_json}),reason,request_id,now))
        return after


def bootstrap_owner(email):
    """Local operator command only; never callable through registration or HTTP."""
    with closing(db()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT 1 FROM member_access WHERE role='owner'").fetchone():
            raise MemberConflict("已有擁有者，不能再次初始化")
        row = conn.execute("SELECT id FROM users WHERE email=? AND is_active=1 AND is_verified=1", (email.strip().lower(),)).fetchone()
        if not row:
            raise ValueError("請指定已啟用且已驗證的現有帳號")
        before = _access(conn, row[0])
        conn.execute("INSERT INTO member_access(user_id,role) VALUES(?,'owner') ON CONFLICT(user_id) DO UPDATE SET role='owner',version=version+1", (row[0],))
        conn.execute("INSERT INTO member_audit(actor_id,target_id,action,before_json,after_json,reason,request_id,created_at) VALUES(NULL,?,'bootstrap_owner',?,?,?,'bootstrap_owner',?)",
                     (row[0],json.dumps(before),json.dumps(_access(conn,row[0])),"Local operator initialization",time.time()))
