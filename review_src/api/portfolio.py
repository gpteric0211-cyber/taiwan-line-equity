"""Authenticated mobile portfolio endpoints; shared services also serve LINE."""

from __future__ import annotations
from contextlib import closing
from decimal import Decimal
import time
import math
from typing import Any
from urllib.parse import urlsplit
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from adapter.qwen_local import QwenClientError
from auth.dependencies import get_current_user
from core.db import read_only_db
from repository.portfolio_repository import PortfolioStore
from services.image_input_service import ImageInputError, read_bounded_web_image_upload
from services.portfolio_service import PRIVACY_VERSION, extract_holdings, validate_holdings

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


def _store():
    try:
        return PortfolioStore()
    except (OSError, ValueError):
        raise HTTPException(503, "持股服務尚未初始化，請先執行專案初始化")


def _subject(store, user):
    return store.subject("web", str(user["id"]))


def _mutation(request: Request):
    if request.headers.get("x-equity-request") != "1":
        raise HTTPException(403, "請由本網站送出操作")
    origin = request.headers.get("origin")
    if origin and urlsplit(origin).netloc != request.url.netloc:
        raise HTTPException(403, "操作來源不符")


def _consented(store, subject):
    if not store.has_consent(subject, PRIVACY_VERSION):
        raise HTTPException(409, "請先閱讀並同意持股資料保存說明")


class HoldingInput(BaseModel):
    holdings: list[dict[str, Any]] = Field(min_length=1, max_length=100)


class ConfirmInput(BaseModel):
    draft_id: str = Field(min_length=16, max_length=100)
    holdings: list[dict[str, Any]] | None = Field(default=None, max_length=100)


class ConsentInput(BaseModel):
    version: str
    accepted: bool


class ChatInput(BaseModel):
    code: str = Field(pattern=r"^[0-9]{4,6}$")
    question: str = Field(min_length=1, max_length=2000)


@router.get("")
def get_portfolio(user: dict = Depends(get_current_user)):
    store = _store()
    subject = _subject(store, user)
    result = store.read(subject)
    result["consented"] = store.has_consent(subject, PRIVACY_VERSION)
    result["privacy_version"] = PRIVACY_VERSION
    with closing(read_only_db()) as conn:
        for item in result["holdings"]:
            row = conn.execute(
                "SELECT date,close FROM history_price WHERE code=? AND close>0 ORDER BY date DESC LIMIT 1",
                (item["code"],),
            ).fetchone()
            if row and (not isinstance(row["close"], (int, float)) or not math.isfinite(row["close"])):
                row = None
            item["close"] = row["close"] if row else None
            item["data_date"] = row["date"] if row else None
            item["unrealized_pnl"] = None
            if row and item.get("average_cost") is not None:
                item["unrealized_pnl"] = str(
                    (Decimal(str(row["close"])) - Decimal(item["average_cost"])) * Decimal(item["quantity"])
                )
    return result


@router.post("/consent")
def accept_consent(payload: ConsentInput, request: Request, user: dict = Depends(get_current_user)):
    _mutation(request)
    if payload.version != PRIVACY_VERSION or payload.accepted is not True:
        raise HTTPException(400, "請確認目前版本的資料保存說明")
    store = _store()
    store.consent(_subject(store, user), PRIVACY_VERSION)
    return {"ok": True}


@router.post("/drafts")
def create_draft(payload: HoldingInput, request: Request, user: dict = Depends(get_current_user)):
    _mutation(request)
    store = _store()
    subject = _subject(store, user)
    _consented(store, subject)
    try:
        holdings = validate_holdings(payload.holdings)
        return {
            "draft_id": store.create_draft(subject, holdings),
            "holdings": holdings,
            "requires_confirmation": True,
        }
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/image")
async def upload_image(request: Request, user: dict = Depends(get_current_user)):
    _mutation(request)
    store = _store()
    subject = _subject(store, user)
    _consented(store, subject)
    try:
        upload = await read_bounded_web_image_upload(
            request.headers.get("content-type", ""), request.stream()
        )
        result = await run_in_threadpool(extract_holdings, upload.data)
        if not result["is_portfolio"]:
            raise HTTPException(400, "這張圖片不是持股明細；股價圖請使用行情頁的圖表分析")
        result["draft_id"] = await run_in_threadpool(store.create_draft, subject, result["holdings"])
        return result
    except ImageInputError as exc:
        raise HTTPException(exc.status_code, "請上傳 8 MiB 內的 PNG、JPEG 或 WebP 持股截圖") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except QwenClientError as exc:
        raise HTTPException(503, "圖片辨識目前無法完成，請稍後再試或手動輸入") from exc


