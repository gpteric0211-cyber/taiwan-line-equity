"""Isolated local owner provisioning and emailed credential lifecycle tests."""

from contextlib import closing
import json
import time
import uuid
from urllib.parse import urlsplit, parse_qs
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from core import accounts_database as accounts
from auth import admin_password, admin_session, email_sender, router, service
from auth.local_owner import create_owner
from auth.security import hash_password, hash_token, verify_password
from api import membership

EMAIL = "local-owner@example.invalid"
PASSWORD = "temporary"
NEW_PASSWORD = "ChangedPassword!2026"


@pytest.fixture
def blank(tmp_path, monkeypatch):
    monkeypatch.setenv("EQUITY_AUTH_DB", str(tmp_path / "accounts.sqlite3"))
    monkeypatch.setenv("EQUITY_AUTH_PUBLIC_URL", "https://testserver")
    monkeypatch.setattr(email_sender, "smtp_configured", lambda: False)
    accounts.initialize()


@pytest.fixture
def client(blank):
    create_owner(EMAIL, PASSWORD, allow_temporary_password=True)
    app = FastAPI()
    @app.middleware("http")
    async def synthetic_loopback(request, call_next):
        request.scope["client"] = ("127.0.0.1", 12345)
        return await call_next(request)
    app.include_router(router.router)
    app.include_router(membership.router)
    with TestClient(app, base_url="http://127.0.0.1", headers={"X-Equity-Request":"1"}) as result:
        yield result


def login(client, password=PASSWORD):
    return client.post("/api/admin/login", json={"email":EMAIL,"password":password})


@pytest.fixture
def mail(monkeypatch):
    delivered = []
    monkeypatch.setattr(email_sender, "smtp_configured", lambda: True)
    monkeypatch.setattr(email_sender, "send_password_change_link", lambda address,url: delivered.append((address,url)) or True)
    return delivered


def issue(client, mail):
    assert login(client).status_code == 200
    response = client.post("/api/admin/password/link", json={})
    assert response.status_code == 200, response.text
    address, url = mail[-1]
    assert address == EMAIL and url.startswith("https://testserver/admin/password#token=")
    token = parse_qs(urlsplit(url).fragment)["token"][0]
    assert token not in response.text
    return token


def finish(client, token, **values):
    return client.post("/api/admin/password/complete", json={"token":token,"new_password":NEW_PASSWORD,"confirm_password":NEW_PASSWORD,**values})


def test_local_creation_default_policy_and_duplicate_guards(blank):
    with pytest.raises(ValueError):
        create_owner(EMAIL, PASSWORD)
    create_owner(EMAIL, PASSWORD, allow_temporary_password=True)
    with pytest.raises(ValueError, match="已有擁有者"):
        create_owner("another@example.invalid", NEW_PASSWORD)
    with closing(accounts.db()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM email_verifications").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM member_audit WHERE action='bootstrap_owner'").fetchone()[0] == 1


def test_existing_account_is_never_overwritten(blank):
    with closing(accounts.db()) as conn, conn:
        conn.execute("INSERT INTO users(email,hashed_password,is_verified,is_active,created_at,updated_at) VALUES(?,?,0,1,1,1)", (EMAIL,hash_password(NEW_PASSWORD)))
    with pytest.raises(ValueError, match="帳號已存在"):
        create_owner(EMAIL, PASSWORD, allow_temporary_password=True)
    with closing(accounts.db()) as conn:
        assert verify_password(NEW_PASSWORD, conn.execute("SELECT hashed_password FROM users").fetchone()[0])
        assert conn.execute("SELECT COUNT(*) FROM member_access").fetchone()[0] == 0


