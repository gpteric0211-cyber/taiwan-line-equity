from __future__ import annotations
from contextlib import closing
import sqlite3
from pathlib import Path
from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from repository.portfolio_repository import PortfolioStore, initialize
from services.portfolio_service import PRIVACY_VERSION, validate_holdings


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("EQUITY_USER_DB", str(tmp_path / "private # space" / "users.sqlite3"))
    monkeypatch.setenv("EQUITY_USER_KEY_FILE", str(tmp_path / "private # space" / "users.key"))
    initialize()
    return PortfolioStore()


def holding(code="2330", quantity="1000"):
    return {"code": code, "quantity": quantity, "unit": "shares", "average_cost": "950"}


def test_owner_isolation_encryption_confirmation_and_restart(store):
    alice = store.subject("web", "alice@example.invalid")
    bob = store.subject("line", "U-not-a-real-account")
    rows = validate_holdings([holding()])
    draft = store.create_draft(alice, rows)
    assert store.read(alice)["holdings"] == []
    with pytest.raises(ValueError):
        store.confirm(bob, draft)
    assert store.confirm(alice, draft) == rows
    assert store.confirm(alice, draft, validate_holdings([holding(quantity="999")])) == rows
    assert PortfolioStore().read(alice)["holdings"] == rows
    assert store.read(bob)["holdings"] == []
    with store.connect() as conn:
        encrypted = conn.execute("SELECT encrypted FROM portfolios").fetchone()[0]
        assert b"2330" not in encrypted and b"950" not in encrypted
    assert b"alice@example.invalid" not in store.path.read_bytes()


def test_updates_preserve_other_symbols_and_delete_is_owner_scoped(store):
    alice, bob = store.subject("web", "1"), store.subject("web", "2")
    for user, rows in [(alice, [holding(), holding("6669")]), (bob, [holding("6669")])]:
        store.confirm(user, store.create_draft(user, validate_holdings(rows)))
    store.confirm(alice, store.create_draft(alice, validate_holdings([holding(quantity="2000")])))
    assert {row["code"]: row["quantity"] for row in store.read(alice)["holdings"]} == {
        "2330": "2000",
        "6669": "1000",
    }
    store.remove(alice, "6669")
    assert len(store.read(bob)["holdings"]) == 1
    store.forget(alice)
    assert not store.read(alice)["holdings"] and store.read(bob)["holdings"]


def test_single_use_link_expiry_and_no_cross_account_rebinding(store):
    web, web2, line = (store.subject(*value) for value in [("web", "1"), ("web", "2"), ("line", "U1")])
    store.consent(web, PRIVACY_VERSION)
    draft = store.create_draft(web, validate_holdings([holding()]))
    store.confirm(web, draft)
    token = store.issue_link(web)
    store.consume_link(line, token)
    assert store.read(line) == store.read(web)
    assert store.has_consent(line, PRIVACY_VERSION)
    with pytest.raises(ValueError):
        store.consume_link(line, token)
    with pytest.raises(ValueError):
        store.consume_link(line, store.issue_link(web2))
    store.forget(line, unlink_only=True)
    assert not store.read(line)["holdings"] and store.read(web)["holdings"]


def test_expired_draft_and_unsend_cannot_be_confirmed(store):
    owner = store.subject("line", "U2")
    expired = store.create_draft(owner, validate_holdings([holding()]), ttl_seconds=-1)
    with pytest.raises(ValueError):
        store.confirm(owner, expired)
    draft = store.create_draft(owner, validate_holdings([holding()]), origin="image-message-id")
    store.suppress_message("image-message-id")
    with pytest.raises(ValueError):
        store.confirm(owner, draft)
    assert not store.read(owner)["holdings"]


@pytest.mark.parametrize(
    "row",
    [
        holding(quantity="NaN"),
        holding(quantity=True),
        holding(quantity="-1"),
        holding(quantity="0.5"),
        holding(code="../2330"),
        {**holding(), "unit": "unknown"},
        {**holding(), "average_cost": "Infinity"},
    ],
)
def test_invalid_or_ambiguous_holdings_cannot_be_saved(row):
    with pytest.raises(ValueError):
        validate_holdings([row])


def test_lots_are_converted_once_without_float_rounding():
    assert validate_holdings([{**holding(quantity="1.001"), "unit": "lots"}])[0]["quantity"] == "1001.000"
    with pytest.raises(ValueError):
        validate_holdings([holding(), holding()])


def test_upload_intent_is_expiring_and_single_use(store):
    owner = store.subject("line", "U1")
    assert not store.take_upload(owner)
    store.arm_upload(owner)
    assert store.take_upload(owner)
    assert not store.take_upload(owner)


def test_api_requires_auth_consent_and_same_origin_header(store, monkeypatch):
    from api import portfolio

    app = FastAPI()
    app.include_router(portfolio.router)
    client = TestClient(app)
    assert client.post("/api/portfolio/drafts", json={"holdings": [holding()]}).status_code == 401
    app.dependency_overrides[portfolio.get_current_user] = lambda: {"id": 1}
    assert client.post("/api/portfolio/drafts", json={"holdings": [holding()]}).status_code == 403
    headers = {"X-Equity-Request": "1"}
    assert (
        client.post("/api/portfolio/drafts", json={"holdings": [holding()]}, headers=headers).status_code
        == 409
    )
    assert (
        client.post(
            "/api/portfolio/consent", json={"version": PRIVACY_VERSION, "accepted": True}, headers=headers
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/portfolio/drafts",
            json={"holdings": [holding()]},
            headers={**headers, "Origin": "https://elsewhere.invalid"},
        ).status_code
        == 403
    )
    response = client.post("/api/portfolio/drafts", json={"holdings": [holding()]}, headers=headers)
    assert response.status_code == 200
    payload = response.json()
    assert store.read(store.subject("web", "1"))["holdings"] == []
    assert (
        client.post(
            "/api/portfolio/confirm",
            json={"draft_id": payload["draft_id"], "holdings": payload["holdings"]},
            headers=headers,
        ).status_code
        == 200
    )
    assert store.read(store.subject("web", "1"))["holdings"][0]["code"] == "2330"


