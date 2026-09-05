from __future__ import annotations

"""Strict validator and backend placeholder renderer for model-analysis-v2."""

import json
import re
import time
import unicodedata
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from core.line_model_contract import fact_supports_requested_scope
from core.public_url import normalize_public_https_url


MODEL_ANALYSIS_VERSION = "model-analysis-v2"
MODEL_ANALYSIS_VALIDATOR_VERSION = "ModelAnalysisV2ValidatorV1"
_PLACEHOLDER = re.compile(r"\{\{(F\d{3,})\}\}")
_FACT_OR_EVENT_PLACEHOLDER = re.compile(r"\{\{([FE]\d{3,})\}\}")
# Recognized placeholder tokens are stripped before this check and validated
# separately, as are evidence IDs; neither is treated as prose. A letter prefix
# must not exempt raw periods/values (Q2, EPS9) from the exact-binding contract.
_ARABIC_NUMBER_OR_DATE = re.compile(r"[-+]?\d+(?:[.,]\d+)*(?:%|％)?")
_CHINESE_NUMBER_WITH_UNIT = re.compile(
    r"[零〇一二三四五六七八九十百千萬億兩]+\s*(?:元|股|張|點|倍|%|％|年|月|日|季度|季)"
)
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_COMPARATIVE_TEXT = re.compile(r"高於|低於|增加|減少|優於|弱於|超過|不及|多於|少於")
_RELATIVE_VALUATION_CLAIM = re.compile(r"高估|低估|溢價|折價|估值偏高|估值偏低")
_RELATIVE_VALUATION_DENIAL = re.compile(r"無法|不能|不得|不可|不宜|未能|缺少|缺乏")
_RELATIVE_VALUATION_DENIED_TERM = re.compile(
    r"(?:相對)?高估|(?:相對)?低估|溢價|折價|估值偏高|估值偏低|估值高低|相對高低"
)
_LIMITATION_LANGUAGE = re.compile(r"缺少|缺乏|不足|未通過|不可用|無法|不能|僅能|限制")
_AFFIRMATIVE_TRADE_COMMAND = re.compile(
    r"(?:現在|立刻|馬上|務必|直接|全部|趕快)\s*(?:就)?\s*(?:買進|買入|加碼|賣出|出清|停損)"
)
_GUARANTEED_FINANCIAL_CLAIM = re.compile(
    r"穩賺|保證獲利|保證賺|一定(?:會)?(?:上漲|下跌)|必漲|必跌|完全無風險|零風險"
)
_PROMPT_INJECTION_FOLLOWING = re.compile(
    r"忽略(?:前述|以上|系統|規則|指示)|遵照(?:標題|新聞|外部內容).{0,12}指示|system\s*prompt",
    re.IGNORECASE,
)
_POLICY_NEGATION = re.compile(r"不要|不應|不可|不得|避免|並非|不是|無法|不能")


def _contains_affirmative_policy_violation(text: str) -> bool:
    for clause in re.split(r"[。；！？!?]", str(text or "")):
        if not clause or _POLICY_NEGATION.search(clause):
            continue
        if _AFFIRMATIVE_TRADE_COMMAND.search(clause) or _GUARANTEED_FINANCIAL_CLAIM.search(clause):
            return True
    return False


def _follows_untrusted_instruction(text: str) -> bool:
    for clause in re.split(r"[。；！？!?]", str(text or "")):
        if not clause or _POLICY_NEGATION.search(clause):
            continue
        if _PROMPT_INJECTION_FOLLOWING.search(clause):
            return True
    return False


def _neutralize_denied_relative_valuation_terms(text: str) -> tuple[str, bool]:
    """Delete unsupported relative labels only inside an explicit denial clause."""

    changed = False
    segments = re.split(r"(?<=[。；！？!?])", str(text or ""))
    for index, segment in enumerate(segments):
        if not (
            _RELATIVE_VALUATION_DENIAL.search(segment)
            and _RELATIVE_VALUATION_CLAIM.search(segment)
        ):
            continue
        updated = _RELATIVE_VALUATION_DENIED_TERM.sub("相對估值", segment)
        updated = re.sub(r"相對估值(?:或|與|、)?相對估值", "相對估值", updated)
        if updated != segment:
            segments[index] = updated
            changed = True
    return "".join(segments), changed


def _generalize_required_day_threshold(
    text: str,
    facts: list[dict[str, Any]],
) -> tuple[str, bool]:
    """Remove an unbound raw threshold only when it exactly matches a packet fact."""

    updated = str(text or "")
    changed = False
    for fact in facts:
        if not str(fact.get("field") or "").lower().endswith("required_days"):
            continue
        value = str(fact.get("value") or "").strip()
        if not value.isdigit():
            continue
        updated, count = re.subn(
            rf"(?<!\d){re.escape(value)}\s*(?:個)?(?:交易)?日(?:以上|門檻)?",
            "所需交易日門檻",
            updated,
        )
        changed = changed or bool(count)
    return updated, changed


@dataclass(frozen=True)
class ModelValidationResult:
    passed: bool
    reason_codes: tuple[str, ...]
    analysis: dict[str, Any] | None
    rendered_blocks: tuple[str, ...]
    ungrounded_claim_count: int
    referee_override_count: int
    validation_duration_ms: float = 0.0
    render_duration_ms: float = 0.0


def _reject(
    *reasons: str,
    analysis: dict[str, Any] | None = None,
    started_ns: int | None = None,
    render_duration_ns: int = 0,
) -> ModelValidationResult:
    unique = tuple(dict.fromkeys(reason for reason in reasons if reason)) or ("invalid_model_output",)
    elapsed_ns = max(0, time.perf_counter_ns() - started_ns) if started_ns is not None else 0
    return ModelValidationResult(
        passed=False,
        reason_codes=unique,
        analysis=analysis,
        rendered_blocks=(),
        ungrounded_claim_count=int("ungrounded_numeric_or_date_claim" in unique),
        referee_override_count=int("referee_override_attempt" in unique),
        validation_duration_ms=round(max(0, elapsed_ns - render_duration_ns) / 1_000_000, 3),
        render_duration_ms=round(max(0, render_duration_ns) / 1_000_000, 3),
    )


def parse_model_analysis_v2(text: str) -> dict[str, Any]:
    cleaned = _FENCE.sub("", str(text or "").strip())
    value = json.loads(cleaned)
    if not isinstance(value, dict):
        raise ValueError("model analysis must be a JSON object")
    return value


