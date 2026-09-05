from __future__ import annotations

import math
import json
import re
from html import unescape
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from analysis.practical_status import (
    PRACTICAL_STATUS_CORE_VERSION,
    classify_practical_status_core,
)
from analysis.advisory_decision import build_conditional_advisory
from analysis.low_zone_entry import LOW_ZONE_ENTRY_VERSION
from analysis.screening_prefilter import screening_prefilter_score
from analysis.recommendation_safety import (
    RECOMMENDATION_SAFETY_VERSION,
    apply_safety_cap_to_referee,
    assess_recommendation_safety,
)
from analysis.external_event_impact import classify_company_disclosure
from analysis.support_resistance import (
    SUPPORT_RESISTANCE_ASSEMBLER_VERSION,
    build_ohlcv_support_resistance_levels,
    cluster_levels,
)
from price_volume import analyze_volume_structure
from core.data_quality import (
    EXTERNAL_EVENT_MIN_REFERENCE_VALUE_SCORE,
    EXTERNAL_EVENT_MIN_RELIABILITY_SCORE,
    assess_component_freshness,
    assess_external_event_quality,
    assess_news_radar_quality,
    assess_persisted_price_volume_reconciliation,
    assess_post_close_or_delayed_capture,
    classify_official_trading_state,
)
from core.market_session import recent_market_date_for_eod, tw_market_session_now
from core.price_volume_display_quality import assess_scoped_price_volume_display
from repository.market_microstructure_repository import (
    read_active_stock_master_rows,
    read_daily_market_microstructure,
    read_daily_stock_history,
    read_general_market_context,
    read_intraday_trade_page,
    read_screening_prefilter_rows,
)
from services.estimated_chip_cost_service import build_canonical_cost_snapshot


TPE = ZoneInfo("Asia/Taipei")
VALIDATED_QUALITY = "VALIDATED"
OFFICIAL_QUALITY_VALUES = {"OFFICIAL", "OK", "HIGH"}
INTRADAY_MAX_AGE_SECONDS = 180
SCREENING_STRATEGIES = {"balanced", "support", "momentum", "conservative", "bottom"}
ANALYSIS_MODES = {"close_batch", "intraday"}
CANONICAL_CLOSE_BATCH_CONTRACT_VERSION = "canonical-close-batch-analysis-v1"


def _recommendation_safety_payload(
    snapshot: dict[str, Any],
    selected_date: str,
) -> dict[str, Any]:
    turnover_values: list[float] = []
    for row in reversed(list(snapshot.get("referee_history") or [])):
        amount = _number(row.get("amount"))
        if amount is not None and amount > 0:
            turnover_values.append(amount)
        else:
            close = _number(row.get("close"))
            volume = _number(row.get("volume"))
            if str(row.get("volume_unit") or "shares").lower() == "lots" and volume is not None:
                volume *= 1000
            if close is not None and close > 0 and volume is not None and volume > 0:
                turnover_values.append(close * volume)
        if len(turnover_values) >= 20:
            break
    calculated_at = datetime.now(TPE).isoformat(timespec="seconds")
    return assess_recommendation_safety(
        trade_date=selected_date,
        calculated_at=calculated_at,
        turnover_values=turnover_values,
        trading_restriction_context=snapshot.get("trading_restriction_context"),
        corporate_action_context=snapshot.get("corporate_action_context"),
        company_size_context=snapshot.get("company_size_context"),
        close_price=(snapshot.get("history") or {}).get("close"),
    )


def build_bot_stock_screen(
    *,
    strategy: str = "balanced",
    limit: int = 5,
) -> dict[str, Any]:
    """Return deterministic, referee-approved post-close candidates.

    Technical ranks only bound the number of full read-only referee evaluations.
    A row is returned only when the shared referee and conditional advisory both
    pass their own quality gates.  No model-generated symbol can enter the list.
    """

    normalized_strategy = str(strategy or "balanced").strip().lower()
    if normalized_strategy not in SCREENING_STRATEGIES:
        normalized_strategy = "balanced"
    bounded_limit = max(1, min(int(limit), 8))
    universe = read_screening_prefilter_rows(limit=300)
    trade_date = str(universe.get("trade_date") or "")
    prefilter_rows = list(universe.get("rows") or [])
    if not trade_date or not prefilter_rows:
        return {
            "ok": False,
            "status": "unavailable",
            "trade_date": trade_date or None,
            "strategy": normalized_strategy,
            "candidates": [],
            "message": "最新完整盤後資料尚未形成可篩選的股票池。",
        }
    ranked = sorted(
        prefilter_rows,
        key=lambda row: screening_prefilter_score(row, normalized_strategy),
        reverse=True,
    )
    display_names = {
        str(row.get("code") or ""): (_row_stock_names(row)[0] if _row_stock_names(row) else "")
        for row in read_active_stock_master_rows()
    }
    candidate_rows: list[dict[str, Any]] = []
    allowed_actions = {
        "低檔止跌，可條件式第一批": 0,
        "可條件式分批": 0,
        "可小比例分批觀察": 1,
        "支撐區小比例試單": 2,
    }
    if normalized_strategy == "momentum":
        allowed_actions["不追價，等待較佳位置"] = 3
    for prefiltered in ranked[:80]:
        payload = build_canonical_close_batch_snapshot(
            str(prefiltered.get("code") or ""),
            trade_date=trade_date,
            include_levels=False,
            level_limit=1,
            allow_live_quote_fetch=False,
        )
        referee = dict(payload.get("referee") or {})
        advisory = dict(payload.get("advisory") or {})
        recommendation_safety = dict(payload.get("recommendation_safety") or {})
        low_zone = dict(advisory.get("low_zone_assessment") or {})
        action_state = str(advisory.get("action_state") or "")
        if not referee.get("decision_ready") or not advisory.get("decision_ready"):
            continue
        if not recommendation_safety.get("auto_entry_eligible"):
            continue
        if normalized_strategy == "bottom" and not low_zone.get("batch_entry_eligible"):
            continue
        if action_state not in allowed_actions:
            continue
        stock = dict(payload.get("stock") or {})
        technical = dict(payload.get("technical") or {})
        support = dict(advisory.get("support") or {})
        resistance = dict(advisory.get("resistance") or {})
        candidate_rows.append(
            {
                "code": str(prefiltered.get("code") or ""),
                "name": _without_legal_suffix(
                    str(
                        display_names.get(str(prefiltered.get("code") or ""))
                        or stock.get("name")
                        or prefiltered.get("name")
                        or ""
                    )
                ),
                "trade_date": trade_date,
                "close": _number((payload.get("ohlcv") or {}).get("close")),
                "main_status": str(referee.get("main_status") or ""),
                "main_reasons": [
                    str(item) for item in list(referee.get("main_reasons") or [])[:2]
                ],
                "action_state": action_state,
                "headline": str(advisory.get("headline") or ""),
                "buy_plan": str(advisory.get("buy_plan") or ""),
                "invalidation": str(advisory.get("invalidation") or ""),
                "support": str(support.get("label") or "無資料"),
                "resistance": str(resistance.get("label") or "無資料"),
                "rsi14": _number((technical.get("rsi") or {}).get("rsi14")),
                "low_zone_stage": str(low_zone.get("stage") or "unavailable"),
                "low_zone_stage_label": str(low_zone.get("stage_label") or "資料不足"),
                "low_zone_summary": str(low_zone.get("summary") or ""),
                "rsi_direction": str(low_zone.get("rsi_direction") or ""),
                "confirmation_count": _integer(low_zone.get("confirmation_count")),
                "reward_risk_ratio": _number(low_zone.get("reward_risk_ratio")),
                "batch_entry_eligible": bool(low_zone.get("batch_entry_eligible")),
                "recommendation_safety_status": str(
                    recommendation_safety.get("status") or "unavailable"
                ),
                "average_turnover_20d_twd": _number(
                    (recommendation_safety.get("liquidity") or {}).get("average_turnover_twd")
                ),
                "screening_strategy": normalized_strategy,
                "screening_score_exposed": False,
                "can_override_main_status": False,
            }
        )
        if len(candidate_rows) >= bounded_limit:
            break
    candidate_rows.sort(
        key=lambda row: (
            allowed_actions.get(str(row.get("action_state") or ""), 99),
            str(row.get("code") or ""),
        )
    )
    status = "ok" if candidate_rows else "no_qualified_candidates"
    return {
        "ok": bool(candidate_rows),
        "status": status,
        "trade_date": trade_date,
        "strategy": normalized_strategy,
        "candidate_count": len(candidate_rows),
        "candidates": candidate_rows,
        "quality_contract": {
            "post_close_only": True,
            "shared_referee_required": True,
            "conditional_advisory_required": True,
            "low_zone_entry_assessment_version": LOW_ZONE_ENTRY_VERSION,
            "low_zone_entry_required": normalized_strategy == "bottom",
            "recommendation_safety_required": True,
            "recommendation_safety_version": RECOMMENDATION_SAFETY_VERSION,
            "model_generated_symbols_allowed": False,
        },
        "message": (
            "以下為通過低檔止跌、支撐與報酬風險門檻的條件式第一批候選，不代表已確認最低點。"
            if candidate_rows and normalized_strategy == "bottom"
            else "以下僅為通過盤後資料與裁判層門檻的條件式觀察候選，不代表保證上漲。"
            if candidate_rows
            else "本交易日沒有同時通過資料品質、RSI 止跌、支撐與報酬風險門檻的可分批候選。"
            if normalized_strategy == "bottom"
            else "本交易日沒有同時通過資料品質、裁判層與條件式進場門檻的候選。"
        ),
    }


def _parse_taipei_timestamp(value: Any, *, fallback_date: str | None = None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    candidates = [text]
    if fallback_date and re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", text):
        candidates.insert(0, f"{fallback_date} {text}")
    for candidate in candidates:
        try:
            parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=TPE)
        return parsed.astimezone(TPE)
    return None


def _current_intraday_quote(
    code: str,
    snapshot_quote: dict[str, Any] | None,
    *,
    allow_network: bool,
) -> dict[str, Any]:
    """Return a current-session read-only quote or an explicit unavailable state."""

    session = tw_market_session_now()
    if session.get("session") != "regular":
        return {
            "available": False,
            "required": False,
            "status": "market_closed",
            "reason": "regular session is not open",
        }

    now = datetime.now(TPE)
    today = now.date().isoformat()

    def normalized(candidate: dict[str, Any] | None) -> dict[str, Any] | None:
        row = dict(candidate or {})
        price = _number(row.get("price") if row.get("price") is not None else row.get("close"))
        quote_date = str(row.get("quote_date") or "").strip()[:10]
        observed_at = _parse_taipei_timestamp(
            row.get("updated_at") or row.get("source_tlong") or row.get("trade_time") or row.get("fetched_at"),
            fallback_date=quote_date or today,
        )
        if not quote_date and observed_at:
            quote_date = observed_at.date().isoformat()
        if price is None or price <= 0 or quote_date != today or observed_at is None:
            return None
        age_seconds = (now - observed_at).total_seconds()
        if age_seconds < -30 or age_seconds > INTRADAY_MAX_AGE_SECONDS:
            return None
        status = str(row.get("data_status") or "").strip().lower()
        if status in {"failed", "invalid", "unavailable", "stale"}:
            return None
        return {
            "available": True,
            "required": True,
            "status": "current",
            "quote_date": quote_date,
            "as_of": observed_at.isoformat(timespec="seconds"),
            "price": price,
            "open": _number(row.get("open")),
            "high": _number(row.get("high")),
            "low": _number(row.get("low")),
            "volume_shares": _number(row.get("volume") if row.get("volume") is not None else row.get("cumulative_volume")),
            "source": row.get("source") or row.get("quote_source"),
            "source_quality": "official_current_session",
        }

    current = normalized(snapshot_quote)
    if current:
        return current
    if allow_network:
        try:
            from adapter.mis import fetch_mis_quotes_batch

            current = normalized(
                fetch_mis_quotes_batch([code], persist=False, update_cache=False).get(code)
            )
        except Exception:
            current = None
        if current:
            return current
    return {
        "available": False,
        "required": True,
        "status": "source_delayed",
        "reason": "current-session quote is not fresh enough for an intraday decision",
    }

