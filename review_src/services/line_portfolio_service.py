"""Explicit private-chat portfolio commands, sharing the mobile portfolio service."""

from __future__ import annotations
import re
from repository.portfolio_repository import PortfolioStore
from services.portfolio_service import PRIVACY_VERSION, extract_holdings, validate_holdings

NOTICE = (
    "持股功能會在本機加密保存你確認的股票、股數及成本，直到你要求刪除；圖片只在記憶體處理，辨識草稿最多保存一小時。"
    "資料會交由已設定的模型處理，確認前請先遮住姓名與帳號。"
    "可輸入「我的持股」、「刪除持股 2330」、「刪除全部持股」或「解除綁定」。"
    "同意後請輸入「同意持股保存」。"
)


def subject_for_event(store, event):
    source = event.get("source") or {}
    if source.get("type") != "user" or not source.get("userId"):
        return None
    return store.subject("line", str(source["userId"]))


def portfolio_command(event: dict, question: str) -> str | None:
    command = question.strip()
    prefixes = (
        "我的持股",
        "持股說明",
        "同意持股保存",
        "新增持股",
        "確認持股",
        "刪除持股",
        "刪除全部持股",
        "綁定 ",
        "解除綁定",
        "上傳持股",
    )
    if not command.startswith(prefixes):
        return None
    store = PortfolioStore()
    subject = subject_for_event(store, event)
    if subject is None:
        return "持股紀錄僅限一對一私訊，請直接傳訊息給機器人。"
    if command == "持股說明":
        return NOTICE
    if command == "同意持股保存":
        store.consent(subject, PRIVACY_VERSION)
        return "已記錄同意。可輸入「新增持股 2330 1000股 950」，或先輸入「上傳持股」再傳截圖。成本可省略。"
    if command == "解除綁定":
        store.forget(subject, unlink_only=True)
        return "已解除綁定。"
    if command == "刪除全部持股":
        store.forget(subject)
        return "已刪除目前帳號的持股、辨識草稿及綁定資料。"
    if command.startswith("綁定 "):
        store.consume_link(subject, command.split(maxsplit=1)[1])
        return "已與手機網頁帳號綁定，可使用「我的持股」查詢。"
    if not store.has_consent(subject, PRIVACY_VERSION):
        return NOTICE
    if command == "我的持股":
        rows = store.read(subject)["holdings"]
        if not rows:
            return "目前尚未記錄持股。可輸入「新增持股 2330 1000股 950」。"
        lines = [
            f"{item['code']}｜{item['quantity']} 股｜平均成本 {item['average_cost'] if item['average_cost'] is not None else '未填'}"
            for item in rows
        ]
        return "你的已確認持股\n" + "\n".join(lines) + "\n可輸入股票代號繼續詢問分析。"
    if command.startswith("刪除持股 "):
        code = command.split(maxsplit=1)[1].strip()
        store.remove(subject, code)
        return "已移除此檔持股紀錄。"
    if command.startswith("新增持股 "):
        match = re.fullmatch(r"新增持股\s+([0-9]{4,6})\s+([0-9.,]+)(股|張)(?:\s+([0-9.,]+))?", command)
        if not match:
            return "請輸入「新增持股 2330 1000股 950」，或「新增持股 2330 1張」。最後一欄是每股平均成本，可省略。"
        code, quantity, unit, cost = match.groups()
        rows = validate_holdings(
            [
                {
                    "code": code,
                    "quantity": quantity,
                    "unit": "lots" if unit == "張" else "shares",
                    "average_cost": cost,
                }
            ]
        )
        token = store.create_draft(subject, rows)
        return draft_message(token, rows)
    if command.startswith("確認持股 "):
        parts = command.split()
        if len(parts) != 2:
            return "請完整貼上辨識結果下方的確認指令。"
        # Image drafts are only created after strict numeric/unit validation.
        store.confirm(subject, parts[1])
        return "持股已保存；相同代號會更新股數與成本，其餘持股保留。"
    if command == "上傳持股":
        store.arm_upload(subject)
        return "請在下一則訊息上傳持股截圖；先遮住姓名、帳號及其他個資。辨識後會請你確認。"
    return "請輸入「持股說明」查看指令。"


def draft_message(token, rows):
    lines = [
        f"{row['code']}｜{row['quantity']} 股｜平均成本 {row['average_cost'] if row['average_cost'] is not None else '未填'}"
        for row in rows
    ]
    return (
        "請核對以下持股：\n"
        + "\n".join(lines)
        + "\n相同代號會更新既有紀錄；確認正確後輸入：\n確認持股 "
        + token
        + "\n草稿一小時後失效；若有錯請用「新增持股」重新輸入。"
    )


def portfolio_image(event, image_bytes, *, deadline_monotonic=None):
    store = PortfolioStore()
    subject = subject_for_event(store, event)
    if subject is None:
        return "持股圖片請透過一對一私訊傳送。"
    if not store.has_consent(subject, PRIVACY_VERSION):
        return NOTICE
    result = extract_holdings(image_bytes, deadline_monotonic=deadline_monotonic)
    if not result["is_portfolio"]:
        return "這張圖片不是持股明細。若要分析股價圖，請先輸入股票代號，再傳圖片。"
    try:
        rows = validate_holdings(result["holdings"])
    except ValueError:
        return "有股數、單位或成本看不清楚，未保存持股。請使用手機網頁核對，或用「新增持股 2330 1000股 950」手動輸入。"
    return draft_message(
        store.create_draft(subject, rows, origin=str((event.get("message") or {}).get("id") or "")), rows
    )


def clear_event_portfolio(event, *, unlink_only=False):
    from core.portfolio_storage import database_path

    if not database_path().exists():
        return
    store = PortfolioStore()
    subject = subject_for_event(store, event)
    if subject:
        store.forget(subject, unlink_only=unlink_only)


def wants_portfolio_image(event):
    from core.portfolio_storage import database_path, key_path

    if not database_path().exists() or not key_path().exists():
        return False
    store = PortfolioStore()
    subject = subject_for_event(store, event)
    return bool(subject and store.take_upload(subject))


def suppress_portfolio_image(message_id):
    from core.portfolio_storage import database_path

    if database_path().exists():
        PortfolioStore().suppress_message(message_id)


def holding_context(event, question, context):
    from core.portfolio_storage import database_path

    if not database_path().exists():
        return context
    store = PortfolioStore()
    subject = subject_for_event(store, event)
    if subject is None:
        return context
    codes = re.findall(r"(?<![0-9])([0-9]{4,6})(?![0-9])", question)
    code = codes[0] if len(codes) == 1 else str(context.get("code") or "") if not codes else ""
    rows = store.read(subject)["holdings"]
    context = {**context, "confirmed_holdings": rows}
    if code and any(item["code"] == code for item in rows):
        context["position_state"] = "holding"
    return context