def deterministic_limitation_placeholder_repair(
    output: str | dict[str, Any],
    packet: dict[str, Any],
) -> tuple[dict[str, Any] | None, tuple[str, ...]]:
    """Apply only grounding-safe deletions or label generalizations.

    This never creates a number, date, evidence ID, placeholder, or claim. It
    can remove a redundant cited event marker, remove a non-renderable absence
    placeholder from limitation prose, or generalize a cited indicator/date
    label so the model cannot leak a period number as a raw claim.
    """

    try:
        analysis = parse_model_analysis_v2(output) if isinstance(output, str) else deepcopy(output)
    except (ValueError, TypeError, json.JSONDecodeError):
        return None, ()
    facts = {
        str(item.get("fact_id") or ""): item
        for item in packet.get("facts") or []
        if isinstance(item, dict) and item.get("fact_id")
    }
    events = {
        str(item.get("event_id") or ""): item
        for item in packet.get("events") or []
        if isinstance(item, dict) and item.get("event_id")
    }
    repairs: list[str] = []
    blocks = analysis.get("explanation_blocks")
    if isinstance(blocks, list):
        while len(blocks) > 5:
            removable = next(
                (
                    index
                    for index in range(len(blocks) - 1, -1, -1)
                    if isinstance(blocks[index], dict)
                    and str(blocks[index].get("block_type") or "") == "limitation"
                ),
                None,
            )
            if removable is None:
                break
            del blocks[removable]
            repairs.append("removed_redundant_limitation_block")
    for block in analysis.get("explanation_blocks") or []:
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("block_type") or "")
        template = str(block.get("text_template") or "")
        evidence_ids = {str(item) for item in block.get("evidence_ids") or []}
        event_placeholder_removed = False
        placeholders_before_repair = set(_FACT_OR_EVENT_PLACEHOLDER.findall(template))
        cited_limitation_only = {
            evidence_id
            for evidence_id in evidence_ids
            if evidence_id in facts
            and (
                set(str(item) for item in (facts[evidence_id].get("use_scope") or []))
                == {"limitation"}
                or facts[evidence_id].get("quality")
                in {"missing", "unavailable", "stale", "source_delayed"}
            )
        }
        if (
            block_type != "limitation"
            and cited_limitation_only
            and not cited_limitation_only.intersection(placeholders_before_repair)
            and _LIMITATION_LANGUAGE.search(template)
        ):
            block_type = "limitation"
            block["block_type"] = "limitation"
            repairs.append("reclassified_limitation_evidence_block")
        for evidence_id in _FACT_OR_EVENT_PLACEHOLDER.findall(template):
            fact = facts.get(evidence_id)
            event = events.get(evidence_id)
            fact_scope = set(str(item) for item in (fact or {}).get("use_scope") or [])
            fact_is_absence = bool(
                fact
                and (
                    (fact or {}).get("quality") in {"missing", "unavailable", "stale", "source_delayed"}
                    or fact_scope == {"limitation"}
                )
            )
            event_marker_is_removable = event is not None and evidence_id in evidence_ids
            absence_marker_is_removable = (
                block_type == "limitation"
                and fact_is_absence
                and evidence_id in evidence_ids
            )
            if not event_marker_is_removable and not absence_marker_is_removable:
                continue
            template = template.replace(f"{{{{{evidence_id}}}}}", "")
            event_placeholder_removed = event_placeholder_removed or event is not None
            repairs.append(
                "removed_event_placeholder" if event is not None else "removed_absence_placeholder"
            )
        if event_placeholder_removed:
            template, dangling_list_count = re.subn(
                r"，?\s*分別為\s*(?:與|和|、)?\s*(?=[，。；])",
                "",
                template,
            )
            template, dangling_value_count = re.subn(
                r"(?:(?<=。)|^)[^。；]{0,80}資料為\s*(?=[。；])",
                "",
                template,
            )
            template = re.sub(r"。{2,}", "。", template)
            if dangling_list_count or dangling_value_count:
                repairs.append("removed_event_placeholder_dangling_clause")
        cited_fields = {
            str((facts.get(evidence_id) or {}).get("field") or "")
            for evidence_id in evidence_ids
            if evidence_id in facts
        }
        field_aware_aliases = (
            (r"(?<![A-Za-z0-9])RSI\s*(?:5|10|14)(?![A-Za-z0-9])", "相對強弱指標", "rsi.", "generalized_rsi_label"),
            (r"(?<![A-Za-z0-9])ATR\s*14(?![A-Za-z0-9])", "平均真實波幅", "atr14", "generalized_atr_label"),
            (r"短均線", "均線", "moving_averages.", "generalized_ma_label"),
            (r"(?:五|5)日均線", "短期均線", "moving_averages.ma5", "generalized_ma_label"),
            (r"(?:十|10)日均線", "次短期均線", "moving_averages.ma10", "generalized_ma_label"),
            (r"(?:二十|20)日均線", "中期均線", "moving_averages.ma20", "generalized_ma_label"),
            (r"(?:六十|60)日均線", "長期均線", "moving_averages.ma60", "generalized_ma_label"),
            (r"(?:二十|20)日均量", "資料庫均量", "volume_ma20", "generalized_volume_average_label"),
        )
        for pattern, replacement, required_field, repair_code in field_aware_aliases:
            field_is_cited = (
                any(field.startswith(required_field) for field in cited_fields)
                if required_field.endswith(".")
                else required_field in cited_fields
            )
            if not field_is_cited:
                continue
            updated, count = re.subn(pattern, replacement, template, flags=re.IGNORECASE)
            if count:
                template = updated
                repairs.append(repair_code)
        if any(evidence_id in events for evidence_id in evidence_ids):
            updated, count = re.subn(
                r"(?:一|二|三|四|五|六|七|八|九|十|十一|十二|[1-9]|1[0-2])月",
                "最近一期",
                template,
            )
            if count:
                template = updated
                repairs.append("generalized_cited_event_month")
            for evidence_id in sorted(evidence_ids.intersection(events)):
                updated, count = re.subn(
                    rf"(?<![A-Za-z0-9]){re.escape(evidence_id)}(?![A-Za-z0-9])",
                    "最近事件",
                    template,
                )
                if count:
                    template = updated
                    repairs.append("generalized_cited_event_id")
            template = re.sub(
                r"最近事件(?:\s*[、，,]\s*最近事件)+",
                "近期事件",
                template,
            )
            template = re.sub(r"(?:最近事件){2,}", "近期事件", template)
            template = template.replace("事件近期事件", "近期事件")
        cited_facts = [facts[evidence_id] for evidence_id in evidence_ids if evidence_id in facts]
        template, threshold_changed = _generalize_required_day_threshold(template, cited_facts)
        if threshold_changed:
            repairs.append("generalized_required_day_threshold")
        template, relative_changed = _neutralize_denied_relative_valuation_terms(template)
        if relative_changed:
            repairs.append("neutralized_denied_relative_valuation_terms")
        for evidence_id in _PLACEHOLDER.findall(template):
            fact = facts.get(evidence_id)
            if fact is None or evidence_id not in evidence_ids:
                continue
            if not _localized_render_unit(str(fact.get("unit") or "")):
                continue
            updated, count = re.subn(
                rf"(\{{\{{{re.escape(evidence_id)}\}}\}})\s*(?:元|股|張|點|倍|%|％)",
                r"\1",
                template,
            )
            if count:
                template = updated
                repairs.append("removed_duplicate_render_unit")
        template = re.sub(r"\s+([，。；、])", r"\1", template)
        template = re.sub(r"[ \t]{2,}", " ", template).strip()
        block["text_template"] = template
        conditions = block.get("conditions")
        if isinstance(conditions, list):
            repaired_conditions: list[Any] = []
            for condition in conditions:
                if not isinstance(condition, str):
                    repaired_conditions.append(condition)
                    continue
                repaired_condition, changed = _generalize_required_day_threshold(
                    condition,
                    cited_facts,
                )
                if changed:
                    repairs.append("generalized_required_day_threshold")
                repaired_conditions.append(repaired_condition)
            block["conditions"] = repaired_conditions
    all_required_day_facts = [
        fact
        for fact in facts.values()
        if str(fact.get("field") or "").lower().endswith("required_days")
    ]
    for key in ("missing_data", "research_limitations"):
        values = analysis.get(key)
        if not isinstance(values, list):
            continue
        repaired_values: list[Any] = []
        for value in values:
            if not isinstance(value, str):
                repaired_values.append(value)
                continue
            repaired_value, changed = _generalize_required_day_threshold(
                value,
                all_required_day_facts,
            )
            if changed:
                repairs.append("generalized_required_day_threshold")
            repaired_values.append(repaired_value)
        analysis[key] = repaired_values
    return analysis, tuple(dict.fromkeys(repairs))


def _render_fact(fact: dict[str, Any]) -> str:
    value = re.sub(r"(?<=\d)\.0+$", "", str(fact.get("value")))
    unit = _localized_render_unit(str(fact.get("unit") or ""))
    if fact.get("quality") == "estimated":
        return f"推估 {value}" + unit
    return value + unit


def _localized_render_unit(unit: str) -> str:
    mapping = {
        "TWD": " 元",
        "shares": " 股",
        "lots": " 張",
        "percent": "%",
        "ratio": " 倍",
        "point": " 點",
        "points": " 點",
        "index": "",
        "count": "",
    }
    return mapping.get(str(unit), f" {unit}" if unit else "")


_ABSENCE_CLAIM = "absence_only_evidence_cannot_support_claim"
_UNKNOWN_LIMITATION = "unverifiable_limitation_claim"


def _restricted_limitation_fact(fact: dict[str, Any]) -> bool:
    # Consumer projection only: never recompute DB quality or trust a value's prose.
    scopes = {str(item) for item in fact.get("use_scope") or []}
    return fact.get("quality") not in {"ok", "estimated"} or not scopes or scopes == {"limitation"}


def _limitation_topic_aliases(facts: list[dict[str, Any]]) -> set[str]:
    aliases: set[str] = set()
    for fact in facts:
        if fact.get("authority_tier") != "canonical_db" or not _restricted_limitation_fact(fact):
            continue
        domain, field = str(fact.get("domain") or ""), str(fact.get("field") or "")
        if domain == "support_resistance" or field.startswith("support_resistance."):
            aliases.update(("支撐", "壓力", "支撐壓力", "支撐壓力區間", "support_resistance"))
        if domain == "fundamentals" or field.startswith("fundamentals."):
            aliases.update(("基本面", "fundamentals"))
        if domain == "valuation" and field == "relative_value_assessment":
            aliases.update(("比較基準", "相對估值", "相對估值基準"))
    return aliases


def _eligible_limitation_number(fact: dict[str, Any]) -> Decimal | None:
    if (
        fact.get("authority_tier") != "canonical_db"
        or _restricted_limitation_fact(fact)
        or "numeric_claim" not in (fact.get("use_scope") or [])
        or isinstance(fact.get("value"), bool)
    ):
        return None
    try:
        value = Decimal(str(fact.get("value")))
    except (InvalidOperation, ValueError):
        return None
    return value if value.is_finite() else None


def _limitation_bound_clause(clause: str, facts: list[dict[str, Any]]) -> str | None:
    """Return a narrow comparison capability, never a technical assessment."""
    by_id: dict[str, list[dict[str, Any]]] = {}
    for fact in facts:
        by_id.setdefault(str(fact.get("fact_id") or ""), []).append(fact)

    def unique(fact_id: str) -> dict[str, Any]:
        matches = by_id.get(fact_id, [])
        return matches[0] if len(matches) == 1 else {}

    pe = re.fullmatch(r"本益比為\{\{(F\d{3,})\}\}", clause)
    if pe:
        fact = unique(pe[1])
        if (fact.get("domain"), fact.get("field"), fact.get("unit")) == ("valuation", "pe_ratio", "ratio"):
            if _eligible_limitation_number(fact) is not None:
                return "value"
        return None
    match = re.fullmatch(
        r"(當日成交量|成交量|收盤價|收盤)\{\{(F\d{3,})\}\}"
        r"(低於|高於)(資料庫均量|均量|均線)\{\{(F\d{3,})\}\}", clause,
    )
    if not match:
        return None
    left, right = unique(match[2]), unique(match[5])
    lv, rv = _eligible_limitation_number(left), _eligible_limitation_number(right)
    if lv is None or rv is None or left.get("period") != "daily":
        return None
    if any(not left.get(key) or left.get(key) != right.get(key) for key in ("period", "as_of", "trade_date")):
        return None
    left_key = (str(left.get("domain") or ""), str(left.get("field") or ""))
    right_key = (str(right.get("domain") or ""), str(right.get("field") or ""))
    if (
        match[1] in {"當日成交量", "成交量"} and match[3] == "低於" and match[4] in {"資料庫均量", "均量"}
        and left_key in {("official_ohlcv", "volume_shares"), ("trading_state", "volume_shares")}
        and right_key == ("technical", "volume_ma20")
        and left.get("unit") == right.get("unit") == "shares" and 0 <= lv < rv
    ):
        return "volume_comparison"
    if (
        match[1] in {"收盤", "收盤價"} and match[3] == "高於" and match[4] == "均線"
        and left_key == ("official_ohlcv", "close") and right_key == ("technical", "moving_averages.ma20")
        and left.get("unit") == right.get("unit") == "TWD" and lv > rv
    ):
        return "price_comparison"
    return None


def _research_foreign_cost_sample_insufficient(packet: dict[str, Any]) -> bool:
    """Authorize one nonnumeric research clause, not cost/holdings availability.

    The single-stock packet must contain exactly one canonical foreign-cost pair.
    Do not filter bad/duplicate candidates away and then pick a convenient pair.
    This consumes existing quality metadata; it never recomputes a business
    threshold, changes DB quality, or grants numeric rendering permission.
    """
    request = packet.get("request")
    if not isinstance(request, dict) or not isinstance(request.get("scopes"), list):
        return False
    if "institutional" not in request["scopes"]:
        return False

    def iso_day(value: Any) -> date | None:
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            return None
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None

    cutoff = iso_day(request.get("analysis_cutoff"))
    if cutoff is None:
        return False
    facts = [fact for fact in packet.get("facts") or [] if isinstance(fact, dict)]
    prefix = "canonical_costs.foreign_estimated."
    required = [fact for fact in facts if fact.get("field") == prefix + "required_days"]
    sample = [fact for fact in facts if fact.get("field") == prefix + "sample_days"]
    if len(required) != 1 or len(sample) != 1:
        return False
    required_fact, sample_fact = required[0], sample[0]
    values: list[Decimal] = []
    for fact in (required_fact, sample_fact):
        fact_id, scopes = fact.get("fact_id"), fact.get("use_scope")
        if (
            fact.get("domain") != "institutional_context"
            or fact.get("authority_tier") != "canonical_db"
            or fact.get("quality") != "ok"
            or fact.get("unit") != "count"
            or fact.get("period") != "daily"
            or not isinstance(scopes, list)
            or "numeric_claim" not in scopes or "explanation" not in scopes
            or not isinstance(fact_id, str) or not fact_id
            or sum(other.get("fact_id") == fact_id for other in facts) != 1
        ):
            return False
        value = _eligible_limitation_number(fact)
        if value is None or value != value.to_integral_value():
            return False
        values.append(value)
    for key in ("trade_date", "as_of"):
        observed_day = iso_day(required_fact.get(key))
        if (
            observed_day is None or observed_day > cutoff
            or required_fact.get(key) != sample_fact.get(key)
        ):
            return False
    # Existing canonical required_days is the threshold; no constant day count.
    required_days, sample_days = values
    return 0 <= sample_days < required_days


