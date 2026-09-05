from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from analysis.low_zone_entry import assess_low_zone_entry
from analysis.practical_status import classify_practical_status_core
from analysis.screening_prefilter import SCREENING_PREFILTER_VERSION, screening_prefilter_score
from analysis.support_resistance import build_ohlcv_support_resistance_levels, cluster_levels


STRATEGY_BACKTEST_VERSION = "strategy-backtest-v1"
HORIZONS = (5, 10, 20)
MIN_NORMAL_TURNOVER_TWD = 100_000_000.0
MIN_PANEL_SIZE = 25
BROAD_PANEL_SIZE = 500
TRAIN_FRACTION = 0.60


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _percent(value: Any) -> float | None:
    number = _number(value)
    return round(number * 100, 4) if number is not None else None


def _signal_action_windows(
    frame: pd.DataFrame,
    actions: pd.DataFrame,
) -> pd.Series:
    result = pd.Series(False, index=frame.index, dtype="bool")
    if actions.empty:
        return result
    action_map = {
        str(code): set(pd.to_datetime(group["action_date"], errors="coerce").dropna())
        for code, group in actions.groupby("code", sort=False)
    }
    for code, group in frame.groupby("code", sort=False):
        dates = list(pd.to_datetime(group["trade_date"], errors="coerce"))
        code_actions = action_map.get(str(code), set())
        if not code_actions:
            continue
        indexes = list(group.index)
        for position, signal_date in enumerate(dates):
            if pd.isna(signal_date):
                continue
            past_start = dates[max(0, position - 5)]
            future_end = signal_date + pd.Timedelta(days=3)
            if any(past_start <= action_date <= future_end for action_date in code_actions):
                result.loc[indexes[position]] = True
    return result


def _attach_forward_outcomes(
    frame: pd.DataFrame,
    actions: pd.DataFrame,
) -> pd.DataFrame:
    result = frame.copy()
    for horizon in HORIZONS:
        for name in ("return", "mae", "mfe", "contaminated"):
            result[f"{name}_{horizon}d"] = np.nan if name != "contaminated" else False
    action_map = {
        str(code): set(pd.to_datetime(group["action_date"], errors="coerce").dropna())
        for code, group in actions.groupby("code", sort=False)
    }
    for code, group in result.groupby("code", sort=False):
        indexes = list(group.index)
        dates = list(pd.to_datetime(group["trade_date"], errors="coerce"))
        opens = pd.to_numeric(group["open"], errors="coerce").to_numpy(dtype="float64")
        highs = pd.to_numeric(group["high"], errors="coerce").to_numpy(dtype="float64")
        lows = pd.to_numeric(group["low"], errors="coerce").to_numpy(dtype="float64")
        closes = pd.to_numeric(group["close"], errors="coerce").to_numpy(dtype="float64")
        code_actions = action_map.get(str(code), set())
        candidate_positions = np.flatnonzero(group["score_ready"].to_numpy(dtype="bool"))
        for position in candidate_positions:
            if position + 1 >= len(group):
                continue
            entry = opens[position + 1]
            if not np.isfinite(entry) or entry <= 0:
                continue
            for horizon in HORIZONS:
                target = position + horizon
                if target >= len(group):
                    continue
                signal_date = dates[position]
                target_date = dates[target]
                future_closes = closes[position : target + 1]
                has_gap = bool(
                    len(future_closes) > 1
                    and np.any(
                        np.abs(future_closes[1:] / future_closes[:-1] - 1.0) > 0.20
                    )
                )
                has_action = any(
                    signal_date < action_date <= target_date for action_date in code_actions
                )
                contaminated = bool(has_gap or has_action)
                row_index = indexes[position]
                result.at[row_index, f"contaminated_{horizon}d"] = contaminated
                if contaminated:
                    continue
                exit_close = closes[target]
                path_highs = highs[position + 1 : target + 1]
                path_lows = lows[position + 1 : target + 1]
                if (
                    not np.isfinite(exit_close)
                    or not np.all(np.isfinite(path_highs))
                    or not np.all(np.isfinite(path_lows))
                ):
                    continue
                result.at[row_index, f"return_{horizon}d"] = exit_close / entry - 1.0
                result.at[row_index, f"mfe_{horizon}d"] = float(np.max(path_highs) / entry - 1.0)
                result.at[row_index, f"mae_{horizon}d"] = float(np.min(path_lows) / entry - 1.0)
    return result