@router.post("/confirm")
def confirm_draft(payload: ConfirmInput, request: Request, user: dict = Depends(get_current_user)):
    _mutation(request)
    store = _store()
    subject = _subject(store, user)
    _consented(store, subject)
    try:
        if payload.holdings is None:
            raise ValueError("請送出已核對的持股資料")
        holdings = validate_holdings(payload.holdings)
        return {"ok": True, "holdings": store.confirm(subject, payload.draft_id, holdings)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete("/holdings/{code}")
def delete_holding(code: str, request: Request, user: dict = Depends(get_current_user)):
    _mutation(request)
    store = _store()
    store.remove(_subject(store, user), code)
    return {"ok": True}


@router.delete("")
def forget_portfolio(request: Request, user: dict = Depends(get_current_user)):
    _mutation(request)
    store = _store()
    store.forget(_subject(store, user))
    return {"ok": True}


@router.post("/link")
def issue_link(request: Request, user: dict = Depends(get_current_user)):
    _mutation(request)
    store = _store()
    subject = _subject(store, user)
    _consented(store, subject)
    from repository.member_repository import record_principal
    record_principal(user["id"],subject)
    return {"command": "綁定 " + store.issue_link(subject), "expires_in": 600}


@router.post("/chat")
async def chat(payload: ChatInput, request: Request, user: dict = Depends(get_current_user)):
    _mutation(request)
    store = _store()
    subject = _subject(store, user)
    _consented(store, subject)
    holding = next((item for item in store.read(subject)["holdings"] if item["code"] == payload.code), None)
    if not holding:
        raise HTTPException(404, "請先新增並確認這檔持股")
    from services.line_bot_service import answer_stock_question

    answer = await run_in_threadpool(
        answer_stock_question,
        payload.code + " " + payload.question,
        deadline_monotonic=time.monotonic() + 45,
        conversation_context={
            "code": payload.code,
            "position_state": "holding",
            "confirmed_holdings": [holding],
        },
    )
    return {"answer": answer, "holding": holding, "record_source": "user_confirmed"}


@router.get("/news")
def news(user: dict = Depends(get_current_user)):
    store = _store()
    codes = [item["code"] for item in store.read(_subject(store, user))["holdings"]]
    with closing(read_only_db()) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        records = []
        if "corporate_action_permanent_event" in tables:
            labels = {
                "right": "除權",
                "dividend": "除息",
                "right_dividend": "除權息",
                "split": "股票分割",
                "capital_reduction": "減資",
                "capital_increase": "增資",
                "other": "公司行動",
            }
            for row in conn.execute(
                "SELECT event_id,code,company_name,action_type,effective_date,source_url,verification_status FROM corporate_action_permanent_event ORDER BY effective_date DESC LIMIT 100"
            ):
                records.append(
                    {
                        "event_key": row["event_id"],
                        "event_date": row["effective_date"],
                        "title": f"{row['company_name'] or row['code']}（{row['code']}）{labels.get(row['action_type'], '公司行動')}：生效日期 {row['effective_date']}",
                        "source_url": row["source_url"],
                        "publisher": "公司行動公告",
                        "permanent": 1,
                        "verification_status": row["verification_status"],
                    }
                )
        if "material_news_archive" in tables:
            records.extend(
                dict(row)
                for row in conn.execute(
                    "SELECT event_key,event_date,title,source_url,publisher,1 AS permanent FROM material_news_archive ORDER BY event_date DESC LIMIT 100"
                )
            )
        if "news_radar_event" in tables:
            records.extend(
                dict(row)
                for row in conn.execute(
                    "SELECT event_key,event_date,title,source_url,publisher,0 AS permanent FROM news_radar_event ORDER BY event_date DESC LIMIT 100"
                )
            )
    unique = {}
    for row in records:
        if row["event_key"] not in unique:
            row["related_to_holdings"] = any(code in row["title"] for code in codes)
            if urlsplit(row["source_url"]).scheme not in {"https", "http"}:
                row["source_url"] = ""
            unique[row["event_key"]] = row
    return {
        "items": sorted(unique.values(), key=lambda row: row["event_date"], reverse=True)[:100],
        "verification": "新聞線索須查核原始公告；永久保存不代表已驗證",
    }