_VALUATION_BASELINE_LIMITATION_CLAUSES = frozenset({
    "缺少比較基準", "無法進行相對估值判斷",
    "缺少同業或歷史估值基準", "只能列示估值數值",
})


def _valuation_baseline_absence_fact_id(packet: dict[str, Any]) -> str | None:
    """Bind one known absence status, not arbitrary evidence-summary prose.

    quality=ok describes the canonical status record, not an available peer
    baseline. This grants only the finite nonnumeric limitation clauses below;
    it never changes quality, numeric eligibility, or relative-valuation rules.
    """
    request = packet.get("request")
    if not isinstance(request, dict) or not isinstance(request.get("scopes"), list):
        return None
    if "valuation" not in request["scopes"]:
        return None

    def iso_day(value: Any) -> date | None:
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            return None
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None

    cutoff = iso_day(request.get("analysis_cutoff"))
    if cutoff is None:
        return None
    facts = [f for f in packet.get("facts") or [] if isinstance(f, dict)]
    candidates = [f for f in facts if f.get("field") == "relative_value_assessment"]
    # Do not discard an invalid/duplicate candidate and select a convenient one.
    if len(candidates) != 1:
        return None
    fact = candidates[0]
    fact_id, scopes = fact.get("fact_id"), fact.get("use_scope")
    if (
        fact.get("domain") != "valuation"
        or fact.get("value") != "unavailable_without_peer_or_historical_baseline"
        or fact.get("authority_tier") != "canonical_db"
        or fact.get("quality") != "ok"
        or fact.get("period") != "daily"
        or fact.get("unit") is not None
        or not isinstance(scopes, list) or "explanation" not in scopes
        or not isinstance(fact_id, str) or not re.fullmatch(r"F[0-9]{3,}", fact_id)
        or sum(f.get("fact_id") == fact_id for f in facts) != 1
    ):
        return None
    for key in ("trade_date", "as_of"):
        observed = iso_day(fact.get(key))
        if observed is None or observed > cutoff:
            return None
    return fact_id


def _has_valuation_baseline_limitation_clause(text: str) -> bool:
    normalized = re.sub(r"[^\S\n]+", "", unicodedata.normalize("NFKC", text))
    return any(clause in _VALUATION_BASELINE_LIMITATION_CLAUSES for clause in
               re.split(r"[。;\n,]|但是|然而|而且|但|且", normalized))


def _limitation_surface_reasons(
    text: str, facts: list[dict[str, Any]], events: list[dict[str, Any]], *, metadata_only: bool = False,
    research_cost_sample_insufficient: bool = False,
    valuation_baseline_missing: bool = False,
) -> list[str]:
    """Fully consume a finite clause grammar; unknown prose fails closed.

    No model-supplied type/value can add an alias or authorize an assessment.
    State is local to ONE surface. Only an adjacent comma carries a comparison.
    """
    normalized = unicodedata.normalize("NFKC", text)
    normalized = re.sub(r"[^\S\n]+", "", normalized)
    topics = _limitation_topic_aliases(facts)
    topic_pattern = "(?:" + "|".join(re.escape(t) for t in sorted(topics, key=lambda t: (-len(t), t))) + ")" if topics else r"(?!)"
    topic_list = topic_pattern + r"(?:(?:、|或|及|與|和)" + topic_pattern + r")*"
    absence = rf"(?:(?:目前)?(?:缺少|缺乏)(?:可核對的)?(?:{topic_list})?(?:資料|欄位)?|(?:{topic_list})?(?:資料不足|資料不可用|不可用))"
    epistemic = rf"(?:無法|不能)判斷(?:{topic_list}|是否(?:偏多|偏空|轉強|轉弱)|是否成立)"
    pieces = re.split(r"([。;\n,]|但是|然而|而且|但|且)", normalized)
    previous = ""
    reasons: list[str] = []
    for index in range(0, len(pieces), 2):
        clause = pieces[index]
        separator = pieces[index - 1] if index else ""
        # Even an unpunctuated contrast ends a prior comparison/negation.
        antecedent = previous if separator == "," else ""
        previous = ""
        if not clause:
            if separator in {"但是", "然而", "而且", "但", "且"}:
                reasons.append(_UNKNOWN_LIMITATION)
            continue
        if research_cost_sample_insufficient and clause == "本包外資近期增量成本估算樣本不足":
            # Recognize this whole clause only; do not authorize its next clause.
            continue
        if clause in _VALUATION_BASELINE_LIMITATION_CLAUSES:
            if not valuation_baseline_missing:
                reasons.append(_UNKNOWN_LIMITATION)
            # A supported clause cannot authorize a following directional claim.
            continue
        if re.fullmatch(absence, clause) or clause in topics or clause in {"資料不足", "缺少資料"}:
            continue
        if re.fullmatch(epistemic, clause) or clause in {"只能說明判讀限制", "只能說明後續判讀條件"}:
            continue
        if clause in {"若資料補齊", "待資料補齊"} or re.fullmatch(rf"(?:再評估|再判讀){topic_list}", clause):
            continue
        if not metadata_only:
            capability = _limitation_bound_clause(clause, facts)
            if capability:
                previous = capability
                continue
        if clause in {"量能未放大", "量能未顯著放大"} and antecedent == "volume_comparison":
            previous = "volume_restatement"
            continue
        if clause == "僅反映價格相對位置" and antecedent == "price_comparison":
            continue
        if clause in {"需留意後續量價配合情況", "需留意後續量能變化對趨勢的影響"} and antecedent in {"volume_comparison", "volume_restatement"}:
            continue
        if re.fullmatch(r"(?:近期)?事件(?:尚未驗證|仍待驗證)", clause):
            if events and all(e.get("verification_state") == "unverified" for e in events):
                continue
        # There is deliberately NO v1 current_assessment allow mapping. A possible
        # technical assessment is unknown, not silently authorized by its text.
        assessment = re.fullmatch(
            r"(?:均線(?:結構|排列)?|(?:股價|價格)(?:趨勢)?|趨勢|獲利)"
            r"(?:目前|已|就會)?(?:偏多|偏空|轉弱|轉強|上漲|下跌|改善|惡化)", clause,
        )
        if assessment:
            possible_assessment = not metadata_only and any(
                not _restricted_limitation_fact(f) and f.get("authority_tier") == "canonical_db"
                and "explanation" in (f.get("use_scope") or []) and f.get("domain") == "technical"
                and f.get("field") in {"trend", "moving_average_trend", "technical_status"}
                for f in facts
            )
            reasons.append(_UNKNOWN_LIMITATION if possible_assessment else _ABSENCE_CLAIM)
        else:
            reasons.append(_UNKNOWN_LIMITATION)
    return reasons


def _absence_related_reasons(analysis: dict[str, Any], packet: dict[str, Any]) -> list[str]:
    all_facts = [f for f in packet.get("facts") or [] if isinstance(f, dict)]
    all_events = [e for e in packet.get("events") or [] if isinstance(e, dict)]
    valuation_absence_id = _valuation_baseline_absence_fact_id(packet)
    reasons: list[str] = []
    for block in analysis["explanation_blocks"]:
        ids = set(block.get("evidence_ids") or [])
        facts = [f for f in all_facts if f.get("fact_id") in ids]
        protected = any(_restricted_limitation_fact(f) for f in facts) or (not ids and block["block_type"] == "limitation")
        if block["block_type"] == "limitation" and any(
            _has_valuation_baseline_limitation_clause(text)
            for text in [block["text_template"], *block["conditions"]]
        ):
            # The ok/explanation status alone is not a restricted fact. These
            # claims still need verification even without an absence-only ID.
            protected = True
        if not protected:
            continue
        events = [e for e in all_events if e.get("event_id") in ids]
        for text in [block["text_template"], *block["conditions"]]:
            reasons.extend(_limitation_surface_reasons(
                text, facts, events,
                valuation_baseline_missing=(block["block_type"] == "limitation"
                                           and valuation_absence_id in ids),
            ))
    for surface in ("missing_data", "research_limitations"):
        cost_sample_insufficient = (
            surface == "research_limitations" and bool(analysis.get(surface))
            and _research_foreign_cost_sample_insufficient(packet)
        )
        for text in analysis.get(surface) or []:
            reasons.extend(_limitation_surface_reasons(
                text, all_facts, all_events, metadata_only=True,
                research_cost_sample_insufficient=cost_sample_insufficient,
                valuation_baseline_missing=valuation_absence_id is not None,
            ))
    return reasons




# Finite claim binding (design-v1.2): pure helpers, no I/O or business formulas.
# Prefixes isolate these symbols from the pre-existing validator/repair rules.
_cb_base_PH = re.compile('\\{\\{(F[0-9]{3,})\\}\\}')
_cb_base_HARD = re.compile('[。；;！？!?\\n，,]|並且|但是|然而|同時|且|並|但|而')
_cb_base_LIST = re.compile('[、與及和]')
_cb_base_NAME = re.compile('[與及和]')
_cb_base_OPS = {**{x: '>' for x in ('高於', '大於', '超過', '多於')}, **{x: '<' for x in ('低於', '小於', '不及', '少於')}, '等於': '=', **{x: '>=' for x in ('不低於', '不小於', '大於等於')}, **{x: '<=' for x in ('不高於', '不大於', '小於等於')}}
_cb_base_OP = re.compile('|'.join(sorted(_cb_base_OPS, key=len, reverse=True)))
_cb_base_UNSUPPORTED = re.compile('約等於|接近|顯著高於|偏高|優於|弱於|增加|減少|突破|跌破|上穿|下穿')
_cb_base_RANGE = re.compile('位於.*(?:之間|上方|之上|下方|之下)')
_cb_base_DEFERRED = {'短期均線', '中期均線', '長期均線', '月線', '季線'}
_cb_base_PREFIX = re.compile('^(?:截至\\{\\{F[0-9]{3,}\\}\\}|技術面顯示|技術面|估值指標顯示|估值方面|估值指標|配合)')
_cb_base_NS = re.compile('^(MACD指標中|MACD指標|MACD的|MACD|KD指標|KD)')
_cb_base_MA = {'moving_averages.' + x for x in ('ma5', 'ma10', 'ma20', 'ma60')}
_cb_base_BOLL = {'bollinger.' + x for x in ('upper', 'middle', 'lower')}
_cb_base_PRICE = {'close', 'open', 'high', 'low'} | _cb_base_MA | _cb_base_BOLL
_cb_base_LABELS = {}

