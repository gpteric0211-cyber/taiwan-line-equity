from __future__ import annotations

"""Read-only verification for one complete post-close analysis-data update."""

import argparse
import json
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from core.components import read_components  # noqa: E402
from core.config import DB_PATH  # noqa: E402
from core.market_session import (  # noqa: E402
    is_taiwan_trading_day,
    recent_market_date_for_post_close,
)


DEFAULT_OFFICIAL_REPORT = ROOT / "docs" / "ALL_MARKET_DATABASE_UPDATE_REPORT.json"
DEFAULT_POST_CLOSE_REPORT = ROOT / "docs" / "POST_CLOSE_DAILY_PIPELINE_REPORT.json"
DEFAULT_OUTPUT = ROOT / "docs" / "MANUAL_DAILY_ANALYSIS_UPDATE_VERIFICATION.json"
DEFAULT_EXTERNAL_REPORT = ROOT / "docs" / "MANUAL_EXTERNAL_EVENTS_REPORT.json"


def _codes_on_date(conn: sqlite3.Connection, table: str, entity: str,
                   date_column: str, trade_date: str, where: str = "1=1") -> set[str]:
    if not _table_exists(conn, table):
        return set()
    return {str(row[0]) for row in conn.execute(
        f"SELECT DISTINCT {entity} FROM {table} WHERE {date_column}=? AND ({where})",
        (trade_date,),
    )}


def _coverage(required: set[str], present: set[str]) -> dict[str, Any]:
    missing = sorted(required - present)
    return {"required": len(required), "covered": len(required & present),
            "missing_codes": missing, "complete": bool(required) and not missing}


def _source_ready(result: dict[str, Any]) -> bool:
    # Several adapters return ok=True when only one of many symbols succeeded.
    return bool(result.get("ok") and result.get("status") == "ok")


def _configured_feeds_ready(report: dict[str, Any]) -> bool:
    return all(
        bool((report.get(key) or {}).get("ok"))
        and (report.get(key) or {}).get("status") in {"ok", "disabled"}
        for key in ("authorized_trump_social", "licensed_news", "gdelt_news_radar")
    )


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def _scalar(
    conn: sqlite3.Connection,
    query: str,
    parameters: tuple[Any, ...] = (),
) -> Any:
    row = conn.execute(query, parameters).fetchone()
    return row[0] if row else None


def _count_distinct(
    conn: sqlite3.Connection,
    *,
    table: str,
    entity_column: str,
    date_column: str,
    trade_date: str,
    extra_where: str = "",
) -> int:
    if not _table_exists(conn, table):
        return 0
    suffix = f" AND ({extra_where})" if extra_where else ""
    return int(
        _scalar(
            conn,
            f"SELECT COUNT(DISTINCT {entity_column}) FROM {table} "
            f"WHERE {date_column}=?{suffix}",
            (trade_date,),
        )
        or 0
    )


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _latest_date(conn: sqlite3.Connection, table: str, column: str) -> str | None:
    if not _table_exists(conn, table):
        return None
    value = _scalar(conn, f"SELECT MAX({column}) FROM {table}")
    return str(value) if value else None


def _days_old(value: str | None, target: str) -> int | None:
    if not value:
        return None
    try:
        return (date.fromisoformat(target) - date.fromisoformat(value)).days
    except ValueError:
        return None


def _component_codes() -> set[str]:
    return {
        str(item.get("code") or "").strip().zfill(4)
        for item in read_components()
        if str(item.get("code") or "").strip()
    }


def _previous_trading_date(trade_date: str) -> str:
    cursor = date.fromisoformat(trade_date) - timedelta(days=1)
    while not is_taiwan_trading_day(cursor):
        cursor -= timedelta(days=1)
    return cursor.isoformat()


def _batch_publication(
    conn: sqlite3.Connection,
    trade_date: str,
) -> dict[str, Any] | None:
    if not _table_exists(conn, "full_market_batch_publications"):
        return None
    row = conn.execute(
        """
        SELECT expected_active_asof,classified_ohlcv_count,
               official_no_trade_count,not_applicable_count,
               expected_universe_hash,classified_universe_hash,contract_version
        FROM full_market_batch_publications
        WHERE trade_date=?
        """,
        (trade_date,),
    ).fetchone()
    if not row:
        return None
    keys = (
        "expected_active_asof",
        "classified_ohlcv_count",
        "official_no_trade_count",
        "not_applicable_count",
        "expected_universe_hash",
        "classified_universe_hash",
        "contract_version",
    )
    return dict(zip(keys, row))


