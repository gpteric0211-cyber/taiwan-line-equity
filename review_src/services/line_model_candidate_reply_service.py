from __future__ import annotations

"""Render a human-reviewable LINE reply from validator-approved candidate output.

The renderer never reads the raw model text.  The top-level conclusion remains
the immutable backend referee; the model contributes only validator-rendered
explanation blocks.
"""

import hashlib
from typing import Any

from core.public_url import normalize_public_https_url


DISCLAIMER = "僅供資料整理，不構成投資建議。"
CANDIDATE_REPLY_RENDERER_VERSION = "LineCandidateReplyRendererV1"
_BLOCK_LABELS = {
    "fact": "資料基準",
    "inference": "交叉判讀",
    "scenario": "條件情境",
    "limitation": "限制與風險",
}
_VERIFICATION_LABELS = {
    "primary_verified": "一手來源已驗證",
    "secondary_corroborated": "次級來源已交叉確認",
    "unverified": "未驗證新聞線索",
    "discovered": "待查證新聞線索",
}


class CandidateReplyRenderError(ValueError):
    def __init__(self, message: str, *, reason_code: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


def render_validated_candidate_reply_preview(
    result: dict[str, Any],
    *,
    stock_code: str,
    stock_name: str,
    maximum_characters: int = 4_500,
) -> dict[str, Any]:
    """Build a candidate preview without authorizing production replacement."""

    if str(result.get("validator_result") or "") != "pass":
        raise CandidateReplyRenderError(
            "candidate output did not pass validation",
            reason_code="candidate_validator_not_passed",
        )
    packet = result.get("compacted_packet")
    if not isinstance(packet, dict):
        raise CandidateReplyRenderError(
            "candidate packet is unavailable",
            reason_code="candidate_packet_missing",
        )
    referee = packet.get("referee")
    if not isinstance(referee, dict) or referee.get("immutable") is not True:
        raise CandidateReplyRenderError(
            "immutable referee is unavailable",
            reason_code="immutable_referee_missing",
        )
    main_status = str(referee.get("main_status") or "").strip()
    if not main_status:
        raise CandidateReplyRenderError(
            "referee main status is unavailable",
            reason_code="referee_main_status_missing",
        )
    rendered_blocks = result.get("rendered_blocks")
    explanation_blocks = result.get("explanation_blocks")
    if not isinstance(rendered_blocks, list) or not rendered_blocks:
        raise CandidateReplyRenderError(
            "validator-rendered explanation is unavailable",
            reason_code="rendered_blocks_missing",
        )
    if not isinstance(explanation_blocks, list) or len(explanation_blocks) != len(rendered_blocks):
        raise CandidateReplyRenderError(
            "rendered explanation block mapping is inconsistent",
            reason_code="rendered_block_mapping_invalid",
        )

    display_name = str(stock_name or stock_code or "標的").strip()
    display_code = str(stock_code or "").strip()
    title = f"{display_name}（{display_code}）候選綜合分析" if display_code else f"{display_name}候選綜合分析"
    lines = [title, f"主結論：{main_status}"]
    reasons = [str(item).strip() for item in referee.get("reasons") or [] if str(item).strip()]
    if reasons:
        lines.append("裁判依據：" + "；".join(reasons[:3]))
    analysis_cutoff = str((packet.get("request") or {}).get("analysis_cutoff") or "").strip()
    if analysis_cutoff:
        lines.append(f"資料日期：{analysis_cutoff}")
    lines.append("")
    for index, (block, rendered) in enumerate(zip(explanation_blocks, rendered_blocks), start=1):
        if not isinstance(block, dict) or not isinstance(rendered, str) or not rendered.strip():
            raise CandidateReplyRenderError(
                "validator-rendered explanation contains an invalid block",
                reason_code="rendered_block_mapping_invalid",
            )
        label = _BLOCK_LABELS.get(str(block.get("block_type") or ""), "分析")
        lines.append(f"{label}：{rendered.strip()}")
        if index < len(rendered_blocks):
            lines.append("")
    events = {
        str(item.get("event_id") or ""): item
        for item in packet.get("events") or []
        if isinstance(item, dict) and item.get("event_id")
    }
    used_event_ids = [str(item) for item in result.get("used_event_ids") or []]
    citation_ids: list[str] = []
    citation_lines: list[str] = []
    for event_id in used_event_ids:
        event = events.get(event_id)
        if not event:
            raise CandidateReplyRenderError(
                "candidate citation event is unavailable",
                reason_code="candidate_citation_event_missing",
            )
        url = normalize_public_https_url(event.get("source_url"))
        display_allowed = event.get("allow_display") is True
        citation_required = event.get("citation_required") is True
        if not display_allowed or url is None:
            if citation_required:
                raise CandidateReplyRenderError(
                    "required candidate citation cannot be displayed",
                    reason_code="candidate_citation_not_renderable",
                )
            continue
        title_text = str(event.get("title") or "").strip()
        publisher = str(event.get("publisher") or "").strip()
        if not title_text or not publisher:
            raise CandidateReplyRenderError(
                "candidate citation attribution is incomplete",
                reason_code="candidate_citation_attribution_missing",
            )
        label = _VERIFICATION_LABELS.get(
            str(event.get("verification_state") or ""),
            "事件來源",
        )
        citation_ids.append(event_id)
        citation_lines.extend(
            (
                f"[{len(citation_ids)}] {label}｜{publisher}｜{title_text}",
                url,
            )
        )
        if len(citation_ids) >= 4:
            break
    if citation_lines:
        lines.extend(("", "新聞與事件來源：", *citation_lines))
    lines.extend(("", DISCLAIMER))
    text = "\n".join(lines)
    if len(text) > max(500, int(maximum_characters)):
        raise CandidateReplyRenderError(
            "candidate preview exceeds the bounded LINE text size",
            reason_code="candidate_preview_too_long",
        )
    payload = text.encode("utf-8")
    return {
        "text": text,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "characters": len(text),
        "utf8_bytes": len(payload),
        "main_status": main_status,
        "analysis_cutoff": analysis_cutoff or None,
        "block_count": len(rendered_blocks),
        "citation_count": len(citation_ids),
        "citation_event_ids": citation_ids,
        "citation_source": "backend_model_fact_packet_events_only",
        "referee_source": "immutable_model_fact_packet_v2",
        "explanation_source": "validator_rendered_blocks_only",
        "raw_model_output_used": False,
        "candidate_can_replace_reply": False,
    }
