"""Encrypted phone identity and bounded, durable SMS attempts in the account DB."""

from contextlib import closing
from datetime import datetime
from zoneinfo import ZoneInfo
import hashlib
import hmac
import os
import time
import uuid
from cryptography.fernet import Fernet
from core.accounts_database import db
from core.portfolio_storage import load_key


def verified(user_id):
    with closing(db()) as conn:
        return bool(conn.execute("SELECT 1 FROM account_phone WHERE user_id=?",(user_id,)).fetchone())


def reserve(user_id, phone):
    key=load_key();cipher=Fernet(key)
    digest=hmac.new(key,phone.encode(),hashlib.sha256).hexdigest()
    now=time.time();request_id=uuid.uuid4().hex
    day_start=datetime.fromtimestamp(now,ZoneInfo("Asia/Taipei")).replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
    with closing(db()) as conn,conn:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT 1 FROM account_phone WHERE user_id=? OR phone_hash=?",(user_id,digest)).fetchone():
            raise ValueError("此帳號或手機已完成驗證，請勿重複申請")
        recent=conn.execute("SELECT created_at FROM phone_send_attempt WHERE user_id=? OR phone_hash=? ORDER BY created_at DESC LIMIT 1",(user_id,digest)).fetchone()
        count=conn.execute("SELECT COUNT(*) FROM phone_send_attempt WHERE created_at>? AND (user_id=? OR phone_hash=?)",(now-3600,user_id,digest)).fetchone()[0]
        daily=conn.execute("SELECT COUNT(*) FROM phone_send_attempt WHERE created_at>?",(now-86400,)).fetchone()[0]
        budget=max(1,min(10000,int(os.getenv("PHONE_SMS_DAILY_LIMIT","100"))))
        phone_daily=conn.execute("SELECT COUNT(*) FROM phone_send_attempt WHERE phone_hash=? AND created_at>=?",(digest,day_start)).fetchone()[0]
        if phone_daily>=5:
            raise ValueError("此手機今日已申請 5 次驗證簡訊，請於臺灣時間明日再試")
        if (recent and recent[0]>now-60) or count>=5 or daily>=budget:
            raise ValueError("驗證簡訊請求已達限制，請稍後再試")
        conn.execute("DELETE FROM phone_send_attempt WHERE created_at<?",(now-86400,))
        conn.execute("DELETE FROM phone_challenge WHERE expires_at<=?",(now,))
        conn.execute("INSERT INTO phone_send_attempt VALUES(?,?,?)",(user_id,digest,now))
        conn.execute("INSERT INTO phone_challenge VALUES(?,?,?,?,NULL,?,0) ON CONFLICT(user_id) DO UPDATE SET request_id=excluded.request_id,phone_hash=excluded.phone_hash,encrypted=excluded.encrypted,provider_sid=NULL,expires_at=excluded.expires_at,attempts=0",(user_id,request_id,digest,cipher.encrypt(phone.encode()),now+600))
    return request_id


def attach(user_id, request_id, sid):
    with closing(db()) as conn,conn:
        conn.execute("UPDATE phone_challenge SET provider_sid=? WHERE user_id=? AND request_id=?",(sid,user_id,request_id))


def attempt(user_id):
    with closing(db()) as conn,conn:
        conn.execute("BEGIN IMMEDIATE")
        row=conn.execute("SELECT * FROM phone_challenge WHERE user_id=?",(user_id,)).fetchone()
        if not row or not row["provider_sid"] or row["expires_at"]<=time.time() or row["attempts"]>=5:
            raise ValueError("驗證碼已過期或嘗試次數過多，請重新申請")
        conn.execute("UPDATE phone_challenge SET attempts=attempts+1 WHERE user_id=?",(user_id,))
        return dict(row)


def approve(user_id, request_id):
    with closing(db()) as conn,conn:
        conn.execute("BEGIN IMMEDIATE")
        row=conn.execute("SELECT * FROM phone_challenge WHERE user_id=? AND request_id=? AND expires_at>?",(user_id,request_id,time.time())).fetchone()
        if not row:
            raise ValueError("驗證請求已失效，請重新申請")
        if conn.execute("SELECT 1 FROM account_phone WHERE phone_hash=?",(row["phone_hash"],)).fetchone():
            raise ValueError("此手機已綁定其他帳號")
        conn.execute("INSERT INTO account_phone VALUES(?,?,?,?)",(user_id,row["phone_hash"],row["encrypted"],time.time()))
        conn.execute("DELETE FROM phone_challenge WHERE user_id=?",(user_id,))
