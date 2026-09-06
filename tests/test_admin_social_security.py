"""Synthetic identities only; no production DB, mail, SMS or OAuth calls."""

from contextlib import closing
import time
import uuid
from urllib.parse import urlsplit, parse_qs
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from core import accounts_database as accounts
from auth import router, service, admin_session, social_router
from auth.security import hash_password, create_jwt
from api import membership
from repository import member_repository, social_identity
from adapter import social_login

PASSWORD="SyntheticAdmin!2026"


@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setenv("EQUITY_AUTH_DB",str(tmp_path/"accounts.sqlite3"))
    accounts.initialize()
    with closing(accounts.db()) as conn,conn:
        for n in (1,2,3):
            conn.execute("INSERT INTO users(id,email,hashed_password,is_verified,is_active,created_at,updated_at) VALUES(?,?,?,1,1,1,1)",(n,f"qa{n}@example.invalid",hash_password(PASSWORD)))
            conn.execute("INSERT INTO account_security(user_id,phone_required) VALUES(?,1)",(n,))
    member_repository.bootstrap_owner("qa1@example.invalid")
    app=FastAPI();app.include_router(router.router);app.include_router(membership.router)
    return TestClient(app,base_url="https://testserver",headers={"X-Equity-Request":"1"})


def login(client,n=1):
    return client.post("/api/admin/login",json={"email":f"qa{n}@example.invalid","password":PASSWORD})


def test_admin_phone_exemption_separate_cookie_and_explicit_logout(client):
    assert login(client).status_code==200
    token=client.cookies.get(admin_session.COOKIE)
    assert token
    assert client.get("/api/admin/me").status_code==200
    assert client.get("/api/auth/me").status_code==401
    assert client.get("/api/membership").status_code==401
    assert client.post("/api/admin/logout",json={}).status_code==200
    assert client.get("/api/admin/me",headers={"Cookie":f"{admin_session.COOKIE}={token}"}).status_code==401
    with closing(accounts.db()) as conn:
        assert [r[0] for r in conn.execute("SELECT action FROM admin_event ORDER BY id")]==["login","logout"]
        assert conn.execute("SELECT phone_required FROM account_security WHERE user_id=1").fetchone()[0]==1


def test_customer_token_cannot_enter_admin_even_owner(client):
    response=client.post("/api/auth/login",json={"email":"qa1@example.invalid","password":PASSWORD})
    assert response.status_code==200
    assert client.get("/api/admin/members").status_code==401
    assert client.get("/api/membership").status_code==403
    assert login(client,2).status_code==401


def test_admin_role_revoke_password_rotate_and_audit_changes(client):
    assert login(client).status_code==200
    payload=dict(action="role",role="manager",version=0,reason="QA manager",request_id=str(uuid.uuid4()))
    assert client.patch("/api/admin/members/2",json=payload).status_code==200
    history=client.get("/api/admin/events").json()["items"]
    assert any(x["action"]=="role" and x["target_id"]==2 and "manager" in x["after_json"] for x in history)
    assert login(client,2).status_code==200
    assert client.get("/api/admin/events").status_code==403
    assert client.patch("/api/admin/members/3",json={**payload,"request_id":str(uuid.uuid4())}).status_code==403
    with closing(accounts.db()) as conn,conn:
        conn.execute("UPDATE member_access SET role='member' WHERE user_id=2")
    assert client.get("/api/admin/me").status_code==401
    login(client)
    with closing(accounts.db()) as conn,conn:
        conn.execute("UPDATE account_security SET credential_version=credential_version+1 WHERE user_id=1")
    assert client.get("/api/admin/me").status_code==401


def test_password_change_requires_purpose_correct_single_use_email(client,monkeypatch):
    monkeypatch.setattr(service,"generate_verification_code",lambda:"123456")
    monkeypatch.setattr(service,"send_password_reset_email",lambda *a:True)
    from auth import email_sender
    monkeypatch.setattr(email_sender,"smtp_configured",lambda:True)
    client.post("/api/auth/login",json={"email":"qa1@example.invalid","password":PASSWORD})
    payload={"current_password":PASSWORD,"new_password":"ChangedSecret!2026","confirm_password":"ChangedSecret!2026","code":"123456"}
    assert client.post("/api/auth/change-password",json=payload).status_code==400
    service._store_verification_code("qa1@example.invalid","reset_password","123456")
    assert client.post("/api/auth/change-password",json=payload).status_code==400
    assert client.post("/api/auth/change-password/code",json={}).status_code==200
    assert client.post("/api/auth/change-password/code",json={}).status_code==429
    assert client.post("/api/auth/change-password",json={**payload,"code":"000000"}).status_code==400
    assert client.post("/api/auth/change-password",json=payload).status_code==200
    assert client.post("/api/auth/reset-password",json={"email":"qa1@example.invalid","code":"123456","new_password":PASSWORD,"confirm_password":PASSWORD}).status_code==400


