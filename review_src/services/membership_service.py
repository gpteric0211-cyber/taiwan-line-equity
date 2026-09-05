"""Membership is independent of administrative role and financial transactions."""

import math
import os
import time
from repository import member_repository as members


def effective_access(record, *, now=None):
    now = time.time() if now is None else now
    active = record["plan"] == "complimentary" or (
        record["plan"] == "monthly" and record["expires_at"] is not None and record["expires_at"] > now
    )
    return {**record, "premium": active, "effective_plan": record["plan"] if active else "free",
            "expired": record["plan"] == "monthly" and not active,
            "can_manage_members": record["role"] in {"owner", "manager"}}


def get_membership(user_id):
    return effective_access(members.access(user_id))


def enforcement_enabled():
    # Activate only when identity providers, account migration and grants are ready.
    return os.getenv("EQUITY_MEMBERSHIP_ENFORCEMENT","0").strip().lower() in {"1","true","yes"}


def premium_denial(user_id):
    if not enforcement_enabled():
        return None
    if not get_membership(user_id)["premium"]:
        return "此功能需要有效的進階會員資格；請至帳號頁查看會員狀態。"
    return None


def line_denial(event, question):
    if not enforcement_enabled():
        return None
    # Account linking, records and deletion remain available without a paid plan.
    free_commands=("我的持股","持股說明","同意持股保存","新增持股 ","確認持股 ",
                   "刪除持股 ","刪除全部持股","綁定 ","解除綁定")
    if question.startswith(free_commands):
        return None
    from repository.portfolio_repository import PortfolioStore
    source=event.get("source") or {}
    if source.get("type")!="user" or not source.get("userId"):
        return "會員分析僅限一對一私訊，請直接傳訊息給機器人。"
    store=PortfolioStore()
    account=members.linked_account(store.resolved_owner(store.subject("line",str(source["userId"]))))
    if not account:
        return "請先在手機網頁完成註冊與驗證，再於帳號頁產生指令綁定 LINE。"
    if not account["is_active"] or not account["is_verified"] or (account["phone_required"] and not account["phone_verified"]):
        return "帳號尚未完成驗證或已停用，請至手機網頁確認帳號狀態。"
    return premium_denial(account["id"])


def update_member(actor_id, target_id, payload):
    reason = payload.reason.strip()
    if not reason:
        raise ValueError("請填寫調整原因")
    if payload.action == "role":
        if payload.role not in {"member", "manager"}:
            raise ValueError("無效的管理權限")
        value = {"role": payload.role}
    else:
        expires = payload.expires_at
        if payload.plan == "monthly":
            if expires is None or not math.isfinite(expires) or expires <= time.time():
                raise ValueError("月費資格必須設定未來到期時間")
        elif expires is not None:
            raise ValueError("免費或永久招待資格不設定到期日")
        value = {"plan": payload.plan, "expires_at": expires}
    return effective_access(members.change(actor_id, target_id, action=payload.action, value=value,
                                          version=payload.version, reason=reason, request_id=payload.request_id))
