from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

from analysis.strategy_backtest import (
    HORIZONS,
    build_strategy_backtest_report,
    evaluate_low_zone_rules,
    prepare_point_in_time_frame,
)
from repository.strategy_backtest_repository import read_strategy_backtest_inputs


TPE = ZoneInfo("Asia/Taipei")


def _json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def run_strategy_backtest(
    *,
    database_path: Path | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    codes: list[str] | None = None,
) -> dict[str, Any]:
    inputs = read_strategy_backtest_inputs(
        database_path=database_path,
        date_to=date_to,
        codes=codes,
    )
    raw_rows = inputs["rows"]
    actions = inputs["corporate_actions"]
    frame = prepare_point_in_time_frame(
        raw_rows,
        actions,
        date_from=date_from,
        date_to=date_to,
    )
    low_zone = evaluate_low_zone_rules(frame)
    report = build_strategy_backtest_report(frame, low_zone)
    report["generated_at"] = datetime.now(TPE).isoformat(timespec="seconds")
    report["input_data"] = {
        "database_path_exposed": False,
        "raw_rows": int(len(raw_rows)),
        "raw_stocks": int(raw_rows["code"].nunique()) if not raw_rows.empty else 0,
        "raw_date_from": str(raw_rows["trade_date"].min()) if not raw_rows.empty else None,
        "raw_date_to": str(raw_rows["trade_date"].max()) if not raw_rows.empty else None,
        "known_corporate_actions": int(len(actions)),
        "requested_date_from": date_from,
        "requested_date_to": date_to,
        "requested_codes": len(codes or []),
    }
    report["production_logic_changed"] = False
    return _json_value(report)


def _fmt(value: Any, digits: int = 2) -> str:
    if value is None:
        return "—"
    if isinstance(value, int):
        return f"{value:,}"
    try:
        return f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def _fmt_counts(counts: dict[str, Any]) -> str:
    return "、".join(f"{key}={value}筆" for key, value in counts.items()) or "無"


