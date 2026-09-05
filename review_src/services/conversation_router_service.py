from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any

from adapter.qwen_local import QwenClientError, qwen_chat
from services.model_admission_service import ModelAdmissionError, run_interactive_model


VALID_INTENTS = {
    "active_stock_follow_up",
    "general_investment",
    "market_brief",
    "clarify",
}

_MARKET_EXPLICIT_CUES = (
    "整體台股", "整體市場", "整體走勢", "台股走勢", "股市分析", "盤勢", "台股大盤", "大盤",
    "市場在炒", "市場題材", "最近題材", "熱門題材",
    "哪些類股", "哪個類股", "哪些產業", "哪個產業", "國際消息", "全球市場",
    "重大財經新聞", "財經新聞", "川普", "關稅", "聯準會", "美國政策", "政府政策", "政策影響",
    "美伊", "伊朗", "以色列", "中東", "地緣政治", "荷莫茲",
)
_CURRENT_CUES = ("今天", "今日", "最近", "近期", "目前", "現在", "這幾天", "剛剛")
_NEWS_CUES = ("新聞", "消息", "題材", "政策", "事件", "利多", "利空", "影響")
_STOCK_SCENARIO_CUES = (
    "如果", "假如", "萬一", "那", "所以", "接下來", "明天", "還能", "會不會",
    "怎麼辦", "怎麼看", "有影響", "變差", "變好", "轉弱", "轉強", "跌破", "突破",
)
_STOCK_ANALYSIS_CUES = (
    "營收", "獲利", "財報", "毛利率", "現金流", "法說", "rsi", "macd", "kd",
    "均線", "成交量", "量價", "支撐", "壓力", "股價", "價格", "籌碼", "法人",
    "風險", "買", "賣", "持有", "續抱", "停損", "分批", "進場", "消息",
)
_PROFILE_CUES = (
    "我有持有", "我有買", "我沒持有", "我沒有持有", "我空手", "還沒買",
    "短線", "當沖", "波段", "中線", "長線", "存股",
)


@dataclass(frozen=True)
class ConversationRoute:
    intent: str
    confidence: float
    source: str


def is_market_brief_question(text: str, *, has_active_stock: bool) -> bool:
    normalized = re.sub(r"\s+", "", str(text or "")).lower()
    if not normalized:
        return False
    if any(cue.lower() in normalized for cue in _MARKET_EXPLICIT_CUES):
        return True
    if not has_active_stock:
        return (
            any(cue in normalized for cue in _CURRENT_CUES)
            and any(cue in normalized for cue in _NEWS_CUES)
        )
    return False


def is_natural_stock_follow_up(text: str, *, has_active_stock: bool) -> bool:
    if not has_active_stock:
        return False
    normalized = re.sub(r"[\s，。！？、,.!?：:；;]+", "", str(text or "")).lower()
    if not normalized or re.search(r"(?<!\d)\d{4}(?!\d)", normalized):
        return False
    if is_market_brief_question(normalized, has_active_stock=True):
        return False
    if any(cue in normalized for cue in _PROFILE_CUES):
        return True
    has_scenario = any(cue in normalized for cue in _STOCK_SCENARIO_CUES)
    has_analysis = any(cue in normalized for cue in _STOCK_ANALYSIS_CUES)
    return has_scenario and has_analysis and len(normalized) <= 100


def classify_ambiguous_turn(
    question: str,
    conversation_context: dict[str, Any] | None,
    *,
    model_enabled: bool,
    timeout_seconds: float | None = None,
) -> ConversationRoute:
    """Classify an unresolved turn; the model may route, never create market facts."""

    context = conversation_context or {}
    has_active_stock = bool(str(context.get("code") or ""))
    if is_market_brief_question(question, has_active_stock=has_active_stock):
        return ConversationRoute("market_brief", 1.0, "deterministic")
    if is_natural_stock_follow_up(question, has_active_stock=has_active_stock):
        return ConversationRoute("active_stock_follow_up", 0.95, "deterministic")
    if not model_enabled:
        return ConversationRoute("clarify", 0.0, "fallback")

    prompt = {
        "question": str(question or "")[:400],
        "active_stock": (
            {
                "code": str(context.get("code") or ""),
                "name": str(context.get("stock_name") or ""),
            }
            if has_active_stock
            else None
        ),
        "previous_topic": str(context.get("last_focus") or ""),
        "recent_user_questions": [
            str(item)[:240] for item in list(context.get("recent_user_questions") or [])[-4:]
        ],
    }
    system_prompt = (
        "你只負責判斷繁體中文投資對話的承接意圖，不回答金融問題，也不產生任何金融事實。"
        "只輸出一行 JSON：{\"intent\":\"...\",\"confidence\":0到1}。"
        "intent 只能是 active_stock_follow_up、general_investment、market_brief、clarify。"
        "active_stock_follow_up 只在問題明確承接 active_stock 或上一個情境時使用；"
        "若看起來像新的公司名稱但無法確定，不得硬接舊股票。"
        "market_brief 用於近期新聞、政策、國際事件、整體台股或類股題材；"
        "general_investment 用於不需要即時資料的投資觀念；其餘用 clarify。"
    )
    try:
        admission = run_interactive_model(
            lambda: qwen_chat(
                system_prompt,
                json.dumps(prompt, ensure_ascii=False, separators=(",", ":")),
                timeout_seconds=timeout_seconds,
            ),
            category="interactive_conversation_router",
            deadline_monotonic=(time.monotonic() + timeout_seconds) if timeout_seconds else None,
        )
        raw = admission.value
        match = re.search(r"\{.*\}", str(raw or ""), flags=re.DOTALL)
        parsed = json.loads(match.group(0) if match else "{}")
    except (
        QwenClientError,
        ModelAdmissionError,
        TimeoutError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ):
        return ConversationRoute("clarify", 0.0, "model_error")
    intent = str(parsed.get("intent") or "")
    try:
        confidence = float(parsed.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    if intent not in VALID_INTENTS or not 0 <= confidence <= 1 or confidence < 0.72:
        return ConversationRoute("clarify", confidence if 0 <= confidence <= 1 else 0.0, "model_low_confidence")
    if intent == "active_stock_follow_up" and not has_active_stock:
        return ConversationRoute("clarify", confidence, "model_guard")
    return ConversationRoute(intent, confidence, "model")