def prepare_point_in_time_frame(
    raw_rows: pd.DataFrame,
    corporate_actions: pd.DataFrame,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
) -> pd.DataFrame:
    """Create features from T/T-1 data and labels from T+1 onward."""

    if raw_rows.empty:
        return raw_rows.copy()
    frame = raw_rows.copy().sort_values(["code", "trade_date"], kind="stable").reset_index(drop=True)
    numeric_columns = (
        "open", "high", "low", "close", "volume", "amount", "input_row_count",
        "adjustment_event_count", "ma20", "ma60", "rsi14", "macd_osc", "atr14",
        "volume_ma20", "previous_10d_low", "technical_decision_ready",
    )
    for column in numeric_columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    volume_multiplier = np.where(
        frame["volume_unit"].fillna("shares").astype(str).str.lower().eq("lots"),
        1000.0,
        1.0,
    )
    frame["volume_shares"] = frame["volume"] * volume_multiplier
    frame["turnover_value"] = frame["amount"].where(
        frame["amount"].gt(0),
        frame["close"] * frame["volume_shares"],
    )
    frame["turnover_estimated"] = ~frame["amount"].gt(0)
    frame["history_count"] = frame.groupby("code", sort=False).cumcount() + 1
    frame["average_turnover_20d"] = frame.groupby("code", sort=False)[
        "turnover_value"
    ].transform(lambda values: values.rolling(20, min_periods=20).mean())
    technical_ready = (
        frame["technical_decision_ready"].fillna(0).astype(int).eq(1)
        & frame["technical_data_quality"].fillna("").astype(str).str.lower().eq("ok")
        & frame["input_end_date"].fillna("").astype(str).eq(frame["trade_date"].astype(str))
    )
    frame["technical_ready"] = technical_ready
    for column in ("rsi14", "macd_osc"):
        valid_values = frame[column].where(technical_ready)
        carried = valid_values.groupby(frame["code"], sort=False).ffill()
        frame[f"previous_{column}"] = carried.groupby(frame["code"], sort=False).shift(1)
        frame[f"two_days_ago_{column}"] = carried.groupby(frame["code"], sort=False).shift(2)
    frame["rsi_delta"] = frame["rsi14"] - frame["previous_rsi14"]
    frame["macd_improving"] = frame["macd_osc"] > frame["previous_macd_osc"]
    frame["macd_improving_two_days"] = (
        frame["macd_osc"].gt(frame["previous_macd_osc"])
        & frame["previous_macd_osc"].gt(frame["two_days_ago_macd_osc"])
    )
    close_location = np.where(
        frame["high"].gt(frame["low"]),
        (frame["close"] - frame["low"]) / (frame["high"] - frame["low"]),
        np.where(frame["close"].ge(frame["open"]), 1.0, 0.0),
    )
    previous_close = frame.groupby("code", sort=False)["close"].shift(1)
    frame["price_reversal"] = frame["close"].ge(previous_close) & (
        frame["close"].gt(frame["open"]) | pd.Series(close_location, index=frame.index).ge(0.60)
    )
    frame["confirmation_count"] = (
        frame["rsi_delta"].gt(0).astype(int)
        + frame["macd_improving"].astype(int)
        + frame["price_reversal"].astype(int)
    )
    frame["signal_action_window"] = _signal_action_windows(frame, corporate_actions)
    score_inputs_ready = (
        technical_ready
        & frame["history_count"].ge(60)
        & frame["close"].gt(0)
        & frame["volume_shares"].gt(0)
        & frame["ma20"].gt(0)
        & frame["ma60"].gt(0)
        & frame["rsi14"].between(0, 100)
        & frame["previous_rsi14"].notna()
        & frame["macd_osc"].notna()
        & frame["previous_macd_osc"].notna()
        & frame["average_turnover_20d"].ge(MIN_NORMAL_TURNOVER_TWD)
        & ~frame["signal_action_window"]
    )
    if date_from:
        score_inputs_ready &= frame["trade_date"].astype(str).ge(date_from)
    if date_to:
        score_inputs_ready &= frame["trade_date"].astype(str).le(date_to)
    frame["score_ready"] = score_inputs_ready
    frame["prefilter_score"] = np.nan
    score_records = frame.loc[score_inputs_ready].to_dict("records")
    frame.loc[score_inputs_ready, "prefilter_score"] = [
        screening_prefilter_score(row, "bottom") for row in score_records
    ]
    frame = _attach_forward_outcomes(frame, corporate_actions)

    daily_return = frame.groupby("code", sort=False)["close"].pct_change(fill_method=None)
    daily_market = (
        pd.DataFrame({"trade_date": frame["trade_date"], "return": daily_return})
        .groupby("trade_date", sort=True)["return"]
        .median()
    )
    trailing_market = (1.0 + daily_market).rolling(20, min_periods=10).apply(np.prod, raw=True) - 1.0
    regime = pd.Series("sideways", index=trailing_market.index, dtype="object")
    regime.loc[trailing_market.gt(0.03)] = "bull"
    regime.loc[trailing_market.lt(-0.03)] = "bear"
    regime.loc[trailing_market.isna()] = "unknown"
    frame["market_regime"] = frame["trade_date"].map(regime)

    eligible_counts = frame.loc[frame["score_ready"]].groupby("trade_date").size()
    panel_map = pd.Series("sparse", index=eligible_counts.index, dtype="object")
    panel_map.loc[eligible_counts.ge(MIN_PANEL_SIZE)] = "established_universe"
    panel_map.loc[eligible_counts.ge(BROAD_PANEL_SIZE)] = "broad_market"
    frame["panel"] = frame["trade_date"].map(panel_map).fillna("sparse")
    frame["sample_split"] = "unassigned"
    for panel in ("established_universe", "broad_market"):
        dates = sorted(frame.loc[frame["score_ready"] & frame["panel"].eq(panel), "trade_date"].unique())
        if not dates:
            continue
        split_index = max(1, min(len(dates) - 1, int(len(dates) * TRAIN_FRACTION))) if len(dates) > 1 else 1
        train_dates = set(dates[:split_index])
        mask = frame["panel"].eq(panel) & frame["score_ready"]
        frame.loc[mask, "sample_split"] = np.where(
            frame.loc[mask, "trade_date"].isin(train_dates), "train", "test"
        )

    frame["score_quintile"] = np.nan
    eligible = frame.loc[frame["score_ready"] & ~frame["panel"].eq("sparse")]
    for _date, group in eligible.groupby("trade_date", sort=False):
        ordered = group.sort_values(["prefilter_score", "code"], kind="stable")
        count = len(ordered)
        quintiles = np.floor(np.arange(count) * 5 / count).astype(int) + 1
        frame.loc[ordered.index, "score_quintile"] = quintiles
    for horizon in HORIZONS:
        benchmark = frame.loc[frame["score_ready"]].groupby("trade_date")[
            f"return_{horizon}d"
        ].median()
        frame[f"benchmark_return_{horizon}d"] = frame["trade_date"].map(benchmark)
        frame[f"excess_return_{horizon}d"] = (
            frame[f"return_{horizon}d"] - frame[f"benchmark_return_{horizon}d"]
        )
    return frame


