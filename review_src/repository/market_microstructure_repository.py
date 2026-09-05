from __future__ import annotations

import sqlite3
from contextlib import closing, nullcontext
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from core.market_database_config import resolve_market_db_path
from core.database_access import connect
from core.market_timing import (
    analysis_cutoff_for_reference,
    available_at_or_before_cutoff,
    parse_market_timestamp,
)

from repository.external_event_repository import (
    read_general_external_market_events,
    read_stock_external_market_events,
)
from repository.global_market_repository import read_latest_global_market_rows
from repository.news_radar_repository import read_stock_news_radar_events
from repository.company_size_repository import read_company_size_context
from repository.full_market_batch_repository import resolve_full_market_analysis_date
from repository.estimated_chip_cost_repository import read_canonical_estimated_cost_rows
from repository.corporate_action_repository import (
    read_verified_corporate_actions_at_cutoff,
)
from repository.trading_restriction_repository import read_trading_restriction_context


REVIEW_SRC = Path(__file__).resolve().parents[1]


def _database_path() -> Path:
    return resolve_market_db_path(base_dir=REVIEW_SRC)


def read_only_db() -> sqlite3.Connection:
    """Open the configured existing database without importing write-capable bootstrap code."""

    target = _database_path().resolve()
    conn = connect(
        target,
        readonly=True,
        check_same_thread=False,
        timeout=3,
    )
    conn.enable_load_extension(False)
    conn.execute("PRAGMA busy_timeout=3000")
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA trusted_schema=OFF")
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
    )


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _corporate_action_context(
    conn: sqlite3.Connection,
    *,
    code: str,
    reference_date: str,
    history_dates: list[str],
    analysis_cutoff: str | None = None,
) -> dict[str, Any]:
    legacy_ready = _table_exists(conn, "corporate_actions")
    permanent_ready = _table_exists(conn, "corporate_action_permanent_event")
    if not legacy_ready and not permanent_ready:
        return {"ready": False, "active_window": False, "status": "unavailable"}
    try:
        reference = date.fromisoformat(reference_date)
    except ValueError:
        return {"ready": False, "active_window": False, "status": "invalid_date"}
    start = (reference - timedelta(days=14)).isoformat()
    end = (reference + timedelta(days=14)).isoformat()
    rows = (
        [
            dict(row)
            for row in conn.execute(
                """
                SELECT code,date,action_type,is_confirmed,source,updated_at
                FROM corporate_actions
                WHERE code=? AND date BETWEEN ? AND ?
                ORDER BY is_confirmed DESC,date
                """,
                (code, start, end),
            ).fetchall()
        ]
        if legacy_ready
        else []
    )
    permanent_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    if analysis_cutoff and permanent_ready:
        permanent_rows = read_verified_corporate_actions_at_cutoff(
            conn,
            code=code,
            analysis_cutoff=analysis_cutoff,
            effective_on_or_after=start,
            effective_on_or_before=end,
        )
        permanent_by_key = {
            (str(row.get("effective_date") or ""), str(row.get("action_type") or "")): row
            for row in permanent_rows
        }
        legacy_keys = {
            (str(row.get("date") or ""), str(row.get("action_type") or ""))
            for row in rows
        }
        for key, permanent in permanent_by_key.items():
            if key in legacy_keys:
                continue
            rows.append(
                {
                    "code": code,
                    "date": permanent.get("effective_date"),
                    "action_type": permanent.get("action_type"),
                    "is_confirmed": 1,
                    "source": f"PERMANENT:{permanent.get('source_id')}",
                    "updated_at": permanent.get("verified_at"),
                }
            )
    ordered_dates = sorted(
        {str(item) for item in history_dates if str(item) and str(item) <= reference_date}
    )
    candidates: list[dict[str, Any]] = []
    for row in rows:
        action_date = str(row.get("date") or "")
        action_type = str(row.get("action_type") or "")
        permanent = permanent_by_key.get((action_date, action_type))
        if str(row.get("source") or "").startswith("PERMANENT:") and permanent is None:
            # A permanent projection is only visible once its typed authority row
            # satisfies the point-in-time cutoff.
            continue
        if analysis_cutoff and not available_at_or_before_cutoff(
            row.get("updated_at"),
            analysis_cutoff,
        ):
            # Legacy rows without an offset-aware availability timestamp are
            # not safe for historical point-in-time analysis.  Fail closed
            # instead of letting an undated later import leak backwards.
            continue
        try:
            action = date.fromisoformat(action_date)
        except ValueError:
            continue
        if action <= reference:
            days_from_action = sum(action_date < item <= reference_date for item in ordered_dates)
        else:
            days_from_action = -(action - reference).days
        if -3 <= days_from_action <= 5:
            candidate = {**row, "days_from_action": days_from_action}
            if permanent is not None:
                candidate.update(
                    {
                        "adjustment_method": permanent.get("adjustment_method"),
                        "stock_distribution_ratio": permanent.get(
                            "stock_distribution_ratio"
                        ),
                        "ratio_unit": permanent.get("ratio_unit"),
                        "cash_dividend_per_share": permanent.get(
                            "cash_dividend_per_share"
                        ),
                        "share_count_factor": permanent.get("share_count_factor"),
                        "pre_event_price_multiplier": permanent.get(
                            "pre_event_price_multiplier"
                        ),
                        "verification_status": permanent.get("verification_status"),
                        "source_id": permanent.get("source_id"),
                        "source_url": permanent.get("source_url"),
                        "available_at": permanent.get("available_at"),
                        "directional_weight_eligible": bool(
                            permanent.get("directional_weight_eligible")
                        ),
                    }
                )
            candidates.append(candidate)
    if not candidates:
        return {"ready": True, "active_window": False, "status": "clear"}
    selected = min(
        candidates,
        key=lambda row: (
            abs(int(row.get("days_from_action") or 0)),
            -int(row.get("is_confirmed") or 0),
        ),
    )
    offset = int(selected.get("days_from_action") or 0)
    if offset < 0:
        label = "即將除權息或公司行動，價格基準將調整"
    elif offset == 0:
        selected_action_date = date.fromisoformat(str(selected.get("date")))
        label = (
            "除權息後尚無完整新價格基準，技術指標需重新累積"
            if reference > selected_action_date
            else "本交易日為除權息或公司行動日，技術指標需重新累積"
        )
    else:
        label = "近期除權息或公司行動，技術指標仍在調整期間"
    return {
        "ready": True,
        "active_window": True,
        "status": "active_window",
        "action_date": selected.get("date"),
        "action_type": selected.get("action_type"),
        "days_from_action": offset,
        "confirmed": bool(selected.get("is_confirmed")),
        "label": label,
        "adjustment_method": selected.get("adjustment_method"),
        "stock_distribution_ratio": selected.get("stock_distribution_ratio"),
        "ratio_unit": selected.get("ratio_unit"),
        "cash_dividend_per_share": selected.get("cash_dividend_per_share"),
        "share_count_factor": selected.get("share_count_factor"),
        "pre_event_price_multiplier": selected.get("pre_event_price_multiplier"),
        "verification_status": selected.get("verification_status"),
        "source_id": selected.get("source_id"),
        "source_url": selected.get("source_url"),
        "available_at": selected.get("available_at"),
        "directional_weight_eligible": bool(
            selected.get("directional_weight_eligible")
        ),
    }


