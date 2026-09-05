from __future__ import annotations

import math
import re
from typing import Any


EXTERNAL_EVENT_ANALYSIS_VERSION = "external-event-impact-v1"

POSITIVE_TERMS = (
    "補助", "減稅", "降稅", "擴大採購", "增加採購", "上修財測", "創新高",
    "轉盈", "核准", "簽署合作", "擴大投資", "投資計畫", "擴產", "subsidy", "tax credit",
    "tariff reduction", "signed agreement", "investment commitment", "approval",
)
NEGATIVE_TERMS = (
    "制裁", "出口管制", "禁令", "課徵關稅", "提高關稅", "裁罰", "召回",
    "下修", "衰退", "虧損", "停工", "停止交易", "違約", "火災", "訴訟",
    "sanction", "export control", "tariff increase", "ban", "penalty", "recall",
)

TOPIC_RULES: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("半導體", "晶片", "chip", "semiconductor", "ai accelerator"), ("半導體", "半導體業", "電子工業")),
    (("人工智慧", "ai", "伺服器", "server"), ("AI", "伺服器", "電腦及週邊設備業", "電子工業")),
    (("關稅", "tariff", "貿易", "trade"), ("出口", "電子工業", "電機機械", "鋼鐵工業", "汽車工業", "航運業")),
    (("能源", "電價", "天然氣", "oil", "energy"), ("能源", "油電燃氣業", "綠能環保")),
    (("金融", "銀行", "利率", "央行", "interest rate", "bank"), ("金融", "金融保險業")),
    (("生技", "藥品", "醫療", "drug", "biotech", "health"), ("生技", "生技醫療業", "化學生技醫療")),
    (("國防", "軍工", "defense"), ("國防", "航太", "電機機械")),
    (("航運", "海運", "空運", "shipping"), ("航運", "航運業")),
    (("鋼鐵", "steel"), ("鋼鐵", "鋼鐵工業")),
    (("汽車", "電動車", "automotive", "electric vehicle"), ("汽車", "汽車工業", "電子零組件業")),
)


def _number(value: Any) -> float | None:
    try:
        number = float(str(value or "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def classify_text_event(title: Any, summary: Any = "") -> dict[str, Any]:
    """Classify only explicit language; unknown is safer than invented sentiment."""

    text = re.sub(r"\s+", " ", f"{title or ''} {summary or ''}").strip().lower()
    positive_hits = [term for term in POSITIVE_TERMS if term.lower() in text]
    negative_hits = [term for term in NEGATIVE_TERMS if term.lower() in text]
    if positive_hits and negative_hits:
        direction = "mixed"
    elif negative_hits:
        direction = "negative"
    elif positive_hits:
        direction = "positive"
    else:
        direction = "unknown"
    affected_terms: list[str] = []
    matched_topics: list[str] = []
    for triggers, terms in TOPIC_RULES:
        hits = [trigger for trigger in triggers if trigger.lower() in text]
        if not hits:
            continue
        matched_topics.extend(hits)
        affected_terms.extend(terms)
    evidence_terms = list(dict.fromkeys([*positive_hits, *negative_hits]))[:6]
    affected_terms = list(dict.fromkeys(affected_terms))
    confidence = "medium" if direction != "unknown" and affected_terms else "low"
    return {
        "direction": direction,
        "confidence": confidence,
        "time_horizon": "short_term" if direction != "unknown" else "unknown",
        "affected_terms": affected_terms,
        "evidence_terms": evidence_terms,
        "matched_topics": list(dict.fromkeys(matched_topics))[:6],
        "analysis_version": EXTERNAL_EVENT_ANALYSIS_VERSION,
        "can_override_main_status": False,
    }


def classify_monthly_revenue(
    *,
    month_over_month_pct: Any,
    year_over_year_pct: Any,
    cumulative_year_over_year_pct: Any,
) -> dict[str, Any]:
    mom = _number(month_over_month_pct)
    yoy = _number(year_over_year_pct)
    cumulative = _number(cumulative_year_over_year_pct)
    if yoy is None or cumulative is None:
        direction = "unknown"
        confidence = "unavailable"
        reason = "官方營收年增率或累計年增率缺漏"
    elif yoy >= 10 and cumulative >= 5:
        direction = "positive"
        confidence = "high" if yoy >= 20 and cumulative >= 10 else "medium"
        reason = "單月與累計營收年增率同步達到成長門檻"
    elif yoy <= -10 and cumulative <= -5:
        direction = "negative"
        confidence = "high" if yoy <= -20 and cumulative <= -10 else "medium"
        reason = "單月與累計營收年增率同步落入衰退門檻"
    elif yoy * cumulative < 0 or (mom is not None and abs(mom) >= 10 and mom * yoy < 0):
        direction = "mixed"
        confidence = "medium"
        reason = "月增、年增或累計趨勢方向分歧"
    else:
        direction = "neutral"
        confidence = "medium"
        reason = "營收變化尚未達到明確成長或衰退門檻"
    return {
        "direction": direction,
        "confidence": confidence,
        "time_horizon": "medium_term",
        "reason": reason,
        "metrics": {
            "month_over_month_pct": mom,
            "year_over_year_pct": yoy,
            "cumulative_year_over_year_pct": cumulative,
        },
        "analysis_version": EXTERNAL_EVENT_ANALYSIS_VERSION,
        "can_override_main_status": False,
    }


def classify_company_disclosure(subject: Any, explanation: Any = "") -> dict[str, Any]:
    result = classify_text_event(subject, explanation)
    return {
        **result,
        "confidence": "medium" if result["direction"] != "unknown" else "low",
    }


def score_event_reference_quality(
    *,
    source_quality: str,
    source_class: str,
    event_type: str,
    has_exact_timestamp: bool,
    direction: str,
    affected_terms: list[str] | None = None,
    has_structured_metrics: bool = False,
) -> dict[str, float]:
    """Calibrate authenticity separately from usefulness for stock direction."""

    quality = str(source_quality or "").lower()
    source_kind = str(source_class or "").lower()
    event_kind = str(event_type or "").lower()
    reliability = 0.97 if quality == "official" else 0.82 if quality == "licensed" else 0.0
    if "authorized_social" in source_kind:
        reliability = min(reliability, 0.75)
    if not has_exact_timestamp:
        reliability *= 0.85

    if event_kind == "monthly_revenue" and has_structured_metrics:
        reference_value = 0.95
    elif event_kind == "government_policy":
        reference_value = 0.75 if affected_terms and direction != "unknown" else 0.45
    elif event_kind == "official_news":
        reference_value = 0.65 if affected_terms and direction != "unknown" else 0.35
    elif event_kind == "authorized_social":
        reference_value = 0.62 if affected_terms and direction != "unknown" else 0.3
    else:
        reference_value = 0.5 if direction != "unknown" else 0.25
    if not has_exact_timestamp and event_kind != "monthly_revenue":
        reference_value *= 0.8
    return {
        "reliability_score": round(max(0.0, min(reliability, 1.0)), 4),
        "reference_value_score": round(max(0.0, min(reference_value, 1.0)), 4),
    }
