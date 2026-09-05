from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any
import math

import numpy as np
import pandas as pd


@dataclass
class IndicatorResult:
    value: float | None
    confidence: str
    start_date: str | None
    note: str
    extra: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _valid_num(value: Any) -> bool:
    try:
        return value is not None and not pd.isna(value) and np.isfinite(float(value))
    except Exception:
        return False


def _num(value: Any, default: float = 0.0) -> float:
    return float(value) if _valid_num(value) else default


def _date_text(value: Any) -> str:
    return pd.to_datetime(value).strftime("%Y-%m-%d")


def validate_inputs(
    price_df: pd.DataFrame | None,
    inst_df: pd.DataFrame | None,
    holding_df: pd.DataFrame | None = None,
) -> list[str]:
    issues: list[str] = []
    if price_df is None or price_df.empty:
        return ["缺少股價資料，無法計算籌碼成本推估"]

    price_required = {"date", "stock_id", "Trading_Volume", "Trading_money", "max", "min", "close"}
    missing_price = sorted(price_required.difference(price_df.columns))
    if missing_price:
        issues.append(f"股價資料缺少欄位：{missing_price}")

    if len(price_df) < 60:
        issues.append("股價資料不足60筆，60日成交密集價估算可信度不足")

    if "Trading_Volume" in price_df.columns:
        volume = pd.to_numeric(price_df["Trading_Volume"], errors="coerce").fillna(0)
        if len(volume) and float((volume <= 0).mean()) > 0.1:
            issues.append("超過10%交易日成交量為0，VWAP/成交密集價可能失真")

    if "Trading_money" in price_df.columns:
        money = pd.to_numeric(price_df["Trading_money"], errors="coerce").fillna(0)
        if len(money) and float((money <= 0).mean()) > 0.1:
            issues.append("超過10%交易日成交金額為0，VWAP可能失真")

    if inst_df is None or inst_df.empty:
        issues.append("缺少法人資料，外資/投信成本推估不可用")
    else:
        has_raw = {"date", "stock_id", "name", "buy", "sell"}.issubset(inst_df.columns)
        has_pivot = {"date", "stock_id"}.issubset(inst_df.columns) and (
            "Foreign_Investor" in inst_df.columns
            or "Investment_Trust" in inst_df.columns
            or "foreign_net" in inst_df.columns
            or "trust_net" in inst_df.columns
        )
        if not has_raw and not has_pivot:
            issues.append("法人資料需包含原始 name/buy/sell 或彙總 Foreign_Investor/Investment_Trust 欄位")

    if holding_df is None or holding_df.empty:
        issues.append("缺少外資持股資料，外資累積成本推估無法建立可靠期初庫存")
    elif "ForeignInvestmentShares" not in holding_df.columns:
        issues.append("外資持股資料缺少 ForeignInvestmentShares 欄位")

    return issues


def calc_vwap_row(row: pd.Series) -> float:
    money = _num(row.get("Trading_money"))
    volume = _num(row.get("Trading_Volume"))
    high = _num(row.get("max"))
    low = _num(row.get("min"))
    close = _num(row.get("close"))
    hlc3 = (high + low + close) / 3 if high > 0 and low > 0 and close > 0 else close
    if money <= 0 or volume <= 0 or high <= 0 or low <= 0:
        return float(hlc3) if hlc3 > 0 else float("nan")
    vwap = money / volume
    if low * 0.7 <= vwap <= high * 1.3:
        return float(vwap)
    return float(hlc3) if hlc3 > 0 else float("nan")