def _cb_base_labels(_cb_base_names, fields, domains, unit):
    for name in _cb_base_names.split('/'):
        _cb_base_LABELS[name] = (set(fields.split('/')), set(domains.split('/')), unit)
_cb_base_labels('本益比/市盈率/PE', 'pe_ratio', 'valuation', 'ratio')
_cb_base_labels('股價淨值比/市帳比/PB', 'pb_ratio', 'valuation', 'ratio')
_cb_base_labels('殖利率/股利殖利率', 'dividend_yield_pct', 'valuation', 'percent')
_cb_base_labels(
    '無償配股率/配股率',
    'corporate_action.stock_distribution_ratio',
    'recommendation_safety',
    'ratio',
)
_cb_base_labels(
    '股數倍率/除權後股數倍率/股份數倍率',
    'corporate_action.share_count_factor',
    'recommendation_safety',
    'ratio',
)
_cb_base_labels(
    '價格基準倍率/除權價格基準倍率',
    'corporate_action.pre_event_price_multiplier',
    'recommendation_safety',
    'ratio',
)
for _cb_base_names, _cb_base_field in (('收盤價/收盤', 'close'), ('開盤價/開盤/當日開盤', 'open'), ('最高價/當日最高價/當日最高', 'high'), ('最低價/當日最低價/當日最低', 'low')):
    _cb_base_labels(_cb_base_names, _cb_base_field, 'official_ohlcv', 'TWD')
_cb_base_labels('當日成交量/成交量', 'volume_shares', 'official_ohlcv/trading_state', 'shares')
_cb_base_labels('資料庫均量/均量', 'volume_ma20', 'technical', 'shares')
for _cb_base_word, _cb_base_field in (('中', 'middle'), ('上', 'upper'), ('下', 'lower')):
    _cb_base_labels(f'布林{_cb_base_word}軌/布林通道{_cb_base_word}軌', 'bollinger.' + _cb_base_field, 'technical', 'TWD')
_cb_base_labels('平均真實波幅', 'atr14', 'technical', 'TWD')
_cb_base_labels('DIF', 'macd.dif', 'technical', 'TWD')
_cb_base_labels('Signal/訊號線', 'macd.signal', 'technical', 'TWD')
_cb_base_labels('OSC/Oscillator', 'macd.oscillator', 'technical', 'TWD')
_cb_base_labels('相對強弱指標/RSI', '/'.join(sorted(('rsi.' + x for x in ('rsi5', 'rsi10', 'rsi14')))), 'technical', 'index')
_cb_base_labels('均線/短中長期均線', '/'.join(sorted(_cb_base_MA)), 'technical', 'TWD')
_cb_base_MISSING = 'claim_operand_binding_missing'
_cb_base_AMBIG = 'claim_operand_binding_ambiguous'
_cb_base_STRUCT = 'comparison_structure_unverifiable'
_cb_base_UNKNOWN = 'claim_field_label_unverifiable'
_cb_base_MISMATCH = 'claim_field_label_mismatch'
_cb_base_VALUE = 'claim_operand_value_invalid'
_cb_base_COMP = 'comparison_operands_not_comparable'
_cb_base_FALSE = 'comparison_direction_contradicts_evidence'

def _cb_base_normalize(text):
    chars, offsets = ([], [])
    for i, char in enumerate(text):
        for c in unicodedata.normalize('NFKC', char):
            if not c.isspace() or c == '\n':
                chars.append(c)
                offsets.append(i)
    return (''.join(chars), offsets)

def _cb_base_stripped_label(raw):
    return re.sub('(?:欄位|數值)?(?:為|是)?$', '', raw)

def _cb_base_operand(part, namespace='', boll=False):
    matches = list(_cb_base_PH.finditer(part))
    if not matches:
        return {'label': _cb_base_stripped_label(part), 'id': None, 'error': _cb_base_MISSING}
    if len(matches) != 1:
        return {'label': part, 'id': None, 'error': _cb_base_AMBIG}
    m = matches[0]
    label = _cb_base_stripped_label(part[:m.start()])
    tail = part[m.end():]
    if _cb_base_OP.search(tail) or _cb_base_PH.search(tail) or _cb_base_LIST.search(label):
        return {'label': label, 'id': m[1], 'error': _cb_base_STRUCT}
    alias = label
    if namespace == 'MACD':
        alias = {'快線': 'DIF', '慢線': 'Signal', '柱狀體': 'OSC'}.get(alias, alias)
    if boll:
        alias = {'上軌': '布林上軌', '中軌': '布林中軌', '下軌': '布林下軌'}.get(alias, alias)
    mapping = _cb_base_LABELS.get(alias)
    if namespace == 'KD' and alias in ('K值', 'D值'):
        mapping = ({'kd.k' if alias == 'K值' else 'kd.d'}, {'technical'}, 'index')
    return {'label': label, 'id': m[1], 'error': None, 'mapping': mapping, 'deferred': label in _cb_base_DEFERRED, 'tail': tail}

def _cb_base_decimal_value(fact):
    v = fact.get('value')
    if isinstance(v, (bool, list, dict)) or v is None:
        return None
    try:
        value = Decimal(str(v))
        return value if value.is_finite() else None
    except (InvalidOperation, ValueError):
        return None

def _cb_base_binding(op, facts, cited, comparison=False):
    if op.get('error'):
        return (op['error'], None)
    fid = op['id']
    candidates = [f for f in facts if f.get('fact_id') == fid]
    if fid not in cited or not candidates:
        return (_cb_base_MISSING, None)
    if len(candidates) != 1:
        return (_cb_base_AMBIG, None)
    f = candidates[0]
    if op.get('deferred'):
        return ('deferred_ma_label', f)
    if op['label'] and op.get('mapping') is None:
        return (_cb_base_UNKNOWN, f)
    mapping = op.get('mapping')
    if mapping and (f.get('field') not in mapping[0] or f.get('unit') != mapping[2] or (f.get('domain') is not None and f['domain'] not in mapping[1])):
        return (_cb_base_MISMATCH, f)
    if not f.get('field') or not f.get('unit'):
        return (_cb_base_MISMATCH, f)
    if f.get('authority_tier') != 'canonical_db' or f.get('quality') not in ('ok', 'estimated') or 'numeric_claim' not in f.get('use_scope', []):
        return (_cb_base_COMP if comparison else _cb_base_VALUE, f)
    if _cb_base_decimal_value(f) is None:
        return (_cb_base_COMP if comparison else _cb_base_VALUE, f)
    return (None, f)

def _cb_base_compare(left, right, operator, facts, cited):
    errors, bound = ([], [])
    for op in (left, right):
        err, fact = _cb_base_binding(op, facts, cited, True)
        errors.append(err)
        bound.append(fact)
    if 'deferred_ma_label' in errors:
        decisive = [e for e in errors if e and e != 'deferred_ma_label']
        return (decisive[0] if decisive else 'deferred_ma_label', bound)
    for code in (_cb_base_MISSING, _cb_base_AMBIG, _cb_base_STRUCT, _cb_base_UNKNOWN, _cb_base_MISMATCH, _cb_base_VALUE, _cb_base_COMP):
        if code in errors:
            return (code, bound)
    a, b = bound
    for k in ('period', 'as_of'):
        if not a.get(k) or a.get(k) != b.get(k):
            return (_cb_base_COMP, bound)
    for k in ('unit', 'currency', 'trade_date', 'code', 'entity'):
        if (a.get(k) is not None or b.get(k) is not None) and a.get(k) != b.get(k):
            return (_cb_base_COMP, bound)
    pair = {a['field'], b['field']}
    if not (pair <= _cb_base_PRICE or pair in ({'volume_shares', 'volume_ma20'}, {'macd.dif', 'macd.signal'}, {'kd.k', 'kd.d'})):
        return (_cb_base_COMP, bound)
    av, bv = (_cb_base_decimal_value(a), _cb_base_decimal_value(b))
    truth = {'>': av > bv, '<': av < bv, '=': av == bv, '>=': av >= bv, '<=': av <= bv}[_cb_base_OPS[operator]]
    return (None if truth else _cb_base_FALSE, bound)