def _publication_complete(publication: dict[str, Any] | None) -> bool:
    if not publication:
        return False
    expected = int(publication.get("expected_active_asof") or 0)
    classified = sum(
        int(publication.get(key) or 0)
        for key in (
            "classified_ohlcv_count",
            "official_no_trade_count",
            "not_applicable_count",
        )
    )
    return bool(
        expected > 0
        and classified == expected
        and publication.get("expected_universe_hash")
        == publication.get("classified_universe_hash")
    )


def build_daily_analysis_report(
    database: Path,
    *,
    trade_date: str,
    official_report_path: Path = DEFAULT_OFFICIAL_REPORT,
    post_close_report_path: Path = DEFAULT_POST_CLOSE_REPORT,
) -> dict[str, Any]:
    resolved = database.expanduser().resolve()
    official_report = _load_json(official_report_path)
    external_report = _load_json(DEFAULT_EXTERNAL_REPORT)
    previous_trade_date = _previous_trading_date(trade_date)
    with sqlite3.connect(f"{resolved.as_uri()}?mode=ro", uri=True, timeout=60) as conn:
        integrity = [str(row[0]) for row in conn.execute("PRAGMA quick_check")]
        active_stocks = int(
            _scalar(
                conn,
                "SELECT COUNT(*) FROM stock_master "
                "WHERE is_active=1 AND security_type='stock'",
            )
            or 0
        )
        no_trade_stocks = int(
            _scalar(
                conn,
                """
                SELECT COUNT(DISTINCT n.code)
                FROM stock_no_trade_dates n
                JOIN stock_master s ON s.code=n.code
                WHERE n.trade_date=? AND s.is_active=1 AND s.security_type='stock'
                """,
                (trade_date,),
            )
            or 0
        )
        current_publication = _batch_publication(conn, trade_date)
        required_trading_stocks = int(
            (current_publication or {}).get("classified_ohlcv_count")
            or max(active_stocks - no_trade_stocks, 0)
        )
        previous_no_trade_stocks = int(
            _scalar(
                conn,
                """
                SELECT COUNT(DISTINCT n.code)
                FROM stock_no_trade_dates n
                JOIN stock_master s ON s.code=n.code
                WHERE n.trade_date=? AND s.is_active=1 AND s.security_type='stock'
                """,
                (previous_trade_date,),
            )
            or 0
        )
        previous_publication = _batch_publication(conn, previous_trade_date)
        previous_required_trading_stocks = int(
            (previous_publication or {}).get("classified_ohlcv_count")
            or max(active_stocks - previous_no_trade_stocks, 0)
        )

        metrics = {
            "official_ohlcv": _count_distinct(
                conn,
                table="history_price",
                entity_column="code",
                date_column="date",
                trade_date=trade_date,
                extra_where=(
                    "LOWER(COALESCE(source_quality,''))='official' "
                    "OR UPPER(COALESCE(source,'')) LIKE '%TWSE%' "
                    "OR UPPER(COALESCE(source,'')) LIKE '%TPEX%'"
                ),
            ),
            "previous_official_ohlcv": _count_distinct(
                conn,
                table="history_price",
                entity_column="code",
                date_column="date",
                trade_date=previous_trade_date,
                extra_where=(
                    "LOWER(COALESCE(source_quality,''))='official' "
                    "OR UPPER(COALESCE(source,'')) LIKE '%TWSE%' "
                    "OR UPPER(COALESCE(source,'')) LIKE '%TPEX%'"
                ),
            ),
            "technical_vectors": _count_distinct(
                conn,
                table="technical_indicator_vector_daily",
                entity_column="stock_code",
                date_column="trade_date",
                trade_date=trade_date,
            ),
            "technical_snapshots": _count_distinct(
                conn,
                table="daily_technical_snapshot",
                entity_column="code",
                date_column="trade_date",
                trade_date=trade_date,
            ),
            "institution_activity": _count_distinct(
                conn,
                table="institution_activity_daily",
                entity_column="code",
                date_column="trade_date",
                trade_date=trade_date,
            ),
            "credit_balances": _count_distinct(
                conn,
                table="credit_balance_daily",
                entity_column="code",
                date_column="trade_date",
                trade_date=trade_date,
            ),
            "official_valuations": _count_distinct(
                conn,
                table="twse_daily_valuation",
                entity_column="symbol",
                date_column="data_date",
                trade_date=trade_date,
            ),
            "estimated_chip_costs": _count_distinct(
                conn,
                table="estimated_chip_cost_daily",
                entity_column="code",
                date_column="trade_date",
                trade_date=trade_date,
            ),
            "price_volume_distribution": _count_distinct(
                conn,
                table="price_volume_distribution",
                entity_column="stock_id",
                date_column="trade_date",
                trade_date=trade_date,
            ),
            "price_volume_validated": _count_distinct(
                conn,
                table="price_volume_distribution",
                entity_column="stock_id",
                date_column="trade_date",
                trade_date=trade_date,
                extra_where="UPPER(COALESCE(data_quality,'')) IN ('VALIDATED','SCOPED_VALIDATED')",
            ),
            "price_volume_profiles": _count_distinct(
                conn,
                table="price_volume_profile_daily",
                entity_column="code",
                date_column="date",
                trade_date=trade_date,
            ),
            "price_volume_scores": _count_distinct(
                conn,
                table="price_volume_score_daily",
                entity_column="code",
                date_column="date",
                trade_date=trade_date,
            ),
            "inner_outer_volume": _count_distinct(
                conn,
                table="daily_inner_outer_volume",
                entity_column="stock_code",
                date_column="trade_date",
                trade_date=trade_date,
            ),
            "daily_chip_momentum": _count_distinct(
                conn,
                table="daily_chip_momentum",
                entity_column="stock_id",
                date_column="date",
                trade_date=trade_date,
            ),
            "taiwan50_close_batch": _count_distinct(
                conn,
                table="taiwan50_close_batch_items",
                entity_column="symbol",
                date_column="data_date",
                trade_date=trade_date,
            ),
        }
        close_batch_errors = int(
            _scalar(
                conn,
                "SELECT COALESCE(error_count,0) FROM taiwan50_close_batch_runs "
                "WHERE data_date=?",
                (trade_date,),
            )
            or 0
        ) if _table_exists(conn, "taiwan50_close_batch_runs") else 0
        watchlist_count = int(
            _scalar(conn, "SELECT COUNT(DISTINCT code) FROM watchlist") or 0
        ) if _table_exists(conn, "watchlist") else 0
        component_codes = _component_codes()
        watchlist_codes = {
            str(row[0]).zfill(4)
            for row in conn.execute("SELECT code FROM watchlist")
        } if _table_exists(conn, "watchlist") else set()
        expected_chip_codes = required_trading_stocks
        tdcc_latest = _latest_date(conn, "tdcc_equity_summary", "date")
        tdcc_entities = _count_distinct(
            conn,
            table="tdcc_equity_summary",
            entity_column="code",
            date_column="date",
            trade_date=tdcc_latest or "",
        )
        official_codes = _codes_on_date(conn, "history_price", "code", "date", trade_date,
            "LOWER(COALESCE(source_quality,''))='official' "
            "OR UPPER(COALESCE(source,'')) LIKE '%TWSE%' "
            "OR UPPER(COALESCE(source,'')) LIKE '%TPEX%'")
        active_codes = {str(row[0]) for row in conn.execute(
            "SELECT code FROM stock_master WHERE is_active=1 AND security_type='stock'")}
        no_trade_codes = _codes_on_date(conn, "stock_no_trade_dates", "code", "trade_date", trade_date)
        unclassified_codes = sorted(active_codes - official_codes - no_trade_codes)
        coverage = {}
        for name, table, entity, date_column, where in (
            ("technical_vectors", "technical_indicator_vector_daily", "stock_code", "trade_date", "1=1"),
            ("technical_snapshots", "daily_technical_snapshot", "code", "trade_date", "1=1"),
            ("price_volume_validated", "price_volume_distribution", "stock_id", "trade_date",
             "UPPER(COALESCE(data_quality,'')) IN ('VALIDATED','SCOPED_VALIDATED')"),
            ("price_volume_profiles", "price_volume_profile_daily", "code", "date", "1=1"),
            ("price_volume_scores", "price_volume_score_daily", "code", "date", "1=1"),
            ("inner_outer_volume", "daily_inner_outer_volume", "stock_code", "trade_date", "1=1"),
            ("daily_chip_momentum", "daily_chip_momentum", "stock_id", "date", "1=1"),
        ):
            coverage[name] = _coverage(official_codes, _codes_on_date(
                conn, table, entity, date_column, trade_date, where))
        close_coverage = _coverage(component_codes, _codes_on_date(
            conn, "taiwan50_close_batch_items", "symbol", "data_date", trade_date))

    official_components = {
        key: bool(official_report.get("effective_trade_date") == trade_date
                  and (official_report.get(key) or {}).get("ok"))
        for key in (
            "stock_master",
            "official_exact_date_ohlcv",
            "twse_valuation",
            "tpex_valuation",
            "technical_snapshots",
            "official_institution_activity",
            "official_credit_balances",
            "estimated_institution_cost",
        )
    }
    expected_report_counts = {
        "institution_activity": int(
            (official_report.get("official_institution_activity") or {}).get("row_count")
            or 0
        ),
        "credit_balances": int(
            (official_report.get("official_credit_balances") or {}).get("row_count")
            or 0
        ),
        "official_valuations": int(
            (official_report.get("twse_valuation") or {}).get("row_count")
            or 0
        ) + int(
            (official_report.get("tpex_valuation") or {}).get("rows_written")
            or 0
        ),
        "estimated_chip_costs": int(
            (official_report.get("estimated_institution_cost") or {}).get(
                "target_traded_codes"
            )
            or 0
        ),
    }
    gates = {
        "sqlite_quick_check": integrity == ["ok"],
        "official_report_date": official_report.get("effective_trade_date") == trade_date,
        "official_rows_classified": bool(official_codes) and not unclassified_codes,
        "derived_rows_match_official_codes": all(item["complete"] for item in coverage.values()),
        "close_batch_matches_component_codes": close_coverage["complete"],
        "official_core_ready": bool(official_report.get("official_core_ready")),
        "official_components_ready": all(official_components.values()),
        "official_ohlcv_complete": _publication_complete(current_publication),
        "previous_trading_day_continuity": _publication_complete(
            previous_publication
        ),
        "technical_vectors_complete": (
            required_trading_stocks > 0
            and metrics["technical_vectors"] >= required_trading_stocks
        ),
        "technical_snapshots_complete": (
            required_trading_stocks > 0
            and metrics["technical_snapshots"] >= required_trading_stocks
        ),
        "institution_activity_persisted": (
            expected_report_counts["institution_activity"] > 0
            and metrics["institution_activity"]
            >= expected_report_counts["institution_activity"]
        ),
        "credit_and_lending_balances_persisted": (
            expected_report_counts["credit_balances"] > 0
            and metrics["credit_balances"] >= expected_report_counts["credit_balances"]
        ),
        "official_valuations_persisted": (
            expected_report_counts["official_valuations"] > 0
            and metrics["official_valuations"]
            >= expected_report_counts["official_valuations"]
        ),
        "estimated_chip_costs_persisted": (
            expected_report_counts["estimated_chip_costs"] > 0
            and metrics["estimated_chip_costs"]
            >= expected_report_counts["estimated_chip_costs"]
        ),
        "price_volume_capture_ready": bool(
            official_report.get("effective_trade_date") == trade_date
            and official_report.get("price_volume_capture_ready")
        ),
        "price_volume_verified_date": bool(
            official_report.get("effective_trade_date") == trade_date
            and (
                official_report.get("official_exact_date_ohlcv") or {}
            ).get("trade_date") == trade_date
        ),
        "price_volume_profiles_complete": (
            required_trading_stocks > 0
            and metrics["price_volume_profiles"] >= required_trading_stocks
            and metrics["price_volume_validated"] >= required_trading_stocks
        ),
        "inner_outer_volume_complete": (
            required_trading_stocks > 0
            and metrics["inner_outer_volume"] >= required_trading_stocks
        ),
        "daily_chip_momentum_complete": (
            expected_chip_codes > 0
            and metrics["daily_chip_momentum"] >= expected_chip_codes
        ),
        "taiwan50_close_batch_complete": (
            metrics["taiwan50_close_batch"] == len(component_codes)
            and len(component_codes) >= 50
            and close_batch_errors == 0
        ),
        "tdcc_weekly_fresh": (
            tdcc_entities > 0
            and _days_old(tdcc_latest, trade_date) is not None
            and 0 <= int(_days_old(tdcc_latest, trade_date) or 0) <= 14
        ),
    }
    supplemental_sources = {
        key: official_report.get(key) or {} for key in (
            "global_market_snapshot", "taifex_night_snapshot",
            "official_company_events", "official_trading_restrictions")
    }
    supplemental_sources["external_events"] = external_report
    gates["supplemental_sources_complete"] = bool(
        gates["official_report_date"]
        and all(_source_ready(item) for item in supplemental_sources.values())
        and external_report.get("requested_trade_date") == trade_date
        and _configured_feeds_ready(external_report)
    )
    daily_update_complete = all(gates.values())
    exact_report = official_report.get("official_exact_date_ohlcv") or {}
    exact_source_results = list(exact_report.get("source_results") or [])
    safe_publish_gates = {
        "sqlite_quick_check": gates["sqlite_quick_check"],
        "official_report_date": gates["official_report_date"],
        "official_exact_rows_storage_qualified": bool(
            exact_report.get("storage_allowed")
            and exact_report.get("trade_date") == trade_date
            and exact_source_results
            and all(
                item.get("ok")
                and item.get("storage_qualified")
                and item.get("data_date") == trade_date
                for item in exact_source_results
            )
        ),
        "official_ohlcv_present": metrics["official_ohlcv"] > 0,
        "derived_rows_match_official_codes": gates["derived_rows_match_official_codes"],
        "close_batch_matches_component_codes": close_coverage["complete"],
        "technical_vectors_cover_official_rows": (
            metrics["technical_vectors"] >= metrics["official_ohlcv"]
        ),
        "technical_snapshots_cover_official_rows": (
            metrics["technical_snapshots"] >= metrics["official_ohlcv"]
        ),
        "institution_activity_persisted": gates["institution_activity_persisted"],
        "credit_and_lending_balances_persisted": gates[
            "credit_and_lending_balances_persisted"
        ],
        "official_valuations_persisted": gates["official_valuations_persisted"],
        "estimated_chip_costs_persisted": gates["estimated_chip_costs_persisted"],
        "validated_price_volume_covers_official_rows": (
            metrics["price_volume_validated"] >= metrics["official_ohlcv"]
        ),
        "price_volume_profiles_cover_official_rows": (
            metrics["price_volume_profiles"] >= metrics["official_ohlcv"]
        ),
        "inner_outer_volume_covers_official_rows": (
            metrics["inner_outer_volume"] >= metrics["official_ohlcv"]
        ),
        "daily_chip_covers_official_rows": (
            metrics["daily_chip_momentum"] >= metrics["official_ohlcv"]
        ),
        "taiwan50_close_batch_complete": gates["taiwan50_close_batch_complete"],
        "tdcc_weekly_fresh": gates["tdcc_weekly_fresh"],
    }
    safe_to_publish = all(safe_publish_gates.values())
    return {
        "database": str(resolved),
        "trade_date": trade_date,
        "daily_update_complete": daily_update_complete,
        "safe_to_publish": safe_to_publish,
        "publication_status": (
            "complete"
            if daily_update_complete
            else "source_incomplete_but_storage_verified"
            if safe_to_publish
            else "not_publishable"
        ),
        "analysis_scoring_ready": bool(
            daily_update_complete and official_report.get("price_volume_scoring_ready")
        ),
        "price_volume_scoring_ready": bool(
            gates["official_report_date"] and official_report.get("price_volume_scoring_ready")),
        "price_volume_scoring_note": (
            "today's capture can be complete while the multi-day 80% history gate remains pending"
        ),
        "universe": {
            "active_stocks": active_stocks,
            "no_trade_stocks": no_trade_stocks,
            "required_trading_stocks": required_trading_stocks,
            "previous_trade_date": previous_trade_date,
            "previous_no_trade_stocks": previous_no_trade_stocks,
            "previous_required_trading_stocks": previous_required_trading_stocks,
            "taiwan50_components": len(component_codes),
            "watchlist_codes": watchlist_count,
            "expected_daily_chip_codes": expected_chip_codes,
        },
        "full_market_batch_publications": {
            "current": current_publication,
            "previous": previous_publication,
        },
        "metrics": metrics,
        "unclassified_codes": unclassified_codes,
        "coverage_by_official_code": coverage,
        "supplemental_sources": supplemental_sources,
        "close_batch_errors": close_batch_errors,
        "tdcc": {
            "frequency": "weekly",
            "latest_date": tdcc_latest,
            "distinct_entities": tdcc_entities,
            "days_old_at_trade_date": _days_old(tdcc_latest, trade_date),
        },
        "official_components": official_components,
        "expected_counts_from_official_report": expected_report_counts,
        "gates": gates,
        "failed_gates": [name for name, passed in gates.items() if not passed],
        "safe_publish_gates": safe_publish_gates,
        "failed_safe_publish_gates": [
            name for name, passed in safe_publish_gates.items() if not passed
        ],
        "integrity": integrity,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DB_PATH)
    parser.add_argument("--date", default=recent_market_date_for_post_close())
    parser.add_argument("--official-report", type=Path, default=DEFAULT_OFFICIAL_REPORT)
    parser.add_argument("--post-close-report", type=Path, default=DEFAULT_POST_CLOSE_REPORT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    report = build_daily_analysis_report(
        args.database,
        trade_date=args.date,
        official_report_path=args.official_report,
        post_close_report_path=args.post_close_report,
    )
    output = args.output if args.output.is_absolute() else ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(output)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0 if report["daily_update_complete"] else 4


if __name__ == "__main__":
    raise SystemExit(main())
