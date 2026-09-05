from __future__ import annotations

import argparse
import json
import os
import platform
import ssl
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

try:
    import requests
except Exception as exc:  # pragma: no cover - import failure branch
    requests = None  # type: ignore[assignment]
    REQUESTS_IMPORT_ERROR = exc
else:
    REQUESTS_IMPORT_ERROR = None

try:
    import urllib3
except Exception as exc:  # pragma: no cover - import failure branch
    urllib3 = None  # type: ignore[assignment]
    URLLIB3_IMPORT_ERROR = exc
else:
    URLLIB3_IMPORT_ERROR = None

try:
    import certifi
except Exception as exc:  # pragma: no cover - optional dependency branch
    certifi = None  # type: ignore[assignment]
    CERTIFI_IMPORT_ERROR = exc
else:
    CERTIFI_IMPORT_ERROR = None


REPO_ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = REPO_ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

try:
    from core.config import FUGLE_API_KEY, fugle_key_variants, safe_error
except Exception:  # pragma: no cover - fallback for diagnostics only
    FUGLE_API_KEY = ""

    def fugle_key_variants(value: str) -> list[str]:
        text = str(value or "").strip()
        return [text] if text else []

    def safe_error(exc: Exception | str) -> str:
        return str(exc)


TPE = ZoneInfo("Asia/Taipei")
DEFAULT_CODES = ["2317", "3491", "2382"]
DEFAULT_API_BASE = "https://api.fugle.tw/marketdata/v1.0"
ENDPOINTS = {
    "trades": "/stock/intraday/trades/{symbol}",
    "volumes": "/stock/intraday/volumes/{symbol}",
    "quote": "/stock/intraday/quote/{symbol}",
}


def now_tpe() -> datetime:
    return datetime.now(TPE)


