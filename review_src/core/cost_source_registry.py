from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


CANONICAL_COST_CONTRACT_VERSION = "canonical_cost_context_v1"
CANONICAL_COST_FORMULA_VERSION = "official_net_flow_incremental_inventory_v3"
CANONICAL_COST_TYPES = ("foreign_estimated", "trust_estimated")
CANONICAL_COST_MIN_OFFICIAL_SESSIONS = 60


COST_STATUS_VALUES = {
    "ok",
    "estimated",
    "proxy_only",
    "unavailable",
    "invalid",
    "insufficient_data",
    "missing_required_source",
}

CONFIDENCE_VALUES = {"high", "medium", "low", "unavailable"}

ACCUMULATION_STATUS_VALUES = {
    "building",
    "markup",
    "distribution",
    "neutral",
    "unavailable",
}

VALID_COST_TYPES = {
    "foreign_estimated",
    "trust_estimated",
    "margin_incremental_estimated",
    "margin_reliable_cost",
    "main_force_reference_zone",
    "main_force_branch_cost",
}


@dataclass(frozen=True)
class CostSourceDefinition:
    cost_type: str
    display_label: str
    required_tables: tuple[str, ...]
    required_columns: Mapping[str, tuple[str, ...]]
    optional_columns: Mapping[str, tuple[str, ...]]
    missing_required_fields: tuple[str, ...]
    max_confidence: str
    source_license: str
    source_detail: str
    source_tables: tuple[str, ...]
    calculation_method: str
    allowed_missing_status: str = "missing_required_source"
    forbidden_labels: tuple[str, ...] = ()


COST_SOURCE_REGISTRY: dict[str, CostSourceDefinition] = {
    "foreign_estimated": CostSourceDefinition(
        cost_type="foreign_estimated",
        display_label="外資近期增量部位均價推估",
        required_tables=("institution_activity_daily", "history_price"),
        required_columns={
            "institution_activity_daily": ("foreign_net", "source_quality"),
            "history_price": ("close", "volume", "amount"),
        },
        optional_columns={"foreign_shareholding": ("ForeignInvestmentShares",)},
        missing_required_fields=(),
        max_confidence="medium",
        source_license="public_local_db",
        source_detail="Incremental-position estimate from official foreign net flow and official OHLCV VWAP; it is not total foreign holding cost.",
        source_tables=("institution_activity_daily", "history_price"),
        calculation_method="official_net_flow_incremental_inventory",
        forbidden_labels=("外資真實成本",),
    ),
    "trust_estimated": CostSourceDefinition(
        cost_type="trust_estimated",
        display_label="投信近期增量部位均價推估",
        required_tables=("institution_activity_daily", "history_price"),
        required_columns={
            "institution_activity_daily": ("trust_net", "source_quality"),
            "history_price": ("close", "volume", "amount"),
        },
        optional_columns={},
        missing_required_fields=(),
        max_confidence="medium",
        source_license="public_local_db",
        source_detail="Incremental-position estimate from official investment-trust net flow and official OHLCV VWAP; no trust holding anchor exists.",
        source_tables=("institution_activity_daily", "history_price"),
        calculation_method="official_net_flow_incremental_inventory",
        forbidden_labels=("投信真實成本",),
    ),
    "margin_incremental_estimated": CostSourceDefinition(
        cost_type="margin_incremental_estimated",
        display_label="融資新增部位估算成本",
        required_tables=("margin_daily", "history_price"),
        required_columns={
            "margin_daily": ("margin_balance",),
            "history_price": ("close", "volume", "amount"),
        },
        optional_columns={},
        missing_required_fields=("margin_balance_unit_verified",),
        max_confidence="medium",
        source_license="public_local_db",
        source_detail="Incremental estimate from margin balance delta only when margin balance unit is verified.",
        source_tables=("margin_daily", "history_price"),
        calculation_method="moving_average_inventory_estimate_unit_gated",
        allowed_missing_status="unavailable",
        forbidden_labels=("融資真實成本", "融資放款均額", "融資買進真實成本"),
    ),
    "margin_reliable_cost": CostSourceDefinition(
        cost_type="margin_reliable_cost",
        display_label="可靠融資成本",
        required_tables=("margin_financing_amount_daily",),
        required_columns={
            "margin_financing_amount_daily": (
                "financing_buy_amount",
                "financing_loan_amount",
                "financing_balance_amount",
            )
        },
        optional_columns={},
        missing_required_fields=(
            "financing_buy_amount",
            "financing_loan_amount",
            "financing_balance_amount",
        ),
        max_confidence="high",
        source_license="authorized_source_required",
        source_detail="Requires authorized financing amount fields; margin_balance is not a substitute.",
        source_tables=("margin_financing_amount_daily",),
        calculation_method="not_computed_without_authorized_amount",
    ),
    "main_force_reference_zone": CostSourceDefinition(
        cost_type="main_force_reference_zone",
        display_label="成交密集價參考區",
        required_tables=("price_volume_score_daily",),
        required_columns={
            "price_volume_score_daily": ("weighted_cost", "main_peak_price")
        },
        optional_columns={},
        missing_required_fields=("existing_poc_or_price_volume_profile",),
        max_confidence="medium",
        source_license="public_local_db_proxy",
        source_detail="Uses existing POC / price-volume profile as a reference zone only, not broker branch cost.",
        source_tables=("price_volume_score_daily",),
        calculation_method="existing_price_volume_proxy",
        allowed_missing_status="unavailable",
        forbidden_labels=("主力成本", "主力真實成本", "主力分點成本"),
    ),
    "main_force_branch_cost": CostSourceDefinition(
        cost_type="main_force_branch_cost",
        display_label="主力分點成本",
        required_tables=("broker_branch_trade_daily",),
        required_columns={
            "broker_branch_trade_daily": (
                "buy_shares",
                "sell_shares",
                "buy_amount",
                "sell_amount",
            )
        },
        optional_columns={},
        missing_required_fields=(
            "broker_branch_buy_shares",
            "broker_branch_sell_shares",
            "broker_branch_buy_amount",
            "broker_branch_sell_amount",
        ),
        max_confidence="high",
        source_license="authorized_source_required",
        source_detail="Requires authorized broker branch buy/sell shares and amounts.",
        source_tables=("broker_branch_trade_daily",),
        calculation_method="not_computed_without_authorized_branch_data",
    ),
}


def all_cost_types() -> tuple[str, ...]:
    return tuple(COST_SOURCE_REGISTRY.keys())


def get_cost_definition(cost_type: str) -> CostSourceDefinition:
    return COST_SOURCE_REGISTRY[cost_type]


def missing_fields_text(cost_type: str) -> str:
    return ", ".join(get_cost_definition(cost_type).missing_required_fields)