_STOCK_QUERY_NOISE = (
    "幫我分析",
    "請幫我",
    "幫我看",
    "分析一下",
    "看一下",
    "技術分析",
    "技術面",
    "基本面",
    "籌碼面",
    "支撐賣壓",
    "今日",
    "今天",
    "怎麼樣",
    "怎樣",
    "如何",
    "看看",
    "股票",
    "走勢",
    "分析",
    "請問",
    "請",
    "一下",
    "可以嗎",
    "好嗎",
    "呢",
    "的",
    "RSI",
    "MACD",
)

_STOCK_QUERY_DISCOURSE_PREFIXES = tuple(
    sorted(
        (
            "那所以",
            "所以想問",
            "我想問",
            "想問",
            "那請問",
            "我是說",
            "我說的是",
            "所以",
            "那麼",
            "至於",
            "另外",
            "再來",
            "接著",
            "那",
        ),
        key=len,
        reverse=True,
    )
)


def _without_stock_query_discourse_prefix(text: str) -> str:
    """Remove conversational lead-ins without touching the stock name itself."""

    value = str(text or "")
    # Two passes cover natural combinations such as 「那所以想問星宇呢」.
    for _ in range(2):
        matched = next(
            (prefix for prefix in _STOCK_QUERY_DISCOURSE_PREFIXES if value.startswith(prefix)),
            None,
        )
        if not matched:
            break
        value = value[len(matched) :]
    return value


def _stock_name_probe(text: str) -> str:
    probe = re.sub(r"[\s，。！？、,.!?：:；;（）()【】\[\]]+", "", str(text or ""))
    probe = _without_stock_query_discourse_prefix(probe)
    for word in _STOCK_QUERY_NOISE:
        probe = re.sub(re.escape(word), "", probe, flags=re.IGNORECASE)
    return probe[:20]


def _bounded_edit_distance(left: str, right: str, *, maximum: int = 1) -> int:
    """Return a small edit distance, stopping once it cannot be within maximum."""

    if left == right:
        return 0
    if abs(len(left) - len(right)) > maximum:
        return maximum + 1
    previous = list(range(len(right) + 1))
    for index, left_char in enumerate(left, start=1):
        current = [index]
        row_minimum = index
        for offset, right_char in enumerate(right, start=1):
            value = min(
                current[-1] + 1,
                previous[offset] + 1,
                previous[offset - 1] + (left_char != right_char),
            )
            current.append(value)
            row_minimum = min(row_minimum, value)
        if row_minimum > maximum:
            return maximum + 1
        previous = current
    return previous[-1]