def render_strategy_backtest_markdown(report: dict[str, Any]) -> str:
    evidence = report.get("evidence_assessment") or {}
    ranking_results = evidence.get("ranking_test_results") or []
    ranking_text = "；".join(
        f"{row.get('horizon_sessions')}日相關係數 {_fmt(row.get('spearman_like_correlation'), 3)}、Q5-Q1 {_fmt(row.get('top_minus_bottom_pct'))}%"
        for row in ranking_results
    ) or "無可用樣本"
    exact_counts = evidence.get("full_existing_rule_test_observations") or {}
    stricter_counts = evidence.get("stricter_rule_test_observations") or {}
    stricter_text = "；".join(
        f"{name}（{_fmt_counts(counts)}）" for name, counts in stricter_counts.items()
    ) or "無"
    broad_dates = int(evidence.get("broad_market_trade_dates") or 0)
    broad_required = int(evidence.get("minimum_required_broad_market_dates") or 60)
    broad_summary = (
        f"全市場面板共有 {broad_dates} 個交易日，已通過最低 {broad_required} 日覆蓋門檻。"
        if evidence.get("broad_market_coverage_status") == "sufficient"
        else f"全市場面板只有 {broad_dates} 個交易日，未達最低 {broad_required} 日覆蓋門檻。"
    )
    minimum_observations = int(evidence.get("minimum_required_test_observations_per_horizon") or 30)
    exact_summary = (
        f"已通過每個持有期至少 {minimum_observations} 筆的最低研究門檻。"
        if evidence.get("full_existing_rule_status") == "sample_sufficient"
        else f"尚未通過每個持有期至少 {minimum_observations} 筆的最低研究門檻。"
    )
    lines = [
        "# 策略 Point-in-Time 基線回測",
        "",
        f"- 回測版本：`{report.get('version')}`",
        f"- 產生時間：{report.get('generated_at')}",
        "- 訊號：T 日完整收盤；成交假設：下一交易日開盤；退出：第 5／10／20 個交易日收盤。",
        "- 本報告只驗證規則，不會自動修改正式裁判、RSI、MACD 或篩選係數。",
        "",
        "## 裁決摘要",
        "",
        "- 正式權重決定：**維持不變**。現有預排序權重尚未通過穩定的樣本外單調性驗證。",
        f"- 樣本外排序結果：{ranking_text}。",
        f"- 完整既有進場規則樣本：{_fmt_counts(exact_counts)}；{exact_summary}",
        f"- 更嚴格 RSI／MACD 變體樣本：{stricter_text}；不得因少數高報酬直接升級規則。",
        f"- {broad_summary}",
        "- 結論：本輪完成的是可重跑、無前視偏誤的驗證基線；證據支持繼續蒐集資料，不支持現在改權重。",
        "",
        "## 資料覆蓋",
        "",
        "| 面板 | 觀測值 | 交易日 | 股票數 | 日期範圍 | 每日股票中位數 |",
        "|---|---:|---:|---:|---|---:|",
    ]
    for row in report.get("coverage") or []:
        lines.append(
            f"| {row.get('panel')} | {_fmt(row.get('observations'), 0)} | "
            f"{_fmt(row.get('trade_dates'), 0)} | {_fmt(row.get('stocks'), 0)} | "
            f"{row.get('date_from') or '—'}～{row.get('date_to') or '—'} | "
            f"{_fmt(row.get('median_stocks_per_date'), 1)} |"
        )

    lines.extend(
        [
            "",
            "## 預排序分數單調性",
            "",
            "Q1 為最低分、Q5 為最高分。相關係數愈接近 1，代表分數與後續報酬排序愈一致。",
            "",
            "| 面板 | 樣本 | 持有期 | Q1～Q5 平均報酬% | 相關係數 | Q5-Q1% |",
            "|---|---|---:|---|---:|---:|",
        ]
    )
    monotonicity = (report.get("ranking_validation") or {}).get("monotonicity") or []
    for row in monotonicity:
        if row.get("sample") not in {"full", "test"}:
            continue
        means = "/".join(_fmt(value) for value in row.get("quintile_mean_returns_pct") or [])
        lines.append(
            f"| {row.get('panel')} | {row.get('sample')} | {row.get('horizon_sessions')} | "
            f"{means} | {_fmt(row.get('spearman_like_correlation'), 3)} | "
            f"{_fmt(row.get('top_minus_bottom_pct'))} |"
        )

    lines.extend(
        [
            "",
            "## 低檔止跌條件比較（每檔訊號間隔至少 20 個交易日）",
            "",
            "| 條件 | 面板 | 樣本 | 持有期 | 次數 | 平均報酬% | 勝率% | 超額報酬% | MAE% | MFE% | P5報酬% | 診斷回撤% |",
            "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    cohort_metrics = (report.get("low_zone_validation") or {}).get("cohort_metrics") or []
    for row in cohort_metrics:
        if row.get("sample") not in {"full", "test"}:
            continue
        lines.append(
            f"| {row.get('cohort')} | {row.get('panel')} | {row.get('sample')} | "
            f"{row.get('horizon_sessions')} | {_fmt(row.get('observations'), 0)} | "
            f"{_fmt(row.get('mean_return_pct'))} | {_fmt(row.get('win_rate_pct'))} | "
            f"{_fmt(row.get('mean_excess_return_pct'))} | {_fmt(row.get('mean_mae_pct'))} | "
            f"{_fmt(row.get('mean_mfe_pct'))} | {_fmt(row.get('p05_return_pct'))} | "
            f"{_fmt(row.get('diagnostic_max_drawdown_pct'))} |"
        )

    lines.extend(
        [
            "",
            "## 市場狀態切分（既有完整規則）",
            "",
            "僅列完整既有規則；樣本仍少，不能據此調參。",
            "",
            "| 市場狀態 | 持有期 | 次數 | 平均報酬% | 勝率% | 超額報酬% | MAE% |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    regime_metrics = (report.get("low_zone_validation") or {}).get("market_regime_metrics") or []
    for row in regime_metrics:
        if row.get("cohort") != "full_existing_rule":
            continue
        lines.append(
            f"| {row.get('market_regime')} | {row.get('horizon_sessions')} | "
            f"{_fmt(row.get('observations'), 0)} | {_fmt(row.get('mean_return_pct'))} | "
            f"{_fmt(row.get('win_rate_pct'))} | {_fmt(row.get('mean_excess_return_pct'))} | "
            f"{_fmt(row.get('mean_mae_pct'))} |"
        )

    lines.extend(["", "## 已知限制", ""])
    for item in report.get("limitations") or []:
        lines.append(f"- {item}")
    lines.extend(
        [
            "",
            "診斷回撤是把同一訊號日的報酬等權平均後依日期複利；因持有期重疊，不等同可實現的投資組合最大回撤。",
            "",
            "僅供量化資料整理與研究，不構成投資建議；未計入交易成本、稅負、滑價與實際成交限制。",
            "",
        ]
    )
    return "\n".join(lines)
