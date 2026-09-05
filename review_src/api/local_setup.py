"""First account creation is available only from the local machine."""

from __future__ import annotations
from contextlib import closing
import re
import time
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from core.accounts_database import db
from auth.security import hash_password, password_policy_error

router = APIRouter(prefix="/api/setup", tags=["local-setup"])


def local_request(request: Request) -> bool:
    return bool(
        request.client
        and request.client.host in {"127.0.0.1", "::1"}
        and request.url.hostname in {"127.0.0.1", "localhost", "::1"}
    )


@router.get("")
def setup_status(request: Request):
    if not local_request(request):
        return {"setup_required": False}
    with closing(db()) as conn:
        return {"setup_required": conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0}


class SetupInput(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=10, max_length=128)


@router.post("")
def create_first_account(payload: SetupInput, request: Request):
    from api.portfolio import _mutation

    _mutation(request)
    if not local_request(request):
        raise HTTPException(403, "首次帳號必須在主機本機建立")
    email = payload.email.strip().lower()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise HTTPException(400, "Email 格式不正確")
    error = password_policy_error(payload.password)
    if error:
        raise HTTPException(400, error)
    hashed = hash_password(payload.password)
    now = time.time()
    with closing(db()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
            raise HTTPException(409, "已有帳號，請直接登入")
        conn.execute(
            "INSERT INTO users(email,hashed_password,is_verified,is_active,created_at,updated_at) VALUES(?,?,1,1,?,?)",
            (email, hashed, now, now),
        )
    return {"ok": True}