def _metric_row(frame: pd.DataFrame, horizon: int) -> dict[str, Any]:
    returns = pd.to_numeric(frame[f"return_{horizon}d"], errors="coerce")
    valid = frame.loc[returns.notna()].copy()
    if valid.empty:
        return {
            "observations": 0,
            "trade_dates": 0,
            "stocks": 0,
            "mean_return_pct": None,
            "median_return_pct": None,
            "win_rate_pct": None,
            "mean_excess_return_pct": None,
            "excess_win_rate_pct": None,
            "mean_mae_pct": None,
            "mean_mfe_pct": None,
            "p05_return_pct": None,
            "worst_return_pct": None,
            "diagnostic_max_drawdown_pct": None,
        }
    outcome = valid[f"return_{horizon}d"].astype(float)
    excess = pd.to_numeric(valid[f"excess_return_{horizon}d"], errors="coerce")
    daily_batches = outcome.groupby(valid["trade_date"]).mean().sort_index()
    equity_curve = pd.concat(
        [pd.Series([1.0], index=["initial"]), (1.0 + daily_batches).cumprod()]
    )
    drawdown = equity_curve / equity_curve.cummax() - 1.0
    return {
        "observations": int(len(valid)),
        "trade_dates": int(valid["trade_date"].nunique()),
        "stocks": int(valid["code"].nunique()),
        "mean_return_pct": _percent(outcome.mean()),
        "median_return_pct": _percent(outcome.median()),
        "win_rate_pct": _percent((outcome > 0).mean()),
        "mean_excess_return_pct": _percent(excess.mean()) if excess.notna().any() else None,
        "excess_win_rate_pct": _percent((excess.dropna() > 0).mean()) if excess.notna().any() else None,
        "mean_mae_pct": _percent(valid[f"mae_{horizon}d"].astype(float).mean()),
        "mean_mfe_pct": _percent(valid[f"mfe_{horizon}d"].astype(float).mean()),
        "p05_return_pct": _percent(outcome.quantile(0.05)),
        "worst_return_pct": _percent(outcome.min()),
        "diagnostic_max_drawdown_pct": _percent(drawdown.min()),
    }