def read_active_stock_master_rows(connection: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    """Return the active official stock universe for read-only name/code resolution."""

    with (nullcontext(connection) if connection is not None else closing(read_only_db())) as conn:
        if connection is None:
            conn.execute("BEGIN")
        if not _table_exists(conn, "stock_master"):
            return []
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT code,name,market,exchange
                FROM stock_master
                WHERE is_active=1 AND security_type='stock'
                ORDER BY code
                """
            ).fetchall()
        ]
        valuation_columns = _table_columns(conn, "twse_daily_valuation")
        required_columns = {"symbol", "name", "data_date", "source_status"}
        if required_columns.issubset(valuation_columns):
            trading_names = {
                str(row["symbol"]): str(row["name"] or "").strip()
                for row in conn.execute(
                    """
                    SELECT valuation.symbol,valuation.name
                    FROM twse_daily_valuation AS valuation
                    INNER JOIN (
                        SELECT symbol,MAX(data_date) AS latest_date
                        FROM twse_daily_valuation
                        WHERE LOWER(COALESCE(source_status,''))='ok'
                          AND TRIM(COALESCE(name,''))<>''
                        GROUP BY symbol
                    ) AS latest
                      ON latest.symbol=valuation.symbol
                     AND latest.latest_date=valuation.data_date
                    WHERE LOWER(COALESCE(valuation.source_status,''))='ok'
                      AND TRIM(COALESCE(valuation.name,''))<>''
                    """
                ).fetchall()
            }
            for row in rows:
                row["trading_name"] = trading_names.get(str(row.get("code") or ""))
        return rows


def read_general_market_context(reference_date: str) -> dict[str, Any]:
    """Read a date-bounded market context for non-symbol LINE questions."""

    with closing(read_only_db()) as conn:
        conn.execute("BEGIN")
        global_rows = (
            read_latest_global_market_rows(conn, reference_date=reference_date)
            if _table_exists(conn, "global_market_daily_snapshot")
            else []
        )
        event_rows = (
            read_general_external_market_events(
                conn,
                reference_date=reference_date,
                lookback_days=21,
                limit=100,
            )
            if _table_exists(conn, "external_market_event")
            else []
        )
        return {
            "reference_date": reference_date,
            "global_market_rows": global_rows,
            "external_event_rows": event_rows,
        }


def read_screening_prefilter_rows(*, limit: int = 300) -> dict[str, Any]:
    """Read a liquid, latest-date technical universe for read-only screening.

    This is only a compute-bounding prefilter.  It never produces the user-facing
    verdict; the service must run every shortlisted stock through the shared
    referee before returning it as a candidate.
    """

    bounded_limit = max(20, min(int(limit), 1000))
    with closing(read_only_db()) as conn:
        conn.execute("BEGIN")
        required_tables = {
            "stock_master",
            "history_price",
            "daily_technical_snapshot",
        }
        if not all(_table_exists(conn, table) for table in required_tables):
            return {"trade_date": None, "rows": []}
        history_columns = _table_columns(conn, "history_price")
        amount_sql = (
            "CASE WHEN h.amount IS NOT NULL AND h.amount>0 THEN h.amount "
            "ELSE h.close*h.volume END"
            if "amount" in history_columns
            else "h.close*h.volume"
        )
        trade_date = str(resolve_full_market_analysis_date(conn) or "")
        if not trade_date:
            return {"trade_date": None, "rows": []}
        rows = conn.execute(
            f"""
            SELECT
                sm.code,sm.name,sm.market,h.close,h.volume,
                {amount_sql} AS turnover_value,
                t.ma20,t.ma60,t.rsi14,t.macd_osc,t.volume_ma20,
                t.previous_20d_low,t.previous_20d_high,
                (
                    SELECT previous.rsi14
                    FROM daily_technical_snapshot AS previous
                    WHERE previous.code=t.code
                      AND previous.trade_date<t.trade_date
                      AND previous.decision_ready=1
                    ORDER BY previous.trade_date DESC
                    LIMIT 1
                ) AS previous_rsi14,
                (
                    SELECT previous.macd_osc
                    FROM daily_technical_snapshot AS previous
                    WHERE previous.code=t.code
                      AND previous.trade_date<t.trade_date
                      AND previous.decision_ready=1
                    ORDER BY previous.trade_date DESC
                    LIMIT 1
                ) AS previous_macd_osc
            FROM daily_technical_snapshot AS t
            INNER JOIN stock_master AS sm
              ON sm.code=t.code
             AND sm.is_active=1
             AND sm.security_type='stock'
            INNER JOIN history_price AS h
              ON h.code=t.code AND h.date=t.trade_date
            WHERE t.trade_date=?
              AND t.decision_ready=1
              AND h.close>0
              AND h.volume>0
              AND UPPER(COALESCE(h.source_quality,'')) IN ('OFFICIAL','OK','HIGH')
              AND (
                    UPPER(COALESCE(h.source,'')) LIKE 'TWSE%'
                 OR UPPER(COALESCE(h.source,'')) LIKE 'TPEX%'
              )
            ORDER BY turnover_value DESC,sm.code
            LIMIT ?
            """,
            (trade_date, bounded_limit),
        ).fetchall()
        return {"trade_date": trade_date, "rows": [dict(row) for row in rows]}


def read_daily_market_microstructure(
    code: str,
    trade_date: str | None = None,
    *,
    analysis_cutoff: str | None = None,
) -> dict[str, Any]:
    """Read one date-consistent OHLCV/distribution/score snapshot."""

    with closing(read_only_db()) as conn:
        conn.execute("BEGIN")
        if not _table_exists(conn, "history_price"):
            return {"history": None, "distribution": [], "profile": None, "score": None}
        history_columns = _table_columns(conn, "history_price")
        history_amount = "amount" if "amount" in history_columns else "NULL AS amount"
        history_market = "market" if "market" in history_columns else "NULL AS market"
        no_trade = None
        publication_date = resolve_full_market_analysis_date(conn)
        selected_date = resolve_full_market_analysis_date(conn, trade_date)
        explicit_cutoff = parse_market_timestamp(analysis_cutoff)
        event_reference_date = (
            explicit_cutoff.date().isoformat()
            if explicit_cutoff is not None
            else selected_date
            if trade_date
            else date.today().isoformat()
        )
        effective_analysis_cutoff = (
            analysis_cutoff_for_reference(
                event_reference_date,
                explicit_cutoff=analysis_cutoff,
            )
            if event_reference_date
            else None
        )
        if selected_date:
            history = conn.execute(
                f"""
                SELECT date,code,open,high,low,close,volume,{history_amount},
                       source,source_quality,{history_market}
                FROM history_price
                WHERE code=? AND date=?
                LIMIT 1
                """,
                (code, selected_date),
            ).fetchone()
        else:
            history = None
        if selected_date and _table_exists(conn, "stock_no_trade_dates"):
            row = conn.execute(
                """
                SELECT trade_date,code,market,reason,source,source_url,
                       source_quality,evidence_json,verified_at
                FROM stock_no_trade_dates
                WHERE code=? AND trade_date=?
                LIMIT 1
                """,
                (code, selected_date),
            ).fetchone()
            no_trade = dict(row) if row else None

        intraday_quote = None
        if not trade_date and _table_exists(conn, "intraday_quote_snapshot"):
            row = conn.execute(
                """
                SELECT stock_id,quote_time,price,open,high,low,close,volume,
                       turnover,vwap,change,change_pct,source,last_updated_phase,
                       data_status,updated_at
                FROM intraday_quote_snapshot
                WHERE stock_id=?
                LIMIT 1
                """,
                (code,),
            ).fetchone()
            intraday_quote = dict(row) if row else None

        technical = None
        if selected_date and _table_exists(conn, "daily_technical_snapshot"):
            row = conn.execute(
                """
                SELECT *
                FROM daily_technical_snapshot
                WHERE code=? AND trade_date=?
                LIMIT 1
                """,
                (code, selected_date),
            ).fetchone()
            technical = dict(row) if row else None

        recent_referee_context: list[dict[str, Any]] = []
        referee_history: list[dict[str, Any]] = []
        referee_component_dates = {"institution": None, "margin": None}
        institution_rows: list[dict[str, Any]] = []
        estimated_cost_rows: list[dict[str, Any]] = []
        persisted_outlook: dict[str, Any] | None = None
        global_market_rows: list[dict[str, Any]] = []
        taifex_night_rows: list[dict[str, Any]] = []
        official_event_rows: list[dict[str, Any]] = []
        external_event_rows: list[dict[str, Any]] = []
        news_radar_rows: list[dict[str, Any]] = []
        trading_restriction_context: dict[str, Any] = {
            "ready": False,
            "status": "unavailable",
            "items": [],
        }
        corporate_action_context: dict[str, Any] = {
            "ready": False,
            "active_window": False,
            "status": "unavailable",
        }
        company_size_context: dict[str, Any] = {
            "ready": False,
            "status": "unavailable",
        }
        if selected_date:
            technical_join = ""
            technical_select = """
                NULL AS ma20,
                NULL AS rsi14,
                NULL AS macd_osc,
                NULL AS technical_decision_ready,
                NULL AS technical_data_quality
            """
            if _table_exists(conn, "daily_technical_snapshot"):
                technical_join = (
                    "LEFT JOIN daily_technical_snapshot AS tech "
                    "ON tech.code=history.code AND tech.trade_date=history.date"
                )
                technical_select = """
                    tech.ma20 AS ma20,
                    tech.rsi14 AS rsi14,
                    tech.macd_osc AS macd_osc,
                    tech.decision_ready AS technical_decision_ready,
                    tech.data_quality AS technical_data_quality
                """
            recent_referee_context = [
                dict(row)
                for row in conn.execute(
                    f"""
                    SELECT history.date,history.open,history.high,history.low,
                           history.close,history.volume,
                           history.source,history.source_quality,
                           {technical_select}
                    FROM history_price AS history
                    {technical_join}
                    WHERE history.code=? AND history.date<=?
                    ORDER BY history.date DESC
                    LIMIT 20
                    """,
                    (code, selected_date),
                ).fetchall()
            ]
            volume_unit = (
                "history.volume_unit"
                if "volume_unit" in history_columns
                else "'shares' AS volume_unit"
            )
            referee_amount = (
                "history.amount AS amount"
                if "amount" in history_columns
                else "NULL AS amount"
            )
            referee_history = list(
                reversed(
                    [
                        dict(row)
                        for row in conn.execute(
                            f"""
                            SELECT history.date,history.code,history.open,history.high,
                                    history.low,history.close,history.volume,{volume_unit},
                                    {referee_amount},
                                    history.source,history.source_quality
                            FROM history_price AS history
                            WHERE history.code=? AND history.date<=?
                            ORDER BY history.date DESC
                            LIMIT 160
                            """,
                            (code, selected_date),
                        ).fetchall()
                    ]
                )
            )
            for component, table in (
                ("institution", "institution_daily"),
                ("margin", "margin_daily"),
            ):
                if _table_exists(conn, table):
                    row = conn.execute(
                        f"SELECT MAX(date) AS latest_date FROM {table} WHERE code=? AND date<=?",
                        (code, selected_date),
                    ).fetchone()
                    referee_component_dates[component] = (
                        str(row["latest_date"]) if row and row["latest_date"] else None
                    )

            if _table_exists(conn, "institution_activity_daily"):
                institution_rows = [
                    dict(row)
                    for row in conn.execute(
                        """
                        SELECT trade_date AS date,
                               foreign_buy,foreign_sell,foreign_net,
                               trust_buy,trust_sell,trust_net,
                               dealer_buy,dealer_sell,dealer_net,
                               source,source_quality
                        FROM institution_activity_daily
                        WHERE code=? AND trade_date<=?
                        ORDER BY trade_date DESC
                        LIMIT 5
                        """,
                        (code, selected_date),
                    ).fetchall()
                ]
            elif _table_exists(conn, "institution_daily"):
                institution_rows = [
                    dict(row)
                    for row in conn.execute(
                        """
                        SELECT date,foreign_net,trust_net,dealer_net,
                               source,source_quality
                        FROM institution_daily
                        WHERE code=? AND date<=?
                        ORDER BY date DESC
                        LIMIT 5
                        """,
                        (code, selected_date),
                    ).fetchall()
                ]

            if _table_exists(conn, "estimated_chip_cost_daily"):
                estimated_cost_rows = read_canonical_estimated_cost_rows(
                    conn,
                    code=code,
                    trade_date=selected_date,
                )

            if _table_exists(conn, "taifex_night_daily_snapshot"):
                latest_night_date = conn.execute(
                    "SELECT MAX(trade_date) AS latest_date FROM taifex_night_daily_snapshot"
                ).fetchone()
                if latest_night_date and latest_night_date["latest_date"]:
                    taifex_night_rows = [
                        dict(row)
                        for row in conn.execute(
                            """
                            SELECT trade_date,contract,contract_month,last,change_pct,
                                   volume,trading_session,source,source_quality,fetched_at
                            FROM taifex_night_daily_snapshot
                            WHERE trade_date=?
                            ORDER BY contract
                            """,
                            (str(latest_night_date["latest_date"]),),
                        ).fetchall()
                    ]

            if _table_exists(conn, "official_company_event"):
                official_event_rows = [
                    dict(row)
                    for row in conn.execute(
                        """
                        SELECT disclosed_date,disclosed_time,fact_date,subject,
                               explanation,article_code,attention_level,
                               source,source_quality,fetched_at,available_at,
                               market_session,effective_tw_trade_date
                        FROM official_company_event
                        WHERE code=?
                          AND disclosed_date<=?
                          AND available_at IS NOT NULL
                          AND datetime(available_at)<=datetime(?)
                        ORDER BY disclosed_date DESC, disclosed_time DESC
                        LIMIT 5
                        """,
                        (code, event_reference_date, effective_analysis_cutoff),
                    ).fetchall()
                ]

            if _table_exists(conn, "next_day_outlook_daily"):
                row = conn.execute(
                    """
                    SELECT calc_date,generated_at,model_version,label,confidence,
                           us_score,night_score,payload_json
                    FROM next_day_outlook_daily
                    WHERE code=? AND calc_date<=?
                    ORDER BY calc_date DESC
                    LIMIT 1
                    """,
                    (code, selected_date),
                ).fetchone()
                persisted_outlook = dict(row) if row else None

            if _table_exists(conn, "global_market_daily_snapshot"):
                latest_global_date = conn.execute(
                    """
                    SELECT MAX(market_date) AS latest_date
                    FROM global_market_daily_snapshot
                    WHERE market_date<=?
                    """,
                    (selected_date,),
                ).fetchone()
                if latest_global_date and latest_global_date["latest_date"]:
                    global_market_rows = [
                        dict(row)
                        for row in conn.execute(
                            """
                            SELECT market_date,ticker,display_name,close,previous_close,
                                   change_pct,currency,source,source_quality,fetched_at
                            FROM global_market_daily_snapshot
                            WHERE market_date=?
                            ORDER BY ticker
                            """,
                            (str(latest_global_date["latest_date"]),),
                        ).fetchall()
                    ]

        valuation = None
        if selected_date and _table_exists(conn, "twse_daily_valuation"):
            row = conn.execute(
                """
                SELECT data_date AS trade_date,symbol AS code,close_price,
                       dividend_yield,pe_ratio,pb_ratio,source,source_status
                FROM twse_daily_valuation
                WHERE symbol=? AND data_date=?
                LIMIT 1
                """,
                (code, selected_date),
            ).fetchone()
            valuation = dict(row) if row else None
        if valuation is None and selected_date and _table_exists(conn, "valuation"):
            row = conn.execute(
                """
                SELECT date AS trade_date,code,NULL AS close_price,
                       dividend_yield,pe AS pe_ratio,pb AS pb_ratio,
                       source,'legacy' AS source_status
                FROM valuation
                WHERE code=? AND date=?
                LIMIT 1
                """,
                (code, selected_date),
            ).fetchone()
            valuation = dict(row) if row else None

        stock = None
        if _table_exists(conn, "stock_master"):
            row = conn.execute(
                """
                SELECT code,name,market,exchange,security_type,is_active,
                       source,source_status,last_seen_date
                FROM stock_master
                WHERE code=?
                LIMIT 1
                """,
                (code,),
            ).fetchone()
            stock = dict(row) if row else None
            if stock and _table_exists(conn, "stock_industry_profile"):
                industry_row = conn.execute(
                    """
                    SELECT industry,industry_code,source,quality
                    FROM stock_industry_profile
                    WHERE code=? AND quality='ok'
                    LIMIT 1
                    """,
                    (code,),
                ).fetchone()
                if industry_row:
                    stock.update(
                        {
                            "industry": industry_row["industry"],
                            "industry_code": industry_row["industry_code"],
                            "industry_source": industry_row["source"],
                            "industry_quality": industry_row["quality"],
                        }
                    )

        if stock:
            stock_terms = {
                str(stock.get("name") or "").strip(),
                str(stock.get("industry") or "").strip(),
            }
            if _table_exists(conn, "stock_theme_profile"):
                stock_terms.update(
                    str(row["tag_name"] or "").strip()
                    for row in conn.execute(
                        """
                        SELECT tag_name
                        FROM stock_theme_profile
                        WHERE code=? AND quality='ok'
                        """,
                        (code,),
                    ).fetchall()
                )
            if _table_exists(conn, "external_market_event") and event_reference_date:
                external_event_rows = read_stock_external_market_events(
                    conn,
                    code=code,
                    reference_date=event_reference_date,
                    stock_terms=stock_terms,
                    lookback_days=60,
                    limit=20,
                    analysis_cutoff=effective_analysis_cutoff,
                )
            if _table_exists(conn, "news_radar_event") and event_reference_date:
                news_radar_rows = read_stock_news_radar_events(
                    conn,
                    reference_date=event_reference_date,
                    stock_terms=stock_terms,
                    lookback_days=3,
                    limit=8,
                    analysis_cutoff=effective_analysis_cutoff,
                )

            if selected_date:
                history_record = dict(history) if history else {}
                market = str((stock or {}).get("market") or history_record.get("market") or "")
            trading_restriction_context = read_trading_restriction_context(
                conn,
                code=code,
                market=market,
                reference_date=selected_date,
            )
            corporate_action_context = _corporate_action_context(
                conn,
                code=code,
                # Price/technical facts remain anchored to selected_date, but
                # corporate-action recency is relative to the analysis request
                # cutoff.  This keeps current Web and LINE semantics aligned
                # while preserving true historical cutoffs.
                reference_date=event_reference_date,
                history_dates=[str(row.get("date") or "") for row in referee_history],
                analysis_cutoff=effective_analysis_cutoff,
            )
            company_size_context = read_company_size_context(
                conn,
                code=code,
                reference_date=selected_date,
            )

        distribution: list[dict[str, Any]] = []
        if selected_date and _table_exists(conn, "price_volume_distribution"):
            columns = _table_columns(conn, "price_volume_distribution")

            def expr(name: str) -> str:
                return name if name in columns else f"NULL AS {name}"

            distribution = [
                dict(row)
                for row in conn.execute(
                    f"""
                    SELECT price,volume_lots,{expr('volume_shares')},
                           {expr('total_volume_lots')},{expr('volume_at_bid')},
                           {expr('volume_at_ask')},{expr('neutral_volume_lots')},
                           {expr('source')},{expr('data_quality')},
                           {expr('source_quality')},{expr('snapshot_time')},
                           {expr('fetched_at')}
                    FROM price_volume_distribution
                    WHERE stock_id=? AND trade_date=?
                    ORDER BY price DESC
                    """,
                    (code, selected_date),
                ).fetchall()
            ]

        profile = None
        if selected_date and _table_exists(conn, "price_volume_profile_daily"):
            row = conn.execute(
                "SELECT * FROM price_volume_profile_daily WHERE code=? AND date=? LIMIT 1",
                (code, selected_date),
            ).fetchone()
            profile = dict(row) if row else None

        score = None
        if selected_date and _table_exists(conn, "price_volume_score_daily"):
            row = conn.execute(
                "SELECT * FROM price_volume_score_daily WHERE code=? AND date=? LIMIT 1",
                (code, selected_date),
            ).fetchone()
            score = dict(row) if row else None

        capture = None
        if selected_date and _table_exists(conn, "fugle_intraday_capture_runs"):
            capture_columns = _table_columns(conn, "fugle_intraday_capture_runs")

            def capture_expr(name: str) -> str:
                return name if name in capture_columns else f"NULL AS {name}"

            row = conn.execute(
                f"""
                SELECT {capture_expr('code')},{capture_expr('trade_date')},
                       {capture_expr('endpoint')},{capture_expr('source')},
                       {capture_expr('snapshot_time')},{capture_expr('page_count')},
                       {capture_expr('provider_row_count')},{capture_expr('normalized_row_count')},
                       {capture_expr('stored_row_count')},{capture_expr('capture_complete')},
                       {capture_expr('data_quality')},{capture_expr('reason')},
                       {capture_expr('latest_trade_time')},{capture_expr('latest_cumulative_volume')},
                       {capture_expr('captured_volume_lots')},{capture_expr('fetched_at')}
                FROM fugle_intraday_capture_runs
                WHERE code=? AND trade_date=? AND endpoint='trades' AND source='FUGLE'
                LIMIT 1
                """,
                (code, selected_date),
            ).fetchone()
            capture = dict(row) if row else None

        return {
            "history": dict(history) if history else None,
            "no_trade_evidence": no_trade,
            "selected_date": selected_date,
            "analysis_cutoff": effective_analysis_cutoff,
            "publication_date": publication_date,
            "intraday_quote": intraday_quote,
            "stock": stock,
            "technical": technical,
            "recent_referee_context": recent_referee_context,
            "referee_history": referee_history,
            "referee_component_dates": referee_component_dates,
            "institution_rows": institution_rows,
            "estimated_cost_rows": estimated_cost_rows,
            "persisted_outlook": persisted_outlook,
            "global_market_rows": global_market_rows,
            "taifex_night_rows": taifex_night_rows,
            "official_event_rows": official_event_rows,
            "external_event_rows": external_event_rows,
            "news_radar_rows": news_radar_rows,
            "trading_restriction_context": trading_restriction_context,
            "corporate_action_context": corporate_action_context,
            "company_size_context": company_size_context,
            "valuation": valuation,
            "distribution": distribution,
            "profile": profile,
            "score": score,
            "capture": capture,
        }


def read_daily_stock_history(
    code: str,
    *,
    date_from: str | None,
    date_to: str | None,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    """Read date-descending OHLCV with same-date technicals and valuation."""

    with closing(read_only_db()) as conn:
        conn.execute("BEGIN")
        if not _table_exists(conn, "history_price"):
            return {"rows": [], "total_rows": 0, "stock": None}
        filters = ["hp.code=?"]
        params: list[Any] = [code]
        effective_date_to = resolve_full_market_analysis_date(conn, date_to)
        if date_from:
            filters.append("hp.date>=?")
            params.append(date_from)
        if effective_date_to:
            filters.append("hp.date<=?")
            params.append(effective_date_to)
        elif not date_to:
            return {"rows": [], "total_rows": 0, "stock": None, "as_of_date": None}
        where_sql = " AND ".join(filters)
        history_columns = _table_columns(conn, "history_price")
        amount_expr = "hp.amount" if "amount" in history_columns else "NULL"
        market_expr = "hp.market" if "market" in history_columns else "NULL"

        technical_join = ""
        technical_select = ",".join(
            f"NULL AS {column}"
            for column in (
                "formula_version", "input_row_count", "rsi5", "rsi10", "rsi14",
                "ma5", "ma10", "ma20", "ma60", "macd_dif", "macd_signal",
                "macd_osc", "kd_k", "kd_d", "atr14", "boll_mid", "boll_upper",
                "boll_lower", "obv", "volume_ma20", "technical_data_quality",
                "technical_decision_ready", "technical_quality_reason",
            )
        )
        if _table_exists(conn, "daily_technical_snapshot"):
            technical_join = (
                "LEFT JOIN daily_technical_snapshot tech "
                "ON tech.code=hp.code AND tech.trade_date=hp.date"
            )
            technical_select = """
                tech.formula_version,tech.input_row_count,
                tech.rsi5,tech.rsi10,tech.rsi14,
                tech.ma5,tech.ma10,tech.ma20,tech.ma60,
                tech.macd_dif,tech.macd_signal,tech.macd_osc,
                tech.kd_k,tech.kd_d,tech.atr14,
                tech.boll_mid,tech.boll_upper,tech.boll_lower,
                tech.obv,tech.volume_ma20,
                tech.data_quality AS technical_data_quality,
                tech.decision_ready AS technical_decision_ready,
                tech.quality_reason AS technical_quality_reason
            """

        valuation_join = ""
        valuation_select = (
            "NULL AS pe_ratio,NULL AS pb_ratio,NULL AS dividend_yield,"
            "NULL AS valuation_source,NULL AS valuation_source_status"
        )
        if _table_exists(conn, "twse_daily_valuation"):
            valuation_join = (
                "LEFT JOIN twse_daily_valuation val "
                "ON val.symbol=hp.code AND val.data_date=hp.date"
            )
            valuation_select = (
                "val.pe_ratio,val.pb_ratio,val.dividend_yield,"
                "val.source AS valuation_source,val.source_status AS valuation_source_status"
            )
        elif _table_exists(conn, "valuation"):
            valuation_join = (
                "LEFT JOIN valuation val ON val.code=hp.code AND val.date=hp.date"
            )
            valuation_select = (
                "val.pe AS pe_ratio,val.pb AS pb_ratio,val.dividend_yield,"
                "val.source AS valuation_source,'legacy' AS valuation_source_status"
            )

        total = conn.execute(
            f"SELECT COUNT(*) AS c FROM history_price hp WHERE {where_sql}",
            params,
        ).fetchone()
        rows = [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT hp.date AS trade_date,hp.code,hp.open,hp.high,hp.low,hp.close,
                       hp.volume,{amount_expr} AS amount,{market_expr} AS market,
                       hp.source AS history_source,hp.source_quality AS history_source_quality,
                       {technical_select},{valuation_select}
                FROM history_price hp
                {technical_join}
                {valuation_join}
                WHERE {where_sql}
                ORDER BY hp.date DESC
                LIMIT ? OFFSET ?
                """,
                (*params, int(limit), int(offset)),
            ).fetchall()
        ]
        stock = None
        if _table_exists(conn, "stock_master"):
            row = conn.execute(
                "SELECT * FROM stock_master WHERE code=? LIMIT 1",
                (code,),
            ).fetchone()
            stock = dict(row) if row else None
        return {
            "rows": rows,
            "total_rows": int(total["c"] or 0) if total else 0,
            "stock": stock,
            "as_of_date": effective_date_to,
        }


