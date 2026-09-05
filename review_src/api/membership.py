"""Authenticated membership administration; never accepts payment assertions."""

from typing import Literal
from pathlib import Path
import sqlite3
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from auth.dependencies import get_current_user
from api.portfolio import _mutation
from repository import member_repository as members
from services.membership_service import effective_access, get_membership, update_member

router = APIRouter(tags=["membership"])


def membership(user):
    try:
        return get_membership(user["id"])
    except sqlite3.OperationalError as exc:
        raise HTTPException(503, "會員服務尚未完成初始化，請聯絡管理員") from exc


def manager(user=Depends(get_current_user)):
    if not membership(user)["can_manage_members"]:
        raise HTTPException(403, "需要會員管理權限")
    return user


@router.get("/members")
def member_page():
    return FileResponse(Path(__file__).resolve().parents[1] / "static" / "members.html")


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
