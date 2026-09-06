"""Interactive, local-only first owner creation; passwords never enter argv."""

import getpass
from contextlib import closing
from auth import service
from auth.email_sender import smtp_configured
from auth.schemas import EMAIL_RE, normalize_email
from auth.security import password_policy_error
from core.accounts_database import db
from repository.member_repository import bootstrap_owner


def setup_owner():
    with closing(db()) as conn:
        if conn.execute("SELECT 1 FROM member_access WHERE role='owner'").fetchone():
            raise ValueError("已有擁有者，請登入後臺授權其他管理員。")
    if not smtp_configured():
        raise ValueError("請先設定 SMTP 寄信服務，第一位擁有者也需要驗證 Email。")
    email = normalize_email(input("管理員 Email："))
    if not EMAIL_RE.fullmatch(email):
        raise ValueError("Email 格式不正確")
    with closing(db()) as conn:
        if conn.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            raise ValueError("帳號已存在；請先驗證 Email，再使用 owner --email 指定此帳號。")
    password = getpass.getpass("設定密碼（輸入不顯示）：")
    if password != getpass.getpass("再次輸入密碼："):
        raise ValueError("兩次密碼不一致")
    error = password_policy_error(password)
    if error:
        raise ValueError(error)
    ok, message = service.create_user(email, password, "local-owner-setup")
    if not ok:
        raise ValueError(message)
    print("驗證碼已寄出。")
    ok, message = service.verify_email(email, input("Email 驗證碼：").strip())
    if not ok:
        raise ValueError(message + "；請在帳號頁完成驗證，再執行 owner --email。")
    bootstrap_owner(email)
    print("擁有者已建立。請至 /members 登入後臺；後臺管理不需要手機驗證。")