def _cb_base_analyze(text, block_type, surface, facts, cited):
    """Pure finite-grammar simulation, with explicit rejected/deferred spans."""
    if surface != 'text_template' or block_type == 'scenario':
        return {'reasons': [], 'claims': [], 'deferred': ['deferred_future_condition']}
    normal, offsets = _cb_base_normalize(text)
    claims, deferred = ([], [])

    def record(segment, start, code=None, operands=None, operator=None, bound=None):
        claims.append({'normalized_segment': segment, 'raw_start': offsets[start] if start < len(offsets) else len(text), 'reason': code, 'operands': [{k: v for k, v in op.items() if k != 'mapping'} for op in operands or []], 'operator': operator, 'bound_facts': bound or []})
    ends = [(-1, 0)] + [(m.start(), m.end()) for m in _cb_base_HARD.finditer(normal)] + [(len(normal), len(normal))]
    for i in range(len(ends) - 1):
        start, end = (ends[i][1], ends[i + 1][0])
        segment = normal[start:end]
        if not segment:
            continue
        if re.match('^(若|如果|假如|倘若|只要)', segment):
            deferred.append('deferred_future_condition')
            continue
        body = segment
        while (p := _cb_base_PREFIX.match(body)):
            body = body[p.end():]
        namespace = ''
        if (ns := _cb_base_NS.match(body)):
            namespace = 'MACD' if ns[0].startswith('MACD') else 'KD'
            body = body[ns.end():]
        if not body:
            continue
        if (_cb_base_UNSUPPORTED.search(body) or _cb_base_RANGE.search(body)) and (_cb_base_PH.search(body) or re.search('(?:收盤價|價格)位於(?:布林|中軌|上軌|下軌)', body)):
            record(segment, start, _cb_base_STRUCT)
            continue
        operators = list(_cb_base_OP.finditer(body))
        if operators:
            if not _cb_base_PH.search(body) and (not any((label in body for label in _cb_base_LABELS))):
                continue
            if len(operators) > 1:
                record(segment, start, _cb_base_STRUCT)
                continue
            operator = operators[0]
            left = _cb_base_operand(body[:operator.start()], namespace)
            right_text = body[operator.end():]
            parts = _cb_base_LIST.split(right_text)
            right_ops, unary_ops, ma_parts = ([], [], [])
            ma_label = None
            boll = '布林' in right_text
            for part in parts:
                if re.match('^(短中長期均線|均線)(?:分別為|為)?\\{\\{', part):
                    m = re.match('^(短中長期均線|均線)(?:分別為|為)?', part)
                    ma_label = m[1]
                    op = _cb_base_operand(ma_label + part[m.end():], namespace)
                    ma_parts.append(op)
                elif ma_label and _cb_base_PH.match(part):
                    op = _cb_base_operand(ma_label + part, namespace)
                    ma_parts.append(op)
                else:
                    op = _cb_base_operand(part, namespace, boll)
                if re.match('^為(?:正值|負值|正|負)', op.get('tail', '')):
                    unary_ops.append(op)
                else:
                    right_ops.append(op)
            if ma_label and ma_label == '短中長期均線' and (len(ma_parts) != 3):
                record(segment, start, _cb_base_MISSING, [left] + right_ops)
                continue
            if ma_parts and len({o['id'] for o in ma_parts}) != len(ma_parts):
                record(segment, start, _cb_base_AMBIG, [left] + right_ops)
                continue
            ma_fields = [next((f.get('field') for f in facts if f.get('fact_id') == o['id']), None) for o in ma_parts]
            if ma_label == '短中長期均線' and len(set(ma_fields)) != len(ma_fields):
                record(segment, start, _cb_base_AMBIG, [left] + right_ops)
                continue
            if len(right_ops) > 7:
                record(segment, start, _cb_base_STRUCT)
                continue
            for right in right_ops:
                code, bound = _cb_base_compare(left, right, operator[0], facts, cited)
                if code == 'deferred_ma_label':
                    deferred.append(code)
                else:
                    record(segment, start, code, [left, right], operator[0], bound)
            for op in unary_ops:
                code, f = _cb_base_binding(op, facts, cited)
                record(segment, start, code, [op], bound=[f] if f else [])
                deferred.append('unary_sign_not_verified')
            continue
        if not _cb_base_PH.search(body):
            continue
        if '分別為' in body or '均為' in body:
            marker = '分別為' if '分別為' in body else '均為'
            lhs, rhs = body.split(marker, 1)
            _cb_base_names = _cb_base_NAME.split(lhs)
            ps = list(_cb_base_PH.finditer(rhs))
            if lhs in ('均線', '短中長期均線'):
                _cb_base_names = [lhs] * len(ps)
                bad = len(ps) < 2 or (lhs == '短中長期均線' and len(ps) != 3)
                bad = bad or len({p[1] for p in ps}) != len(ps)
            else:
                bad = len(_cb_base_names) != len(ps) or marker == '均為'
            connectors = [rhs[ps[j].end():ps[j + 1].start()] for j in range(len(ps) - 1)]
            allowed = ('、', '與', '及', '和') if lhs in ('均線', '短中長期均線') else ('與', '及', '和')
            if bad or any((x not in allowed for x in connectors)):
                record(segment, start, _cb_base_MISSING if len(_cb_base_names) != len(ps) or len(ps) < 3 else _cb_base_STRUCT)
                continue
            for name, p in zip(_cb_base_names, ps):
                op = _cb_base_operand(name + p[0], namespace, '布林' in lhs)
                code, f = _cb_base_binding(op, facts, cited)
                if code == 'deferred_ma_label':
                    deferred.append(code)
                else:
                    record(segment, start, code, [op], bound=[f] if f else [])
            continue
        parts = _cb_base_LIST.split(body)
        ma_label = None
        for part in parts:
            if not _cb_base_PH.search(part):
                continue
            if ma_label and _cb_base_PH.match(part):
                part = ma_label + part
            op = _cb_base_operand(part, namespace, '布林' in body)
            if op.get('label') in ('均線', '短中長期均線'):
                ma_label = op['label']
            code, f = _cb_base_binding(op, facts, cited)
            if code == 'deferred_ma_label':
                deferred.append(code)
            else:
                record(segment, start, code, [op], bound=[f] if f else [])
            if re.match('^為(?:正值|負值|正|負)', op.get('tail', '')):
                deferred.append('unary_sign_not_verified')
    return {'reasons': list(dict.fromkeys((c['reason'] for c in claims if c['reason']))), 'claims': claims, 'deferred': list(dict.fromkeys(deferred))}

# Position/range/cost qualification and per-claim reason precedence.
_cb_COST = 'canonical_costs.foreign_estimated'
_cb_SAMPLE, _cb_REQUIRED = (_cb_COST + '.sample_days', _cb_COST + '.required_days')
_cb_FULL = {'外資估算樣本天數': _cb_SAMPLE, '外資成本估算樣本天數': _cb_SAMPLE, '外資近期增量成本估算樣本天數': _cb_SAMPLE, '外資估算所需天數': _cb_REQUIRED, '外資成本估算所需天數': _cb_REQUIRED, '外資近期增量成本估算所需天數': _cb_REQUIRED}
_cb_LOCAL = {'樣本天數': _cb_SAMPLE, '目前樣本天數': _cb_SAMPLE, '所需天數': _cb_REQUIRED, '所需的天數': _cb_REQUIRED, '門檻天數': _cb_REQUIRED, '所需': _cb_REQUIRED, '所需的': _cb_REQUIRED}
_cb_SUFFIX = re.compile('(?:天數|個交易日|天|日)?$')
_cb_POSITION = re.compile('^(.*?)位於(.*?)(上方|之上|下方|之下)$')
_cb_RANGE = re.compile('^(.*?)位於(.*?)與(.*?)之間(?:\\((含兩端|不含兩端)\\))?$')

def _cb_first_error(errors):
    return next((c for c in (_cb_base_MISSING, _cb_base_AMBIG, _cb_base_STRUCT, _cb_base_UNKNOWN, _cb_base_MISMATCH, _cb_base_VALUE, _cb_base_COMP, _cb_base_FALSE) if c in errors), None)

def _cb_iso(value):
    if not isinstance(value, str):
        return None
    if re.fullmatch('\\d{4}-\\d{2}-\\d{2}', value):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    try:
        observed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    if observed.tzinfo is None or observed.utcoffset() is None:
        return None
    return observed.date()

def _cb_cost_cue(text, facts):
    if (_cb_base_PH.search(text) or _cb_base_OP.search(text) or '未達' in text) and re.search('樣本天數|所需|門檻天數|未達', text):
        return True
    ids = set(_cb_base_PH.findall(text))
    return bool(_cb_base_OP.search(text) and any((f.get('fact_id') in ids and str(f.get('field', '')).startswith('canonical_costs.') and str(f.get('field', '')).endswith(('.sample_days', '.required_days')) for f in facts)))

def _cb_cost_operand(text, context):
    op = _cb_base_operand(text)
    if op.get('error'):
        return op
    if not _cb_SUFFIX.fullmatch(op.get('tail', '')):
        op['error'] = _cb_base_STRUCT
    label = op['label']
    field = _cb_FULL.get(label) or (_cb_LOCAL.get(label) if context else None)
    if label in ('所需', '所需的') and (not op.get('tail')):
        op['error'] = _cb_base_STRUCT
    if field:
        op['mapping'] = ({field}, {'institutional_context'}, 'count')
    elif label:
        op['mapping'] = None
    op['tail'] = '' if not op.get('error') else op.get('tail', '')
    return op

def _cb_metadata_pair(a, b):
    for key in ('period', 'as_of'):
        if not a.get(key) or a.get(key) != b.get(key):
            return False
    return all((not (a.get(k) is not None or b.get(k) is not None) or a.get(k) == b.get(k) for k in ('unit', 'currency', 'trade_date', 'code', 'entity')))

def _cb_cost_eligible(fact, cutoff):
    value = _cb_base_decimal_value(fact)
    stamp, as_of, limit = (_cb_iso(fact.get('trade_date')), _cb_iso(fact.get('as_of')), _cb_iso(cutoff))
    return fact.get('field') in (_cb_SAMPLE, _cb_REQUIRED) and fact.get('domain') == 'institutional_context' and (fact.get('unit') == 'count') and (fact.get('period') == 'daily') and (value is not None) and (value == value.to_integral_value()) and (stamp is not None) and (as_of == stamp) and (limit is not None) and (stamp <= limit)

def _cb_truth(a, operator, b):
    return {'>': a > b, '<': a < b, '=': a == b, '>=': a >= b, '<=': a <= b}[operator]

def _cb_evaluate(text, kind, operands, facts, cited, pairs=(), cutoff=None, shape_errors=(), mode=None, ma_operands=(), ma_count=None):
    """One construct, all slots checked by phase before any truth evaluation."""
    bindings = [_cb_base_binding(op, facts, cited, kind != 'cost_scalar') for op in operands]
    errors, bound = ([x[0] for x in bindings], [x[1] for x in bindings])
    errors += list(shape_errors)
    if kind in ('position', 'range') and any((op.get('tail') for op in operands)):
        errors.append(_cb_base_STRUCT)
    if ma_operands:
        ma_ids = [operands[i].get('id') for i in ma_operands]
        ma_fields = [bound[i].get('field') for i in ma_operands if bound[i]]
        if len(ma_ids) != len(set(ma_ids)) or len(ma_fields) != len(set(ma_fields)):
            errors.append(_cb_base_AMBIG)
        if ma_count is not None and len(ma_operands) != ma_count:
            errors.append(_cb_base_STRUCT)
    if kind.startswith('cost'):
        for fact in bound:
            if fact and fact.get('field') in (_cb_SAMPLE, _cb_REQUIRED):
                if sum((f.get('field') == fact['field'] for f in facts)) != 1:
                    errors.append(_cb_base_AMBIG)
                if fact.get('domain') != 'institutional_context' or fact.get('unit') != 'count':
                    errors.append(_cb_base_MISMATCH)
        if kind == 'cost_threshold' and all(bound):
            if [f.get('field') for f in bound] != [_cb_SAMPLE, _cb_REQUIRED]:
                errors.append(_cb_base_MISMATCH)
    deferred = ['deferred_ma_label'] if 'deferred_ma_label' in errors else []
    code = _cb_first_error(errors)
    records = []
    if not code and (not deferred):
        if kind.startswith('cost'):
            if any((not _cb_cost_eligible(f, cutoff) for f in bound)):
                code = _cb_base_VALUE if kind == 'cost_scalar' else _cb_base_COMP
            elif kind != 'cost_scalar' and ({f['field'] for f in bound} != {_cb_SAMPLE, _cb_REQUIRED} or not _cb_metadata_pair(*bound)):
                code = _cb_base_COMP
        elif any((f.get('field') not in _cb_base_PRICE or f.get('unit') != 'TWD' for f in bound)):
            code = _cb_base_COMP
        if kind != 'cost_scalar' and (not code):
            if any((not _cb_metadata_pair(bound[i], bound[j]) for i, _, j in pairs)):
                code = _cb_base_COMP
        if kind == 'range' and (not code):
            x, a, b = [_cb_base_decimal_value(f) for f in bound]
            if a > b or (mode == 'unspecified' and x in (a, b)):
                code = _cb_base_STRUCT
        if not code:
            for left, operator, right in pairs:
                a, b = (_cb_base_decimal_value(bound[left]), _cb_base_decimal_value(bound[right]))
                value = _cb_truth(a, operator, b)
                records.append({'left_id': bound[left]['fact_id'], 'operator': operator, 'right_id': bound[right]['fact_id'], 'left_value': str(a), 'right_value': str(b), 'truth': value})
            if any((not r['truth'] for r in records)):
                code = _cb_base_FALSE
    claim = {'normalized_segment': text, 'raw_start': 0, 'reason': code, 'operands': [{k: v for k, v in op.items() if k != 'mapping'} for op in operands], 'operator': kind if pairs else None, 'bound_facts': bound, 'kind': kind, 'range_mode': mode, 'pairs': records, 'all_slots_checked_before_truth': True}
    return {'reasons': [code] if code else [], 'claims': [claim], 'deferred': deferred}

