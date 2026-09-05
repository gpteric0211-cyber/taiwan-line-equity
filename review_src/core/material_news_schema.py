"""Permanent material-event metadata; archive classification is not verification."""

from __future__ import annotations
import sqlite3

MATERIAL_EVENT_TERMS = (
    "除權",
    "除息",
    "減資",
    "股票分割",
    "分割股票",
    "合併",
    "下市",
    "終止上市",
    "停止交易",
    "停止買賣",
    "恢復買賣",
    "變更面額",
    "面額變更",
    "股利",
    "重大訊息",
)


def ensure_material_news_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS material_news_archive(
          event_key TEXT PRIMARY KEY, event_date TEXT NOT NULL,
          published_at TEXT NOT NULL, title TEXT NOT NULL, publisher TEXT NOT NULL,
          source_url TEXT NOT NULL, source_id TEXT NOT NULL,
          verification_status TEXT NOT NULL DEFAULT 'unverified',
          archive_reason TEXT NOT NULL, archived_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_material_news_date ON material_news_archive(event_date DESC)"
    )


def archive_material_news(conn: sqlite3.Connection, source_tables=None) -> int:
    ensure_material_news_schema(conn)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if source_tables is not None:
        tables.intersection_update(source_tables)
    queries = []
    predicate = " OR ".join("instr(title,?)>0" for _ in MATERIAL_EVENT_TERMS)
    if "news_radar_event" in tables:
        queries.append(
            (
                f"""SELECT event_key,event_date,published_at,title,publisher,source_url,
            source_id,verification_status,'material_event_keyword'
            FROM news_radar_event WHERE {predicate}""",
                MATERIAL_EVENT_TERMS,
            )
        )
    if "official_company_event" in tables:
        # This table contains MOPS material disclosures. Preserve their metadata;
        # the inherited rolling rule for the detailed source table is unchanged.
        queries.append(
            (
                """SELECT 'official:'||event_key,disclosed_date,
            disclosed_date||'T'||substr(COALESCE(disclosed_time,'000000'),1,2)||':'||
            substr(COALESCE(disclosed_time,'000000'),3,2)||':'||substr(COALESCE(disclosed_time,'000000'),5,2)||'+08:00',
            COALESCE(company_name,'')||'（'||code||'）'||subject,'公開資訊觀測站',
            CASE market WHEN 'listed' THEN 'https://openapi.twse.com.tw/v1/opendata/t187ap04_L'
            ELSE 'https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap04_O' END,
            source,CASE WHEN lower(source_quality)='official' THEN 'official_source' ELSE 'unverified' END,
            'official_material_disclosure' FROM official_company_event WHERE 1""",
                (),
            )
        )
    if "external_market_event" in tables:
        queries.append(
            (
                f"""SELECT 'external:'||event_key,event_date,COALESCE(published_at,event_date),
            title,publisher,source_url,source_id,
            CASE WHEN lower(source_quality)='official' THEN 'official_source' ELSE 'unverified' END,
            'material_event_keyword' FROM external_market_event WHERE {predicate}""",
                MATERIAL_EVENT_TERMS,
            )
        )
    if "official_trading_restriction" in tables:
        queries.append(
            (
                """SELECT 'restriction:'||event_key,announcement_date,
            fetched_at,COALESCE(company_name,'')||'（'||code||'）'||reason,'臺灣證券交易所',
            CASE source_id
              WHEN 'twse_capital_reduction' THEN 'https://www.twse.com.tw/zh/announcement/reduction/twtavu.html'
              WHEN 'twse_capital_resumption' THEN 'https://www.twse.com.tw/zh/announcement/reduction/twtauu.html'
              ELSE 'https://www.twse.com.tw/zh/announcement/change/twtb7u.html' END,
            source_id,'official_source','official_corporate_action'
            FROM official_trading_restriction WHERE source_id IN
            ('twse_capital_reduction','twse_capital_resumption','twse_par_value_change')""",
                (),
            )
        )
    count = 0
    for query, parameters in queries:
        cursor = conn.execute(
            """INSERT INTO material_news_archive(
            event_key,event_date,published_at,title,publisher,source_url,source_id,verification_status,archive_reason)
            """
            + query
            + """
            ON CONFLICT(event_key) DO UPDATE SET title=excluded.title,publisher=excluded.publisher,
            source_url=excluded.source_url,published_at=excluded.published_at""",
            parameters,
        )
        count += max(0, cursor.rowcount)
    return count