def test_local_login_without_smtp_or_email_verification_can_manage(client):
    assert login(client).status_code == 200
    assert client.get("/api/admin/me").json()["role"] == "owner"
    assert client.get("/api/admin/members").status_code == 200
    with closing(accounts.db()) as conn, conn:
        conn.execute("INSERT INTO users(email,hashed_password,is_verified,is_active,created_at,updated_at) VALUES('member@example.invalid','unused',1,1,1,1)")
    payload = dict(action="plan",plan="complimentary",version=0,reason="test invitation",request_id=str(uuid.uuid4()))
    assert client.patch("/api/admin/members/2",json=payload).status_code == 200
    assert client.get("/api/auth/me").status_code == 401
    assert client.post("/api/auth/login",json={"email":EMAIL,"password":PASSWORD}).status_code != 200
    assert client.post("/api/admin/password/link",json={}).status_code == 503
    with closing(accounts.db()) as conn:
        user = conn.execute("SELECT * FROM users WHERE email=?",(EMAIL,)).fetchone()
        assert user["is_verified"] == 0 and user["last_login_at"]
        assert conn.execute("SELECT phone_required FROM account_security WHERE user_id=1").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM admin_password_link").fetchone()[0] == 0
    assert client.post("/api/admin/logout",json={}).status_code == 200
    assert client.get("/api/admin/me").status_code == 401


@pytest.mark.parametrize("headers", [
    {"Host":"public.example.invalid"}, {"X-Forwarded-For":"127.0.0.1"},
    {"Forwarded":"for=127.0.0.1"}, {"X-Forwarded-Host":"localhost"},
    {"X-Forwarded-Proto":"https"}, {"CF-Connecting-IP":"127.0.0.1"}, {"CF-Ray":"synthetic"},
])
def test_initial_password_and_cookie_cannot_use_public_or_proxy(client, headers):
    assert login(client).status_code == 200
    assert client.get("/api/admin/me",headers=headers).status_code == 401
    assert client.post("/api/admin/login",headers=headers,json={"email":EMAIL,"password":PASSWORD}).status_code == 401


def test_public_registration_cannot_replace_local_owner_password(client, monkeypatch):
    monkeypatch.setattr(service,"send_verification_email",lambda *a:pytest.fail("must not send"))
    assert service.create_user(EMAIL, NEW_PASSWORD,"synthetic-ip")[0] is False
    assert login(client).status_code == 200
    with closing(accounts.db()) as conn, conn:
        conn.execute("UPDATE users SET is_verified=1 WHERE id=1")
    # Even another Email verification flow cannot expose a temporary password.
    assert client.get("/api/admin/me", headers={"Host":"public.example.invalid"}).status_code == 401


def test_link_get_is_readonly_complete_verifies_email_and_revokes_sessions(client, mail):
    token = issue(client, mail)
    cookie = client.cookies.get(admin_session.COOKIE)
    with closing(accounts.db()) as conn:
        before = [tuple(r) for r in conn.execute("SELECT * FROM admin_password_link")]
        assert before[0][0] == hash_token(token) and token not in str(before)
    response = client.get("/admin/password")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    with closing(accounts.db()) as conn:
        assert before == [tuple(r) for r in conn.execute("SELECT * FROM admin_password_link")]
    assert finish(client,token,confirm_password="DifferentPassword2026").status_code == 400
    assert finish(client,token).status_code == 200
    assert finish(client,token).status_code == 400
    assert client.get("/api/admin/me",headers={"Cookie":f"{admin_session.COOKIE}={cookie}"}).status_code == 401
    assert login(client).status_code == 401
    assert client.post("/api/admin/login",headers={"Host":"public.example.invalid"},json={"email":EMAIL,"password":NEW_PASSWORD}).status_code == 200
    with closing(accounts.db()) as conn:
        assert conn.execute("SELECT is_verified FROM users WHERE id=1").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM local_admin_identity").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM admin_event WHERE action='password_change'").fetchone()[0] == 1


@pytest.mark.parametrize("invalidate", ["expiry","credential","role","inactive","email"])
def test_invalidated_link_cannot_change_password(client,mail,invalidate):
    token = issue(client,mail)
    with closing(accounts.db()) as conn, conn:
        conn.execute({"expiry":"UPDATE admin_password_link SET expires_at=1",
                      "credential":"UPDATE account_security SET credential_version=1",
                      "role":"UPDATE member_access SET role='member'",
                      "inactive":"UPDATE users SET is_active=0",
                      "email":"UPDATE users SET email='changed@example.invalid'"}[invalidate])
    assert finish(client,token).status_code == 400
    with closing(accounts.db()) as conn:
        assert verify_password(PASSWORD,conn.execute("SELECT hashed_password FROM users").fetchone()[0])


