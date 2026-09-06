"""Offline auth verification. No real mail, SMS or account data."""
from contextlib import closing
import time
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from core import accounts_database as accounts
from core.portfolio_storage import initialize_key
from auth import router as auth_router, service
from api import phone_verification as phone_api, membership
from adapter import phone_verification as provider
from repository import phone_repository as phones

PASSWORD="SyntheticPassword!2026"
NEW_PASSWORD="ChangedSynthetic!2026"
EMAIL="account@example.invalid"

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setenv("EQUITY_AUTH_DB",str(tmp_path/"accounts.sqlite3"))
    monkeypatch.setenv("EQUITY_USER_KEY_FILE",str(tmp_path/"phone.key"))
    initialize_key();accounts.initialize()
    monkeypatch.setattr(provider,"configured",lambda:True)
    from auth import email_sender
    monkeypatch.setattr(email_sender,"smtp_configured",lambda:True)
    monkeypatch.setattr(service,"send_verification_email",lambda *args:True)
    monkeypatch.setattr(service,"send_password_reset_email",lambda *args:True)
    monkeypatch.setattr(service,"generate_verification_code",lambda:"123456")
    app=FastAPI()
    for router in (auth_router.router,phone_api.router,membership.router):app.include_router(router)
    return TestClient(app,base_url="https://testserver",headers={"X-Equity-Request":"1"})

def register(client):
    response=client.post("/api/auth/register",json={"email":EMAIL,"password":PASSWORD,"confirm_password":PASSWORD})
    assert response.status_code==200,response.text
    assert client.post("/api/auth/verify-email",json={"email":EMAIL,"code":"123456"}).status_code==200
    response=client.post("/api/auth/login",json={"email":EMAIL,"password":PASSWORD})
    assert response.status_code==200,response.text
    return response.json()

def test_confirmation_and_new_member_phone_gate(client):
    assert client.post("/api/auth/register",json={"email":EMAIL,"password":PASSWORD,"confirm_password":NEW_PASSWORD}).status_code==422
    with closing(accounts.db()) as conn:assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]==0
    register(client)
    assert client.get("/api/auth/me").json()["user"]["phone_required"]==1
    assert client.get("/api/membership").status_code==403
    assert client.get("/api/auth/phone").status_code==200

def test_unconfigured_sms_disables_signup_without_writes(client,monkeypatch):
    monkeypatch.setattr(provider,"configured",lambda:False)
    assert not client.get("/api/auth/options").json()["registration_enabled"]
    assert client.post("/api/auth/register",json={"email":EMAIL,"password":PASSWORD,"confirm_password":PASSWORD}).status_code==503
    with closing(accounts.db()) as conn:assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]==0

def test_phone_proof_encrypted_cooldown_no_replay(client,monkeypatch):
    register(client);sent=[]
    monkeypatch.setattr(provider,"send",lambda phone:sent.append(phone) or "VE"+"a"*32)
    monkeypatch.setattr(provider,"check",lambda sid,code:code=="654321")
    assert client.post("/api/auth/phone/start",json={"phone":"0912345678"}).status_code==200
    assert sent==["+886912345678"]
    assert client.post("/api/auth/phone/start",json={"phone":"0912345678"}).status_code==429
    assert len(sent)==1
    assert client.post("/api/auth/phone/verify",json={"code":"000000"}).status_code==400
    assert not phones.verified(1)
    assert client.post("/api/auth/phone/verify",json={"code":"654321"}).status_code==200
    assert phones.verified(1) and client.get("/api/membership").status_code==200
    assert client.post("/api/auth/phone/verify",json={"code":"654321"}).status_code==400
    assert b"912345678" not in accounts.path().read_bytes()

def test_phone_attempt_budget_before_provider(client,monkeypatch):
    register(client);calls=[]
    monkeypatch.setattr(provider,"send",lambda phone:"VE"+"b"*32)
    monkeypatch.setattr(provider,"check",lambda sid,code:calls.append(code) or False)
    client.post("/api/auth/phone/start",json={"phone":"0912345678"})
    for _ in range(6):assert client.post("/api/auth/phone/verify",json={"code":"000000"}).status_code==400
    assert len(calls)==5 and not phones.verified(1)

