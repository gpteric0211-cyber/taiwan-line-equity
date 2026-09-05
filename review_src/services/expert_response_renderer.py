from __future__ import annotations

from typing import Any, Mapping


RESPONSE_STYLE_VERSION = "ResponseStyleV1"
EXPERT_RENDERER_VERSION = "ExpertResponseRendererV1"
DISCLAIMER = "以上為資料導向的研究說明，不保證報酬，亦非個別投資建議。"


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _entity_label(entity: Mapping[str, Any], snapshot: Mapping[str, Any]) -> str:
    stock = snapshot.get("stock") or {}
    code = str(entity.get("code") or snapshot.get("code") or "")
    name = str(stock.get("name") or stock.get("stock_name") or entity.get("name") or code)
    return f"{name}（{code}）"


def _technical_structure(snapshot: Mapping[str, Any]) -> str | None:
    technical = snapshot.get("technical") or {}
    if not technical.get("decision_ready"):
        return None
    macd = technical.get("macd") or {}
    dif = _number(macd.get("dif"))
    signal = _number(macd.get("signal"))
    oscillator = _number(macd.get("oscillator"))
    if dif is None or signal is None or oscillator is None:
        return "技術資料已通過品質門檻，但目前只保留結構判讀，不逐項朗讀指標"
    axis = "零軸上" if dif >= 0 else "零軸下"
    cross = "DIF 位於 signal 之上" if dif >= signal else "DIF 位於 signal 之下"
    histogram = "柱狀體偏正" if oscillator >= 0 else "柱狀體偏負"
    return f"MACD 位於{axis}，{cross}，{histogram}；需配合量能確認是否延續"


def _single_entity_blocks(
    entity: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    scan: Mapping[str, Any],
    *,
    evidence_ids: list[str],
    projection: Mapping[str, Any],
) -> list[dict[str, Any]]:
    label = _entity_label(entity, snapshot)
    trade_date = str(snapshot.get("trade_date") or "日期未取得")
    referee = snapshot.get("referee") or {}
    status = snapshot.get("analysis_status") or {}
    if referee.get("decision_ready"):
        conclusion = str(referee.get("main_status") or "主結論已形成")
    elif referee.get("partial_analysis_available"):
        conclusion = "資料部分可用，但目前不形成主結論"
    elif status.get("status") == "blocked":
        conclusion = "依官方狀態不進行判斷"
    else:
        conclusion = "資料不足，暫不形成主結論"
    referenced = [
        str(item.get("code") or "")
        for item in list(projection.get("referenced_stocks") or [])
        if isinstance(item, Mapping)
    ]
    transition = "接著看" if any(code and code != entity.get("code") for code in referenced) else ""
    lead = f"{transition}{label}，資料截至 {trade_date} 收盤：{conclusion}。"
    blocks: list[dict[str, Any]] = [
        {
            "block_type": "conclusion",
            "text": lead,
            "evidence_ids": evidence_ids,
            "source_fields": ["analysis_status", "referee.main_status", "trade_date"],
        }
    ]
    scan_state = str(scan.get("scan_state") or "")
    if scan_state == "verified_material":
        blocks.insert(
            0,
            {
                "block_type": "material_event",
                "text": "本次 cutoff 前已有通過驗證的重大事件；事件先使舊展望失效，再談新的方向條件。",
                "evidence_ids": list(scan.get("verified_material_event_ids") or []),
                "source_fields": ["event_scan.verified_material_event_ids"],
            },
        )
    elif scan_state == "scan_incomplete":
        blocks.append(
            {
                "block_type": "uncertainty",
                "text": "事件安全掃描仍有未完成範圍，因此不能提高信心，也不把未驗證消息當成方向。",
                "evidence_ids": [],
                "source_fields": ["event_scan.incomplete_scopes"],
            }
        )
    drivers = [
        str(reason).strip()
        for reason in list(referee.get("main_reasons") or [])
        if str(reason).strip()
    ]
    technical = _technical_structure(snapshot)
    if technical:
        drivers.append(technical)
    coverage = dict(referee.get("component_coverage") or {})
    if coverage and not all(coverage.values()):
        missing = "支撐" if not coverage.get("support") else "賣壓"
        drivers.append(f"{missing}區尚未可靠形成；保留另一側結構，但不據此補造主結論")
    if drivers:
        blocks.append(
            {
                "block_type": "drivers",
                "text": "真正重要的因素是：" + "；".join(drivers[:4]) + "。",
                "evidence_ids": evidence_ids,
                "source_fields": ["referee.main_reasons", "technical", "referee.component_coverage"],
            }
        )
    position = projection.get("position_state")
    if position == "持有":
        scenario = "若已持有，先觀察既有趨勢與量能是否維持；事件或價格結構失效時應重新評估，而非把目前判斷當保證。"
    elif position == "空手":
        scenario = "若目前空手，等待支撐、量能與事件風險同時改善後再重新評估，沒有確認前不因單一訊號追價。"
    elif position == "想加碼":
        scenario = "若想加碼，需等新確認條件出現；原支撐或事件前提失效時停止加碼評估。"
    else:
        scenario = "後續應以價量結構與新事件是否改變既有前提作為重新評估條件。"
    blocks.append(
        {
            "block_type": "conditional_scenario",
            "text": scenario,
            "evidence_ids": [],
            "source_fields": ["conversation_projection.position_state", "referee"],
        }
    )
    return blocks