def test_resend_replaces_old_link_and_rate_limits(client,mail,monkeypatch):
    token = issue(client,mail)
    assert client.post("/api/admin/password/link",json={}).status_code == 429
    now = time.time()
    for n in range(1,5):
        monkeypatch.setattr(admin_password.time,"time",lambda n=n:now+61*n)
        assert client.post("/api/admin/password/link",json={}).status_code == 200
    monkeypatch.setattr(admin_password.time,"time",lambda:now+61*5)
    assert client.post("/api/admin/password/link",json={}).status_code == 429
    assert finish(client,token).status_code == 400
    latest = parse_qs(urlsplit(mail[-1][1]).fragment)["token"][0]
    assert finish(client,latest).status_code == 200


def test_mail_failure_invalidates_link_and_no_unauthenticated_send(client,monkeypatch):
    monkeypatch.setattr(email_sender,"smtp_configured",lambda:True)
    monkeypatch.setattr(email_sender,"send_password_change_link",lambda *a:False)
    assert client.post("/api/admin/password/link",json={}).status_code == 401
    login(client)
    assert client.post("/api/admin/password/link",json={}).status_code == 503
    with closing(accounts.db()) as conn:
        assert conn.execute("SELECT used_at FROM admin_password_link").fetchone()[0] is not None


def test_origin_comes_from_configuration_and_ready_supervisor_only(blank,tmp_path,monkeypatch):
    for origin in ("http://example.invalid", "https://user@example.invalid", "https://example.invalid/path", "https://example.invalid?x=1"):
        monkeypatch.setenv("EQUITY_AUTH_PUBLIC_URL",origin)
        with pytest.raises(HTTPException): admin_password.public_origin()
    monkeypatch.setenv("EQUITY_AUTH_PUBLIC_URL", "")
    state = tmp_path / "endpoint.json"
    monkeypatch.setenv("EQUITY_PUBLIC_ENDPOINT_FILE",str(state))
    state.write_text(json.dumps({"status":"ready","origin":"https://configured.example.invalid"}))
    assert admin_password.public_origin() == "https://configured.example.invalid"
    state.write_text(json.dumps({"status":"starting","origin":"https://configured.example.invalid"}))
    with pytest.raises(HTTPException): admin_password.public_origin()


def test_email_body_contains_link_not_otp(monkeypatch):
    sent = []
    monkeypatch.setattr(email_sender,"_send_email",lambda *args:sent.append(args) or True)
    assert email_sender.send_password_change_link(EMAIL,"https://example.invalid/admin/password#token=synthetic")
    assert 'href="https://example.invalid/admin/password#token=synthetic"' in sent[0][2]
    assert "15 分鐘" in sent[0][2]


def test_production_app_gate_allows_independent_admin_flow(blank,mail,tmp_path,monkeypatch):
    """Exercise real create_app middleware, without importing the live market app."""
    import sqlite3
    import sys
    from types import SimpleNamespace
    from core import config
    from equity.application import create_app
    market = tmp_path / "market.sqlite3"
    with closing(sqlite3.connect(market)) as conn:
        conn.execute("CREATE TABLE synthetic(id INTEGER)")
        conn.commit()
    monkeypatch.setattr(config,"DB_PATH",market)
    create_owner(EMAIL,PASSWORD,allow_temporary_password=True)
    legacy = FastAPI()
    legacy.include_router(router.router)
    @legacy.get("/api/quotes")
    def quotes():
        return {"synthetic":True}
    @legacy.middleware("http")
    async def loopback(request,call_next):
        request.scope["client"]=("127.0.0.1",12345)
        return await call_next(request)
    monkeypatch.setitem(sys.modules,"app",SimpleNamespace(app=legacy))
    app = create_app()
    with TestClient(app,base_url="http://127.0.0.1",headers={"X-Equity-Request":"1"}) as client:
        assert client.get("/api/admin/me").status_code==401
        assert client.get("/api/quotes").status_code==401
        token=issue(client,mail)
        assert client.get("/api/admin/members").status_code==200
        assert client.get("/api/quotes").status_code==401
        assert client.get("/admin/password").headers["Referrer-Policy"]=="no-referrer"
        assert finish(client,token).status_code==200
        assert client.get("/api/admin/members").status_code==401
        assert login(client,NEW_PASSWORD).status_code==200
        assert client.post("/api/admin/logout",json={}).status_code==200
