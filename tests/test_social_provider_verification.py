"""Locally signed OIDC fixtures test signature and claim rejection, without network."""

import json
import time
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from adapter import social_login, phone_verification


@pytest.mark.parametrize("provider",["google","apple"])
@pytest.mark.parametrize("invalid",["none","signature","aud","iss","exp","nonce"])
def test_oidc_signature_and_required_claims(monkeypatch,provider,invalid):
    monkeypatch.setenv("EQUITY_AUTH_PUBLIC_URL","https://example.invalid")
    monkeypatch.setenv("EQUITY_OAUTH_"+provider.upper()+"_CLIENT_ID","synthetic")
    monkeypatch.setenv("EQUITY_OAUTH_"+provider.upper()+"_CLIENT_SECRET","secret")
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    jwk=json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()));jwk["kid"]="qa"
    claims=dict(sub="subject",iss=social_login.PROVIDERS[provider][4],aud="synthetic",iat=int(time.time()),exp=int(time.time())+300,nonce="nonce")
    if invalid in {"aud","iss","nonce"}:claims[invalid]="wrong"
    if invalid=="exp":claims["exp"]=int(time.time())-60
    signer=rsa.generate_private_key(public_exponent=65537,key_size=2048) if invalid=="signature" else key
    token=jwt.encode(claims,signer,algorithm="RS256",headers={"kid":"qa"})
    monkeypatch.setattr(social_login,"_json_request",lambda method,*a,**kw:{"id_token":token} if method=="POST" else {"keys":[jwk]})
    if invalid=="none":
        assert social_login.verify_identity(provider,"code","nonce","verifier")=="subject"
    else:
        with pytest.raises((ValueError,jwt.PyJWTError)):social_login.verify_identity(provider,"code","nonce","verifier")


def test_paid_twilio_account_is_blocked_before_sms(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setenv("PHONE_VERIFICATION_MODE","trial")
    monkeypatch.setenv("TWILIO_ACCOUNT_SID","AC"+"a"*32)
    monkeypatch.setenv("TWILIO_AUTH_TOKEN","synthetic")
    monkeypatch.setenv("TWILIO_VERIFY_SERVICE_SID","VA"+"b"*32)
    monkeypatch.setattr(phone_verification.requests,"get",lambda *a,**kw:SimpleNamespace(raise_for_status=lambda:None,json=lambda:{"type":"Full"}))
    calls=[]
    monkeypatch.setattr(phone_verification,"_call",lambda *a:calls.append(a))
    with pytest.raises(phone_verification.PhoneUnavailable,match="付費"):phone_verification.send("+886912345678")
    assert calls==[]


def test_oauth_access_log_removes_authorization_code():
    import logging
    from auth.access_log import OAuthQueryFilter
    record=logging.LogRecord("uvicorn.access",20,"",0,'%s "%s %s HTTP/%s" %d',("client","GET","/api/auth/social/google/return?code=secret-code&state=secret-state","1.1",200),None)
    OAuthQueryFilter().filter(record)
    assert "secret-code" not in record.getMessage() and "secret-state" not in record.getMessage()