def prepare_price_df(price_df: pd.DataFrame) -> pd.DataFrame:
    df = price_df.copy()
    df["date"] = pd.to_datetime(df["date"])
    for col in ["Trading_Volume", "Trading_money", "open", "max", "min", "close"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df["vwap"] = df.apply(calc_vwap_row, axis=1)
    df["hlc3"] = (df["max"] + df["min"] + df["close"]) / 3
    df["trade_price"] = df["vwap"].fillna(df["hlc3"]).fillna(df["close"])
    return df.sort_values(["stock_id", "date"]).reset_index(drop=True)


def prepare_institution_net(inst_df: pd.DataFrame | None) -> pd.DataFrame:
    if inst_df is None or inst_df.empty:
        return pd.DataFrame(columns=["date", "stock_id", "Foreign_Investor", "Investment_Trust"])

    df = inst_df.copy()
    df["date"] = pd.to_datetime(df["date"])

    if {"name", "buy", "sell"}.issubset(df.columns):
        df["buy"] = pd.to_numeric(df["buy"], errors="coerce").fillna(0.0)
        df["sell"] = pd.to_numeric(df["sell"], errors="coerce").fillna(0.0)
        df["net"] = df["buy"] - df["sell"]
        pivot = (
            df.pivot_table(index=["date", "stock_id"], columns="name", values="net", aggfunc="sum", fill_value=0.0)
            .reset_index()
        )
        pivot.columns.name = None
        df = pivot

    rename_map = {
        "foreign_net": "Foreign_Investor",
        "trust_net": "Investment_Trust",
        "dealer_net": "Dealer_self",
    }
    df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
    for col in ["Foreign_Investor", "Investment_Trust", "Dealer_self"]:
        if col not in df.columns:
            df[col] = 0.0
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
    return df.sort_values(["stock_id", "date"]).reset_index(drop=True)


def prepare_holding_df(holding_df: pd.DataFrame | None) -> pd.DataFrame:
    if holding_df is None or holding_df.empty:
        return pd.DataFrame(columns=["date", "stock_id", "ForeignInvestmentShares"])
    df = holding_df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df["ForeignInvestmentShares"] = pd.to_numeric(df["ForeignInvestmentShares"], errors="coerce")
    return df.sort_values(["stock_id", "date"]).reset_index(drop=True)


def prepare_actions_df(actions_df: pd.DataFrame | None) -> pd.DataFrame:
    cols = ["date", "stock_id", "cash_dividend_per_share", "stock_dividend_value_per_share", "split_factor"]
    if actions_df is None or actions_df.empty:
        return pd.DataFrame(columns=cols)
    df = actions_df.copy()
    df["date"] = pd.to_datetime(df["date"])
    for col in cols:
        if col not in df.columns and col not in {"date", "stock_id"}:
            df[col] = 0.0 if col != "split_factor" else 1.0
    for col in ["cash_dividend_per_share", "stock_dividend_value_per_share", "split_factor"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(1.0 if col == "split_factor" else 0.0)
    return df[cols].sort_values(["stock_id", "date"]).reset_index(drop=True)


def build_daily_frame(
    price_df: pd.DataFrame,
    inst_df: pd.DataFrame | None,
    holding_df: pd.DataFrame | None = None,
    actions_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    daily = prepare_price_df(price_df).merge(prepare_institution_net(inst_df), on=["date", "stock_id"], how="left")
    for col in ["Foreign_Investor", "Investment_Trust", "Dealer_self"]:
        daily[col] = pd.to_numeric(daily.get(col), errors="coerce").fillna(0.0)
    daily["institution_missing"] = False if inst_df is not None and not inst_df.empty else True

    holding = prepare_holding_df(holding_df)
    if not holding.empty:
        exact_dates = set(zip(holding["stock_id"], holding["date"]))
        daily = pd.merge_asof(
            daily.sort_values(["stock_id", "date"]),
            holding[["date", "stock_id", "ForeignInvestmentShares"]].sort_values(["stock_id", "date"]),
            on="date",
            by="stock_id",
            direction="backward",
        )
        daily["is_foreign_holding_update"] = [
            (sid, dt) in exact_dates for sid, dt in zip(daily["stock_id"], daily["date"])
        ]
        daily["ForeignInvestmentShares"] = daily.groupby("stock_id")["ForeignInvestmentShares"].ffill()
    else:
        daily["ForeignInvestmentShares"] = np.nan
        daily["is_foreign_holding_update"] = False

    actions = prepare_actions_df(actions_df)
    if not actions.empty:
        daily = daily.merge(actions, on=["date", "stock_id"], how="left")
    for col, default in {
        "cash_dividend_per_share": 0.0,
        "stock_dividend_value_per_share": 0.0,
        "split_factor": 1.0,
    }.items():
        if col not in daily.columns:
            daily[col] = default
        daily[col] = pd.to_numeric(daily[col], errors="coerce").fillna(default)
    return daily.sort_values(["stock_id", "date"]).reset_index(drop=True)


def apply_opening_actions(shares: float, total_cost: float, row: pd.Series) -> tuple[float, float]:
    if shares <= 0:
        return 0.0, 0.0
    cash_div = _num(row.get("cash_dividend_per_share"))
    stock_div = _num(row.get("stock_dividend_value_per_share"))
    split_factor = _num(row.get("split_factor"), 1.0)
    if cash_div > 0:
        total_cost -= shares * cash_div
    if stock_div > 0:
        shares *= 1.0 + stock_div / 10.0
    if split_factor > 0 and split_factor != 1.0:
        shares *= split_factor
    return max(shares, 0.0), max(total_cost, 0.0)


def compute_foreign_cost(daily: pd.DataFrame) -> IndicatorResult:
    if daily.empty:
        return IndicatorResult(None, "invalid", None, "缺少股價資料", {})
    anchors = daily[
        daily["ForeignInvestmentShares"].apply(_valid_num)
        & daily["trade_price"].apply(_valid_num)
        & daily["is_foreign_holding_update"].astype(bool)
    ]
    if anchors.empty:
        return IndicatorResult(
            None,
            "invalid",
            None,
            "缺少外資持股原始更新日，無法建立外資累積成本 anchor",
            {"required": "foreign_shareholding.ForeignInvestmentShares"},
        )

    start_idx = int(anchors.index[0])
    start_row = daily.loc[start_idx]
    shares = float(start_row["ForeignInvestmentShares"])
    price = float(start_row["trade_price"])
    total_cost = shares * price
    start_date = _date_text(start_row["date"])
    reconcile_count = 0
    mild_reconcile = 0
    severe_reconcile = 0

    for idx in range(start_idx + 1, len(daily)):
        row = daily.loc[idx]
        price = _num(row.get("trade_price"))
        shares, total_cost = apply_opening_actions(shares, total_cost, row)
        net = _num(row.get("Foreign_Investor"))
        if price > 0 and net > 0:
            total_cost += net * price
            shares += net
        elif price > 0 and net < 0 and shares > 0:
            sell_qty = min(shares, abs(net))
            avg_cost = total_cost / shares if shares > 0 else 0.0
            total_cost -= sell_qty * avg_cost
            shares -= sell_qty
            if shares <= 0:
                shares = 0.0
                total_cost = 0.0
        if bool(row.get("is_foreign_holding_update", False)):
            real_shares = _num(row.get("ForeignInvestmentShares"), float("nan"))
            if real_shares > 0 and shares > 0:
                ratio = real_shares / shares
                avg_cost = total_cost / shares
                shares = real_shares
                total_cost = avg_cost * shares
                reconcile_count += 1
                if not (0.80 <= ratio <= 1.20):
                    severe_reconcile += 1
                elif not (0.95 <= ratio <= 1.05):
                    mild_reconcile += 1

    if shares <= 0 or total_cost <= 0:
        return IndicatorResult(None, "invalid", start_date, "模型庫存歸零，無有效外資累積成本", {})
    if severe_reconcile:
        return IndicatorResult(
            None,
            "invalid",
            start_date,
            "外資持股校正差異超過20%，推估不可靠",
            {"reconcile_count": reconcile_count, "severe_reconcile_count": severe_reconcile},
        )
    confidence = "low" if mild_reconcile else "medium"
    return IndicatorResult(
        round(total_cost / shares, 2),
        confidence,
        start_date,
        "使用外資持股原始更新日建立期初庫存，逐日納入外資淨買賣超；公開資料推估，非籌碼K線原始算法",
        {"final_shares": round(shares, 2), "reconcile_count": reconcile_count, "mild_reconcile_count": mild_reconcile},
    )


def compute_trust_buy_cost(daily: pd.DataFrame) -> IndicatorResult:
    if daily.empty:
        return IndicatorResult(None, "invalid", None, "缺少股價資料", {})
    net_position = 0.0
    segment_buy_shares = 0.0
    segment_buy_cost = 0.0
    start_date: str | None = None
    for _, row in daily.iterrows():
        net = _num(row.get("Investment_Trust"))
        price = _num(row.get("trade_price"))
        new_pos = net_position + net
        if new_pos <= 0:
            net_position = 0.0
            segment_buy_shares = 0.0
            segment_buy_cost = 0.0
            start_date = None
            continue
        if net > 0 and price > 0:
            if net_position <= 0:
                start_date = _date_text(row["date"])
            segment_buy_shares += net
            segment_buy_cost += net * price
        net_position = new_pos

    if segment_buy_shares <= 0:
        return IndicatorResult(None, "none", None, "投信近期沒有形成有效買超區間", {"coverage_ratio": 0.0})
    coverage_ratio = net_position / segment_buy_shares
    confidence = "low" if coverage_ratio < 0.3 else "medium"
    return IndicatorResult(
        round(segment_buy_cost / segment_buy_shares, 2),
        confidence,
        start_date,
        "只統計最近一段有效買超區間；累積淨部位歸零即重置，不代表投信真實持倉成本",
        {
            "segment_buy_shares": round(segment_buy_shares, 2),
            "net_position": round(net_position, 2),
            "coverage_ratio": round(coverage_ratio, 4),
        },
    )


def simple_tick(price: float) -> float:
    if price < 10:
        return 0.01
    if price < 50:
        return 0.05
    if price < 100:
        return 0.1
    if price < 500:
        return 0.5
    if price < 1000:
        return 1.0
    return 5.0


def compute_poc60(price_df: pd.DataFrame, lookback: int = 60) -> IndicatorResult:
    price = prepare_price_df(price_df)
    window = price.tail(lookback).copy()
    if len(window) < min(lookback, 20):
        return IndicatorResult(None, "invalid", None, "近60日股價資料不足", {"available_days": len(window)})
    high = _num(window["max"].max())
    low = _num(window["min"].min())
    close = _num(window["close"].iloc[-1])
    if high <= low or close <= 0:
        return IndicatorResult(None, "invalid", None, "價格區間無效", {})
    tick = simple_tick(close)
    bucket_size = max(tick, math.ceil(max((high - low) * 0.005, tick) / tick) * tick)
    buckets: dict[float, float] = {}
    allocated_total = 0.0
    raw_total = 0.0
    for _, row in window.iterrows():
        day_low = _num(row.get("min"))
        day_high = _num(row.get("max"))
        volume = _num(row.get("Trading_Volume"))
        center_price = _num(row.get("trade_price"), _num(row.get("close")))
        if day_low <= 0 or day_high <= 0 or volume <= 0 or center_price <= 0:
            continue
        first = math.floor(day_low / bucket_size) * bucket_size + bucket_size / 2
        last = math.ceil(day_high / bucket_size) * bucket_size + bucket_size / 2
        centers = []
        x = first
        guard = 0
        while x <= last + 1e-9 and guard < 500:
            centers.append(round(x, 4))
            x += bucket_size
            guard += 1
        weights = {c: 1.0 / (abs(c - center_price) + bucket_size) for c in centers}
        total_weight = sum(weights.values())
        if total_weight <= 0:
            continue
        raw_total += volume
        for c, w in weights.items():
            allocated = volume * w / total_weight
            allocated_total += allocated
            buckets[c] = buckets.get(c, 0.0) + allocated
    if not buckets:
        return IndicatorResult(None, "invalid", None, "近60日無有效成交量", {})
    poc = max(buckets.items(), key=lambda kv: kv[1])[0]
    confidence = "high" if abs(allocated_total - raw_total) <= max(raw_total * 0.001, 1) else "low"
    return IndicatorResult(
        round(float(poc), 2),
        confidence,
        _date_text(window["date"].iloc[0]),
        "近60日 high-low 分箱，成交量以 VWAP 為中心正規化分配",
        {
            "lookback": lookback,
            "bucket_size": bucket_size,
            "bucket_count": len(buckets),
            "raw_volume": round(raw_total, 2),
            "allocated_volume": round(allocated_total, 2),
        },
    )


def calculate_chip_cost_indicators(
    price_df: pd.DataFrame,
    inst_df: pd.DataFrame | None,
    holding_df: pd.DataFrame | None = None,
    actions_df: pd.DataFrame | None = None,
) -> dict[str, Any]:
    input_issues = validate_inputs(price_df, inst_df, holding_df)
    daily = build_daily_frame(price_df, inst_df, holding_df, actions_df)
    latest_date = _date_text(daily["date"].max()) if not daily.empty else None
    return {
        "date": latest_date,
        "input_issues": input_issues,
        "foreign_cost_estimate": compute_foreign_cost(daily).to_dict(),
        "trust_buy_cost_estimate": compute_trust_buy_cost(daily).to_dict(),
        "poc60_estimate": compute_poc60(price_df, 60).to_dict(),
        "disclaimer": "以上為公開資料推估，非籌碼K線原始算法，僅供參考。",
    }