def _with_cooldown(frame: pd.DataFrame, sessions: int = 20) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    selected: list[int] = []
    for _code, group in frame.sort_values(["code", "trade_date"]).groupby("code", sort=False):
        last_position: int | None = None
        for index, row in group.iterrows():
            position = int(row["history_count"])
            if last_position is None or position - last_position >= sessions:
                selected.append(index)
                last_position = position
    return frame.loc[selected].copy()


def evaluate_low_zone_rules(frame: pd.DataFrame) -> pd.DataFrame:
    """Replay the existing referee and low-zone rule using data available at each T close."""

    records: list[dict[str, Any]] = []
    for code, group in frame.groupby("code", sort=False):
        group = group.reset_index(drop=False)
        candidate_positions = np.flatnonzero(
            (
                group["score_ready"]
                & group["rsi14"].between(30, 45)
                & group["history_count"].ge(61)
            ).to_numpy(dtype="bool")
        )
        for position in candidate_positions:
            if position < 3:
                continue
            current = group.iloc[position]
            history = group.iloc[max(0, position - 159) : position + 1]
            history_rows = [
                {
                    "date": row.trade_date,
                    "open": row.open,
                    "high": row.high,
                    "low": row.low,
                    "close": row.close,
                    "volume": row.volume_shares,
                }
                for row in history.itertuples(index=False)
            ]
            levels = build_ohlcv_support_resistance_levels(history_rows, float(current["close"]))
            supports = cluster_levels(
                [level for level in levels if level.get("side") == "support"],
                float(current["close"]),
            )
            resistances = cluster_levels(
                [level for level in levels if level.get("side") == "resistance"],
                float(current["close"]),
            )
            if not supports or not resistances:
                continue
            support = supports[0]
            resistance = resistances[0]
            prior = group.iloc[position - 1]
            three_days_ago = group.iloc[position - 3]
            core = classify_practical_status_core(
                {
                    "current_price": current["close"],
                    "previous_close": prior["close"],
                    "ma20": current["ma20"],
                    "ma20_3days_ago": three_days_ago["ma20"],
                    "ma60": current["ma60"],
                    "rsi": current["rsi14"],
                    "macd_osc": current["macd_osc"],
                    "macd_osc_prev": current["previous_macd_osc"],
                    "atr": current["atr14"],
                    "volume": current["volume_shares"],
                    "volume_avg_20d": current["volume_ma20"],
                    "low_10d": current["previous_10d_low"],
                    "support_zone_upper": support.get("zone_high") or support.get("price"),
                    "support_zone_lower": support.get("zone_low") or support.get("price"),
                    "resistance_zone_upper": resistance.get("zone_high") or resistance.get("price"),
                }
            )
            referee_ready = str(core.get("status") or "") != "資料不足"
            referee = {
                "decision_ready": referee_ready,
                "main_status": core.get("status"),
                "main_reasons": list(core.get("reasons") or []),
                "support_zone": support,
                "resistance_zone": resistance,
            }
            recent = group.iloc[max(0, position - 19) : position + 1].iloc[::-1]
            recent_context = [
                {
                    "date": row.trade_date,
                    "open": row.open,
                    "high": row.high,
                    "low": row.low,
                    "close": row.close,
                    "volume": row.volume_shares,
                    "rsi14": row.rsi14,
                    "macd_osc": row.macd_osc,
                    "technical_decision_ready": int(bool(row.technical_ready)),
                    "technical_data_quality": row.technical_data_quality,
                }
                for row in recent.itertuples(index=False)
            ]
            assessment = assess_low_zone_entry(
                referee=referee,
                current_price=current["close"],
                technical={
                    "decision_ready": bool(current["technical_ready"]),
                    "rsi": {"rsi14": current["rsi14"]},
                    "macd": {"oscillator": current["macd_osc"]},
                    "atr14": current["atr14"],
                    "volume_ma20": current["volume_ma20"],
                    "previous_10d_low": current["previous_10d_low"],
                },
                recent_context=recent_context,
                price_basis="completed_close",
            )
            record = {
                "source_index": int(current["index"]),
                "code": str(code),
                "trade_date": str(current["trade_date"]),
                "history_count": int(current["history_count"]),
                "panel": str(current["panel"]),
                "sample_split": str(current["sample_split"]),
                "market_regime": str(current["market_regime"]),
                "rsi14": float(current["rsi14"]),
                "rsi_delta": float(current["rsi_delta"]),
                "confirmation_count": int(current["confirmation_count"]),
                "macd_improving_two_days": bool(current["macd_improving_two_days"]),
                "referee_status": str(core.get("status") or "資料不足"),
                "stage": str(assessment.get("stage") or "unavailable"),
                "batch_entry_eligible": bool(assessment.get("batch_entry_eligible")),
            }
            for horizon in HORIZONS:
                for name in ("return", "mae", "mfe", "benchmark_return", "excess_return"):
                    record[f"{name}_{horizon}d"] = current.get(f"{name}_{horizon}d")
            records.append(record)
    return pd.DataFrame(records)


