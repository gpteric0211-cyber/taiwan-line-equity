"""Twilio Verify adapter. No fake success, fallback code or raw response logging."""

import os
import re
import requests


class PhoneUnavailable(RuntimeError):
    pass


def configured():
    return bool(os.getenv("PHONE_VERIFICATION_MODE","disabled")=="trial"
                and re.fullmatch(r"AC[0-9a-fA-F]{32}",os.getenv("TWILIO_ACCOUNT_SID",""))
                and re.fullmatch(r"VA[0-9a-fA-F]{32}",os.getenv("TWILIO_VERIFY_SERVICE_SID",""))
                and os.getenv("TWILIO_AUTH_TOKEN"))


def _call(resource, data):
    if not configured():
        raise PhoneUnavailable("手機驗證尚未開通，請聯絡管理員")
    service=os.environ["TWILIO_VERIFY_SERVICE_SID"]
    try:
        response=requests.post(f"https://verify.twilio.com/v2/Services/{service}/{resource}",
            auth=(os.environ["TWILIO_ACCOUNT_SID"],os.environ["TWILIO_AUTH_TOKEN"]),
            data=data,timeout=(5,12),allow_redirects=False)
        if response.status_code==404 and resource=="VerificationCheck":
            return {"status":"expired"}
        response.raise_for_status()
        return response.json()
    except (requests.RequestException,ValueError) as exc:
        raise PhoneUnavailable("手機驗證服務暫時無法完成，請稍後再試") from exc


def send(phone):
    # User permits free trial only. Refuse paid accounts before sending any SMS.
    if not configured():
        raise PhoneUnavailable("手機驗證尚未開通，請聯絡管理員")
    account=os.environ["TWILIO_ACCOUNT_SID"]
    try:
        response=requests.get(f"https://api.twilio.com/2010-04-01/Accounts/{account}.json",
            auth=(account,os.environ["TWILIO_AUTH_TOKEN"]),timeout=(5,12),allow_redirects=False)
        response.raise_for_status()
        if response.json().get("type")!="Trial":
            raise PhoneUnavailable("目前僅允許免費試用手機驗證，未啟用付費發送")
    except (requests.RequestException,ValueError) as exc:
        raise PhoneUnavailable("無法確認免費試用資格，未發送簡訊") from exc
    result=_call("Verifications",{"To":phone,"Channel":"sms"})
    sid=result.get("sid","")
    if result.get("status")!="pending" or not re.fullmatch(r"VE[0-9a-fA-F]{32}",sid):
        raise PhoneUnavailable("手機驗證服務未接受請求，請稍後再試")
    return sid


def check(sid, code):
    return _call("VerificationCheck",{"VerificationSid":sid,"Code":code}).get("status")=="approved"
