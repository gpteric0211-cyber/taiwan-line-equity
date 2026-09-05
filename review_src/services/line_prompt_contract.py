"""Make the existing financial output contract explicit at the end of the prompt."""

from __future__ import annotations
import json


def grounded_stock_prompt(question, facts):
    contract = facts.get("answer_contract") or {}
    required = []
    for key in ("required_claims", "required_evidence"):
        for value in contract.get(key) or []:
            if isinstance(value, str) and value not in required:
                required.append(value)
    return (
        f"使用者問題：{question}\n"
        f"FACTS：{json.dumps(facts, ensure_ascii=False, separators=(',', ':'))}\n"
        "本輪輸出檢查：以下證據句須逐字保留，不更動數字、單位或標點："
        + json.dumps(required, ensure_ascii=False)
        + "\n其他解釋請使用定性比較與待確認條件，勿加入任何數字、均線天數、價格門檻或百分比。"
        "若需引用其他數值，只能逐字引用 FACTS.verified_claims 或 answer_contract.approved_advisory_claims 的完整句子。"
        "不自行四捨五入、計算損益或設定進出場價；缺少當日資料時明確指出資料不足。"
        "結尾逐字使用：僅供資料整理，不構成投資建議。"
    )