def _cohort_summaries(frame: pd.DataFrame, cohorts: dict[str, pd.Series]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for cohort_name, mask in cohorts.items():
        cohort = _with_cooldown(frame.loc[mask.fillna(False)].copy())
        for panel in ("established_universe", "broad_market"):
            for split in ("full", "train", "test"):
                scoped = cohort.loc[cohort["panel"].eq(panel)]
                if split != "full":
                    scoped = scoped.loc[scoped["sample_split"].eq(split)]
                for horizon in HORIZONS:
                    rows.append(
                        {
                            "cohort": cohort_name,
                            "panel": panel,
                            "sample": split,
                            "horizon_sessions": horizon,
                            **_metric_row(scoped, horizon),
                        }
                    )
    return rows


def _cohort_regime_summaries(
    frame: pd.DataFrame,
    cohorts: dict[str, pd.Series],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for cohort_name, mask in cohorts.items():
        cohort = _with_cooldown(frame.loc[mask.fillna(False)].copy())
        cohort = cohort.loc[cohort["panel"].eq("established_universe")]
        for regime in ("bull", "sideways", "bear", "unknown"):
            scoped = cohort.loc[cohort["market_regime"].eq(regime)]
            if scoped.empty:
                continue
            for horizon in HORIZONS:
                rows.append(
                    {
                        "cohort": cohort_name,
                        "market_regime": regime,
                        "horizon_sessions": horizon,
                        **_metric_row(scoped, horizon),
                    }
                )
    return rows


def _evidence_assessment(
    monotonicity_rows: list[dict[str, Any]],
    low_zone_metrics: list[dict[str, Any]],
    panel_coverage: list[dict[str, Any]],
) -> dict[str, Any]:
    broad = next((row for row in panel_coverage if row["panel"] == "broad_market"), {})
    broad_dates = int(broad.get("trade_dates") or 0)
    broad_sufficient = broad_dates >= 60
    primary_panel = "broad_market" if broad_sufficient else "established_universe"
    ranking_test = [
        row
        for row in monotonicity_rows
        if row["panel"] == primary_panel and row["sample"] == "test"
    ]
    ranking_train = [
        row
        for row in monotonicity_rows
        if row["panel"] == primary_panel and row["sample"] == "train"
    ]

    def _ranking_direction_passes(rows: list[dict[str, Any]], minimum_correlation: float) -> bool:
        return len(rows) == len(HORIZONS) and all(
            _number(row.get("spearman_like_correlation")) is not None
            and float(row["spearman_like_correlation"]) >= minimum_correlation
            and _number(row.get("top_minus_bottom_pct")) is not None
            and float(row["top_minus_bottom_pct"]) > 0
            for row in rows
        )

    ranking_test_passes = _ranking_direction_passes(ranking_test, 0.50)
    ranking_train_passes = _ranking_direction_passes(ranking_train, 0.0)
    ranking_validated = bool(ranking_test_passes and ranking_train_passes)

    def _test_counts(cohort: str) -> dict[str, int]:
        return {
            f"{int(row['horizon_sessions'])}d": int(row["observations"])
            for row in low_zone_metrics
            if row["cohort"] == cohort
            and row["panel"] == primary_panel
            and row["sample"] == "test"
        }

    existing_counts = _test_counts("full_existing_rule")
    stricter_counts = {
        cohort: _test_counts(cohort)
        for cohort in (
            "full_plus_rsi_delta_1_5",
            "full_plus_macd_two_days",
            "full_plus_both_stricter",
        )
    }
    existing_min = min(existing_counts.values(), default=0)
    stricter_statuses = {
        cohort: "sample_sufficient" if min(counts.values(), default=0) >= 30 else "insufficient_sample"
        for cohort, counts in stricter_counts.items()
    }
    every_rule_sufficient = bool(
        existing_min >= 30
        and stricter_statuses
        and all(status == "sample_sufficient" for status in stricter_statuses.values())
    )
    reasons = []
    if not ranking_validated:
        reasons.append(
            "The current ranking weights are not directionally consistent across both train and out-of-sample horizons."
        )
    if existing_min < 30:
        reasons.append("The exact entry rule does not yet reach 30 out-of-sample observations in every horizon.")
    insufficient_stricter = [
        cohort for cohort, status in stricter_statuses.items() if status != "sample_sufficient"
    ]
    if insufficient_stricter:
        reasons.append(
            "Stricter variants below 30 observations in at least one horizon: "
            + ", ".join(insufficient_stricter)
            + "."
        )
    if broad_dates < 60:
        reasons.append("Broad-market coverage is below 60 trading dates.")
    reasons.append("Keep production coefficients unchanged until every frozen evidence gate passes.")
    return {
        "production_weight_decision": "hold",
        "primary_evidence_panel": primary_panel,
        "ranking_weight_status": "validated" if ranking_validated else "not_validated",
        "ranking_train_direction_passes": ranking_train_passes,
        "ranking_test_direction_passes": ranking_test_passes,
        "ranking_test_results": [
            {
                "horizon_sessions": row["horizon_sessions"],
                "spearman_like_correlation": row["spearman_like_correlation"],
                "top_minus_bottom_pct": row["top_minus_bottom_pct"],
            }
            for row in ranking_test
        ],
        "full_existing_rule_status": (
            "sample_sufficient" if existing_min >= 30 else "promising_but_insufficient_sample"
        ),
        "full_existing_rule_test_observations": existing_counts,
        "stricter_rule_status": (
            "sample_sufficient" if every_rule_sufficient else "partially_or_fully_insufficient"
        ),
        "stricter_rule_statuses": stricter_statuses,
        "stricter_rule_test_observations": stricter_counts,
        "every_rule_sample_gate_status": "pass" if every_rule_sufficient else "pending",
        "broad_market_coverage_status": "sufficient" if broad_dates >= 60 else "insufficient",
        "broad_market_trade_dates": broad_dates,
        "minimum_required_test_observations_per_horizon": 30,
        "minimum_required_broad_market_dates": 60,
        "decision_reasons": reasons,
    }


def build_strategy_backtest_report(
    frame: pd.DataFrame,
    low_zone_results: pd.DataFrame,
) -> dict[str, Any]:
    eligible = frame.loc[frame["score_ready"] & ~frame["panel"].eq("sparse")].copy()
    ranking_rows: list[dict[str, Any]] = []
    monotonicity_rows: list[dict[str, Any]] = []
    for panel in ("established_universe", "broad_market"):
        for split in ("full", "train", "test"):
            scoped = eligible.loc[eligible["panel"].eq(panel)]
            if split != "full":
                scoped = scoped.loc[scoped["sample_split"].eq(split)]
            for horizon in HORIZONS:
                quintile_means: list[float | None] = []
                for quintile in range(1, 6):
                    bucket = scoped.loc[scoped["score_quintile"].eq(quintile)]
                    metrics = _metric_row(bucket, horizon)
                    ranking_rows.append(
                        {
                            "panel": panel,
                            "sample": split,
                            "horizon_sessions": horizon,
                            "score_quintile": quintile,
                            **metrics,
                        }
                    )
                    quintile_means.append(metrics["mean_return_pct"])
                valid = [value for value in quintile_means if value is not None]
                correlation = (
                    float(np.corrcoef(np.arange(1, 6), np.asarray(quintile_means, dtype=float))[0, 1])
                    if len(valid) == 5 and np.std(valid) > 0
                    else None
                )
                monotonicity_rows.append(
                    {
                        "panel": panel,
                        "sample": split,
                        "horizon_sessions": horizon,
                        "quintile_mean_returns_pct": quintile_means,
                        "spearman_like_correlation": round(correlation, 4) if correlation is not None else None,
                        "top_minus_bottom_pct": (
                            round(float(quintile_means[4]) - float(quintile_means[0]), 4)
                            if quintile_means[4] is not None and quintile_means[0] is not None
                            else None
                        ),
                    }
                )

    low_base = eligible.loc[eligible["rsi14"].between(30, 45)].copy()
    exact_indexes = set(
        low_zone_results.loc[low_zone_results.get("batch_entry_eligible", False).eq(True), "source_index"].tolist()
        if not low_zone_results.empty
        else []
    )
    cohorts = {
        "rsi_30_45_only": low_base.index.to_series().isin(set(low_base.index)),
        "rsi_turn_up": low_base["rsi_delta"].gt(0),
        "confirmation_2_of_3": low_base["rsi_delta"].gt(0) & low_base["confirmation_count"].ge(2),
        "full_existing_rule": low_base.index.to_series().isin(exact_indexes),
        "full_plus_rsi_delta_1_5": low_base.index.to_series().isin(exact_indexes) & low_base["rsi_delta"].ge(1.5),
        "full_plus_macd_two_days": low_base.index.to_series().isin(exact_indexes) & low_base["macd_improving_two_days"],
        "full_plus_both_stricter": (
            low_base.index.to_series().isin(exact_indexes)
            & low_base["rsi_delta"].ge(1.5)
            & low_base["macd_improving_two_days"]
        ),
    }
    low_zone_metrics = _cohort_summaries(low_base, cohorts)
    regime_metrics = _cohort_regime_summaries(low_base, cohorts)
    panel_coverage = []
    for panel in ("established_universe", "broad_market", "sparse"):
        scoped = frame.loc[frame["score_ready"] & frame["panel"].eq(panel)]
        panel_coverage.append(
            {
                "panel": panel,
                "observations": int(len(scoped)),
                "trade_dates": int(scoped["trade_date"].nunique()),
                "stocks": int(scoped["code"].nunique()),
                "date_from": str(scoped["trade_date"].min()) if not scoped.empty else None,
                "date_to": str(scoped["trade_date"].max()) if not scoped.empty else None,
                "median_stocks_per_date": (
                    float(scoped.groupby("trade_date").size().median()) if not scoped.empty else None
                ),
            }
        )
    stage_counts = (
        [
            {"stage": str(stage), "observations": int(count)}
            for stage, count in low_zone_results["stage"].value_counts().items()
        ]
        if not low_zone_results.empty
        else []
    )
    return {
        "version": STRATEGY_BACKTEST_VERSION,
        "screening_formula_version": SCREENING_PREFILTER_VERSION,
        "methodology": {
            "signal_time": "completed close T",
            "entry_price": "next trading session open T+1",
            "exit_price": "close after 5/10/20 stock trading sessions",
            "mae_mfe": "future session low/high relative to T+1 open",
            "liquidity_gate": "rolling 20-session average turnover >= TWD 100,000,000",
            "corporate_action_control": "exclude signal windows and outcomes crossing known corporate actions",
            "price_discontinuity_control": "exclude outcomes crossing an absolute close gap above 20%",
            "overlap_control": "cohort metrics use the first signal then a 20-session cooldown per stock",
            "benchmark": "same-date cross-sectional median return of research-eligible stocks",
            "walk_forward": "first 60% of dates=train; final 40%=test within each coverage panel",
            "diagnostic_drawdown": "compound equal-weight signal-date batch returns; overlapping holdings mean this is not a portfolio backtest",
        },
        "coverage": panel_coverage,
        "ranking_validation": {
            "quintile_metrics": ranking_rows,
            "monotonicity": monotonicity_rows,
        },
        "low_zone_validation": {
            "evaluated_low_rsi_rows": int(len(low_zone_results)),
            "stage_counts": stage_counts,
            "cohort_metrics": low_zone_metrics,
            "market_regime_metrics": regime_metrics,
        },
        "evidence_assessment": _evidence_assessment(
            monotonicity_rows,
            low_zone_metrics,
            panel_coverage,
        ),
        "limitations": [
            "歷史股票池由目前 stock master 回建，仍有存活者偏誤。",
            "公司規模與官方注意／處置的 point-in-time 快照從最新完整交易日才開始，因此歷史回測只套用成交金額與公司行動安全閘門。",
            "全市場 technical-ready 覆蓋期間很短；未具足夠未來交易日的持有期維持 unavailable，不補假值。",
            "未模擬交易成本、滑價、稅負、下單金額、漲跌停成交限制或投資組合資金約束。",
            "診斷最大回撤使用重疊的訊號日批次複利，不可解讀為可實現的投資組合回撤。",
            "結果只作研究證據，不會自動修改正式裁判或預排序係數。",
        ],
    }
