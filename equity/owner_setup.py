"""Interactive local first-owner setup; no SMTP dependency or password in argv."""

import getpass
from contextlib import closing
from auth.local_owner import create_owner
from core.accounts_database import db


def setup_owner(*, allow_temporary_password=False):
    with closing(db()) as conn:
        if conn.execute("SELECT 1 FROM member_access WHERE role='owner'").fetchone():
            raise ValueError("已有擁有者，請登入後臺授權其他管理員。")
    email = input("管理員 Email：")
    password = getpass.getpass("設定密碼（輸入不顯示）：")
    if password != getpass.getpass("再次輸入密碼："):
        raise ValueError("兩次密碼不一致")
    create_owner(email, password, allow_temporary_password=allow_temporary_password)
    print("擁有者已建立，請在此主機開啟 /members 直接登入。日後修改密碼需收取 Email 連結。")
