"""Authenticated membership administration; never accepts payment assertions."""

from typing import Literal
from pathlib import Path
import sqlite3
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from auth.dependencies import get_current_user
from auth.admin_session import current_admin
from auth.schemas import LoginRequest
from api.portfolio import _mutation
from repository import member_repository as members
from services.membership_service import effective_access, get_membership, update_member

router = APIRouter(tags=["membership"])


def membership(user):
    try:
        return get_membership(user["id"])
    except sqlite3.OperationalError as exc:
        raise HTTPException(503, "會員服務尚未完成初始化，請聯絡管理員") from exc


def manager(user=Depends(current_admin)):
    if not membership(user)["can_manage_members"]:
        raise HTTPException(403, "需要會員管理權限")
    return user


@router.post("/api/admin/login")
def admin_login(payload: LoginRequest, request: Request, response: Response):
    from auth import admin_session
    from auth.dependencies import get_client_ip
    from auth.security import AUTH_COOKIE_SECURE
    _mutation(request)
    token=admin_session.login(payload.email,payload.password,get_client_ip(request),request)
    response.set_cookie(admin_session.COOKIE,token,max_age=admin_session.TTL,httponly=True,
                        secure=AUTH_COOKIE_SECURE or request.url.scheme=="https",samesite="strict",path="/api/admin")
    response.headers["Cache-Control"]="no-store"
    return {"ok":True}


@router.post("/api/admin/logout")
def admin_logout(request: Request, response: Response):
    from auth import admin_session
    _mutation(request)
    admin_session.logout(request.cookies.get(admin_session.COOKIE,""))
    response.delete_cookie(admin_session.COOKIE,path="/api/admin")
    return {"ok":True}


@router.get("/api/admin/me")
def admin_me(user=Depends(manager)):
    return membership(user)


@router.get("/api/admin/events")
def admin_events(offset: int = Query(default=0,ge=0),user=Depends(manager)):
    from auth.admin_session import events
    if user["role"]!="owner":
        raise HTTPException(403,"只有擁有者可以檢視完整管理紀錄")
    return {"items":events(offset)}


@router.get("/members")
def member_page():
    return FileResponse(Path(__file__).resolve().parents[1] / "static" / "members.html")


@router.post("/api/admin/password/link")
def admin_password_link(request: Request, response: Response, user=Depends(manager)):
    from auth.admin_password import request_link
    _mutation(request)
    response.headers["Cache-Control"] = "no-store"
    return {"message": request_link(user["id"])}


class AdminPasswordChange(BaseModel):
    model_config = {"extra": "forbid"}
    token: str = Field(min_length=43, max_length=43, pattern=r"^[A-Za-z0-9_-]+$")
    new_password: str = Field(min_length=10, max_length=128)
    confirm_password: str = Field(min_length=10, max_length=128)


@router.post("/api/admin/password/complete")
def admin_password_complete(payload: AdminPasswordChange, request: Request, response: Response):
    from auth.admin_password import complete
    from auth.admin_session import COOKIE
    _mutation(request)
    message = complete(payload.token, payload.new_password, payload.confirm_password)
    response.delete_cookie(COOKIE, path="/api/admin")
    response.headers["Cache-Control"] = "no-store"
    return {"message": message}


@router.get("/admin/password")
def admin_password_page():
    return FileResponse(Path(__file__).resolve().parents[1] / "static" / "admin-password.html",
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
                 "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})


@router.get("/api/membership")
def mine(user=Depends(get_current_user)):
    return membership(user)


@router.get("/api/admin/members")
def member_list(q: str = Query(default="", max_length=254), offset: int = Query(default=0, ge=0),
                limit: int = Query(default=30, ge=1, le=100), user=Depends(manager)):
    result = members.list_members(q, offset, limit)
    result["items"] = [effective_access(item) for item in result["items"]]
    return result


@router.get("/api/admin/members/{user_id}/audit")
def member_audit(user_id: int, user=Depends(manager)):
    return {"items": members.audit(user_id)}


class MemberChange(BaseModel):
    model_config = {"extra": "forbid"}
    action: Literal["plan", "role"]
    plan: Literal["free", "monthly", "complimentary"] = "free"
    role: Literal["member", "manager"] = "member"
    expires_at: float | None = None
    version: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=500)
    request_id: str = Field(min_length=16, max_length=100)


@router.patch("/api/admin/members/{user_id}")
def member_change(user_id: int, payload: MemberChange, request: Request, user=Depends(manager)):
    _mutation(request)
    try:
        return update_member(user["id"], user_id, payload)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except members.MemberConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
