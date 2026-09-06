"""Synthetic accounts only: migrations, role boundaries, expiry and retry safety."""

from contextlib import closing
import json
import sqlite3
import time
import uuid
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from core import accounts_database as accounts
from repository import member_repository as members
from services.membership_service import effective_access
from api import membership


@pytest.fixture
def member_db(tmp_path, monkeypatch):
    monkeypatch.setenv("EQUITY_AUTH_DB", str(tmp_path / "accounts # isolated.sqlite3"))
    accounts.initialize()
    with closing(accounts.db()) as conn, conn:
        for identifier in range(1,5):
            conn.execute("INSERT INTO users(id,email,hashed_password,is_verified,is_active,created_at,updated_at) VALUES(?,?,?,1,1,1,1)",
                         (identifier,f"member{identifier}@example.invalid","synthetic-not-a-password"))
    members.bootstrap_owner("member1@example.invalid")
    return accounts


def change(actor=1, target=2, **overrides):
    data=dict(action="plan",value={"plan":"complimentary","expires_at":None},version=0,
              reason="Synthetic QA grant",request_id=str(uuid.uuid4()))
    return members.change(actor,target,**{**data,**overrides})


def test_upgrade_existing_v1_and_idempotent_preserves_accounts(tmp_path, monkeypatch):
    from auth.models import CREATE_TABLES_SQL
    path=tmp_path/"legacy.sqlite3"
    monkeypatch.setenv("EQUITY_AUTH_DB",str(path))
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.executescript(CREATE_TABLES_SQL)
        conn.execute("CREATE TABLE account_migration(version INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO account_migration VALUES(1)")
        conn.execute("INSERT INTO users(email,hashed_password,created_at,updated_at) VALUES('qa@example.invalid','unchanged',1,1)")
    assert accounts.initialize()["users"]==1
    assert accounts.initialize()["users"]==1
    with closing(accounts.db()) as conn:
        assert conn.execute("SELECT hashed_password FROM users").fetchone()[0]=="unchanged"
        assert [row[0] for row in conn.execute("SELECT version FROM account_migration ORDER BY version")]==[1,2,3,4,5,6,7,8]


def test_failed_migration_rolls_back_all_ddl(tmp_path):
    from core.membership_schema import migrate
    with closing(sqlite3.connect(tmp_path/"broken.sqlite3")) as conn:
        conn.execute("CREATE TABLE account_migration(version INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO account_migration VALUES(1)")
        conn.execute("CREATE TABLE member_audit(id INTEGER)")
        conn.commit()
        with pytest.raises(sqlite3.OperationalError):
            migrate(conn)
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='member_access'").fetchone()
        assert conn.execute("SELECT MAX(version) FROM account_migration").fetchone()[0]==1


def test_expiry_exact_boundary_and_lifetime_without_role_escalation(member_db):
    grant=change()
    assert effective_access(grant,now=10**15)["premium"]
    assert not effective_access(grant)["can_manage_members"]
    assert not effective_access(members.access(1))["premium"]
    monthly={**grant,"plan":"monthly","expires_at":100}
    assert effective_access(monthly,now=99)["premium"]
    assert not effective_access(monthly,now=100)["premium"]
    assert effective_access(monthly,now=100)["expired"]


def test_manager_cannot_self_grant_promote_or_edit_owner(member_db):
    change(target=3,action="role",value={"role":"manager"})
    for target in (1,3):
        with pytest.raises(PermissionError):
            change(actor=3,target=target)
    with pytest.raises(PermissionError):
        change(actor=3,target=2,action="role",value={"role":"manager"})
    assert change(actor=3,target=2)["plan"]=="complimentary"
    with pytest.raises(PermissionError):
        change(actor=2,target=4)


def test_stale_update_duplicate_and_reused_id(member_db):
    request_id=str(uuid.uuid4())
    first=change(request_id=request_id)
    assert change(request_id=request_id)==first
    assert len(members.audit(2))==1
    with pytest.raises(members.MemberConflict):
        change()
    with pytest.raises(members.MemberConflict):
        change(request_id=request_id,reason="Changed payload")
    assert len(members.audit(2))==1