def social_config(monkeypatch,provider="google"):
    monkeypatch.setenv("EQUITY_AUTH_PUBLIC_URL","https://testserver")
    monkeypatch.setenv("EQUITY_OAUTH_"+provider.upper()+"_CLIENT_ID","synthetic-client")
    monkeypatch.setenv("EQUITY_OAUTH_"+provider.upper()+"_CLIENT_SECRET","synthetic-secret")


def web_login(client,n=1):
    with closing(accounts.db()) as conn,conn:
        conn.execute("UPDATE account_security SET phone_required=0 WHERE user_id=?",(n,))
    assert client.post("/api/auth/login",json={"email":f"qa{n}@example.invalid","password":PASSWORD}).status_code==200


def test_social_disabled_and_get_returns_no_db_write(client):
    assert client.post("/api/auth/social/google/start",json={}).status_code==503
    before=accounts.path().read_bytes()
    assert client.get("/api/auth/social/google/return?code=synthetic&state=abc").status_code==200
    assert accounts.path().read_bytes()==before


@pytest.mark.parametrize("provider",["google","apple","line"])
def test_social_binding_login_replay_and_unlink(client,monkeypatch,provider):
    social_config(monkeypatch,provider);web_login(client)
    monkeypatch.setattr(social_login,"verify_identity",lambda *a:"synthetic-subject")
    start=client.post(f"/api/auth/social/{provider}/start",json={"action":"link","password":PASSWORD})
    assert start.status_code==200,start.text
    query=parse_qs(urlsplit(start.json()["url"]).query)
    assert query["nonce"] and query["state"]
    if provider!="apple":assert query["code_challenge_method"]==["S256"]
    cookie=client.cookies.get(social_router.COOKIE)
    state=query["state"][0]
    assert client.post(f"/api/auth/social/{provider}/callback",data={"code":"synthetic","state":"bad"},follow_redirects=False).status_code==303
    client.cookies.set(social_router.COOKIE,cookie)
    result=client.post(f"/api/auth/social/{provider}/callback",data={"code":"synthetic","state":state},follow_redirects=False)
    assert result.headers["location"]=="/account#security"
    assert social_identity.linked(1)==[provider]
    client.cookies.clear()
    client.cookies.set(social_router.COOKIE,cookie)
    assert client.post(f"/api/auth/social/{provider}/callback",data={"code":"synthetic","state":state},follow_redirects=False).headers["location"]=="/account#social-error"
    start=client.post(f"/api/auth/social/{provider}/start",json={})
    state=parse_qs(urlsplit(start.json()["url"]).query)["state"][0]
    assert client.post(f"/api/auth/social/{provider}/callback",data={"code":"synthetic","state":state},follow_redirects=False).headers["location"]=="/account#security"
    assert client.get("/api/auth/me").status_code==200
    assert client.post(f"/api/auth/social/{provider}/unlink",json={"password":"wrong"}).status_code==400
    assert client.post(f"/api/auth/social/{provider}/unlink",json={"password":PASSWORD}).status_code==200
    assert social_identity.linked(1)==[]


def test_social_no_email_automerge_cross_account_and_rotated_credentials(client):
    digest=social_identity.identity_hash("google","client","subject")
    with pytest.raises(ValueError):social_identity.resolve("google",digest)
    social_identity.resolve("google",digest,1,0)
    with pytest.raises(ValueError):social_identity.resolve("google",digest,2,0)
    with pytest.raises(ValueError):social_identity.resolve("google","another",1,0)
    with closing(accounts.db()) as conn,conn:conn.execute("UPDATE account_security SET credential_version=1 WHERE user_id=3")
    with pytest.raises(ValueError):social_identity.resolve("google","third",3,0)