def test_change_password_confirms_current_and_revokes_tokens(client):
    old=register(client)["access_token"]
    payload={"current_password":"incorrect","new_password":NEW_PASSWORD,"confirm_password":NEW_PASSWORD,"code":"123456"}
    assert client.post("/api/auth/change-password/code",json={}).status_code==200
    assert client.post("/api/auth/change-password",json=payload).status_code==400
    assert client.post("/api/auth/change-password",json={**payload,"confirm_password":PASSWORD}).status_code==422
    payload["current_password"]=PASSWORD
    assert client.post("/api/auth/change-password",json=payload).status_code==200
    assert client.get("/api/auth/me",headers={"Authorization":"Bearer "+old}).status_code==401
    assert client.post("/api/auth/login",json={"email":EMAIL,"password":PASSWORD}).status_code==401
    assert client.post("/api/auth/login",json={"email":EMAIL,"password":NEW_PASSWORD}).status_code==200
    assert client.get("/api/auth/me").status_code==200

def test_password_reset_single_use_cooldown_and_revoke(client):
    old=register(client)["access_token"]
    assert client.post("/api/auth/forgot-password",json={"email":EMAIL}).status_code==200
    assert client.post("/api/auth/forgot-password",json={"email":EMAIL}).status_code==400
    payload={"email":EMAIL,"code":"123456","new_password":NEW_PASSWORD,"confirm_password":NEW_PASSWORD}
    assert client.post("/api/auth/reset-password",json=payload).status_code==200
    assert client.get("/api/auth/me",headers={"Authorization":"Bearer "+old}).status_code==401
    assert client.post("/api/auth/reset-password",json=payload).status_code==400

def test_email_attempts_and_expiry(client):
    client.post("/api/auth/register",json={"email":EMAIL,"password":PASSWORD,"confirm_password":PASSWORD})
    for _ in range(5):assert client.post("/api/auth/verify-email",json={"email":EMAIL,"code":"000000"}).status_code==400
    assert client.post("/api/auth/verify-email",json={"email":EMAIL,"code":"123456"}).status_code==400
    with closing(accounts.db()) as conn,conn:
        conn.execute("UPDATE email_verifications SET attempts=0,expires_at=?",(time.time()-1,))
    assert client.post("/api/auth/verify-email",json={"email":EMAIL,"code":"123456"}).status_code==400


def test_phone_five_per_taiwan_day_before_provider_and_next_day(client,monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from types import SimpleNamespace
    register(client)
    clock=[datetime(2026,9,6,1,tzinfo=ZoneInfo("Asia/Taipei")).timestamp()]
    monkeypatch.setattr(phones,"time",SimpleNamespace(time=lambda:clock[0]))
    sent=[]
    monkeypatch.setattr(provider,"send",lambda phone:sent.append(phone) or "VE"+"a"*32)
    for _ in range(5):
        assert client.post("/api/auth/phone/start",json={"phone":"0912345678"}).status_code==200
        clock[0]+=3601
    response=client.post("/api/auth/phone/start",json={"phone":"+886912345678"})
    assert response.status_code==429 and "今日" in response.json()["detail"]
    assert len(sent)==5
    clock[0]=datetime(2026,9,7,0,1,tzinfo=ZoneInfo("Asia/Taipei")).timestamp()
    assert client.post("/api/auth/phone/start",json={"phone":"0912345678"}).status_code==200
    assert len(sent)==6


def test_phone_daily_limit_shared_between_accounts(client,monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from types import SimpleNamespace
    register(client)
    clock=[datetime(2026,9,6,12,tzinfo=ZoneInfo("Asia/Taipei")).timestamp()]
    monkeypatch.setattr(phones,"time",SimpleNamespace(time=lambda:clock[0]))
    with closing(accounts.db()) as conn,conn:
        for n in range(2,7):
            conn.execute("INSERT INTO users(id,email,hashed_password,is_verified,is_active,created_at,updated_at) VALUES(?,?,?,1,1,1,1)",(n,f"shared{n}@example.invalid","synthetic"))
    # All reservations occur within the same calendar day, independent of user id.
    for n in range(1,6):
        phones.reserve(n,"+886912345678")
        clock[0]+=61
    with pytest.raises(ValueError,match="今日"):phones.reserve(6,"+886912345678")