def read_intraday_trade_page(
    code: str,
    trade_date: str | None,
    *,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    """Read captured trades only; never claims that the capture is complete."""

    with closing(read_only_db()) as conn:
        conn.execute("BEGIN")
        latest_history = None
        if _table_exists(conn, "history_price"):
            published_date = resolve_full_market_analysis_date(conn)
            row = (
                conn.execute(
                    """
                    SELECT date,volume,source,source_quality
                    FROM history_price
                    WHERE code=? AND date<=?
                    ORDER BY date DESC
                    LIMIT 1
                    """,
                    (code, published_date),
                ).fetchone()
                if published_date
                else None
            )
            latest_history = dict(row) if row else None
        if not _table_exists(conn, "fugle_intraday_trades"):
            return {
                "trade_date": trade_date,
                "rows": [],
                "total_rows": 0,
                "latest_history": latest_history,
            }
        selected_date = trade_date
        if not selected_date:
            row = conn.execute(
                "SELECT MAX(trade_date) AS trade_date FROM fugle_intraday_trades WHERE code=?",
                (code,),
            ).fetchone()
            selected_date = str(row["trade_date"] or "") if row else ""
        if not selected_date:
            return {
                "trade_date": None,
                "rows": [],
                "total_rows": 0,
                "latest_history": latest_history,
            }
        capture = None
        if _table_exists(conn, "fugle_intraday_capture_runs"):
            capture_row = conn.execute(
                """
                SELECT snapshot_time,page_count,provider_row_count,normalized_row_count,
                       stored_row_count,capture_complete,data_quality,latest_trade_time,
                       captured_volume_lots
                FROM fugle_intraday_capture_runs
                WHERE code=? AND trade_date=? AND endpoint='trades' AND source='FUGLE'
                LIMIT 1
                """,
                (code, selected_date),
            ).fetchone()
            capture = dict(capture_row) if capture_row else None
        total_row = conn.execute(
            "SELECT COUNT(*) AS c FROM fugle_intraday_trades WHERE code=? AND trade_date=?",
            (code, selected_date),
        ).fetchone()
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT trade_date,trade_time,price,size,volume,bid,ask,serial,
                       data_quality,side_inferred,side_label_zh,side_method,
                       side_confidence,prev_price,prev_price_source
                FROM fugle_intraday_trades
                WHERE code=? AND trade_date=?
                ORDER BY trade_time ASC, serial ASC
                LIMIT ? OFFSET ?
                """,
                (code, selected_date, int(limit), int(offset)),
            ).fetchall()
        ]
        return {
            "trade_date": selected_date,
            "rows": rows,
            "total_rows": int(total_row["c"] or 0) if total_row else 0,
            "latest_history": latest_history,
            "capture": capture,
        }


def codes_with_price_volume_distribution(codes: list[str], trade_date: str) -> list[str]:
    if not codes or not trade_date:
        return []
    placeholders = ",".join("?" for _ in codes)
    with closing(read_only_db()) as conn:
        conn.execute("BEGIN")
        if not _table_exists(conn, "price_volume_distribution"):
            return []
        rows = conn.execute(
            f"""
            SELECT DISTINCT stock_id
            FROM price_volume_distribution
            WHERE trade_date=? AND stock_id IN ({placeholders})
            ORDER BY stock_id
            """,
            [trade_date, *codes],
        ).fetchall()
        return [str(row["stock_id"]) for row in rows]