def _cb_analyze_special(core, namespace, resolve, facts, cited, cutoff, inherited_cost_context=False):
    """Return None for generic grammar; callers do not silently retry failed shapes."""
    numeric = _cb_base_PH.search(core) or re.search('(?:收盤價|價格)位於(?:布林|中軌|上軌|下軌)', core)
    if re.search('位於(?:均線系統上方|中性偏強區間)$', core):
        return None
    position = _cb_POSITION.fullmatch(core) if numeric else None
    interval = _cb_RANGE.fullmatch(core) if numeric else None
    if interval:
        x, a, b, marker = interval.groups()
        left, ex = resolve(x, namespace, inherited=True)
        aa, ea = resolve(a, namespace)
        rhs_ns = 'BOLL' if re.match('布林(?:通道|帶)?[中上下]軌', a) else namespace
        bb, eb = resolve(b, rhs_ns)
        ops = [_cb_base_operand(left, namespace), _cb_base_operand(aa, namespace, rhs_ns == 'BOLL'), _cb_base_operand(bb, namespace, rhs_ns == 'BOLL')]
        mode = {None: 'unspecified', '含兩端': 'closed', '不含兩端': 'open'}[marker]
        edges = ((1, '<=' if mode == 'closed' else '<', 0), (0, '<=' if mode == 'closed' else '<', 2))
        return _cb_evaluate(core, 'range', ops, facts, cited, edges, shape_errors=[e for e in (ex, ea, eb) if e], mode=mode)
    if position:
        x, rhs, tail = position.groups()
        left, ex = resolve(x, namespace, inherited=True)
        parts, shape_errors, ma_indices, ma_count = ([], [ex] if ex else [], [], None)
        if '各期均線' in rhs:
            group = re.fullmatch('(?:(.*?)[、與及和])?各期均線\\((.*?)\\)', rhs)
            if not group:
                return _cb_evaluate(core, 'position', [_cb_base_operand(left)], facts, cited, shape_errors=[*shape_errors, _cb_base_STRUCT])
            if group[1]:
                parts.append((group[1], False))
            members = _cb_base_LIST.split(group[2])
            if not 2 <= len(members) <= 7 or not all((_cb_base_PH.fullmatch(t) for t in members)):
                shape_errors.append(_cb_base_STRUCT)
            parts.extend((('均線' + t, True) for t in members))
        else:
            ma_label = None
            for piece in _cb_base_LIST.split(rhs):
                match = re.match('^(短中長期均線|均線)(?:分別為|為)?(?=\\{\\{)', piece)
                if match:
                    ma_label = match[1]
                    if ma_label == '短中長期均線':
                        ma_count = 3
                    parts.append((ma_label + piece[match.end():], True))
                elif ma_label and _cb_base_PH.match(piece):
                    parts.append((ma_label + piece, True))
                else:
                    parts.append((piece, False))
            if len(parts) > 1 and any((_cb_base_PH.fullmatch(piece) and (not is_ma) for piece, is_ma in parts)):
                shape_errors.append(_cb_base_STRUCT)
        if not 1 <= len(parts) <= 7:
            shape_errors.append(_cb_base_STRUCT)
        ops = [_cb_base_operand(left, namespace)]
        for part, is_ma in parts:
            value, error = resolve(part, namespace)
            if error:
                shape_errors.append(error)
            if is_ma:
                ma_indices.append(len(ops))
            op = _cb_base_operand(value, namespace, '布林' in rhs)
            if op.get('tail'):
                shape_errors.append(_cb_base_STRUCT)
            ops.append(op)
        operator = '>' if tail in ('上方', '之上') else '<'
        return _cb_evaluate(core, 'position', ops, facts, cited, [(0, operator, j) for j in range(1, len(ops))], shape_errors=shape_errors, ma_operands=ma_indices, ma_count=ma_count)
    if _cb_base_RANGE.search(core) and (_cb_base_PH.search(core) or re.search('(?:收盤價|價格)位於(?:布林|中軌|上軌|下軌)', core)):
        return {'reasons': [_cb_base_STRUCT], 'claims': [{'normalized_segment': core, 'reason': _cb_base_STRUCT, 'raw_start': 0, 'operands': [], 'bound_facts': [], 'operator': None}], 'deferred': []}
    if not _cb_cost_cue(core, facts):
        return None
    matches = list(re.finditer(_cb_base_OP.pattern + '|未達', core))
    if len(matches) > 1:
        return _cb_evaluate(core, 'cost', [], facts, cited, shape_errors=[_cb_base_STRUCT])
    pieces = [core[:matches[0].start()], core[matches[0].end():]] if matches else [core]
    explicit = any((any((piece.startswith(label) for label in _cb_FULL)) for piece in pieces))
    context = namespace == 'COST' or explicit
    errors, ops = ([], [])
    for i, piece in enumerate(pieces):
        if not piece and i == 0 and matches:
            if not inherited_cost_context:
                errors.append(_cb_base_MISSING)
            else:
                piece, error = resolve(piece, namespace, inherited=True)
                if error:
                    errors.append(error)
        elif not _cb_base_PH.search(piece):
            errors.append(_cb_base_MISSING)
        ops.append(_cb_cost_operand(piece, context))
    operator = matches[0][0] if matches else None
    kind = 'cost_threshold' if operator == '未達' else 'cost' if operator else 'cost_scalar'
    pairs = [(0, '<' if operator == '未達' else _cb_base_OPS[operator], 1)] if operator else []
    result = _cb_evaluate(core, kind, ops, facts, cited, pairs, cutoff, errors)
    result['cost_context_explicit'] = explicit
    return result

def _cb_prioritize_comparison(result):
    """A RHS list is one claim: all binding phases precede any direction error."""
    comparisons = [c for c in result['claims'] if c.get('operator')]
    code = _cb_first_error([c['reason'] for c in comparisons])
    for claim in comparisons:
        claim['pair_local_reason'] = claim['reason']
        claim['pairs'] = []
        bound = claim.get('bound_facts', [])
        if code in (None, _cb_base_FALSE) and (not result['deferred']) and (len(bound) == 2) and all(bound) and (claim['operator'] in _cb_base_OPS):
            a, b = [_cb_base_decimal_value(f) for f in bound]
            operator = _cb_base_OPS[claim['operator']]
            claim['pairs'] = [{'left_id': bound[0]['fact_id'], 'operator': operator, 'right_id': bound[1]['fact_id'], 'left_value': str(a), 'right_value': str(b), 'truth': _cb_truth(a, operator, b)}]
        claim['pair_truth_withheld_by_prior_phase_or_deferred'] = not bool(claim['pairs'])
    if len(comparisons) > 1:
        for i, claim in enumerate(comparisons):
            claim['reason'] = code if i == 0 else None
            claim['priority_scope'] = 'whole_comparison_list'
        result['reasons'] = list(dict.fromkeys((c['reason'] for c in result['claims'] if c['reason'])))
    return result

# Bounded same-block declaration/subject/namespace state; reset at barriers.
_cb_base_labels('市淨率', 'pb_ratio', 'valuation', 'ratio')
_cb_base_labels('股息率/股息殖利率', 'dividend_yield_pct', 'valuation', 'percent')
for _cb_word, _cb_field in (('中', 'middle'), ('上', 'upper'), ('下', 'lower')):
    _cb_base_labels('布林帶' + _cb_word + '軌', 'bollinger.' + _cb_field, 'technical', 'TWD')
_cb_SEP = re.compile('[。；;！？!?\\n，,]|並且|但是|然而|同時|且|並(?!非)|但|而')
_cb_STRONG = re.compile('[。；;！？!?\\n]|但是|然而|但|而')
_cb_DATE = re.compile('^截至\\{\\{F[0-9]{3,}\\}\\}')
_cb_CONTEXT = re.compile('^(?:技術面顯示|估值指標顯示|籌碼與機構面資料顯示|籌碼面資料顯示|機構籌碼方面|估值數據顯示|估值面顯示|估值方面|估值指標|技術面|配合)')
_cb_FUTURE = re.compile('^(?:若|如果|假如|倘若|只要)')
_cb_QUOTES = re.compile('[「」『』“”\\"\']')
_cb_SHORT_BOLL = re.compile('(^|[、與及和]|' + _cb_base_OP.pattern + ')([上中下]軌)(?=(?:欄位|數值)?(?:為|是)?(?:\\{\\{|$|[與及和]))')
_cb_DATE_STATEMENT = re.compile(r'^資料日期為\{\{(F[0-9]{3,})\}\}$')


def _cb_date_statement_binding(text, facts, cited):
    """Classify one exact date statement without entering numeric grammar."""
    match = _cb_DATE_STATEMENT.fullmatch(text)
    if match is None:
        return None
    fact_id = match[1]
    candidates = [fact for fact in facts if fact.get('fact_id') == fact_id]
    if fact_id not in cited or not candidates:
        return {'fact_id': fact_id, 'reason': _cb_base_MISSING}
    if len(candidates) != 1:
        return {'fact_id': fact_id, 'reason': _cb_base_AMBIG}
    fact = candidates[0]
    eligible = (
        fact.get('field') in (None, '', 'trade_date')
        and fact.get('unit') in (None, '')
        and fact.get('authority_tier') == 'canonical_db'
        and fact.get('quality') in ('ok', 'estimated')
        and 'date_claim' in fact.get('use_scope', [])
        and _cb_iso(fact.get('value')) is not None
    )
    return {
        'fact_id': fact_id,
        'reason': None if eligible else _cb_base_UNKNOWN,
    }