def test_vision_drops_personal_fields_and_preserves_unknown_units(monkeypatch):
    from services import portfolio_service

    monkeypatch.setenv("QWEN_VISION_ENABLED", "true")
    monkeypatch.setattr(
        portfolio_service, "verify_and_sanitize_image", lambda data: SimpleNamespace(data=b"sanitized")
    )
    observed = {}

    def model(*args, **kwargs):
        observed.update(kwargs)
        return {
            "is_portfolio": True,
            "holdings": [{**holding(), "unit": "unreadable", "account": "private-account"}],
        }

    monkeypatch.setattr(portfolio_service, "qwen_vision_json", model)
    monkeypatch.setattr(
        portfolio_service, "run_interactive_model", lambda fn, **kwargs: SimpleNamespace(value=fn())
    )
    result = portfolio_service.extract_holdings(b"input")
    assert "account" not in result["holdings"][0]
    assert result["holdings"][0]["unit"] == "unknown"
    assert observed["response_template"]["is_portfolio"] is True
    assert result["image_stored"] is False


def test_line_commands_share_web_records_and_require_confirmation(store):
    from services.line_portfolio_service import portfolio_command

    event = {"source": {"type": "user", "userId": "U-test"}}
    assert "同意持股保存" in portfolio_command(event, "新增持股 2330 1張 950")
    portfolio_command(event, "同意持股保存")
    answer = portfolio_command(event, "新增持股 2330 1張 950")
    token = answer.split("確認持股 ", 1)[1].splitlines()[0]
    owner = store.subject("line", "U-test")
    assert not store.read(owner)["holdings"]
    portfolio_command(event, "確認持股 " + token)
    assert store.read(owner)["holdings"][0]["quantity"] == "1000"
    group = {"source": {"type": "group", "userId": "U-test", "groupId": "G-test"}}
    assert "一對一" in portfolio_command(group, "我的持股")


def test_expired_temporary_data_is_removed_without_new_imports(store):
    owner = store.subject("web", "1")
    draft = store.create_draft(owner, validate_holdings([holding()]))
    store.confirm(owner, draft)
    store.issue_link(owner)
    store.arm_upload(owner)
    with store.connect(write=True) as conn:
        for table in ("portfolio_drafts", "portfolio_links", "portfolio_intents"):
            conn.execute(f"UPDATE {table} SET expires_at=0")
    assert store.cleanup_expired() == 3
    assert store.read(owner)["holdings"][0]["code"] == "2330"


def test_confirmed_position_is_stock_scoped_and_never_relabels_market_facts():
    from services.portfolio_analysis import (
        confirmed_position,
        with_position_facts,
        public_conversation_context,
    )
    from services.line_bot_service import _answer_numeric_claims_are_field_bound

    context = {"code": "2330", "confirmed_holdings": [{**holding(), "account": "private"}]}
    assert confirmed_position(context, "6669") is None
    position = confirmed_position(context, "2330")
    assert "account" not in position
    original = {"referee": {"main_status": "資料不足"}, "official_ohlcv": {"close": None}}
    result, claim = with_position_facts(original, position)
    assert result["referee"] == original["referee"]
    assert result["official_ohlcv"] == original["official_ohlcv"]
    assert "user_confirmed_holding" not in original
    assert result["user_confirmed_holding"]["market_data"] is False
    assert _answer_numeric_claims_are_field_bound(claim, result)
    assert not _answer_numeric_claims_are_field_bound("平均成本每股 2330 元", result)
    assert public_conversation_context(context) == {"code": "2330"}


def test_mobile_chat_uses_only_confirmed_holdings_for_the_logged_in_owner(store, monkeypatch):
    from api import portfolio
    from services import line_bot_service

    owner = store.subject("web", "1")
    store.consent(owner, PRIVACY_VERSION)
    draft = store.create_draft(owner, validate_holdings([holding()]))
    app = FastAPI()
    app.include_router(portfolio.router)
    app.dependency_overrides[portfolio.get_current_user] = lambda: {"id": 1}
    client = TestClient(app)
    headers = {"X-Equity-Request": "1"}
    captured = {}

    def answer(question, **kwargs):
        captured.update(kwargs["conversation_context"])
        return "分析測試"

    monkeypatch.setattr(line_bot_service, "answer_stock_question", answer)
    assert (
        client.post(
            "/api/portfolio/chat", json={"code": "2330", "question": "趨勢"}, headers=headers
        ).status_code
        == 404
    )
    store.confirm(owner, draft)
    assert (
        client.post(
            "/api/portfolio/chat", json={"code": "2330", "question": "趨勢"}, headers=headers
        ).status_code
        == 200
    )
    assert captured["confirmed_holdings"][0]["average_cost"] == "950"
    assert (
        client.post(
            "/api/portfolio/chat", json={"code": "6669", "question": "趨勢"}, headers=headers
        ).status_code
        == 404
    )


def test_current_trend_routes_to_analysis_and_explicit_history_keeps_its_period():
    from services.line_bot_service import _history_limit

    assert _history_limit("2330 目前趨勢與近期風險") is None
    assert _history_limit("2330 近 20 個交易日") == 20
    assert _history_limit("2330 歷史資料") == 20
    assert _history_limit("2330 近一個月") == 20
