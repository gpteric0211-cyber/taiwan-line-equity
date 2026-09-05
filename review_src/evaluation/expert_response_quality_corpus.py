from __future__ import annotations

import hashlib
from typing import Any


CORPUS_VERSION = "ExpertResponseQualityCorpusV1"
RATING_DIMENSIONS = (
    "factual_grounding",
    "context_continuity",
    "entity_correctness",
    "specificity",
    "conditional_usefulness",
    "naturalness",
    "non_template_quality",
    "uncertainty_handling",
)

_TARGETS = (
    "台積電（2330）",
    "聯發科（2454）",
    "鴻海（2317）",
    "緯穎（6669）",
    "星宇航空（2646）",
    "長榮（2603）",
    "長榮航（2618）",
    "M31（6643）",
    "廣達（2382）",
    "國泰金（2882）",
)


def _case(category: str, prompt: str, scenario: str) -> dict[str, Any]:
    digest = hashlib.sha256(f"{category}|{prompt}".encode("utf-8")).hexdigest()[:16]
    return {
        "case_id": f"erq-{digest}",
        "category": category,
        "scenario": scenario,
        "prompt": prompt,
        "blind_labels": ["answer_a", "answer_b"],
        "rating_dimensions": list(RATING_DIMENSIONS),
        "human_rating_required": True,
    }


def build_expert_response_quality_corpus() -> list[dict[str, Any]]:
    focused_templates = (
        "請用最精簡方式說明{target}目前真正重要的兩個因素與資料截止時間。",
        "{target}現在偏多、偏空還是資料不足？直接回答並說明失效條件。",
        "我只想看{target}的技術結構與量能是否互相確認，不要逐項念指標。",
    )
    comprehensive_templates = (
        "完整評估{target}的基本面、籌碼、技術、估值、最新事件與隔日風險；缺資料請精確指出。",
        "我持有{target}，請整合趨勢、波動、法人、融資、事件與全球市場，分成維持與失效情境。",
        "我目前空手，請全面研究{target}，說明什麼條件值得重新評估、什麼條件不該追價。",
    )
    corpus = [
        _case("focused", template.format(target=target), "single_stock_focused")
        for target in _TARGETS
        for template in focused_templates
    ]
    corpus.extend(
        _case("comprehensive", template.format(target=target), "single_stock_comprehensive")
        for target in _TARGETS
        for template in comprehensive_templates
    )
    context_prompts = (
        ("先問聯發科，下一輪說『星宇呢』；直接切換並承接比較脈絡。", "alias_switch"),
        ("先問聯發科，下一輪說『所以星宇呢』；不可要求不必要確認。", "alias_switch"),
        ("先問台積電，下一輪說『它明天呢』；承接唯一 active stock。", "pronoun"),
        ("先問長榮，下一輪說『那現在呢』；保持同一 entity 與 cutoff 語意。", "pronoun"),
        ("請比較長榮航和長榮，兩檔都要保留，不可只回答其中一檔。", "comparison"),
        ("比較台積電與聯發科，再追問『哪個事件風險較大』。", "comparison_followup"),
        ("先談 M31，再明確改問台積；新股票必須優先。", "explicit_switch"),
        ("先談星宇航空，再問『剛才那檔的量能呢』。", "pronoun"),
        ("使用者先說空手，兩輪後追問進場條件；不可重問是否持有。", "profile_continuity"),
        ("使用者先說已持有，追問失效條件；不可改寫成空手建議。", "profile_continuity"),
        ("使用者說偏波段，後續問技術面；沿用週期但不可補造持倉。", "horizon"),
        ("上一輪價格數字已過期，下一輪問現在；舊數字只能標歷史。", "historical_claim"),
        ("先問 2454，後輸入『2317 台積電』；必須指出身分衝突。", "identity_conflict"),
        ("輸入『星雨』；只提示候選，不可靜默改成 2646。", "typo"),
        ("先問鴻海，再問『聯發科呢』；不可因 active stock 回到 2317。", "explicit_switch"),
        ("比較星宇與長榮航，追問『前者的事件呢』。", "comparison_reference"),
        ("先問台積與聯發科，追問『兩者量能差在哪』。", "comparison_reference"),
        ("對話摘要含舊 RSI 數字，現在問 RSI；不可當成 current fact。", "historical_claim"),
        ("使用者更正上一輪股票名稱後再追問；應沿用更正，不重複錯股。", "correction"),
        ("在 12 turns 邊界追問 active stock；摘要與近期對話都不可遺失身分。", "memory_boundary"),
    )
    corpus.extend(_case("context", prompt, scenario) for prompt, scenario in context_prompts)
    edge_prompts = (
        ("只有支撐、沒有可靠賣壓的 6669；回答可用部分但不補造區間。", "missing_data"),
        ("2454 新重大事件剛在 cutoff 後可取得；舊展望必須作廢。", "material_event"),
        ("事件掃描 timeout；說明 scan_incomplete，不得提高信心。", "scan_incomplete"),
        ("兩個 verified 來源對事件方向相反；說明衝突，不硬判多空。", "event_conflict"),
        ("只有未驗證新聞索引；可提線索，不得加入方向權重。", "news_radar"),
        ("地緣事件沒有可證明個股曝險；不得影響個股方向。", "geopolitical"),
        ("官方估值缺本益比但有股價淨值比；回答可靠部分與精確缺口。", "missing_data"),
        ("技術指標 119/120 warm-up 邊界；不可把未就緒值當成 0。", "technical_quality"),
        ("除權息調整基準不一致；停止跨日比較並說明原因。", "corporate_action"),
        ("量單位未確認；不得把張數當股數計算量能。", "unit_conflict"),
        ("圖片是清楚股票技術圖；只描述 typed observation，不能進 referee。", "stock_chart"),
        ("圖片是自拍；自然簡短拒絕，不查行情。", "non_stock_image"),
        ("圖片是新聞頁截圖；不可當股票技術圖或保存完整 OCR。", "non_stock_image"),
        ("圖片損毀或截斷；模型前拒絕並說明格式問題。", "invalid_image"),
        ("新聞存在但 rights 只允許 metadata；不可長篇重述原文。", "rights"),
        ("資料完整但 referee 是不判斷；模型不得改成買進或賣出。", "referee_gate"),
        ("使用者聲稱今日漲停但官方資料不符；不能升級成 canonical fact。", "user_claim"),
        ("支撐與技術可用、法人資料延遲；回答其餘內容並標 source_delayed。", "partial_coverage"),
        ("同一事件被多家媒體轉載；只算一個 dedup cluster。", "dedup"),
        ("Web 與 LINE 問同一題；評分時確認主結論與文字 hash 完全相同。", "channel_parity"),
    )
    corpus.extend(_case("edge", prompt, scenario) for prompt, scenario in edge_prompts)
    return corpus


EXPERT_RESPONSE_QUALITY_CORPUS = tuple(build_expert_response_quality_corpus())