def test_role_revocation_and_inactive_actor_rechecked(member_db):
    change(target=3,action="role",value={"role":"manager"})
    change(target=3,action="role",value={"role":"member"},version=1)
    with pytest.raises(PermissionError):
        change(actor=3,target=2)
    with closing(accounts.db()) as conn, conn:
        conn.execute("UPDATE users SET is_active=0 WHERE id=1")
    with pytest.raises(PermissionError):
        change()


def test_no_public_owner_registration_and_owner_not_removable(member_db):
    with pytest.raises(members.MemberConflict):
        members.bootstrap_owner("member2@example.invalid")
    with pytest.raises(PermissionError):
        change(target=1,action="role",value={"role":"member"})


def test_admin_api_auth_csrf_input_and_readonly_listing(member_db):
    app=FastAPI();app.include_router(membership.router)
    client=TestClient(app)
    assert client.get("/api/admin/members").status_code==401
    app.dependency_overrides[membership.current_admin]=lambda:{"id":2}
    assert client.get("/api/admin/members").status_code==403
    app.dependency_overrides[membership.current_admin]=lambda:{"id":1}
    before=accounts.path().read_bytes()
    response=client.get("/api/admin/members")
    assert response.status_code==200 and response.json()["total"]==4
    assert "hashed_password" not in response.text
    assert accounts.path().read_bytes()==before
    assert client.get("/api/admin/members?q=%25").json()["total"]==0
    payload=dict(action="plan",plan="monthly",expires_at=time.time()-1,version=0,reason="QA",request_id=str(uuid.uuid4()))
    assert client.patch("/api/admin/members/2",json=payload).status_code==403
    headers={"X-Equity-Request":"1"}
    assert client.patch("/api/admin/members/2",json=payload,headers=headers).status_code==400
    payload.update(plan="complimentary",expires_at=None)
    assert client.patch("/api/admin/members/2",json=payload,headers={**headers,"Origin":"https://other.invalid"}).status_code==403
    response=client.patch("/api/admin/members/2",json=payload,headers=headers)
    assert response.status_code==200 and response.json()["premium"]
    assert not response.json()["can_manage_members"]
    assert client.patch("/api/admin/members/2",json={**payload,"is_paid":True},headers=headers).status_code==422
    assert json.loads(members.audit(2)[0]["after_json"])["plan"]=="complimentary"


def test_unconfigured_email_fails_without_disclosing_otp(monkeypatch, caplog):
    from auth import email_sender
    monkeypatch.setattr(email_sender, "smtp_configured", lambda: False)
    assert email_sender.send_verification_email("private@example.invalid", "948271") is False
    assert "948271" not in caplog.text and "private@example.invalid" not in caplog.text


def test_web_line_share_expiry_and_revocation(member_db,tmp_path,monkeypatch):
    from services.membership_service import premium_denial,line_denial
    from repository.portfolio_repository import PortfolioStore,initialize
    from services.portfolio_service import PRIVACY_VERSION
    monkeypatch.setenv("EQUITY_USER_DB",str(tmp_path/"portfolio.sqlite3"))
    monkeypatch.setenv("EQUITY_USER_KEY_FILE",str(tmp_path/"portfolio.key"))
    monkeypatch.setenv("EQUITY_MEMBERSHIP_ENFORCEMENT","1")
    initialize();store=PortfolioStore()
    web=store.subject("web","2");line=store.subject("line","synthetic-user")
    store.consent(web,PRIVACY_VERSION)
    members.record_principal(2,web)
    store.consume_link(line,store.issue_link(web))
    event={"source":{"type":"user","userId":"synthetic-user"}}
    assert premium_denial(2) and line_denial(event,"2330 趨勢")
    change()
    assert premium_denial(2) is None and line_denial(event,"2330 趨勢") is None
    change(version=1,value={"plan":"monthly","expires_at":time.time()-1})
    assert premium_denial(2) and line_denial(event,"2330 趨勢")
    assert line_denial(event,"刪除全部持股") is None
    with closing(accounts.db()) as conn,conn:conn.execute("UPDATE users SET is_active=0 WHERE id=2")
    assert "停用" in line_denial(event,"2330 趨勢")
