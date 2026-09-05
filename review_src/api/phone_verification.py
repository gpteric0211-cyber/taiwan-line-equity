"""Phone proof is required after email verification, before new members use APIs."""

import re
from fastapi import APIRouter,Depends,HTTPException,Request
from pydantic import BaseModel,Field
from auth.dependencies import get_current_user
from api.portfolio import _mutation
from adapter import phone_verification as provider
from repository import phone_repository as phones

router=APIRouter(prefix="/api/auth/phone",tags=["phone-verification"])


class PhoneInput(BaseModel):
    phone: str=Field(min_length=10,max_length=24)


class CodeInput(BaseModel):
    code: str=Field(pattern=r"^[0-9]{4,10}$")


@router.get("")
def status(user=Depends(get_current_user)):
    return {"configured":provider.configured(),"verified":phones.verified(user["id"])}


@router.post("/start")
def start(payload:PhoneInput,request:Request,user=Depends(get_current_user)):
    _mutation(request)
    phone=re.sub(r"[\s()-]","",payload.phone)
    if re.fullmatch(r"09[0-9]{8}",phone):phone="+886"+phone[1:]
    if not re.fullmatch(r"\+8869[0-9]{8}",phone):
        raise HTTPException(400,"請輸入台灣手機號碼，例如 09xxxxxxxx")
    if not provider.configured():raise HTTPException(503,"手機驗證尚未開通，請聯絡管理員")
    try:
        request_id=phones.reserve(user["id"],phone)
        sid=provider.send(phone)
        phones.attach(user["id"],request_id,sid)
        return {"ok":True,"message":"驗證簡訊已送出，請於 10 分鐘內輸入","retry_after":60}
    except provider.PhoneUnavailable as exc:raise HTTPException(503,str(exc)) from exc
    except ValueError as exc:raise HTTPException(429,str(exc)) from exc


@router.post("/verify")
def verify(payload:CodeInput,request:Request,user=Depends(get_current_user)):
    _mutation(request)
    try:
        challenge=phones.attempt(user["id"])
        if not provider.check(challenge["provider_sid"],payload.code):raise ValueError("驗證碼錯誤或已失效")
        phones.approve(user["id"],challenge["request_id"])
        return {"ok":True,"message":"手機驗證完成"}
    except provider.PhoneUnavailable as exc:raise HTTPException(503,str(exc)) from exc
    except ValueError as exc:raise HTTPException(400,str(exc)) from exc
