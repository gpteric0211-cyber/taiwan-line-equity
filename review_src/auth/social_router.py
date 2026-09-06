"""Social login/link flow with browser binding, one-use state and strict provider checks."""

import hmac
import secrets
from pathlib import Path
from urllib.parse import parse_qs
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from typing import Literal
from adapter import social_login as provider_api
from repository import social_identity as identities
from auth.dependencies import get_current_user, get_client_ip
from auth.security import create_jwt, decode_jwt, hash_token
from auth import service
from auth.social_session import issue_session
from auth.access_log import install

install()

router = APIRouter(prefix="/social")
COOKIE = "__Host-equity-oauth"


class Start(BaseModel):
    action: Literal["login", "link"] = "login"
    password: str = Field(default="", max_length=128)


def _password(user, password, request):
    ip = get_client_ip(request)
    if service.too_many_failed_logins(ip,user["email"]):
        raise HTTPException(429,"嘗試次數過多，請稍後再試")
    if not identities.password_matches(user["id"],password):
        service.record_login_attempt(ip,user["email"],False,"social_link")
        raise HTTPException(400,"目前密碼錯誤")


@router.get("/options")
def options():
    return {"items": provider_api.options()}


@router.get("/links")
def links(user=Depends(get_current_user)):
    return {"items": identities.linked(user["id"])}


@router.post("/{provider}/start")
def start(provider: str, payload: Start, request: Request, response: Response):
    from api.portfolio import _mutation
    _mutation(request)
    try:
        cfg = provider_api.config(provider)
    except ValueError as exc:
        raise HTTPException(503,str(exc)) from exc
    if str(request.base_url).rstrip("/") != cfg["redirect"].split("/api/")[0]:
        raise HTTPException(400,"請從正式網站網址登入")
    user = None
    if payload.action == "link":
        user = get_current_user(request,None)
        _password(user,payload.password,request)
    state,nonce,verifier = (secrets.token_urlsafe(32) for _ in range(3))
    identity = {"provider":provider,"state":state,"nonce":nonce,"verifier":verifier,
                "action":payload.action,"cv":user["credential_version"] if user else None}
    ticket = create_jwt(user["id"] if user else "login",token_type="oauth",expires_seconds=600,extra=identity)
    identities.remember_state(state)
    response.set_cookie(COOKIE,ticket,max_age=600,secure=True,httponly=True,samesite="none",path="/")
    response.headers["Cache-Control"]="no-store"
    return {"url":provider_api.authorization_url(provider,state,nonce,verifier)}


@router.get("/{provider}/return")
def returned(provider: str):
    if provider not in {"google","line"}:
        raise HTTPException(404)
    return FileResponse(Path(__file__).resolve().parents[1]/"static"/"social-return.html",
                        headers={"Cache-Control":"no-store","Referrer-Policy":"no-referrer"})


@router.post("/{provider}/callback")
async def callback(provider: str, request: Request):
    ticket = decode_jwt(request.cookies.get(COOKIE,""))
    if not ticket or ticket.get("type")!="oauth" or ticket.get("provider")!=provider:
        raise HTTPException(400,"登入流程已過期，請重試")
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw)>16384:
            raise HTTPException(413,"登入資料過長")
    try:
        values = parse_qs(raw.decode(),strict_parsing=True)
        state = values.get("state",[""])[0]
        code = values.get("code",[""])[0]
        if (len(values.get("state",[]))!=1 or len(values.get("code",[]))!=1
                or not hmac.compare_digest(state,ticket["state"]) or not code
                or not identities.consume_state(state)):
            raise ValueError("登入流程已過期，請重試")
        subject = await run_in_threadpool(provider_api.verify_identity,provider,code,ticket["nonce"],ticket["verifier"])
        digest = identities.identity_hash(provider,provider_api.config(provider)["client"],subject)
        linking = ticket["action"]=="link"
        user_id = identities.resolve(provider,digest,int(ticket["sub"]) if linking else None,ticket.get("cv"))
        result = RedirectResponse("/account#security",status_code=303)
        if not linking:
            from auth.router import _set_auth_cookies
            access,session = issue_session(user_id)
            _set_auth_cookies(result,access,session,secure=True)
    except Exception:
        # Never return provider responses, authorization codes or identity claims.
        result = RedirectResponse("/account#social-error",status_code=303)
    result.delete_cookie(COOKIE,path="/",secure=True,samesite="none")
    result.headers["Cache-Control"]="no-store"
    result.headers["Referrer-Policy"]="no-referrer"
    return result


@router.post("/{provider}/unlink")
def unlink(provider: str, payload: Start, request: Request, user=Depends(get_current_user)):
    from api.portfolio import _mutation
    _mutation(request)
    if provider not in provider_api.PROVIDERS:
        raise HTTPException(404)
    _password(user,payload.password,request)
    identities.unlink(user["id"],provider)
    return {"ok":True,"message":"已解除綁定，仍可使用 Email 與密碼登入"}