def render_expert_response(
    *,
    entity_snapshots: list[tuple[Mapping[str, Any], Mapping[str, Any]]],
    scan: Mapping[str, Any],
    evidence_ids_by_entity: Mapping[str, list[str]],
    profile: str,
    conversation_projection: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    projection = dict(conversation_projection or {})
    if len(entity_snapshots) == 1:
        entity, snapshot = entity_snapshots[0]
        blocks = _single_entity_blocks(
            entity,
            snapshot,
            scan,
            evidence_ids=list(evidence_ids_by_entity.get(str(entity.get("code") or "")) or []),
            projection=projection,
        )
    else:
        labels = [_entity_label(entity, snapshot) for entity, snapshot in entity_snapshots]
        dates = [str(snapshot.get("trade_date") or "") for _entity, snapshot in entity_snapshots]
        comparison_lines = []
        comparison_evidence: list[str] = []
        for entity, snapshot in entity_snapshots:
            code = str(entity.get("code") or "")
            referee = snapshot.get("referee") or {}
            conclusion = (
                str(referee.get("main_status") or "主結論未形成")
                if referee.get("decision_ready")
                else "資料部分可用、不形成主結論"
                if referee.get("partial_analysis_available")
                else "資料不足、不形成主結論"
            )
            comparison_lines.append(f"{_entity_label(entity, snapshot)}：{conclusion}")
            comparison_evidence.extend(evidence_ids_by_entity.get(code) or [])
        blocks = [
            {
                "block_type": "conclusion",
                "text": f"{'與'.join(labels)}截至 {max(dates) if dates else '日期未取得'} 收盤的比較，必須分別沿用兩檔各自的裁判結論。",
                "evidence_ids": comparison_evidence,
                "source_fields": ["entity_analyses.referee", "entity_analyses.trade_date"],
            },
            {
                "block_type": "comparison",
                "text": "目前差異是：" + "；".join(comparison_lines) + "。",
                "evidence_ids": comparison_evidence,
                "source_fields": ["entity_analyses.referee.main_status"],
            },
        ]
        if scan.get("scan_state") == "scan_incomplete":
            blocks.append(
                {
                    "block_type": "uncertainty",
                    "text": "至少一個事件研究範圍尚未完整，不能因缺口而把兩檔硬排出高低。",
                    "evidence_ids": [],
                    "source_fields": ["event_scan.incomplete_scopes"],
                }
            )
        blocks.append(
            {
                "block_type": "conditional_scenario",
                "text": "比較結論只有在兩檔使用同一 cutoff、資料品質與事件範圍時才可延續；任一前提變動就應重做比較。",
                "evidence_ids": [],
                "source_fields": ["analysis_cutoff", "coverage"],
            }
        )
    visible_blocks = blocks if str(profile).lower() == "comprehensive" else blocks[:4]
    text = "\n\n".join(block["text"] for block in visible_blocks) + f"\n\n{DISCLAIMER}"
    return {
        "text": text,
        "explanation_blocks": blocks,
        "renderer_version": EXPERT_RENDERER_VERSION,
        "response_style_version": RESPONSE_STYLE_VERSION,
        "disclaimer_count": text.count("不保證報酬"),
    }