def _cb_family(_cb_field):
    if _cb_field.startswith(_cb_COST + '.'):
        return 'COST'
    for prefix in ('macd.', 'bollinger.', 'kd.'):
        if _cb_field.startswith(prefix):
            return {'macd.': 'MACD', 'bollinger.': 'BOLL', 'kd.': 'KD'}[prefix]
    return 'other'

def _cb_label_mapping(label, namespace):
    alias = label
    if namespace == 'MACD':
        alias = {'快線': 'DIF', '慢線': 'Signal', '柱狀體': 'OSC'}.get(alias, alias)
    if namespace == 'BOLL':
        alias = {'上軌': '布林上軌', '中軌': '布林中軌', '下軌': '布林下軌'}.get(alias, alias)
    if namespace == 'KD' and alias in ('K值', 'D值'):
        return ({'kd.k' if alias == 'K值' else 'kd.d'}, {'technical'}, 'index')
    return _cb_base_LABELS.get(alias)

def _cb_analyze(text, block_type, surface, facts, cited, analysis_cutoff=None):
    """Input-pure; no DB/I/O/model calls; original offset and lineage preserved."""
    if surface != 'text_template' or block_type == 'scenario':
        return {'reasons': [], 'claims': [], 'deferred': ['deferred_future_condition'], 'units': []}
    normal, offsets = _cb_base_normalize(text)
    declarations, subject, namespace = ({}, None, '')
    claims, deferred, units = ([], [], [])

    def raw_span(a, b):
        return [offsets[a] if a < len(offsets) else len(text), offsets[b - 1] + 1 if b > a else offsets[a] if a < len(offsets) else len(text)]

    def reset():
        nonlocal subject, namespace
        declarations.clear()
        subject, namespace = (None, '')

    def fail(unit, reason):
        item = {'normalized_segment': unit['original'], 'raw_start': unit['raw_span'][0], 'reason': reason, 'operands': [], 'operator': None, 'bound_facts': []}
        claims.append(item)
        unit['reasons'] = [reason]
        reset()

    def resolve(part, ns, expansions, inherited=False):
        if _cb_base_PH.search(part):
            return (part, None)
        if not part and inherited:
            if subject is None:
                return (part, _cb_base_MISSING)
            expansions.append({'kind': 'implicit_subject', **deepcopy(subject)})
            return (subject['label'] + '{{' + subject['id'] + '}}', None)
        label = _cb_base_stripped_label(part)
        mapping = _cb_label_mapping(label, ns)
        if label in _cb_base_DEFERRED or (mapping and len(mapping[0]) != 1):
            return (part, _cb_base_MISSING)
        if not mapping:
            return (part, _cb_base_MISSING)
        _cb_field = next(iter(mapping[0]))
        choices = declarations.get(_cb_field, {})
        if not choices:
            return (part, _cb_base_MISSING)
        if len(choices) != 1:
            return (part, _cb_base_AMBIG)
        fid, spans = next(iter(choices.items()))
        expansions.append({'kind': 'declared_reference', 'id': fid, 'field': _cb_field, 'label': label, 'binding_origin_spans': deepcopy(spans)})
        return (label + '{{' + fid + '}}', None)
    separators = list(_cb_SEP.finditer(normal))
    boundaries = [(0, None)] + [(m.end(), m.group()) for m in separators]
    ends = [m.start() for m in separators] + [len(normal)]
    for (start, preceding), end in zip(boundaries, ends):
        if preceding and _cb_STRONG.fullmatch(preceding):
            reset()
        segment = normal[start:end]
        if not segment:
            continue
        unit = {'original': segment, 'raw_span': raw_span(start, end), 'preceding_separator': preceding, 'declarations_before': deepcopy(declarations), 'subject_before': deepcopy(subject), 'namespace_before': namespace, 'expansions': [], 'reasons': []}
        units.append(unit)
        if _cb_FUTURE.match(segment):
            deferred.append('deferred_future_condition')
            unit['deferred'] = ['deferred_future_condition']
            reset()
            continue
        body = segment
        if (match := _cb_DATE.match(body)):
            reset()
            body = body[match.end():]
        if (match := _cb_CONTEXT.match(body)):
            reset()
            body = body[match.end():]
        if not body:
            continue
        date_binding = _cb_date_statement_binding(body, facts, cited)
        if date_binding is not None:
            unit['verified_date_statement'] = date_binding['fact_id']
            if date_binding['reason']:
                fail(unit, date_binding['reason'])
            else:
                reset()
            continue
        if _cb_DATE.match(body) or _cb_CONTEXT.match(body):
            fail(unit, _cb_base_STRUCT)
            continue
        if _cb_QUOTES.search(body):
            if _cb_base_PH.search(body) or _cb_base_OP.search(body):
                fail(unit, _cb_base_STRUCT)
            else:
                reset()
            continue
        explicit_ns = _cb_base_NS.match(body)
        local_ns = ('MACD' if explicit_ns[0].startswith('MACD') else 'KD') if explicit_ns else namespace
        core = body[explicit_ns.end():] if explicit_ns else body
        expansions = unit['expansions']
        operators = list(_cb_base_OP.finditer(core))
        virtual = core
        explicit_comparison_cue = _cb_base_PH.search(core) or any((label in core for label in _cb_base_LABELS)) or re.search('價格|中軌|上軌|下軌', core)
        numeric_range = _cb_base_PH.search(core) or re.search('(?:收盤價|價格)位於(?:布林|中軌|上軌|下軌)', core)
        unsupported = _cb_base_UNSUPPORTED.search(core) or re.search('大致位於|略高於|不在區間內|介於|落在', core) or re.search('(?:\\}\\}|收盤價|價格)在.*(?:之間|上方|之上|下方|之下)(?:\\(|$)', core)
        if unsupported and explicit_comparison_cue:
            fail(unit, _cb_base_STRUCT)
            continue

        def special_for(ns):
            local_expansions = []
            result = _cb_analyze_special(core, ns, lambda part, scope, inherited=False: resolve(part, scope, local_expansions, inherited), facts, cited, analysis_cutoff, inherited_cost_context=namespace == 'COST')
            if result is not None:
                result['_expansions'] = local_expansions
            return result
        special = special_for(local_ns)
        if special and special.get('cost_context_explicit'):
            local_ns = 'COST'
        if special is None and len(operators) == 1 and (_cb_base_PH.search(core) or any((label in core for label in _cb_base_LABELS))):
            op = operators[0]
            left_placeholders = list(_cb_base_PH.finditer(core[:op.start()]))
            if left_placeholders and left_placeholders[-1].end() != op.start():
                fail(unit, _cb_base_STRUCT)
                continue
            left, err = resolve(core[:op.start()], local_ns, expansions, inherited=True)
            right = core[op.end():]
            parts = re.split('([、與及和])', right)
            errors = [err] if err else []
            for i in range(0, len(parts), 2):
                if not _cb_base_PH.search(parts[i]):
                    parts[i], err = resolve(parts[i], local_ns, expansions)
                    if err:
                        errors.append(err)
            if errors:
                reason = next((c for c in (_cb_base_MISSING, _cb_base_AMBIG, _cb_base_STRUCT, _cb_base_UNKNOWN) if c in errors))
                fail(unit, reason)
                continue
            virtual = left + op.group() + ''.join(parts)
        elif special is None and (not _cb_base_PH.search(core)):
            if operators and any((label in core for label in _cb_base_LABELS)):
                fail(unit, _cb_base_STRUCT)
            else:
                reset()
            continue

        def _cb_evaluate(ns):
            if special is not None:
                return (core, special_for(ns))
            local = virtual
            if ns == 'BOLL':
                local = _cb_SHORT_BOLL.sub(lambda m: m[1] + '布林' + m[2], local)
            if ns in ('MACD', 'KD'):
                local = ns + local
            return (local, _cb_prioritize_comparison(_cb_base_analyze(local, block_type, surface, facts, cited)))
        virtual_text, result = _cb_evaluate(local_ns)
        bound = [f for c in result['claims'] for f in c.get('bound_facts', []) if f]
        families = {_cb_family(f.get('field', '')) for f in bound}
        if not explicit_ns and local_ns and (families != {local_ns}):
            virtual_text, result = _cb_evaluate('')
            bound = [f for c in result['claims'] for f in c.get('bound_facts', []) if f]
            families = {_cb_family(f.get('field', '')) for f in bound}
        if special is not None:
            expansions[:] = result.pop('_expansions', [])
        unit['virtual_evaluation_text'] = virtual_text
        explicit_spans = {}
        for match in _cb_base_PH.finditer(segment):
            explicit_spans.setdefault(match[1], []).append(raw_span(start + match.start(), start + match.end()))
        for claim in result['claims']:
            claim['raw_start'] = unit['raw_span'][0]
            claim['raw_unit_span'] = unit['raw_span']
            for operand in claim['operands']:
                origins = [e for e in expansions if e['id'] == operand['id']]
                operand['binding_origin_spans'] = origins[0]['binding_origin_spans'] if origins else explicit_spans.get(operand['id'], [])
                operand['binding_mode'] = origins[0]['kind'] if origins else 'explicit_placeholder'
            claims.append(claim)
        unit['reasons'] = result['reasons']
        unit['deferred'] = result['deferred']
        deferred.extend(result['deferred'])
        all_ops = [o for c in result['claims'] for o in c.get('operands', [])]
        tail_present = any((o.get('tail') for o in all_ops))
        if result['reasons'] or result['deferred'] or (not result['claims']) or tail_present:
            reset()
            continue
        for operand in all_ops:
            fid = operand['id']
            if not operand['label'] or fid not in explicit_spans or operand['binding_mode'] != 'explicit_placeholder':
                continue
            matches = [f for f in facts if f.get('fact_id') == fid]
            if len(matches) != 1:
                raise AssertionError('successful local evaluator allowed duplicate binding')
            spans = declarations.setdefault(matches[0]['field'], {}).setdefault(fid, [])
            for span in explicit_spans[fid]:
                if span not in spans:
                    spans.append(span)
        pair_claims = [c for c in result['claims'] if c.get('operator')]
        selected = pair_claims[0]['operands'][0] if pair_claims else all_ops[0] if len(all_ops) == 1 else None
        subject = None
        if selected and selected['label'] and selected['binding_origin_spans']:
            fact = next((f for f in facts if f.get('fact_id') == selected['id']))
            subject = {'id': selected['id'], 'label': selected['label'], 'field': fact['field'], 'binding_origin_spans': deepcopy(selected['binding_origin_spans'])}
        namespace = next(iter(families)) if len(families) == 1 and 'other' not in families else ''
        unit['declarations_after'] = deepcopy(declarations)
        unit['subject_after'] = deepcopy(subject)
        if namespace == 'COST' and local_ns != 'COST':
            namespace = ''
        unit['namespace_after'] = namespace
    return {'reasons': list(dict.fromkeys((c['reason'] for c in claims if c['reason']))), 'claims': claims, 'deferred': list(dict.fromkeys(deferred)), 'units': units}