def _similar_stock_names(text: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Suggest close official names but never silently resolve a misspelling."""

    probe = _stock_name_probe(text)
    if len(probe) < 2:
        return []
    matches: list[tuple[int, str, str, dict[str, Any]]] = []
    for row in rows:
        names = _row_stock_names(row)
        distances = [
            _bounded_edit_distance(probe, name, maximum=1)
            for name in names
            if abs(len(name) - len(probe)) <= 1
        ]
        distance = min(distances, default=2)
        if distance != 1 or not names:
            continue
        matches.append((distance, names[0], str(row.get("code") or ""), row))
    matches.sort(key=lambda item: (item[0], item[1], item[2]))
    return [
        {
            "code": str(row.get("code") or ""),
            "name": _row_stock_names(row)[0],
            "official_name": unescape(str(row.get("name") or "")).strip(),
            "market": row.get("market"),
            "exchange": row.get("exchange"),
        }
        for _, _, _, row in matches[:5]
    ]


def _prefix_stock_name_suggestions(
    text: str,
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return official-name candidates for a natural short name.

    A short prefix such as ``星宇`` is useful evidence for suggesting
    ``星宇航空`` but is not strong enough to silently select a stock.  The LINE
    layer can therefore ask for a quick confirmation before fetching analysis.
    """

    probe = _stock_name_probe(text)
    if len(probe) < 2:
        return []
    matches = [
        row
        for row in rows
        if any(name.startswith(probe) and name != probe for name in _row_stock_names(row))
    ]
    unique = {
        str(row.get("code") or ""): row
        for row in matches
        if str(row.get("code") or "")
    }
    return [_normalized_stock_row(unique[code]) for code in sorted(unique)[:5]]


def _without_legal_suffix(name: str) -> str:
    value = unescape(str(name or "")).strip()
    for suffix in ("股份有限公司", "有限公司", "公司"):
        if value.endswith(suffix):
            return value[: -len(suffix)].strip()
    return value


def _without_exchange_name_markers(name: str) -> str:
    """Return a user-query alias without temporary exchange name markers.

    TWSE may append ``*`` (or its full-width form) to a trading name.  The
    marker is useful provenance but is not part of the company name users type.
    Keep the original name separately and add only a marker-free lookup alias.
    """

    value = unescape(str(name or "")).strip()
    return re.sub(r"[*＊]", "", value).strip()


def _row_stock_names(row: dict[str, Any]) -> list[str]:
    trading_name = unescape(str(row.get("trading_name") or "")).strip()
    official_name = unescape(str(row.get("name") or "")).strip()
    legal_short_name = _without_legal_suffix(official_name)
    values = [
        _without_exchange_name_markers(trading_name),
        _without_exchange_name_markers(legal_short_name),
        _without_exchange_name_markers(official_name),
        trading_name,
        legal_short_name,
        official_name,
    ]
    return list(dict.fromkeys(value for value in values if value))


def _normalized_stock_row(row: dict[str, Any]) -> dict[str, Any]:
    names = _row_stock_names(row)
    official_name = unescape(str(row.get("name") or "")).strip()
    return {
        "code": str(row.get("code") or ""),
        "name": names[0] if names else official_name,
        "official_name": official_name,
        "market": row.get("market"),
        "exchange": row.get("exchange"),
    }


def _name_candidate_rows(text: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized_text = str(text or "").strip()
    exact = [row for row in rows if normalized_text in _row_stock_names(row)]
    candidates = exact
    if not candidates:
        embedded_matches: list[tuple[int, int, dict[str, Any]]] = []
        for row in rows:
            for name in _row_stock_names(row):
                for match in re.finditer(re.escape(name), normalized_text):
                    embedded_matches.append((match.start(), match.end(), row))
        # A full official short name must outrank a shorter stock name occurring
        # inside the same text span.  For example, 「長榮航」 identifies 2618;
        # the nested 「長榮」 match must not make the query ambiguous.  Matches
        # at separate positions remain separate candidates, so a true query for
        # two stocks still fails closed and asks the user to choose.
        maximal_matches = [
            candidate
            for candidate in embedded_matches
            if not any(
                other_start <= candidate[0]
                and other_end >= candidate[1]
                and (other_end - other_start) > (candidate[1] - candidate[0])
                for other_start, other_end, _other_row in embedded_matches
            )
        ]
        candidates = [row for _start, _end, row in maximal_matches]
    unique = {
        str(row.get("code") or ""): row
        for row in candidates
        if str(row.get("code") or "")
    }
    return [unique[code] for code in sorted(unique)]


def resolve_bot_stock_query(query: str) -> dict[str, Any]:
    """Resolve a user phrase to one official listed/OTC stock without guessing."""

    text = str(query or "").strip()
    if not text or len(text) > 200:
        return {
            "ok": False,
            "status": "invalid_request",
            "reason": "query must contain 1 to 200 characters",
            "candidates": [],
        }
    text_without_dates = re.sub(
        r"(?<!\d)20\d{2}[-/]\d{1,2}[-/]\d{1,2}(?!\d)",
        " ",
        text,
    )
    code_matches = list(
        dict.fromkeys(re.findall(r"(?<!\d)(\d{4})(?!\d)", text_without_dates))
    )
    try:
        rows = read_active_stock_master_rows()
    except Exception:
        return {
            "ok": False,
            "status": "unavailable",
            "reason": "stock master database is unavailable",
            "candidates": [],
        }
    code_set = set(code_matches)
    code_candidates = [
        row for row in rows if str(row.get("code") or "") in code_set
    ]
    name_candidates = _name_candidate_rows(text_without_dates, rows)
    valid_code_set = {str(row.get("code") or "") for row in code_candidates}
    name_code_set = {str(row.get("code") or "") for row in name_candidates}
    unmatched_codes = sorted(code_set - valid_code_set)
    if code_matches and name_candidates and (
        unmatched_codes or valid_code_set != name_code_set
    ):
        conflict_rows = {
            str(row.get("code") or ""): row
            for row in [*code_candidates, *name_candidates]
        }
        return {
            "ok": False,
            "status": "conflict",
            "reason": "stock name and four-digit code do not identify the same single stock",
            "candidates": [
                _normalized_stock_row(conflict_rows[code])
                for code in sorted(conflict_rows)
            ],
            "unmatched_codes": unmatched_codes,
        }
    candidates = code_candidates if code_matches else name_candidates
    normalized = [_normalized_stock_row(row) for row in candidates[:10]]
    if not normalized:
        prefix_suggestions = _prefix_stock_name_suggestions(text, rows)
        # A matching natural prefix is stronger than a one-character typo.
        # Do not dilute a clear 「星宇」→「星宇航空」 candidate with unrelated
        # fuzzy matches such as 「中宇」 or 「大宇」.
        suggestions = prefix_suggestions or _similar_stock_names(text, rows)
        return {
            "ok": False,
            "status": "not_found",
            "reason": (
                "no exact active listed/OTC stock matched; similar names require user confirmation"
                if suggestions
                else "no active listed/OTC stock matched the query"
            ),
            "candidates": [],
            "suggestions": suggestions,
        }
    if len(normalized) != 1:
        return {
            "ok": False,
            "status": "ambiguous",
            "reason": "multiple active stocks matched the query",
            "candidates": normalized,
        }
    return {
        "ok": True,
        "status": "ok",
        "reason": "resolved from official stock master",
        "stock": normalized[0],
        "candidates": normalized,
    }


def bot_market_data_readiness() -> dict[str, Any]:
    try:
        rows = read_active_stock_master_rows()
    except Exception:
        return {
            "status": "unavailable",
            "ready": False,
            "mode": "read_only",
            "reason": "market-data database cannot be opened read-only",
            "active_stock_count": 0,
        }
    ready = bool(rows)
    return {
        "status": "ok" if ready else "unavailable",
        "ready": ready,
        "mode": "read_only",
        "reason": "active official stock master is readable" if ready else "active stock master is empty",
        "active_stock_count": len(rows),
    }


def normalize_bot_stock_code(value: Any) -> str | None:
    text = str(value or "").strip()
    return text if re.fullmatch(r"\d{4}", text) else None


def normalize_bot_trade_date(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def _daily_freshness(
    selected_date: str | None,
    requested_date: str | None,
    publication_date: str | None = None,
) -> dict[str, Any]:
    calendar_expected_date = recent_market_date_for_eod()
    expected_date = publication_date or calendar_expected_date
    if requested_date:
        ready = selected_date == requested_date
        return {
            "status": "requested_date" if ready else "unavailable",
            "ready": ready,
            "requested_date": requested_date,
            "actual_date": selected_date,
            "expected_latest_trade_date": expected_date,
            "calendar_expected_trade_date": calendar_expected_date,
            "reason": "explicit historical date requested" if ready else "requested date is unavailable",
        }
    if not selected_date:
        return {
            "status": "unavailable",
            "ready": False,
            "requested_date": None,
            "actual_date": None,
            "expected_latest_trade_date": expected_date,
            "calendar_expected_trade_date": calendar_expected_date,
            "reason": "latest official trade date is unavailable",
        }
    ready = selected_date == expected_date
    return {
        "status": "current" if ready else "stale",
        "ready": ready,
        "requested_date": None,
        "actual_date": selected_date,
        "expected_latest_trade_date": expected_date,
        "calendar_expected_trade_date": calendar_expected_date,
        "publication_status": (
            "source_delayed"
            if publication_date and publication_date < calendar_expected_date
            else "current"
        ),
        "reason": (
            "latest official trade date matches the atomically published full-market session"
            if ready
            else "selected trade date is older than the published full-market session"
        ),
    }


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _integer(value: Any) -> int:
    number = _number(value)
    return int(round(number)) if number is not None else 0


def _iso_timestamp(value: Any) -> str | None:
    number = _number(value)
    if number is None or number <= 0:
        return None
    try:
        return datetime.fromtimestamp(number, TPE).strftime("%Y-%m-%d %H:%M:%S")
    except (OSError, OverflowError, ValueError):
        return None


def _normalized_trade_time(value: Any, trade_date: str) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.isdigit():
        raw = int(text)
        seconds = raw / 1_000_000 if raw >= 10**14 else raw / 1_000 if raw >= 10**11 else raw
        try:
            parsed = datetime.fromtimestamp(seconds, TPE)
            return parsed.strftime("%Y-%m-%d %H:%M:%S.%f").rstrip("0").rstrip(".")
        except (OSError, OverflowError, ValueError):
            return None
    try:
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=TPE)
        return parsed.astimezone(TPE).strftime("%Y-%m-%d %H:%M:%S.%f").rstrip("0").rstrip(".")
    except ValueError:
        pass
    if re.fullmatch(r"\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?", text):
        return f"{trade_date} {text}"
    return None


def _official_history_is_trusted(history: dict[str, Any] | None) -> bool:
    if not history:
        return False
    source = str(history.get("source") or "").upper()
    quality = str(history.get("source_quality") or "").upper()
    return ("TWSE" in source or "TPEX" in source) and quality in OFFICIAL_QUALITY_VALUES


def _dominance(inner_lots: int, outer_lots: int) -> tuple[str, str]:
    if outer_lots > inner_lots:
        return "outer_stronger", "外盤較強"
    if inner_lots > outer_lots:
        return "inner_stronger", "內盤較強"
    return "balanced", "內外盤相當"


def _quality_values(rows: list[dict[str, Any]], key: str) -> set[str]:
    return {
        str(row.get(key) or "").strip().upper()
        for row in rows
        if str(row.get(key) or "").strip()
    }


def _source_values(rows: list[dict[str, Any]]) -> set[str]:
    return {
        str(row.get("source") or "").strip().upper()
        for row in rows
        if str(row.get("source") or "").strip()
    }


def _technical_payload(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {
            "available": False,
            "status": "unavailable",
            "decision_ready": False,
            "reason": "daily technical snapshot has not been materialized",
        }
    return {
        "available": True,
        "status": row.get("data_quality") or row.get("technical_data_quality") or "unavailable",
        "decision_ready": bool(row.get("decision_ready") or row.get("technical_decision_ready")),
        "reason": row.get("quality_reason") or row.get("technical_quality_reason"),
        "formula_version": row.get("formula_version"),
        "input_row_count": _integer(row.get("input_row_count")),
        "input_start_date": row.get("input_start_date"),
        "input_end_date": row.get("input_end_date"),
        "adjustment_event_count": _integer(row.get("adjustment_event_count")),
        "history_source": row.get("history_source"),
        "source_quality": row.get("source_quality"),
        "computed_at": row.get("computed_at"),
        "rsi": {
            "rsi5": _number(row.get("rsi5")),
            "rsi10": _number(row.get("rsi10")),
            "rsi14": _number(row.get("rsi14")),
        },
        "moving_averages": {
            "ma5": _number(row.get("ma5")),
            "ma10": _number(row.get("ma10")),
            "ma20": _number(row.get("ma20")),
            "ma60": _number(row.get("ma60")),
        },
        "macd": {
            "dif": _number(row.get("macd_dif")),
            "signal": _number(row.get("macd_signal")),
            "oscillator": _number(row.get("macd_osc")),
        },
        "kd": {
            "k": _number(row.get("kd_k")),
            "d": _number(row.get("kd_d")),
        },
        "atr14": _number(row.get("atr14")),
        "bollinger": {
            "middle": _number(row.get("boll_mid")),
            "upper": _number(row.get("boll_upper")),
            "lower": _number(row.get("boll_lower")),
        },
        "obv": _number(row.get("obv")),
        "volume_ma20": _number(row.get("volume_ma20")),
        "previous_10d_low": _number(row.get("previous_10d_low")),
    }


def _valuation_payload(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {
            "available": False,
            "status": "unavailable",
            "reason": "same-date official valuation is unavailable",
        }
    source_status = str(row.get("source_status") or row.get("valuation_source_status") or "").lower()
    source = str(row.get("source") or row.get("valuation_source") or "").strip()
    metrics = {
        "pe_ratio": _number(row.get("pe_ratio")),
        "pb_ratio": _number(row.get("pb_ratio")),
        "dividend_yield_pct": _number(row.get("dividend_yield")),
    }
    official_source = "TWSE" in source.upper() or "TPEX" in source.upper()
    available = bool(
        official_source
        and source_status in {"ok", "official"}
        and any(value is not None for value in metrics.values())
    )
    return {
        "available": available,
        "status": source_status or "unavailable",
        "reason": (
            "same-date official valuation is available"
            if available
            else "valuation requires an official same-date source and at least one numeric metric"
        ),
        "trade_date": row.get("trade_date"),
        **metrics,
        "source": source or None,
    }


def _institutional_context_payload(
    snapshot: dict[str, Any],
    selected_date: str | None,
) -> dict[str, Any]:
    rows = list(snapshot.get("institution_rows") or [])
    latest = rows[0] if rows else {}
    flow_freshness = assess_component_freshness(
        latest.get("date"), selected_date, max_lag_days=3
    )
    costs = list(snapshot.get("estimated_cost_rows") or [])
    canonical_costs = build_canonical_cost_snapshot(costs, expected_date=selected_date)
    cost_date = selected_date if costs else None
    cost_freshness = assess_component_freshness(
        cost_date, selected_date, max_lag_days=3
    )
    usable_costs = list(canonical_costs.get("estimated_costs") or [])

    flow_ready = bool(flow_freshness.get("ready"))
    note_parts: list[str] = []
    if flow_ready:
        values = [
            _number(latest.get("foreign_net")),
            _number(latest.get("trust_net")),
            _number(latest.get("dealer_net")),
        ]
        available_values = [value for value in values if value is not None]
        if available_values:
            positive = sum(1 for value in available_values if value > 0)
            negative = sum(1 for value in available_values if value < 0)
            if positive and not negative:
                note_parts.append("最近法人動向偏買方")
            elif negative and not positive:
                note_parts.append("最近法人動向偏賣方")
            else:
                note_parts.append("最近法人動向多空分歧")
    if usable_costs:
        cost_text = "、".join(
            f"{str(item.get('label') or '法人近期增量部位均價推估')} {item['value']:g}"
            for item in usable_costs[:2]
        )
        note_parts.append(f"{cost_text}；這是官方淨流量的近期增量估算，不是真實總持倉成本")
    available = bool(flow_ready or usable_costs)
    return {
        "available": available,
        "status": "ok" if available else "source_delayed",
        "trade_date": latest.get("date") if flow_ready else cost_date,
        "flow": {
            "available": flow_ready,
            "trade_date": latest.get("date") if flow_ready else None,
            "foreign_net": _number(latest.get("foreign_net")) if flow_ready else None,
            "trust_net": _number(latest.get("trust_net")) if flow_ready else None,
            "dealer_net": _number(latest.get("dealer_net")) if flow_ready else None,
        },
        "estimated_costs": usable_costs,
        "canonical_costs": canonical_costs.get("costs"),
        "cost_contract_version": canonical_costs.get("contract_version"),
        "freshness": {"flow": flow_freshness, "cost": cost_freshness},
        "note": "；".join(note_parts),
        "can_override_main_status": False,
    }


def _global_market_context_payload(
    snapshot: dict[str, Any],
    selected_date: str | None,
    industry_code: str | None = None,
) -> dict[str, Any]:
    rows = list(snapshot.get("global_market_rows") or [])
    if rows:
        market_date = str(rows[0].get("market_date") or "")[:10] or None
        freshness = assess_component_freshness(
            market_date, selected_date, max_lag_days=3
        )
        official_code = str(industry_code or "").strip().zfill(2)
        sector_weights: dict[str, float]
        if official_code == "24":
            sector_weights = {"^IXIC": 0.15, "^SOX": 0.45, "TSM": 0.25, "XLK": 0.15}
        elif official_code in {"25", "26", "27", "28", "29", "30", "31", "36"}:
            sector_weights = {"^IXIC": 0.35, "^SOX": 0.15, "XLK": 0.50}
        elif official_code == "17":
            sector_weights = {"^GSPC": 0.25, "XLF": 0.75}
        elif official_code == "22":
            sector_weights = {"^GSPC": 0.25, "XLV": 0.75}
        elif official_code == "23":
            sector_weights = {"^GSPC": 0.20, "XLE": 0.55, "XLU": 0.25}
        elif official_code in {"01", "03", "04", "05", "06", "08", "09", "10", "11", "14", "15", "21", "35"}:
            sector_weights = {"^GSPC": 0.20, "XLI": 0.45, "XLB": 0.35}
        elif official_code in {"02", "18", "32", "34", "37", "38"}:
            sector_weights = {"^GSPC": 0.20, "XLY": 0.45, "XLP": 0.35}
        elif official_code in {"12", "16"}:
            sector_weights = {"^GSPC": 0.20, "XLY": 0.50, "XLI": 0.30}
        else:
            sector_weights = {"^GSPC": 0.40, "^IXIC": 0.30, "XLI": 0.30}
        weights = sector_weights
        usable = [
            row
            for row in rows
            if str(row.get("source_quality") or "").lower() == "supplemental"
            and _number(row.get("change_pct")) is not None
            and str(row.get("ticker") or "") in weights
        ]
        weight_total = sum(weights[str(row.get("ticker"))] for row in usable)
        score = (
            sum(
                weights[str(row.get("ticker"))] * float(_number(row.get("change_pct")) or 0)
                for row in usable
            )
            / weight_total
            if weight_total > 0
            else None
        )
        available = bool(freshness.get("ready") and len(usable) >= 2 and score is not None)
        if available:
            if float(score) >= 0.5:
                stance = "positive"
                note = "最近可用的美股收盤背景偏正向，只提高觀察信心，不覆蓋個股支撐與裁判結論。"
            elif float(score) <= -0.5:
                stance = "negative"
                note = "最近可用的美股收盤背景偏弱，進場條件應更嚴格，但不單獨改寫個股裁判結論。"
            else:
                stance = "neutral"
                note = "最近可用的美股收盤背景中性，個股仍以自身支撐、趨勢與籌碼為主。"
            return {
                "available": True,
                "status": "ok",
                "market_date": market_date,
                "stance": stance,
                "score": round(float(score), 4),
                "formula_version": "industry-mapped-global-close-weighted-change-v2",
                "industry_code": official_code or None,
                "component_tickers": sorted(str(row.get("ticker")) for row in usable),
                "coverage_count": len(usable),
                "freshness": freshness,
                "note": note,
                "can_override_main_status": False,
            }
        return {
            "available": False,
            "status": "source_delayed",
            "market_date": market_date,
            "freshness": freshness,
            "coverage_count": len(usable),
            "note": "",
            "can_override_main_status": False,
        }

    row = snapshot.get("persisted_outlook") or {}
    try:
        payload = json.loads(str(row.get("payload_json") or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        payload = {}
    factor = ((payload.get("factors") or {}).get("us") or {}) if isinstance(payload, dict) else {}
    factor_date = str(factor.get("date") or "")[:10] or None
    row_freshness = assess_component_freshness(
        row.get("calc_date"), selected_date, max_lag_days=3
    )
    factor_freshness = assess_component_freshness(
        factor_date, selected_date, max_lag_days=3
    )
    score = _number(factor.get("score"))
    if score is None:
        score = _number(row.get("us_score"))
    available = bool(
        row_freshness.get("ready")
        and factor_freshness.get("ready")
        and factor.get("available")
        and score is not None
    )
    if not available:
        return {
            "available": False,
            "status": "source_delayed",
            "market_date": factor_date,
            "freshness": {"snapshot": row_freshness, "factor": factor_freshness},
            "note": "",
            "can_override_main_status": False,
        }
    if score >= 8:
        stance = "positive"
        note = "最近可用的美股收盤背景偏正向，只提高觀察信心，不覆蓋個股支撐與裁判結論。"
    elif score <= -8:
        stance = "negative"
        note = "最近可用的美股收盤背景偏弱，進場條件應更嚴格，但不單獨改寫個股裁判結論。"
    else:
        stance = "neutral"
        note = "最近可用的美股收盤背景中性，個股仍以自身支撐、趨勢與籌碼為主。"
    return {
        "available": True,
        "status": "ok",
        "market_date": factor_date,
        "stance": stance,
        "score": round(float(score), 2),
        "freshness": {"snapshot": row_freshness, "factor": factor_freshness},
        "note": note,
        "can_override_main_status": False,
    }


def _taifex_night_context_payload(
    snapshot: dict[str, Any],
    selected_date: str | None,
    code: str,
) -> dict[str, Any]:
    rows = list(snapshot.get("taifex_night_rows") or [])
    if not rows:
        return {"available": False, "status": "unavailable", "can_override_main_status": False}
    trade_date = str(rows[0].get("trade_date") or "")[:10] or None
    freshness = assess_component_freshness(trade_date, selected_date, max_lag_days=1)
    broad = {"TX": 0.70, "MTX": 0.30}
    finance = {"TX": 0.35, "MTX": 0.15, "TF": 0.35, "ZFF": 0.15}
    electronics = {"TX": 0.30, "MTX": 0.10, "TE": 0.40, "ZEF": 0.20}
    semiconductor = {**electronics, "SOF": 0.25}
    if code in {"2880", "2881", "2882", "2883", "2884", "2885", "2886", "2887", "2888", "2890", "2891", "2892"}:
        weights = finance
    elif code in {"2330", "2303", "2454", "3034", "3711", "2449", "6669", "3661"}:
        weights = semiconductor
    elif code in {"2308", "2317", "2327", "2357", "2382", "2395", "2408", "2412", "3008", "3017", "3231", "3653", "4938"}:
        weights = electronics
    else:
        weights = broad
    usable = [
        row for row in rows
        if str(row.get("source_quality") or "").lower() == "official"
        and str(row.get("contract") or "") in weights
        and _number(row.get("change_pct")) is not None
        and _number(row.get("volume")) is not None
        and float(_number(row.get("volume")) or 0) > 0
    ]
    weight_total = sum(weights[str(row.get("contract"))] for row in usable)
    score = (
        sum(weights[str(row.get("contract"))] * float(_number(row.get("change_pct")) or 0) for row in usable)
        / weight_total
        if weight_total > 0
        else None
    )
    available = bool(freshness.get("ready") and len(usable) >= 1 and score is not None)
    if not available:
        return {
            "available": False,
            "status": "source_delayed",
            "trade_date": trade_date,
            "freshness": freshness,
            "coverage_count": len(usable),
            "can_override_main_status": False,
        }
    stance = "positive" if float(score) >= 0.5 else "negative" if float(score) <= -0.5 else "neutral"
    note = (
        "最近官方台灣期貨夜盤偏多，只提高隔日觀察信心，不覆蓋個股裁判結論。"
        if stance == "positive"
        else "最近官方台灣期貨夜盤偏弱，隔日進場條件應更嚴格，但不單獨改寫個股結論。"
        if stance == "negative"
        else "最近官方台灣期貨夜盤中性，個股仍以自身支撐、趨勢與籌碼為主。"
    )
    return {
        "available": True,
        "status": "ok",
        "trade_date": trade_date,
        "stance": stance,
        "score": round(float(score), 4),
        "coverage_count": len(usable),
        "formula_version": "taifex-night-related-contract-weight-v1",
        "freshness": freshness,
        "note": note,
        "can_override_main_status": False,
    }


def _event_reference_day(as_of_date: str | None) -> date:
    try:
        return date.fromisoformat(str(as_of_date or "")[:10])
    except ValueError:
        return datetime.now(TPE).date()


def _official_event_context_payload(
    snapshot: dict[str, Any],
    as_of_date: str | None = None,
) -> dict[str, Any]:
    rows = list(snapshot.get("official_event_rows") or [])
    reference_day = _event_reference_day(as_of_date)
    usable: list[dict[str, Any]] = []
    for row in rows:
        try:
            disclosed = date.fromisoformat(str(row.get("disclosed_date") or "")[:10])
        except ValueError:
            continue
        age_days = (reference_day - disclosed).days
        if age_days < 0 or age_days > 7 or str(row.get("source_quality") or "").lower() != "official":
            continue
        impact = classify_company_disclosure(row.get("subject"), row.get("explanation"))
        disclosure_text = f"{row.get('subject') or ''} {row.get('explanation') or ''}"
        event_type = (
            "investor_conference"
            if any(keyword in disclosure_text for keyword in ("法說", "法人說明會"))
            else "material_information"
        )
        decay_weight = 0.5 ** (age_days / 7)
        usable.append(
            {
                "disclosed_date": disclosed.isoformat(),
                "disclosed_time": str(row.get("disclosed_time") or ""),
                "available_at": row.get("available_at"),
                "retrieved_at": row.get("fetched_at") or row.get("available_at"),
                "market_session": str(row.get("market_session") or "unknown"),
                "effective_tw_trade_date": row.get("effective_tw_trade_date"),
                "event_type": event_type,
                "source_id": str(row.get("source") or "MOPS"),
                "source_quality": str(row.get("source_quality") or "official"),
                "subject": str(row.get("subject") or "")[:500],
                "explanation_excerpt": str(row.get("explanation") or "")[:500],
                "attention_level": str(row.get("attention_level") or "normal"),
                "direction": impact["direction"],
                "confidence": impact["confidence"],
                "age_days": age_days,
                "effective_weight": round(0.97 * 0.7 * decay_weight, 4),
            }
        )
    if not usable:
        return {"available": False, "status": "unavailable", "events": [], "can_override_main_status": False}
    attention = any(row.get("attention_level") == "attention" for row in usable)
    note = (
        f"近 7 日有 {len(usable)} 則官方重大訊息，包含需優先閱讀的事件；不能只靠標題直接判定利多或利空。"
        if attention
        else f"近 7 日有 {len(usable)} 則官方重大訊息；應結合內容與價格反應，不以標題直接判定多空。"
    )
    return {
        "available": True,
        "status": "ok",
        "latest_date": usable[0]["disclosed_date"],
        "events": usable[:3],
        "attention_required": attention,
        "note": note,
        "can_override_main_status": False,
    }


def _external_event_context_payload(
    snapshot: dict[str, Any],
    as_of_date: str | None = None,
) -> dict[str, Any]:
    """Apply source gates, newest-first dedupe, and event-type time decay."""

    rows = sorted(
        list(snapshot.get("external_event_rows") or []),
        key=lambda row: str(row.get("published_at") or row.get("event_date") or ""),
        reverse=True,
    )
    reference_day = _event_reference_day(as_of_date)
    accepted: list[dict[str, Any]] = []
    seen_fingerprints: set[str] = set()
    seen_topics: set[tuple[str, str, str, tuple[str, ...]]] = set()
    half_life_days = {
        "authorized_social": 3.0,
        "government_policy": 7.0,
        "official_news": 7.0,
        "monthly_revenue": 45.0,
    }
    for row in rows:
        try:
            event_day = date.fromisoformat(str(row.get("event_date") or "")[:10])
        except ValueError:
            continue
        age_days = (reference_day - event_day).days
        if age_days < 0 or age_days > 60:
            continue
        quality = assess_external_event_quality(row)
        reliability = float(quality["reliability_score"])
        reference_value = float(quality["reference_value_score"])
        if not quality["ready"]:
            continue
        fingerprint = str(row.get("content_fingerprint") or row.get("event_key") or "")
        if not fingerprint or fingerprint in seen_fingerprints:
            continue
        matched_terms = tuple(sorted(str(item) for item in row.get("matched_stock_terms") or [] if str(item)))
        affected_terms = tuple(sorted(str(item) for item in row.get("affected_terms") or [] if str(item)))
        topic_terms = matched_terms or affected_terms
        topic_key = (
            str(row.get("source_id") or ""),
            str(row.get("event_type") or ""),
            str(row.get("code") or ""),
            topic_terms,
        )
        if topic_key in seen_topics:
            continue
        seen_fingerprints.add(fingerprint)
        seen_topics.add(topic_key)
        event_type = str(row.get("event_type") or "")
        half_life = half_life_days.get(event_type, 7.0)
        time_decay = 0.5 ** (age_days / half_life)
        effective_weight = reliability * reference_value * time_decay
        if effective_weight < 0.12:
            continue
        accepted.append(
            {
                "event_date": event_day.isoformat(),
                "published_at": row.get("published_at"),
                "available_at": row.get("available_at") or row.get("retrieved_at"),
                "retrieved_at": row.get("retrieved_at") or row.get("available_at"),
                "event_type": event_type,
                "source_id": str(row.get("source_id") or "external_event"),
                "source_quality": str(row.get("source_quality") or "licensed"),
                "content_fingerprint": fingerprint,
                "title": str(row.get("title") or "")[:500],
                "summary_excerpt": str(row.get("summary_excerpt") or "")[:500],
                "publisher": str(row.get("publisher") or ""),
                "url": str(row.get("source_url") or ""),
                "direction": str(row.get("direction") or "unknown"),
                "confidence": str(row.get("confidence") or "low"),
                "mapping_method": str(row.get("mapping_method") or ""),
                "matched_stock_terms": list(matched_terms),
                "affected_terms": list(affected_terms),
                "metrics": dict(row.get("metrics") or {}),
                "age_days": age_days,
                "reliability_score": round(reliability, 4),
                "reference_value_score": round(reference_value, 4),
                "time_decay": round(time_decay, 4),
                "effective_weight": round(effective_weight, 4),
                "can_override_main_status": False,
            }
        )
        if len(accepted) >= 6:
            break
    if not accepted:
        return {
            "available": False,
            "status": "unavailable",
            "events": [],
            "impact_stance": "insufficient",
            "can_override_main_status": False,
        }
    direction_value = {"positive": 1.0, "negative": -1.0}
    directional = [row for row in accepted if row["direction"] in direction_value]
    denominator = sum(float(row["effective_weight"]) for row in directional)
    score = (
        sum(direction_value[row["direction"]] * float(row["effective_weight"]) for row in directional)
        / denominator
        if denominator > 0
        else 0.0
    )
    stance = "positive" if score >= 0.2 else "negative" if score <= -0.2 else "mixed_or_neutral"
    newest = accepted[0]
    note = (
        f"最新事件日期為 {newest['event_date']}，加權後消息面偏正向；已依來源可靠度、參考價值與時間衰減計權，不能單獨推論必漲。"
        if stance == "positive"
        else f"最新事件日期為 {newest['event_date']}，加權後消息面偏負向；已降低舊聞權重，仍需由價格與量能確認。"
        if stance == "negative"
        else f"最新事件日期為 {newest['event_date']}，加權後消息方向分歧或中性；舊聞已時間衰減，不強行選邊。"
    )
    return {
        "available": True,
        "status": "ok",
        "latest_date": newest["event_date"],
        "impact_stance": stance,
        "impact_score": round(score, 4),
        "events": accepted,
        "quality_contract": {
            "newest_first": True,
            "content_deduplicated": True,
            "topic_latest_wins": True,
            "time_decay_applied": True,
            "minimum_reliability_score": EXTERNAL_EVENT_MIN_RELIABILITY_SCORE,
            "minimum_reference_value_score": EXTERNAL_EVENT_MIN_REFERENCE_VALUE_SCORE,
        },
        "note": note,
        "can_override_main_status": False,
    }


_MARKET_TOPIC_TERMS = (
    "川普", "關稅", "貿易", "美國", "中國", "兩岸", "聯準會", "降息", "升息",
    "利率", "匯率", "新台幣", "美元", "日圓", "通膨", "就業", "景氣", "政策",
    "AI", "人工智慧", "半導體", "晶片", "伺服器", "電子", "電力", "能源", "石油",
    "天然氣", "航運", "航空", "軍工", "國防", "生技", "金融", "銀行", "保險",
    "房市", "營建", "鋼鐵", "塑化", "化工", "橡膠", "汽車", "觀光", "內需",
    "美伊", "伊朗", "Iran", "以色列", "Israel", "中東", "Middle East", "荷莫茲", "Hormuz",
)

_MARKET_TOPIC_ALIASES: dict[str, tuple[str, ...]] = {
    "美伊": ("伊朗", "Iran", "荷莫茲", "Hormuz"),
    "伊朗": ("伊朗", "Iran", "Iranian", "荷莫茲", "Hormuz"),
    "以色列": ("以色列", "Israel", "Israeli"),
    "中東": ("中東", "Middle East", "伊朗", "Iran", "以色列", "Israel", "荷莫茲", "Hormuz"),
    "荷莫茲": ("荷莫茲", "Hormuz", "伊朗", "Iran"),
}


def _market_query_terms(query: str) -> list[str]:
    normalized = re.sub(r"\s+", "", str(query or ""))
    direct = [term for term in _MARKET_TOPIC_TERMS if term.lower() in normalized.lower()]
    expanded: list[str] = []
    for term in direct:
        expanded.extend(_MARKET_TOPIC_ALIASES.get(term, (term,)))
    return list(dict.fromkeys(expanded))


def build_bot_market_brief(query: str = "") -> dict[str, Any]:
    """Build a read-only, dated market/news context for non-symbol questions."""

    reference_date = datetime.now(TPE).date().isoformat()
    try:
        snapshot = read_general_market_context(reference_date)
    except Exception:
        return {
            "ok": False,
            "status": "unavailable",
            "reference_date": reference_date,
            "reason": "trusted market context is unavailable",
            "query": str(query or "")[:200],
        }
    external = _external_event_context_payload(snapshot, reference_date)
    global_context = _global_market_context_payload(snapshot, reference_date)
    topic_terms = _market_query_terms(query)
    matched_events: list[dict[str, Any]] = []
    if topic_terms and external.get("available"):
        for event in list(external.get("events") or []):
            searchable = " ".join(
                [
                    str(event.get("title") or ""),
                    str(event.get("summary_excerpt") or ""),
                    " ".join(str(item) for item in event.get("affected_terms") or []),
                ]
            ).lower()
            if any(term.lower() in searchable for term in topic_terms):
                matched_events.append(event)
    topic_match_required = bool(topic_terms)
    return {
        "ok": bool(external.get("available") or global_context.get("available")),
        "status": (
            "ok"
            if external.get("available") or global_context.get("available")
            else "source_delayed"
        ),
        "reference_date": reference_date,
        "query": str(query or "")[:200],
        "topic_terms": topic_terms,
        "topic_match_required": topic_match_required,
        "topic_match": bool(matched_events) if topic_match_required else True,
        "matched_events": matched_events[:4],
        "external_event_context": external,
        "global_market_context": global_context,
        "quality_note": (
            "消息依發布時間由新到舊、來源可靠度、參考價值與時間衰減整理；"
            "消息不能單獨推論個股必漲或必跌。"
        ),
        "can_override_main_status": False,
    }


def _news_radar_context_payload(
    snapshot: dict[str, Any],
    as_of_date: str | None = None,
) -> dict[str, Any]:
    """Expose fresh GDELT metadata as leads; never treat it as verified evidence."""

    rows = sorted(
        list(snapshot.get("news_radar_rows") or []),
        key=lambda row: str(row.get("published_at") or ""),
        reverse=True,
    )
    reference_day = _event_reference_day(as_of_date)
    accepted: list[dict[str, Any]] = []
    fingerprints: set[str] = set()
    for row in rows:
        quality = assess_news_radar_quality(row)
        if not quality["ready_for_radar"]:
            continue
        try:
            event_day = date.fromisoformat(str(row.get("event_date") or "")[:10])
        except ValueError:
            continue
        age_days = (reference_day - event_day).days
        if age_days < 0 or age_days > 3:
            continue
        fingerprint = str(row.get("content_fingerprint") or row.get("event_key") or "")
        if not fingerprint or fingerprint in fingerprints:
            continue
        fingerprints.add(fingerprint)
        metrics = dict(row.get("metrics") or {})
        accepted.append(
            {
                "event_date": event_day.isoformat(),
                "published_at": row.get("published_at"),
                "available_at": row.get("available_at") or row.get("retrieved_at"),
                "retrieved_at": row.get("retrieved_at") or row.get("available_at"),
                "source_id": str(row.get("source_id") or "news_radar"),
                "source_quality": "supplemental",
                "content_fingerprint": fingerprint,
                "publisher": str(row.get("publisher") or "")[:120],
                "url": str(row.get("source_url") or "")[:1000],
                "title": str(row.get("title") or "")[:500],
                "matched_stock_terms": list(row.get("matched_stock_terms") or [])[:8],
                "mapping_method": str(row.get("mapping_method") or ""),
                "language": str(metrics.get("language") or "")[:40],
                "source_country": str(metrics.get("source_country") or "")[:80],
                "potential_direction": str(metrics.get("potential_direction") or "unknown"),
                "verification_status": "unverified",
                "age_days": age_days,
                "can_override_main_status": False,
            }
        )
        if len(accepted) >= 5:
            break
    if not accepted:
        return {
            "available": False,
            "status": "unavailable",
            "events": [],
            "ready_for_referee": False,
            "can_override_main_status": False,
        }
    return {
        "available": True,
        "status": "unverified",
        "latest_date": accepted[0]["event_date"],
        "events": accepted,
        "note": "以下為免費新聞索引線索，尚未通過一手來源查證，不計入消息多空與裁判主結論。",
        "requires_primary_source_verification": True,
        "ready_for_referee": False,
        "can_override_main_status": False,
    }


def _unavailable_referee(reason_code: str, reason: str) -> dict[str, Any]:
    return {
        "decision_ready": False,
        "main_status": "資料不足",
        "main_reasons": [reason],
        "reason_code": reason_code,
        "source": "shared_project_referee",
        "version": PRACTICAL_STATUS_CORE_VERSION,
        "can_be_overridden_by_model": False,
    }


def _zone_bounds(value: Any) -> tuple[float | None, float | None]:
    zone = value if isinstance(value, dict) else {}
    price = _number(zone.get("price"))
    lower = _number(zone.get("zone_low"))
    upper = _number(zone.get("zone_high"))
    return lower if lower is not None else price, upper if upper is not None else price


def _shared_referee_payload(
    *,
    snapshot: dict[str, Any],
    history: dict[str, Any],
    technical: dict[str, Any],
    freshness: dict[str, Any],
    recommendation_safety: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the shared dashboard input assembler and referee after strict source gates."""

    safety = dict(recommendation_safety or {})

    def unavailable(reason_code: str, reason: str) -> dict[str, Any]:
        result = _unavailable_referee(reason_code, reason)
        result["recommendation_safety"] = safety
        return result

    if safety.get("hard_blocked"):
        reasons = [
            str(item)
            for item in list(safety.get("blocking_reasons") or [])
            if str(item).strip()
        ]
        return {
            "decision_ready": False,
            "main_status": "不判斷",
            "main_reasons": reasons[:2] or ["安全資格具有否決條件，不進行技術裁判"],
            "reason_code": "recommendation_safety_hard_block",
            "source": "shared_project_referee",
            "version": PRACTICAL_STATUS_CORE_VERSION,
            "recommendation_safety": safety,
            "can_be_overridden_by_model": False,
        }

    if not freshness.get("ready") or not _official_history_is_trusted(history):
        return unavailable(
            "official_daily_not_ready",
            "官方日線尚未通過最近完整交易日與來源品質檢查",
        )
    if not technical.get("decision_ready"):
        return unavailable(
            "technical_not_ready",
            "同日技術指標尚未通過資料品質檢查",
        )

    recent = list(snapshot.get("recent_referee_context") or [])
    selected_date = str(snapshot.get("selected_date") or "")
    recent_context_ready = bool(
        len(recent) >= 4
        and str(recent[0].get("date") or "") == selected_date
        and all(_official_history_is_trusted(row) for row in recent[:4])
        and all(bool(row.get("technical_decision_ready")) for row in recent[:4])
        and all(
            str(row.get("technical_data_quality") or "").lower() in {"ok", "official"}
            for row in recent[:4]
        )
    )
    if not recent_context_ready:
        return unavailable(
            "recent_context_not_ready",
            "風險判讀所需的多日技術資料尚未完整通過品質檢查",
        )

    component_dates = snapshot.get("referee_component_dates") or {}
    institution_freshness = assess_component_freshness(
        component_dates.get("institution"), selected_date, max_lag_days=3
    )
    margin_freshness = assess_component_freshness(
        component_dates.get("margin"), selected_date, max_lag_days=3
    )
    referee_history = list(snapshot.get("referee_history") or [])
    normalized_history: list[dict[str, Any]] = []
    for row in referee_history:
        normalized = dict(row)
        volume = _number(normalized.get("volume"))
        if str(normalized.get("volume_unit") or "shares").lower() == "lots" and volume is not None:
            normalized["volume"] = volume * 1000
            normalized["volume_unit"] = "shares"
        normalized_history.append(normalized)
    history_ready = bool(
        len(normalized_history) >= 60
        and str(normalized_history[-1].get("date") or "") == selected_date
        and all(_official_history_is_trusted(row) for row in normalized_history)
        and all(
            all(
                _number(row.get(field)) is not None
                for field in ("open", "high", "low", "close", "volume")
            )
            for row in normalized_history
        )
    )
    if not history_ready:
        return unavailable(
            "official_history_not_ready",
            "共用支撐賣壓組裝器需要至少 60 個交易日的完整官方 OHLCV",
        )
    current_price = _number(history.get("close"))
    if current_price is None or current_price <= 0:
        return unavailable("current_price_not_ready", "官方收盤價不可用")
    levels = build_ohlcv_support_resistance_levels(normalized_history, current_price)
    supports = cluster_levels(
        [level for level in levels if level.get("side") == "support"],
        current_price,
    )
    resistances = cluster_levels(
        [level for level in levels if level.get("side") == "resistance"],
        current_price,
    )
    support_zone = supports[0] if supports else None
    resistance_zone = resistances[0] if resistances else None
    support_lower, support_upper = _zone_bounds(support_zone)
    _resistance_lower, resistance_upper = _zone_bounds(resistance_zone)
    if None in (support_lower, support_upper, resistance_upper):
        available_components = {
            "support": bool(support_lower is not None and support_upper is not None),
            "resistance": bool(resistance_upper is not None),
        }
        if any(available_components.values()):
            missing_labels = [
                label
                for key, label in (("support", "支撐區"), ("resistance", "賣壓區"))
                if not available_components[key]
            ]
            return {
                "decision_ready": False,
                "partial_analysis_available": True,
                "main_status": "資料部分可用",
                "main_reasons": [
                    f"官方 OHLCV 已形成單側結構；{''.join(missing_labels)}尚未可靠形成，因此不產生主結論"
                ],
                "reason_code": "partial_support_resistance",
                "source": "shared_project_referee",
                "version": PRACTICAL_STATUS_CORE_VERSION,
                "input_assembler_version": SUPPORT_RESISTANCE_ASSEMBLER_VERSION,
                "support_zone": support_zone if available_components["support"] else None,
                "resistance_zone": resistance_zone if available_components["resistance"] else None,
                "support_resistance_method": (
                    "OHLCV成交密集區 + 技術關卡共振｜法人／融資僅作附加背景"
                ),
                "support_resistance_semantics": (
                    "多日官方 OHLCV 權重成交密集區與技術關卡共振；不是單日逐筆成交分價量"
                ),
                "component_coverage": available_components,
                "omissions": [
                    {"component": key, "reason": "reliable_zone_not_formed"}
                    for key, ready in available_components.items()
                    if not ready
                ],
                "component_freshness": {
                    "institution": institution_freshness,
                    "margin": margin_freshness,
                },
                "recommendation_safety": safety,
                "can_be_overridden_by_model": False,
            }
        return unavailable(
            "shared_support_resistance_not_ready",
            "共用 OHLCV 成交密集區尚未形成可靠的支撐或賣壓區",
        )

    moving_averages = technical.get("moving_averages") or {}
    rsi = technical.get("rsi") or {}
    macd = technical.get("macd") or {}
    core_result = classify_practical_status_core(
        {
            "current_price": current_price,
            "previous_close": _number(recent[1].get("close")),
            "ma20": _number(moving_averages.get("ma20")),
            "ma20_3days_ago": _number(recent[3].get("ma20")),
            "ma60": _number(moving_averages.get("ma60")),
            "rsi": _number(rsi.get("rsi14")),
            "macd_osc": _number(macd.get("oscillator")),
            "macd_osc_prev": _number(recent[1].get("macd_osc")),
            "atr": _number(technical.get("atr14")),
            "volume": _number(history.get("volume")),
            "volume_avg_20d": _number(technical.get("volume_ma20")),
            "low_10d": _number(technical.get("previous_10d_low")),
            "support_zone_upper": support_upper,
            "support_zone_lower": support_lower,
            "resistance_zone_upper": resistance_upper,
        }
    )
    core_result = apply_safety_cap_to_referee(core_result, safety)
    main_status = str(core_result.get("status") or "資料不足")
    reasons = [
        str(reason)
        for reason in list(core_result.get("reasons") or [])
        if str(reason).strip()
    ][:2]
    if main_status == "資料不足":
        return unavailable(
            "core_input_not_ready",
            "裁判層必要數值不完整或未通過數值範圍檢查",
        )
    return {
        "decision_ready": True,
        "main_status": main_status,
        "main_reasons": reasons,
        "reason_code": None,
        "source": "shared_project_referee",
        "version": PRACTICAL_STATUS_CORE_VERSION,
        "input_assembler_version": SUPPORT_RESISTANCE_ASSEMBLER_VERSION,
        "support_zone": support_zone,
        "resistance_zone": resistance_zone,
        "support_resistance_method": (
            "OHLCV成交密集區 + 技術關卡共振｜法人／融資僅作附加背景"
        ),
        "support_resistance_semantics": (
            "多日官方 OHLCV 權重成交密集區與技術關卡共振；不是單日逐筆成交分價量"
        ),
        "component_freshness": {
            "institution": institution_freshness,
            "margin": margin_freshness,
        },
        "recommendation_safety": safety,
        "can_be_overridden_by_model": False,
    }


def _build_canonical_close_batch_snapshot_impl(
    code: str,
    *,
    trade_date: str | None = None,
    include_levels: bool = True,
    level_limit: int = 100,
    analysis_mode: str = "close_batch",
    allow_live_quote_fetch: bool = False,
    analysis_cutoff: str | None = None,
) -> dict[str, Any]:
    normalized_code = normalize_bot_stock_code(code)
    if not normalized_code:
        return {"ok": False, "status": "invalid_request", "reason": "stock code must be exactly four digits"}
    normalized_date = normalize_bot_trade_date(trade_date)
    if trade_date and not normalized_date:
        return {"ok": False, "status": "invalid_request", "reason": "trade_date must be YYYY-MM-DD"}
    normalized_analysis_mode = str(analysis_mode or "").strip().lower()
    if normalized_analysis_mode not in ANALYSIS_MODES:
        return {
            "ok": False,
            "status": "invalid_request",
            "reason": "analysis_mode must be close_batch or intraday",
        }
    if normalized_date and normalized_analysis_mode == "intraday":
        return {
            "ok": False,
            "status": "invalid_request",
            "reason": "intraday analysis cannot be combined with an explicit historical trade_date",
        }
    level_limit = max(1, min(int(level_limit), 200))
    try:
        snapshot = (
            read_daily_market_microstructure(
                normalized_code,
                normalized_date,
                analysis_cutoff=analysis_cutoff,
            )
            if analysis_cutoff is not None
            else read_daily_market_microstructure(normalized_code, normalized_date)
        )
    except Exception:
        return {
            "ok": False,
            "status": "unavailable",
            "reason": "market-data database is unavailable",
            "code": normalized_code,
            "trade_date": normalized_date,
            "analysis_mode": normalized_analysis_mode,
        }
    history = snapshot.get("history") or None
    selected_date = str(snapshot.get("selected_date") or normalized_date or "") or None
    freshness = _daily_freshness(
        selected_date,
        normalized_date,
        str(snapshot.get("publication_date") or "") or None,
    )
    rows = list(snapshot.get("distribution") or [])
    technical = _technical_payload(snapshot.get("technical"))
    valuation = _valuation_payload(snapshot.get("valuation"))
    stock = snapshot.get("stock") or None
    institutional_context = _institutional_context_payload(snapshot, selected_date)
    global_market_context = _global_market_context_payload(
        snapshot,
        selected_date,
        str((stock or {}).get("industry_code") or "") or None,
    )
    taifex_night_context = _taifex_night_context_payload(snapshot, selected_date, normalized_code)
    official_event_context = _official_event_context_payload(snapshot, selected_date)
    external_event_context = _external_event_context_payload(snapshot, selected_date)
    news_radar_context = _news_radar_context_payload(snapshot, selected_date)
    trading_state = classify_official_trading_state(
        history=history,
        no_trade_evidence=snapshot.get("no_trade_evidence"),
        stock=stock,
        trading_restriction_context=snapshot.get("trading_restriction_context"),
    )
    if not history:
        verified_no_trade = trading_state.get("status") in {
            "no_trade",
            "trading_halt",
            "no_regular_lot_ohlcv",
            "no_ohlcv_residual_activity",
        }
        inactive_official_universe = trading_state.get("status") == "inactive_official_universe"
        referee_reason = str(trading_state.get("reason") or "官方日線不可用")
        referee = (
            {
                "decision_ready": False,
                "main_status": "不判斷",
                "main_reasons": [referee_reason],
                "reason_code": str(trading_state.get("status") or "no_trade"),
                "source": "shared_project_referee",
                "version": PRACTICAL_STATUS_CORE_VERSION,
                "can_be_overridden_by_model": False,
            }
            if verified_no_trade or inactive_official_universe
            else _unavailable_referee(
                "official_daily_not_ready",
                "官方日線資料不足，無法形成主結論",
            )
        )
        return {
            "ok": False,
            "status": str(trading_state.get("status") or "unavailable"),
            "reason": str(trading_state.get("reason") or "official daily close is unavailable for the requested date"),
            "code": normalized_code,
            "trade_date": selected_date,
            "data_date": selected_date,
            "analysis_mode": normalized_analysis_mode,
            "update_mode": normalized_analysis_mode,
            "is_realtime": False,
            "timezone": "Asia/Taipei",
            "freshness": freshness,
            "trading_state": trading_state,
            "stock": stock,
            "technical": technical,
            "valuation": valuation,
            "referee": referee,
            "price_levels": [],
        }
    ohlcv = {
        "date": selected_date,
        "open": _number(history.get("open")),
        "high": _number(history.get("high")),
        "low": _number(history.get("low")),
        "close": _number(history.get("close")),
        "volume_shares": _number(history.get("volume")),
        "source": str(history.get("source") or "") or None,
        "source_quality": str(history.get("source_quality") or "") or None,
        "official_trusted": _official_history_is_trusted(history),
    }
    recommendation_safety = _recommendation_safety_payload(snapshot, str(selected_date))
    intraday_quote = (
        _current_intraday_quote(
            normalized_code,
            snapshot.get("intraday_quote"),
            allow_network=allow_live_quote_fetch,
        )
        if normalized_analysis_mode == "intraday"
        else {
            "available": False,
            "required": False,
            "status": "historical_request" if normalized_date else "not_used_for_close_batch",
            "reason": (
                "an explicit historical date uses that date's completed close"
                if normalized_date
                else "close-batch analysis uses the latest completed official close"
            ),
        }
    )
    if normalized_analysis_mode == "close_batch":
        decision_price = history.get("close")
        decision_price_basis = "completed_close"
    else:
        decision_price = intraday_quote.get("price") if intraday_quote.get("available") else None
        decision_price_basis = (
            "intraday" if intraday_quote.get("available") else "unavailable_current_session"
        )
    mode_metadata = {
        "data_date": selected_date,
        "analysis_cutoff": snapshot.get("analysis_cutoff"),
        "analysis_mode": normalized_analysis_mode,
        "update_mode": normalized_analysis_mode,
        "is_realtime": bool(
            normalized_analysis_mode == "intraday" and intraday_quote.get("available")
        ),
        "timezone": "Asia/Taipei",
    }
    decision_audit = {
        "trade_date": selected_date,
        "calculated_at": recommendation_safety.get("calculated_at"),
        "price_basis": None,
        "analysis_mode": normalized_analysis_mode,
        "update_mode": normalized_analysis_mode,
        "is_realtime": mode_metadata["is_realtime"],
        "safety_version": RECOMMENDATION_SAFETY_VERSION,
    }
    if not rows:
        referee = _shared_referee_payload(
            snapshot=snapshot,
            history=history,
            technical=technical,
            freshness=freshness,
            recommendation_safety=recommendation_safety,
        )
        recent = list(snapshot.get("recent_referee_context") or [])
        advisory = build_conditional_advisory(
            referee=referee,
            current_price=decision_price,
            technical=technical,
            previous_macd_osc=(recent[1].get("macd_osc") if len(recent) > 1 else None),
            institutional_context=institutional_context,
            global_market_context=global_market_context,
            taifex_night_context=taifex_night_context,
            official_event_context=official_event_context,
            external_event_context=external_event_context,
            recent_context=recent,
            price_basis=decision_price_basis,
            recommendation_safety=recommendation_safety,
            decision_audit={**decision_audit, "price_basis": decision_price_basis},
        )
        advisory["price_basis"] = decision_price_basis
        advisory["price_as_of"] = intraday_quote.get("as_of") or selected_date
        component_status = "stale" if freshness.get("status") == "stale" else "source_delayed"
        component_reason = (
            str(freshness.get("reason") or "official daily data is stale")
            if component_status == "stale"
            else "price-volume distribution has not been captured for the official close date"
        )
        return {
            "ok": False,
            "status": component_status,
            "reason": component_reason,
            "code": normalized_code,
            "trade_date": selected_date,
            **mode_metadata,
            "freshness": freshness,
            "close": _number(history.get("close")),
            "ohlcv": ohlcv,
            "intraday_quote": intraday_quote,
            "stock": stock,
            "trading_state": trading_state,
            "technical": technical,
            "valuation": valuation,
            "institutional_context": institutional_context,
            "global_market_context": global_market_context,
            "taifex_night_context": taifex_night_context,
            "official_event_context": official_event_context,
            "external_event_context": external_event_context,
            "news_radar_context": news_radar_context,
            "recommendation_safety": recommendation_safety,
            "decision_audit": {**decision_audit, "price_basis": decision_price_basis},
            "advisory": advisory,
            "price_levels": [],
            "price_level_count": 0,
            "price_levels_truncated": False,
            "data_quality": {
                "decision_ready": False,
                "official_close_trusted": bool(ohlcv["official_trusted"]),
                "distribution_validated": False,
                "session_complete": False,
                "reason_code": "exact_date_price_volume_missing",
                "component_status": component_status,
                "component_reason": component_reason,
            },
            "flow_summary": {
                "direction_available": False,
                "inner_lots": None,
                "outer_lots": None,
                "neutral_lots": None,
                "net_active_lots": None,
                "dominance": "unavailable",
                "dominance_zh": "分價量尚未取得",
                "is_institutional_net_buy_sell": False,
            },
            "support_pressure": {"available": False, "status": "source_delayed"},
            "referee": referee,
            "multi_day_score": {
                "available": False,
                "referee_eligible": False,
                "status": "not_enabled_pending_trading_date_coverage",
            },
        }

    total_lots = 0
    total_inner = 0
    total_outer = 0
    total_neutral = 0
    direction_level_count = 0
    profile: list[dict[str, Any]] = []
    levels: list[dict[str, Any]] = []
    for row in rows:
        price = _number(row.get("price"))
        lots = _integer(row.get("volume_lots"))
        if price is None or price <= 0 or lots <= 0:
            continue
        raw_inner = _number(row.get("volume_at_bid"))
        raw_outer = _number(row.get("volume_at_ask"))
        direction_available = bool(
            raw_inner is not None
            and raw_outer is not None
            and raw_inner >= 0
            and raw_outer >= 0
            and raw_inner + raw_outer <= lots
        )
        inner = int(round(raw_inner)) if direction_available and raw_inner is not None else 0
        outer = int(round(raw_outer)) if direction_available and raw_outer is not None else 0
        neutral = max(lots - inner - outer, 0) if direction_available else lots
        total_lots += lots
        if direction_available:
            direction_level_count += 1
            total_inner += inner
            total_outer += outer
        total_neutral += neutral
        dominance, dominance_zh = (
            _dominance(inner, outer) if direction_available else ("unavailable", "內外盤資料缺失")
        )
        levels.append(
            {
                "price": price,
                "volume_lots": lots,
                "direction_available": direction_available,
                "inner_lots": inner if direction_available else None,
                "outer_lots": outer if direction_available else None,
                "neutral_lots": neutral,
                "net_active_lots": outer - inner if direction_available else None,
                "dominance": dominance,
                "dominance_zh": dominance_zh,
            }
        )
        profile.append({"price": price, "volume": lots})

    official_volume_shares = _number(history.get("volume"))
    captured_volume_shares = total_lots * 1000
    volume_diff_pct = None
    if official_volume_shares and official_volume_shares > 0:
        volume_diff_pct = abs(captured_volume_shares - official_volume_shares) / official_volume_shares * 100
    data_qualities = _quality_values(rows, "data_quality")
    source_qualities = _quality_values(rows, "source_quality")
    distribution_sources = _source_values(rows)
    snapshot_quality = assess_post_close_or_delayed_capture(
        selected_date,
        {
            str(row.get("snapshot_time") or "").strip()
            for row in rows
            if str(row.get("snapshot_time") or "").strip()
        },
        snapshot.get("capture"),
    )
    profile_row = snapshot.get("profile") or {}
    profile_validated = bool(
        str(profile_row.get("quality") or "").lower() in {"high", "ok"}
        and "FUGLE" in str(profile_row.get("source_name") or "").upper()
    )
    validated = (
        distribution_sources == {"FUGLE"}
        and data_qualities == {VALIDATED_QUALITY}
        and source_qualities == {VALIDATED_QUALITY}
        and bool(snapshot_quality.get("ready"))
        and profile_validated
    )
    official_trusted = _official_history_is_trusted(history)
    volume_matches = volume_diff_pct is not None and volume_diff_pct <= 5.0
    profile_reconciliation = assess_persisted_price_volume_reconciliation(
        profile_row,
        official_volume_shares=official_volume_shares,
        captured_volume_shares=captured_volume_shares,
    )
    official_scope_compatible = bool(volume_matches or profile_reconciliation.get("ready"))
    decision_ready = bool(
        validated
        and official_trusted
        and official_scope_compatible
        and profile
        and freshness.get("ready")
    )

    if freshness.get("status") == "stale":
        status = "stale"
        reason = str(freshness.get("reason") or "official daily data is stale")
    elif distribution_sources != {"FUGLE"}:
        status = "source_mismatch"
        reason = "price-volume rows must come from one validated same-day capture"
    elif decision_ready:
        status = "ok"
        reason = (
            "price-volume distribution passed official close-volume reconciliation with persisted delayed full-session evidence"
            if snapshot_quality.get("capture_mode") == "delayed_full_session"
            else "same-day regular-session price-volume distribution passed persisted trade and official-scope reconciliation"
        )
    elif data_qualities & {"INTRADAY_SNAPSHOT", "PARTIAL"}:
        status = "source_delayed"
        reason = "price-volume snapshot is awaiting official close-volume reconciliation"
    elif volume_diff_pct is not None and volume_diff_pct > 5.0 and not official_scope_compatible:
        status = "volume_mismatch"
        reason = "captured price-volume total does not match official close volume"
    else:
        status = "unverified"
        reason = "price-volume rows do not carry validated same-day reconciliation evidence"

    direction_available = bool(levels and direction_level_count == len(levels))
    dominance, dominance_zh = (
        _dominance(total_inner, total_outer)
        if direction_available
        else ("unavailable", "內外盤資料不完整")
    )
    support_pressure: dict[str, Any]
    if decision_ready:
        support_pressure = analyze_volume_structure(profile, _number(history.get("close")))

        def usable_zone(value: Any) -> bool:
            zone = value if isinstance(value, dict) else {}
            return any(
                _number(zone.get(key)) is not None
                for key in ("price", "zone_low", "zone_high")
            )

        structure_ok = support_pressure.get("source_status") == "ok"
        support_ready = usable_zone(support_pressure.get("support_zone"))
        pressure_ready = usable_zone(support_pressure.get("pressure_zone"))
        support_pressure["available"] = bool(
            structure_ok and support_ready and pressure_ready
        )
        support_pressure["support_available"] = bool(structure_ok and support_ready)
        support_pressure["pressure_available"] = bool(structure_ok and pressure_ready)
        support_pressure["status"] = (
            "ok"
            if support_pressure["available"]
            else "partial_structure"
            if structure_ok and (support_ready or pressure_ready)
            else "insufficient_structure"
        )
    else:
        support_pressure = {
            "available": False,
            "status": status,
            "reason": "support and pressure are hidden until same-day official volume reconciliation passes",
            "support_zone": None,
            "pressure_zone": None,
            "poc_price": None,
            "poc_volume": None,
        }

    referee = _shared_referee_payload(
        snapshot=snapshot,
        history=history,
        technical=technical,
        freshness=freshness,
        recommendation_safety=recommendation_safety,
    )
    recent = list(snapshot.get("recent_referee_context") or [])
    advisory = build_conditional_advisory(
        referee=referee,
        current_price=decision_price,
        technical=technical,
        previous_macd_osc=(recent[1].get("macd_osc") if len(recent) > 1 else None),
        institutional_context=institutional_context,
        global_market_context=global_market_context,
        taifex_night_context=taifex_night_context,
        official_event_context=official_event_context,
        external_event_context=external_event_context,
        recent_context=recent,
        price_basis=decision_price_basis,
        recommendation_safety=recommendation_safety,
        decision_audit={**decision_audit, "price_basis": decision_price_basis},
    )
    advisory["price_basis"] = decision_price_basis
    advisory["price_as_of"] = intraday_quote.get("as_of") or selected_date

    return {
        "ok": decision_ready,
        "status": status,
        "reason": reason,
        "code": normalized_code,
        "trade_date": selected_date,
        **mode_metadata,
        "freshness": freshness,
        "close": _number(history.get("close")),
        "ohlcv": ohlcv,
        "intraday_quote": intraday_quote,
        "stock": stock,
        "trading_state": trading_state,
        "technical": technical,
        "valuation": valuation,
        "institutional_context": institutional_context,
        "global_market_context": global_market_context,
        "taifex_night_context": taifex_night_context,
        "official_event_context": official_event_context,
        "external_event_context": external_event_context,
        "news_radar_context": news_radar_context,
        "recommendation_safety": recommendation_safety,
        "decision_audit": {**decision_audit, "price_basis": decision_price_basis},
        "advisory": advisory,
        "official_volume_shares": official_volume_shares,
        "captured_volume_lots": total_lots,
        "captured_volume_shares": captured_volume_shares,
        # Additive display metadata only; no consumer may use this as an
        # analysis/referee gate. Existing decision_ready remains unchanged.
        "scoped_price_volume_display": assess_scoped_price_volume_display(
            snapshot, trade_date=selected_date, fresh=bool(freshness.get("ready")),
            official_trusted=official_trusted, valid_level_count=len(levels),
            reconciliation=profile_reconciliation,
        ),
        "volume_diff_pct": round(volume_diff_pct, 4) if volume_diff_pct is not None else None,
        "snapshot_time": next((row.get("snapshot_time") for row in rows if row.get("snapshot_time")), None),
        "fetched_at": next((_iso_timestamp(row.get("fetched_at")) for row in rows if row.get("fetched_at")), None),
        "data_quality": {
            "decision_ready": decision_ready,
            "reason_code": status,
            "component_status": status,
            "component_reason": reason,
            "official_close_trusted": official_trusted,
            "distribution_validated": validated,
            "session_complete": bool(snapshot_quality.get("ready")),
            "snapshot_quality": snapshot_quality,
            "capture_mode": snapshot_quality.get("capture_mode"),
            "profile_validated": profile_validated,
            "volume_matches_official": volume_matches,
            "official_scope_compatible": official_scope_compatible,
            "profile_reconciliation": profile_reconciliation,
            "distribution_sources": sorted(distribution_sources),
            "distribution_states": sorted(data_qualities),
        },
        "flow_summary": {
            "direction_available": direction_available,
            "direction_level_count": direction_level_count,
            "price_level_count": len(levels),
            "inner_lots": total_inner if direction_available else None,
            "outer_lots": total_outer if direction_available else None,
            "neutral_lots": total_neutral,
            "net_active_lots": total_outer - total_inner if direction_available else None,
            "dominance": dominance,
            "dominance_zh": dominance_zh,
            "is_institutional_net_buy_sell": False,
        },
        "semantics": {
            "inner_lots": "來源計算的內盤量（volumeAtBid）",
            "outer_lots": "來源計算的外盤量（volumeAtAsk）",
            "neutral_lots": "總量扣除內外盤後的未分類量，包含來源未歸類的開盤撮合量",
            "net_active_lots": "外盤減內盤，只代表當日主動成交力道，不是法人買超／賣超",
        },
        "price_levels": levels[:level_limit] if include_levels else [],
        "price_level_count": len(levels),
        "price_levels_truncated": bool(include_levels and len(levels) > level_limit),
        "support_pressure": support_pressure,
        "referee": referee,
        "multi_day_score": {
            "available": False,
            "referee_eligible": False,
            "status": "not_enabled_pending_trading_date_coverage",
            "coverage_days": None,
            "required_days": None,
            "minimum_coverage_days": None,
            "grade": None,
            "total_score": None,
        },
    }


def _canonical_analysis_status(payload: dict[str, Any]) -> dict[str, Any]:
    referee = dict(payload.get("referee") or {})
    reason_code = str(referee.get("reason_code") or "") or None
    terminal_block_codes = {
        "recommendation_safety_hard_block",
        "no_trade",
        "trading_halt",
        "no_regular_lot_ohlcv",
        "no_ohlcv_residual_activity",
        "inactive_official_universe",
    }
    decision_ready = bool(referee.get("decision_ready"))
    terminal_block = bool(reason_code in terminal_block_codes)
    status = "ready" if decision_ready else "blocked" if terminal_block else "insufficient_data"
    return {
        "status": status,
        "complete": bool(decision_ready or terminal_block),
        "decision_ready": decision_ready,
        "main_status": referee.get("main_status"),
        "main_reasons": list(referee.get("main_reasons") or []),
        "reason_code": reason_code,
        "source": referee.get("source"),
        "version": referee.get("version"),
    }


def build_canonical_close_batch_snapshot(
    code: str,
    *,
    trade_date: str | None = None,
    include_levels: bool = True,
    level_limit: int = 100,
    analysis_mode: str = "close_batch",
    allow_live_quote_fetch: bool = False,
    analysis_cutoff: str | None = None,
) -> dict[str, Any]:
    """Build the only publishable stock-analysis snapshot for Web, Bot and LINE."""

    arguments = {
        "trade_date": trade_date,
        "include_levels": include_levels,
        "level_limit": level_limit,
        "analysis_mode": analysis_mode,
        "allow_live_quote_fetch": allow_live_quote_fetch,
    }
    if analysis_cutoff is not None:
        arguments["analysis_cutoff"] = analysis_cutoff
    payload = _build_canonical_close_batch_snapshot_impl(code, **arguments)
    data_quality = dict(payload.get("data_quality") or {})
    analysis_status = _canonical_analysis_status(payload)
    referee_present = isinstance(payload.get("referee"), dict) and bool(payload.get("referee"))
    main_reasons = [
        str(item).strip()
        for item in list(analysis_status.get("main_reasons") or [])
        if str(item).strip()
    ]
    return {
        **payload,
        "ok": bool(analysis_status.get("complete")) if referee_present else bool(payload.get("ok")),
        "status": analysis_status.get("status") if referee_present else payload.get("status"),
        "reason": "；".join(main_reasons) if referee_present and main_reasons else payload.get("reason"),
        "analysis_contract_version": CANONICAL_CLOSE_BATCH_CONTRACT_VERSION,
        "analysis_status": analysis_status,
        "microstructure_status": {
            "status": payload.get("status"),
            "reason": payload.get("reason"),
            "decision_ready": bool(data_quality.get("decision_ready")),
        },
    }


def build_bot_daily_market_data(
    code: str,
    *,
    trade_date: str | None = None,
    include_levels: bool = True,
    level_limit: int = 100,
    analysis_mode: str = "close_batch",
    allow_live_quote_fetch: bool = False,
) -> dict[str, Any]:
    """Deprecated component-level builder kept only for non-public tests/tools.

    Production Web, Bot API, screening and LINE consumers must call
    ``build_canonical_close_batch_snapshot`` so top-level status always means
    the shared referee result rather than the price-volume component state.
    """

    return _build_canonical_close_batch_snapshot_impl(
        code,
        trade_date=trade_date,
        include_levels=include_levels,
        level_limit=level_limit,
        analysis_mode=analysis_mode,
        allow_live_quote_fetch=allow_live_quote_fetch,
    )


def build_bot_daily_history(
    code: str,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 60,
    offset: int = 0,
) -> dict[str, Any]:
    normalized_code = normalize_bot_stock_code(code)
    if not normalized_code:
        return {"ok": False, "status": "invalid_request", "reason": "stock code must be exactly four digits"}
    normalized_from = normalize_bot_trade_date(date_from)
    normalized_to = normalize_bot_trade_date(date_to)
    if date_from and not normalized_from:
        return {"ok": False, "status": "invalid_request", "reason": "date_from must be YYYY-MM-DD"}
    if date_to and not normalized_to:
        return {"ok": False, "status": "invalid_request", "reason": "date_to must be YYYY-MM-DD"}
    if normalized_from and normalized_to and normalized_from > normalized_to:
        return {"ok": False, "status": "invalid_request", "reason": "date_from must not be after date_to"}
    limit = max(1, min(int(limit), 200))
    offset = max(0, min(int(offset), 1_000_000))
    try:
        page = read_daily_stock_history(
            normalized_code,
            date_from=normalized_from,
            date_to=normalized_to,
            limit=limit,
            offset=offset,
        )
    except Exception:
        return {
            "ok": False,
            "status": "unavailable",
            "reason": "market-data database is unavailable",
            "code": normalized_code,
            "items": [],
        }
    items: list[dict[str, Any]] = []
    for row in page.get("rows") or []:
        items.append(
            {
                "trade_date": row.get("trade_date"),
                "ohlcv": {
                    "open": _number(row.get("open")),
                    "high": _number(row.get("high")),
                    "low": _number(row.get("low")),
                    "close": _number(row.get("close")),
                    "volume_shares": _number(row.get("volume")),
                    "amount": _number(row.get("amount")),
                    "source": row.get("history_source"),
                    "source_quality": row.get("history_source_quality"),
                },
                "technical": _technical_payload(row),
                "valuation": _valuation_payload(row),
            }
        )
    total_rows = int(page.get("total_rows") or 0)
    next_offset = offset + len(items) if offset + len(items) < total_rows else None
    return {
        "ok": bool(items),
        "status": "ok" if items else "unavailable",
        "reason": "date-sorted daily records" if items else "no daily OHLCV rows match the request",
        "code": normalized_code,
        "stock": page.get("stock"),
        "date_order": "descending",
        "date_from": normalized_from,
        "date_to": normalized_to,
        "limit": limit,
        "offset": offset,
        "next_offset": next_offset,
        "total_rows": total_rows,
        "items": items,
    }


def build_bot_intraday_trade_page(
    code: str,
    *,
    trade_date: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    normalized_code = normalize_bot_stock_code(code)
    if not normalized_code:
        return {"ok": False, "status": "invalid_request", "reason": "stock code must be exactly four digits"}
    normalized_date = normalize_bot_trade_date(trade_date)
    if trade_date and not normalized_date:
        return {"ok": False, "status": "invalid_request", "reason": "trade_date must be YYYY-MM-DD"}
    limit = max(1, min(int(limit), 200))
    offset = max(0, min(int(offset), 1_000_000))
    try:
        page = read_intraday_trade_page(
            normalized_code,
            normalized_date,
            limit=limit,
            offset=offset,
        )
    except Exception:
        return {
            "ok": False,
            "status": "unavailable",
            "reason": "market-data database is unavailable",
            "code": normalized_code,
            "trade_date": normalized_date,
            "items": [],
        }
    selected_date = str(page.get("trade_date") or "") or None
    items: list[dict[str, Any]] = []
    for row in page.get("rows") or []:
        side = str(row.get("side_inferred") or "UNKNOWN").upper()
        items.append(
            {
                "trade_time": _normalized_trade_time(row.get("trade_time"), str(selected_date or "")),
                "price": _number(row.get("price")),
                "size_lots": _integer(row.get("size")),
                "cumulative_volume_lots": _integer(row.get("volume")) or None,
                "bid": _number(row.get("bid")),
                "ask": _number(row.get("ask")),
                "serial": str(row.get("serial") or ""),
                "inferred_side": side,
                "inferred_side_zh": row.get("side_label_zh") or "無法判斷",
                "side_method": row.get("side_method") or "UNKNOWN",
                "side_confidence": row.get("side_confidence") or "UNKNOWN",
            }
        )
    total_rows = int(page.get("total_rows") or 0)
    next_offset = offset + len(items) if offset + len(items) < total_rows else None
    latest_history_date = str((page.get("latest_history") or {}).get("date") or "") or None
    capture = page.get("capture") or {}
    capture_complete = bool(
        int(capture.get("capture_complete") or 0) == 1
        and str(capture.get("data_quality") or "").upper() == "SESSION_COMPLETE"
        and int(capture.get("stored_row_count") or 0) == total_rows
        and total_rows > 0
    )
    return {
        "ok": bool(items),
        "status": "ok" if items and capture_complete else ("unverified" if items else "unavailable"),
        "reason": (
            "full-day paginated trade capture is persisted and session-complete"
            if items and capture_complete
            else "captured trade rows are queryable, but full-day pagination completeness was not proven"
            if items
            else "no captured trade rows are available for the requested date"
        ),
        "code": normalized_code,
        "trade_date": selected_date,
        "latest_official_close_date": latest_history_date,
        "is_latest_official_date": bool(selected_date and selected_date == latest_history_date),
        "capture_completeness": "complete" if capture_complete else "unverified",
        "capture_metadata": {
            "snapshot_time": capture.get("snapshot_time"),
            "page_count": _integer(capture.get("page_count")),
            "provider_row_count": _integer(capture.get("provider_row_count")),
            "stored_row_count": _integer(capture.get("stored_row_count")),
            "latest_trade_time": capture.get("latest_trade_time"),
            "captured_volume_lots": _integer(capture.get("captured_volume_lots")) or None,
        },
        "side_is_estimated": True,
        "side_semantics": "逐筆方向先比較成交價與 bid/ask，缺值才使用 tick rule；不是交易所原始買賣別",
        "limit": limit,
        "offset": offset,
        "next_offset": next_offset,
        "total_rows": total_rows,
        "items": items,
    }
