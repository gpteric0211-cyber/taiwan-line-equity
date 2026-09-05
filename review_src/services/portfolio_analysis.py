"""Attach user-confirmed position facts without changing the market verdict."""

from __future__ import annotations
from copy import deepcopy
from services.portfolio_service import validate_holdings

PRIVATE_CONTEXT_FIELDS = {"confirmed_holdings"}


def confirmed_position(context, code):
    rows = (context or {}).get("confirmed_holdings")
    if not isinstance(rows, list):
        return None
    for raw in rows[:100]:
        if not isinstance(raw, dict) or str(raw.get("code") or "") != code:
            continue
        try:
            return validate_holdings([raw])[0]
        except ValueError:
            return None
    return None


def with_position_facts(facts, position):
    if not position:
        return facts, ""
    result = deepcopy(facts)
    claim = f"你已確認持有 {position['code']}：{position['quantity']} 股"
    if position["average_cost"] is not None:
        claim += f"，平均成本每股 {position['average_cost']} 元"
    claim += "。"
    result["user_confirmed_holding"] = {
        **position,
        "market_data": False,
        "limitations": "使用者自行確認的紀錄；未自動調整除權息、分割、稅費或近期交易，不得當作即時行情或改寫市場主結論。",
        "approved_claim": claim,
    }
    result.setdefault("display", {})["user_confirmed_holding"] = dict(result["user_confirmed_holding"])
    contract = result.setdefault("answer_contract", {})
    contract["approved_advisory_claims"] = [*contract.get("approved_advisory_claims", []), claim]
    return result, claim


def public_conversation_context(context):
    return {key: value for key, value in (context or {}).items() if key not in PRIVATE_CONTEXT_FIELDS}