def validate_model_analysis_v2(
    output: str | dict[str, Any],
    packet: dict[str, Any],
) -> ModelValidationResult:
    """Reject raw financial numbers/dates and resolve only eligible fact placeholders."""

    started_ns = time.perf_counter_ns()
    render_duration_ns = 0
    try:
        analysis = parse_model_analysis_v2(output) if isinstance(output, str) else dict(output)
    except (ValueError, TypeError, json.JSONDecodeError):
        return _reject("invalid_json", started_ns=started_ns)
    if analysis.get("contract_version") != MODEL_ANALYSIS_VERSION:
        return _reject("invalid_contract_version", analysis=analysis, started_ns=started_ns)
    if any(key in analysis for key in ("main_status", "signal", "recommendation", "referee")):
        return _reject("referee_override_attempt", analysis=analysis, started_ns=started_ns)
    blocks = analysis.get("explanation_blocks")
    if not isinstance(blocks, list) or not blocks:
        return _reject("missing_explanation_blocks", analysis=analysis, started_ns=started_ns)
    if len(blocks) > 5:
        return _reject("too_many_explanation_blocks", analysis=analysis, started_ns=started_ns)
    request = packet.get("request") or {}
    comprehensive = str(request.get("depth") or "") == "comprehensive"
    if comprehensive and len(blocks) < 4:
        return _reject("insufficient_comprehensive_blocks", analysis=analysis, started_ns=started_ns)

    facts = {
        str(item.get("fact_id") or ""): item
        for item in packet.get("facts") or []
        if isinstance(item, dict) and item.get("fact_id")
    }
    events = {
        str(item.get("event_id") or ""): item
        for item in packet.get("events") or []
        if isinstance(item, dict) and item.get("event_id")
    }
    reasons: list[str] = []
    rendered: list[str] = []
    covered_requested_scopes: set[str] = set()
    cited_event_ids: set[str] = set()
    allowed_types = {"fact", "inference", "scenario", "limitation"}
    for block in blocks:
        if not isinstance(block, dict):
            reasons.append("invalid_block_schema")
            continue
        block_type = str(block.get("block_type") or "")
        template = str(block.get("text_template") or "").strip()
        evidence_ids = [str(item) for item in block.get("evidence_ids") or []]
        uncertainty = str(block.get("uncertainty") or "")
        conditions = block.get("conditions")
        if (
            block_type not in allowed_types
            or not template
            or len(template) > 260
            or len(evidence_ids) > 8
            or uncertainty not in {"low", "medium", "high"}
            or not isinstance(conditions, list)
            or len(conditions) > 4
            or any(not isinstance(item, str) or len(item) > 160 for item in conditions)
        ):
            reasons.append("invalid_block_schema")
            continue
        if block_type in {"inference", "scenario"} and not evidence_ids:
            reasons.append("inference_without_evidence")
        unknown = [item for item in evidence_ids if item not in facts and item not in events]
        if unknown:
            reasons.append("unknown_evidence_id")
        for evidence_id in evidence_ids:
            fact = facts.get(evidence_id)
            if evidence_id in events:
                cited_event_ids.add(evidence_id)
            if not fact:
                continue
            use_scope = set(str(item) for item in fact.get("use_scope") or [])
            if use_scope == {"limitation"} and block_type != "limitation":
                reasons.append("missing_fact_used_outside_limitation")
        if block_type != "limitation" and _RELATIVE_VALUATION_CLAIM.search(template):
            relative_support = False
            for evidence_id in evidence_ids:
                fact = facts.get(evidence_id) or {}
                field = str(fact.get("field") or "").lower()
                value = str(fact.get("value") or "").lower()
                if (
                    fact.get("quality") in {"ok", "estimated"}
                    and any(
                        marker in field
                        for marker in (
                            "valuation_percentile",
                            "peer_valuation",
                            "relative_value_assessment",
                            "historical_valuation",
                        )
                    )
                    and value
                    and not value.startswith("unavailable")
                ):
                    relative_support = True
                    break
            if not relative_support:
                reasons.append("relative_valuation_without_baseline")

        placeholders = _PLACEHOLDER.findall(template)
        numeric_placeholders = [
            fact_id
            for fact_id in placeholders
            if {"numeric_claim"}.intersection(
                str(item) for item in (facts.get(fact_id) or {}).get("use_scope") or []
            )
        ]
        if _COMPARATIVE_TEXT.search(template) and len(set(numeric_placeholders)) < 2:
            reasons.append("comparison_without_two_numeric_placeholders")
        without_placeholders = _PLACEHOLDER.sub("", template)
        if _ARABIC_NUMBER_OR_DATE.search(without_placeholders) or _CHINESE_NUMBER_WITH_UNIT.search(
            without_placeholders
        ):
            reasons.append("ungrounded_numeric_or_date_claim")
        if any(
            _ARABIC_NUMBER_OR_DATE.search(item) or _CHINESE_NUMBER_WITH_UNIT.search(item)
            for item in conditions
        ):
            reasons.append("ungrounded_numeric_or_date_claim")
        if _contains_affirmative_policy_violation(template) or any(
            _contains_affirmative_policy_violation(item) for item in conditions
        ):
            reasons.append("financial_policy_violation")
        if _follows_untrusted_instruction(template) or any(
            _follows_untrusted_instruction(item) for item in conditions
        ):
            reasons.append("prompt_injection_following")
        render_started_ns = time.perf_counter_ns()
        rendered_text = template
        for fact_id in placeholders:
            fact = facts.get(fact_id)
            if fact is None or fact_id not in evidence_ids:
                reasons.append("placeholder_without_matching_evidence")
                continue
            use_scope = set(str(item) for item in fact.get("use_scope") or [])
            if (
                fact.get("authority_tier") != "canonical_db"
                or not ({"numeric_claim", "date_claim"} & use_scope)
                or fact.get("quality") not in {"ok", "estimated"}
            ):
                reasons.append("ineligible_numeric_placeholder")
                continue
            rendered_text = rendered_text.replace(f"{{{{{fact_id}}}}}", _render_fact(fact))
        if _PLACEHOLDER.search(rendered_text):
            reasons.append("unresolved_placeholder")
        rendered.append(rendered_text)
        render_duration_ns += time.perf_counter_ns() - render_started_ns
        for requested_scope in request.get("scopes") or []:
            scope = str(requested_scope)
            if any(
                (evidence_id in events and scope in {"current_news", "events", "geopolitics"})
                or (
                    evidence_id in facts
                    and fact_supports_requested_scope(facts[evidence_id], scope)
                )
                for evidence_id in evidence_ids
            ):
                covered_requested_scopes.add(scope)

    if comprehensive:
        for requested_scope in request.get("scopes") or []:
            scope = str(requested_scope)
            if scope not in covered_requested_scopes:
                reasons.append(f"requested_scope_not_covered:{scope}")

    used_event_ids = analysis.get("used_event_ids") or []
    normalized_used_event_ids = (
        [str(item) for item in used_event_ids]
        if isinstance(used_event_ids, list)
        else []
    )
    if (
        not isinstance(used_event_ids, list)
        or len(normalized_used_event_ids) != len(set(normalized_used_event_ids))
        or any(item not in events for item in normalized_used_event_ids)
    ):
        reasons.append("unknown_event_id")
    elif set(normalized_used_event_ids) != cited_event_ids:
        reasons.append("event_usage_mismatch")
    for event_id in normalized_used_event_ids:
        event = events.get(event_id) or {}
        if not event.get("citation_required"):
            continue
        if (
            event.get("allow_display") is not True
            or normalize_public_https_url(event.get("source_url")) is None
            or not str(event.get("publisher") or "").strip()
            or not str(event.get("title") or "").strip()
        ):
            reasons.append("citation_not_renderable")
    missing_data = analysis.get("missing_data") or []
    research_limits = analysis.get("research_limitations") or []
    if (
        not isinstance(missing_data, list)
        or not isinstance(research_limits, list)
        or any(not isinstance(item, str) or len(item) > 160 for item in missing_data)
        or any(not isinstance(item, str) or len(item) > 240 for item in research_limits)
    ):
        reasons.append("invalid_limitation_schema")
    elif any(
        _ARABIC_NUMBER_OR_DATE.search(item) or _CHINESE_NUMBER_WITH_UNIT.search(item)
        for item in [*missing_data, *research_limits]
    ):
        reasons.append("ungrounded_numeric_or_date_claim")
    if reasons:
        return _reject(
            *reasons,
            analysis=analysis,
            started_ns=started_ns,
            render_duration_ns=render_duration_ns,
        )
    # Legacy failures above retain their exact reasons, including after repair.
    semantic_reasons = _absence_related_reasons(analysis, packet)
    if semantic_reasons:
        return _reject(
            *semantic_reasons, analysis=analysis, started_ns=started_ns,
            render_duration_ns=render_duration_ns,
        )
    # Preserve every pre-existing rejection; new checks cannot rescue it.
    binding_reasons = []
    for block in blocks:
        result = _cb_analyze(
            block["text_template"], block["block_type"], "text_template",
            packet.get("facts") or [], block.get("evidence_ids") or [],
            request.get("analysis_cutoff"),
        )
        binding_reasons.extend(result["reasons"])
    if binding_reasons:
        return _reject(
            *binding_reasons, analysis=analysis, started_ns=started_ns,
            render_duration_ns=render_duration_ns,
        )
    elapsed_ns = max(0, time.perf_counter_ns() - started_ns)
    return ModelValidationResult(
        passed=True,
        reason_codes=("pass",),
        analysis=analysis,
        rendered_blocks=tuple(rendered),
        ungrounded_claim_count=0,
        referee_override_count=0,
        validation_duration_ms=round(max(0, elapsed_ns - render_duration_ns) / 1_000_000, 3),
        render_duration_ms=round(max(0, render_duration_ns) / 1_000_000, 3),
    )