def mask_key(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return "not configured"
    if len(text) <= 4:
        return "****"
    return f"****{text[-4:]}"


def sanitize_text(value: Any, key_variants: list[str]) -> str:
    text = safe_error(str(value))
    for key in key_variants:
        key = str(key or "").strip()
        if key:
            text = text.replace(key, "***")
    return text


def normalize_codes(raw: str) -> list[str]:
    codes: list[str] = []
    for part in str(raw or "").replace(";", ",").split(","):
        text = part.strip()
        if not text:
            continue
        if text.isdigit() and len(text) < 4:
            text = text.zfill(4)
        codes.append(text)
    return codes


def load_api_key() -> tuple[str, list[str], str]:
    if os.getenv("FUGLE_DIAG_FORCE_MISSING_KEY", "").strip().lower() in {"1", "true", "yes", "on"}:
        return "", [], "forced_missing_for_test"
    configured = str(FUGLE_API_KEY or "").strip()
    if not configured:
        configured = str(os.getenv("FUGLE_API_KEY", "") or "").strip()
    variants = fugle_key_variants(configured)
    return configured, variants, "review_src.core.config_then_environment"


def get_package_version(module: Any, import_error: Exception | None) -> str:
    if import_error is not None:
        return f"import_error: {import_error}"
    return str(getattr(module, "__version__", "unknown"))


def environment_summary(api_base: str, api_key: str, key_source: str) -> dict[str, Any]:
    certifi_path = ""
    if certifi is not None:
        try:
            certifi_path = str(certifi.where())
        except Exception as exc:
            certifi_path = f"error: {safe_error(exc)}"
    return {
        "python_version": sys.version.replace("\n", " "),
        "requests_version": get_package_version(requests, REQUESTS_IMPORT_ERROR),
        "urllib3_version": get_package_version(urllib3, URLLIB3_IMPORT_ERROR),
        "certifi_importable": certifi is not None,
        "certifi_error": safe_error(CERTIFI_IMPORT_ERROR) if CERTIFI_IMPORT_ERROR else "",
        "certifi_where": certifi_path,
        "openssl_version": ssl.OPENSSL_VERSION,
        "os": f"{platform.system()} {platform.release()} ({platform.platform()})",
        "requests_ca_bundle_set": bool(os.getenv("REQUESTS_CA_BUNDLE")),
        "ssl_cert_file_set": bool(os.getenv("SSL_CERT_FILE")),
        "api_base": api_base,
        "api_key_configured": bool(api_key),
        "api_key_source": key_source,
        "api_key_masked": mask_key(api_key),
        "full_api_key_displayed": False,
    }


def request_url(
    url: str,
    *,
    key_variants: list[str],
    timeout: float,
    verify: bool | str,
) -> dict[str, Any]:
    if requests is None:
        return {
            "ok": False,
            "error_type": "REQUESTS_IMPORT_ERROR",
            "error": safe_error(REQUESTS_IMPORT_ERROR) if REQUESTS_IMPORT_ERROR else "requests import failed",
            "http_status": None,
            "json_ok": False,
        }
    start = time.time()
    last_error = ""
    for key in key_variants:
        try:
            response = requests.get(
                url,
                headers={"X-API-KEY": key, "User-Agent": "Taiwan50Dashboard/FugleDiagnostics"},
                timeout=timeout,
                verify=verify,
            )
            elapsed_ms = round((time.time() - start) * 1000, 1)
            payload: Any = None
            json_ok = False
            parse_error = ""
            try:
                payload = response.json()
                json_ok = True
            except Exception as exc:
                parse_error = sanitize_text(exc, key_variants)
            return {
                "ok": True,
                "http_status": response.status_code,
                "json_ok": json_ok,
                "json": payload,
                "json_parse_error": parse_error,
                "elapsed_ms": elapsed_ms,
                "error_type": "",
                "error": "",
            }
        except requests.exceptions.SSLError as exc:  # type: ignore[union-attr]
            last_error = sanitize_text(exc, key_variants)
            return {
                "ok": False,
                "http_status": None,
                "json_ok": False,
                "error_type": "TLS_CERTIFICATE_ERROR",
                "error": last_error,
            }
        except requests.exceptions.Timeout as exc:  # type: ignore[union-attr]
            last_error = sanitize_text(exc, key_variants)
            return {
                "ok": False,
                "http_status": None,
                "json_ok": False,
                "error_type": "TIMEOUT",
                "error": last_error,
            }
        except requests.exceptions.RequestException as exc:  # type: ignore[union-attr]
            last_error = sanitize_text(exc, key_variants)
            if "401" not in last_error:
                return {
                    "ok": False,
                    "http_status": None,
                    "json_ok": False,
                    "error_type": "REQUEST_ERROR",
                    "error": last_error,
                }
    return {
        "ok": False,
        "http_status": None,
        "json_ok": False,
        "error_type": "REQUEST_ERROR",
        "error": last_error or "request failed",
    }


def classify_http_status(status: int | None) -> str:
    if status is None:
        return "no_http_status"
    if status == 200:
        return "ok"
    if status in {401, 403}:
        return "api_key_or_permission"
    if status == 429:
        return "rate_limit"
    if status >= 500:
        return "server_error"
    return "http_error"


def payload_root(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("data"), list):
        return data
    return payload


def payload_rows(payload: Any) -> list[Any]:
    if not isinstance(payload, dict):
        return []
    root = payload_root(payload)
    data = root.get("data")
    if isinstance(data, list):
        return data
    for key in ["items", "trades", "volumes", "priceVolumes"]:
        value = root.get(key)
        if isinstance(value, list):
            return value
    return []


def payload_date(payload: Any) -> str:
    root = payload_root(payload)
    if not isinstance(root, dict):
        return ""
    for key in ["date", "tradeDate"]:
        value = root.get(key)
        if value:
            return str(value)
    if isinstance(payload, dict):
        for key in ["date", "tradeDate"]:
            value = payload.get(key)
            if value:
                return str(value)
    return ""


def payload_symbol(payload: Any) -> str:
    root = payload_root(payload)
    if not isinstance(root, dict):
        return ""
    for key in ["symbol", "code"]:
        value = root.get(key)
        if value:
            return str(value)
    if isinstance(payload, dict):
        for key in ["symbol", "code"]:
            value = payload.get(key)
            if value:
                return str(value)
    return ""


def preview_rows(rows: list[Any], max_preview: int) -> list[Any]:
    preview: list[Any] = []
    for row in rows[:max_preview]:
        if isinstance(row, dict):
            preview.append({str(k): row.get(k) for k in list(row.keys())[:20]})
        else:
            preview.append(row)
    return preview


def endpoint_analysis(endpoint: str, payload: Any, today: str, max_preview: int) -> dict[str, Any]:
    rows = payload_rows(payload)
    sample_fields: list[str] = []
    if rows and isinstance(rows[0], dict):
        sample_fields = [str(k) for k in rows[0].keys()]
    response_date = payload_date(payload)
    base = {
        "response_date": response_date,
        "response_symbol": payload_symbol(payload),
        "data_length": len(rows),
        "sample_fields": sample_fields,
        "preview": preview_rows(rows, max_preview),
        "is_current_date": bool(response_date and response_date == today),
        "has_date_symbol_data": bool(response_date and payload_symbol(payload) and isinstance(rows, list)),
    }
    field_set = set(sample_fields)
    if endpoint == "trades":
        base.update(
            {
                "has_time_price_size": {"time", "price", "size"}.issubset(field_set),
                "has_volume": "volume" in field_set,
                "has_bid_ask": {"bid", "ask"}.issubset(field_set),
                "has_serial": "serial" in field_set,
                "has_explicit_side": bool(field_set & {"side", "volumeAtBid", "volumeAtAsk"}),
                "time_sales_usable": {"time", "price", "size"}.issubset(field_set) and len(rows) > 0,
                "side_status": "explicit_side_available"
                if bool(field_set & {"side", "volumeAtBid", "volumeAtAsk"})
                else ("side_inference_possible_but_not_approved" if {"price", "bid", "ask"}.issubset(field_set) else "inner_outer_not_available"),
            }
        )
    elif endpoint == "volumes":
        base.update(
            {
                "has_price_volume": {"price", "volume"}.issubset(field_set),
                "has_volume_at_bid_ask": {"volumeAtBid", "volumeAtAsk"}.issubset(field_set),
                "price_volume_usable": {"price", "volume"}.issubset(field_set) and len(rows) > 0,
                "inner_outer_status": "inner_outer_available_from_volumes"
                if {"volumeAtBid", "volumeAtAsk"}.issubset(field_set)
                else "inner_outer_not_available",
            }
        )
    return base


def write_json_debug(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def markdown_bool(value: Any) -> str:
    if value is True:
        return "是"
    if value is False:
        return "否"
    return str(value)


def make_report(result: dict[str, Any]) -> str:
    summary = result["summary"]
    env = result["environment"]
    lines = [
        "# Fugle Intraday Connection Report",
        "",
        "## 結論摘要",
        "",
        f"- 是否有 FUGLE_API_KEY：{markdown_bool(summary['has_api_key'])}",
        f"- 是否有顯示完整 API key：否",
        f"- API key 狀態：{env['api_key_masked']}",
        f"- default TLS verify 是否成功：{markdown_bool(summary['default_tls_ok'])}",
        f"- certifi TLS verify 是否成功：{markdown_bool(summary['certifi_tls_ok'])}",
        f"- 是否仍有 CERTIFICATE_VERIFY_FAILED：{markdown_bool(summary['certificate_verify_failed'])}",
        f"- 是否成功呼叫 Fugle API：{markdown_bool(summary['fugle_api_called'])}",
        f"- 是否遇到 API key / 權限 / rate limit 問題：{summary['auth_or_rate_limit_status']}",
        f"- 是否遇到 TLS / 憑證問題：{markdown_bool(summary['tls_problem'])}",
        f"- 是否建議進入下一階段 DB 匯入設計：{markdown_bool(summary['recommend_db_design_next'])}",
        f"- JSON runtime 是否有保存：{markdown_bool(summary['json_saved'])}",
        "",
        "本階段未寫 DB、未做全市場、未新增排程、未新增正式 parser、未改 GET API、未改前端、未改分析邏輯。",
        "",
        "## TLS 診斷",
        "",
        f"- Python 版本：`{env['python_version']}`",
        f"- requests 版本：`{env['requests_version']}`",
        f"- urllib3 版本：`{env['urllib3_version']}`",
        f"- certifi 可 import：{markdown_bool(env['certifi_importable'])}",
        f"- certifi.where()：`{env['certifi_where']}`",
        f"- OpenSSL version：`{env['openssl_version']}`",
        f"- OS：`{env['os']}`",
        f"- REQUESTS_CA_BUNDLE：{markdown_bool(env['requests_ca_bundle_set'])}",
        f"- SSL_CERT_FILE：{markdown_bool(env['ssl_cert_file_set'])}",
        f"- API base：`{env['api_base']}`",
        "",
    ]
    for tls in result["tls_tests"]:
        lines.extend(
            [
                f"### {tls['mode']}",
                "",
                f"- URL：`{tls['url']}`",
                f"- ok：{markdown_bool(tls['ok'])}",
                f"- HTTP status：{tls.get('http_status')}",
                f"- status class：{tls.get('status_class')}",
                f"- error type：`{tls.get('error_type') or ''}`",
                f"- error：`{tls.get('error') or ''}`",
                "",
            ]
        )
    lines.extend(["## 各股票結果", ""])
    for code, endpoints in result["stocks"].items():
        lines.extend([f"### {code}", ""])
        for endpoint, info in endpoints.items():
            analysis = info.get("analysis") or {}
            lines.extend(
                [
                    f"#### {endpoint}",
                    "",
                    f"- URL：`{info['url']}`",
                    f"- HTTP status：{info.get('http_status')}",
                    f"- status class：{info.get('status_class')}",
                    f"- JSON parse：{markdown_bool(info.get('json_ok'))}",
                    f"- response date：`{analysis.get('response_date') or ''}`",
                    f"- response symbol：`{analysis.get('response_symbol') or ''}`",
                    f"- data length：{analysis.get('data_length', 0)}",
                    f"- sample fields：`{', '.join(analysis.get('sample_fields') or [])}`",
                    f"- response date 是否為今日：{markdown_bool(analysis.get('is_current_date'))}",
                    f"- error type：`{info.get('error_type') or ''}`",
                    f"- error：`{info.get('error') or ''}`",
                    "",
                ]
            )
            if endpoint == "trades":
                lines.extend(
                    [
                        f"- trades 是否有 date / symbol / data：{markdown_bool(analysis.get('has_date_symbol_data'))}",
                        f"- trades 是否有 time / price / size：{markdown_bool(analysis.get('has_time_price_size'))}",
                        f"- trades 是否足以作 time-sales：{markdown_bool(analysis.get('time_sales_usable'))}",
                        f"- side 判斷：`{analysis.get('side_status') or ''}`",
                        "",
                    ]
                )
            if endpoint == "volumes":
                lines.extend(
                    [
                        f"- volumes 是否有 date / symbol / data：{markdown_bool(analysis.get('has_date_symbol_data'))}",
                        f"- volumes 是否有 price / volume：{markdown_bool(analysis.get('has_price_volume'))}",
                        f"- volumes 是否足以作分價量表：{markdown_bool(analysis.get('price_volume_usable'))}",
                        f"- volumes 是否提供 volumeAtBid / volumeAtAsk：{markdown_bool(analysis.get('has_volume_at_bid_ask'))}",
                        f"- 內外盤狀態：`{analysis.get('inner_outer_status') or ''}`",
                        "",
                    ]
                )
    lines.extend(
        [
            "## 欄位 mapping 初稿",
            "",
            "time-sales:",
            "",
            "- trade_date / date",
            "- code / symbol",
            "- time",
            "- price",
            "- size",
            "- volume",
            "- bid",
            "- ask",
            "- serial",
            "- source = FUGLE",
            "- fetched_at",
            "- data_quality",
            "",
            "price-volume:",
            "",
            "- trade_date / date",
            "- code / symbol",
            "- price",
            "- volume",
            "- volumeAtBid",
            "- volumeAtAsk",
            "- source = FUGLE",
            "- fetched_at",
            "- data_quality",
            "",
            "inner-outer:",
            "",
            "- trade_date / date",
            "- code / symbol",
            "- buy_volume_lots 或 volumeAtAsk",
            "- sell_volume_lots 或 volumeAtBid",
            "- total_volume_lots 或 volume",
            "- neutral_volume_lots",
            "- source = FUGLE",
            "- fetched_at",
            "- data_quality",
            "",
            "注意：volumeAtBid / volumeAtAsk 的中文語意與買賣方向必須另行確認後才能映射成內盤或外盤；未確認前不得寫死方向。",
            "",
            "## 後續正式 scraper 的日期、執行時間與保留規則",
            "",
            "1. 本階段不寫 DB，因此不會產生正式資料保留問題。",
            "2. 若後續進入正式 Fugle scraper，所有正式資料表必須記錄交易日期。",
            "3. 交易日期欄位優先使用 `trade_date`；若既有表使用 `date`，必須維持相容並在文件中說明。",
            "4. Fugle intraday trades / volumes 可能只保留當日盤中資料，隔日可能無法取得前一交易日完整明細。",
            "5. 正式 Fugle scraper 必須在當天盤後執行，例如 15:00 後。",
            "6. 正式流程不得假設可以隔日補抓前一日 intraday trades / volumes。",
            "7. 若當日盤後抓取失敗，應標記 `SOURCE_DELAYED` / `FAILED` / `PARTIAL`，並保留錯誤報告。",
            "8. Fugle 補充資料正式寫入 DB 後，只保留最近 300 個交易日。",
            "9. 300 交易日 prune 只限 Fugle 補充資料表，不得刪 `history_price`、官方日線、法人、TDCC、籌碼資料。",
            "10. 官方日線 `history_price` 仍維持既有 600 交易日保留規則。",
            "11. prune 判斷必須以 `trade_date` / `date` 為準，不得以 `fetched_at` 取代交易日期。",
            "",
            "## 風險與下一步",
            "",
        ]
    )
    for item in result["next_steps"]:
        lines.append(f"- {item}")
    lines.extend(
        [
            "",
            "若 default/certifi TLS 成功且 2317 / 3491 / 2382 的 trades / volumes 都成功，下一步才可另開 Fugle 補充資料 DB schema / parser / 300 交易日保留設計。",
            "",
            "若 TLS 仍失敗，應先修 Windows Python CA / certifi / requests 憑證鏈，不要進 DB 匯入。",
            "",
        ]
    )
    return "\n".join(lines)


def build_dry_run_result(args: argparse.Namespace, api_key: str, key_source: str) -> dict[str, Any]:
    codes = normalize_codes(args.codes)
    env = environment_summary(args.api_base, api_key, key_source)
    endpoints = ["trades", "volumes"] + (["quote"] if args.include_quote else [])
    planned: dict[str, list[str]] = {}
    for code in codes:
        planned[code] = [args.api_base.rstrip("/") + ENDPOINTS[name].format(symbol=code) for name in endpoints]
    return {
        "ok": True,
        "dry_run": True,
        "environment": env,
        "planned_endpoints": planned,
        "writes_db": False,
        "full_api_key_displayed": False,
    }


def run(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    api_key, key_variants, key_source = load_api_key()
    if not api_key or not key_variants:
        result = {
            "ok": False,
            "reason": "missing_fugle_api_key",
            "message": "缺少 FUGLE_API_KEY；請先在環境變數或 review_src/.env 設定。",
            "environment": environment_summary(args.api_base, "", key_source),
            "writes_db": False,
            "full_api_key_displayed": False,
        }
        return 1, result
    if args.dry_run:
        return 0, build_dry_run_result(args, api_key, key_source)
    codes = normalize_codes(args.codes)
    env = environment_summary(args.api_base, api_key, key_source)
    today = now_tpe().strftime("%Y-%m-%d")
    minimal_url = args.api_base.rstrip("/") + ENDPOINTS["volumes"].format(symbol=codes[0] if codes else "2330")
    tls_tests: list[dict[str, Any]] = []

    def tls_attempt(mode: str, verify_value: bool | str, insecure: bool = False) -> dict[str, Any]:
        response = request_url(minimal_url, key_variants=key_variants, timeout=args.timeout, verify=verify_value)
        return {
            "mode": mode,
            "url": minimal_url,
            "ok": bool(response.get("ok")),
            "http_status": response.get("http_status"),
            "status_class": classify_http_status(response.get("http_status")),
            "json_ok": response.get("json_ok"),
            "error_type": response.get("error_type"),
            "error": response.get("error"),
            "insecure": insecure,
        }

    active_verify: bool | str | None = None
    active_tls_mode = ""
    if args.ca_mode in {"auto", "default"}:
        default_test = tls_attempt("default", True)
        tls_tests.append(default_test)
        if default_test["ok"]:
            active_verify = True
            active_tls_mode = "default"
    if active_verify is None and args.ca_mode in {"auto", "certifi"} and certifi is not None:
        certifi_test = tls_attempt("certifi", certifi.where())
        tls_tests.append(certifi_test)
        if certifi_test["ok"]:
            active_verify = certifi.where()
            active_tls_mode = "certifi"
    if active_verify is None and args.insecure_skip_tls_verify:
        insecure_test = tls_attempt("insecure_debug", False, insecure=True)
        tls_tests.append(insecure_test)
        if insecure_test["ok"]:
            active_verify = False
            active_tls_mode = "insecure_debug"

    stocks: dict[str, Any] = {}
    json_saved = False
    endpoints = ["trades", "volumes"] + (["quote"] if args.include_quote else [])
    if active_verify is not None and active_tls_mode != "insecure_debug":
        for code in codes:
            stocks[code] = {}
            for endpoint in endpoints:
                url = args.api_base.rstrip("/") + ENDPOINTS[endpoint].format(symbol=code)
                response = request_url(url, key_variants=key_variants, timeout=args.timeout, verify=active_verify)
                payload = response.get("json")
                analysis = endpoint_analysis(endpoint, payload, today, args.max_preview) if response.get("json_ok") else {}
                record = {
                    "url": url,
                    "ok": response.get("ok"),
                    "http_status": response.get("http_status"),
                    "status_class": classify_http_status(response.get("http_status")),
                    "json_ok": response.get("json_ok"),
                    "json_parse_error": response.get("json_parse_error"),
                    "error_type": response.get("error_type"),
                    "error": response.get("error"),
                    "analysis": analysis,
                }
                stocks[code][endpoint] = record
                if payload is not None and not args.no_save_json:
                    out = Path(args.save_json_dir) / f"{code}_{endpoint}.json"
                    write_json_debug(out, payload)
                    json_saved = True
                time.sleep(max(0.0, args.sleep_seconds))

    default_tls_ok = any(t["mode"] == "default" and t["ok"] for t in tls_tests)
    certifi_tls_ok = any(t["mode"] == "certifi" and t["ok"] for t in tls_tests)
    certificate_verify_failed = any(t.get("error_type") == "TLS_CERTIFICATE_ERROR" for t in tls_tests)
    fugle_api_called = any(
        info.get("http_status") is not None
        for endpoints_by_code in stocks.values()
        for info in endpoints_by_code.values()
    )
    status_classes = [
        str(info.get("status_class"))
        for endpoints_by_code in stocks.values()
        for info in endpoints_by_code.values()
        if info.get("status_class") in {"api_key_or_permission", "rate_limit"}
    ]
    usable_time_sales = any(
        bool(info.get("analysis", {}).get("time_sales_usable"))
        for endpoints_by_code in stocks.values()
        for name, info in endpoints_by_code.items()
        if name == "trades"
    )
    usable_price_volume = any(
        bool(info.get("analysis", {}).get("price_volume_usable"))
        for endpoints_by_code in stocks.values()
        for name, info in endpoints_by_code.items()
        if name == "volumes"
    )
    inner_outer_available = any(
        info.get("analysis", {}).get("inner_outer_status") == "inner_outer_available_from_volumes"
        for endpoints_by_code in stocks.values()
        for name, info in endpoints_by_code.items()
        if name == "volumes"
    )
    all_required_success = all(
        stocks.get(code, {}).get(endpoint, {}).get("http_status") == 200
        for code in codes
        for endpoint in ["trades", "volumes"]
    ) if stocks else False
    summary = {
        "has_api_key": True,
        "full_api_key_displayed": False,
        "default_tls_ok": default_tls_ok,
        "certifi_tls_ok": certifi_tls_ok,
        "certificate_verify_failed": certificate_verify_failed,
        "tls_problem": certificate_verify_failed or active_verify is None,
        "active_tls_mode": active_tls_mode or "none",
        "fugle_api_called": fugle_api_called,
        "auth_or_rate_limit_status": ", ".join(sorted(set(status_classes))) if status_classes else "not_observed",
        "trades_time_sales_usable": usable_time_sales,
        "volumes_price_volume_usable": usable_price_volume,
        "volumes_inner_outer_available": inner_outer_available,
        "recommend_db_design_next": bool(all_required_success and usable_time_sales and usable_price_volume),
        "json_saved": json_saved,
        "writes_db": False,
    }
    next_steps = [
        "API key 權限可能不足時，先確認 response 訊息，不要猜測付費方案。",
        "若遇到 429，需另設 rate limit 與重試策略。",
        "若 TLS 失敗，先修 Python / certifi / CA bundle，不進 DB 匯入。",
        "Fugle intraday 可能只保留當日盤中 / 當日盤後資料；正式 scraper 必須當天盤後執行。",
        "正式 DB 設計前需確認 volumeAtBid / volumeAtAsk 語意，避免內外盤方向寫反。",
        "本階段未寫 DB、未做排程、未做全市場。",
    ]
    result = {
        "ok": bool(active_verify is not None),
        "environment": env,
        "tls_tests": tls_tests,
        "stocks": stocks,
        "summary": summary,
        "next_steps": next_steps,
        "writes_db": False,
        "full_api_key_displayed": False,
    }
    return 0, result


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose Fugle intraday TLS and endpoint payloads.")
    parser.add_argument("--codes", default=",".join(DEFAULT_CODES))
    parser.add_argument("--output", default="docs/FUGLE_INTRADAY_CONNECTION_REPORT.md")
    parser.add_argument("--save-json-dir", default="logs/fugle_connection_json")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--sleep-seconds", type=float, default=1.0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-save-json", action="store_true")
    parser.add_argument("--max-preview", type=int, default=10)
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument("--include-quote", action="store_true")
    parser.add_argument("--insecure-skip-tls-verify", action="store_true")
    parser.add_argument("--ca-mode", choices=["auto", "certifi", "default"], default="auto")
    args = parser.parse_args()

    code, result = run(args)
    output = Path(args.output)
    if args.dry_run:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return code
    if result.get("reason") == "missing_fugle_api_key":
        print(result["message"])
        return code
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(make_report(result), encoding="utf-8")
    print(f"Report written: {output}")
    print(json.dumps(result.get("summary", {}), ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
