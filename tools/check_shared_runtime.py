"""Check local shared APIs with disposable accounts; send no LINE messages."""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="Running loopback service URL")
    parser.add_argument("--public", action="store_true", help="Check current Tunnel and LINE empty-event delivery")
    args = parser.parse_args()
    parsed = urlsplit(args.url)
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.scheme != "http":
        raise ValueError("--url must be a local HTTP service")
    from equity.__main__ import load_settings

    load_settings()
    import requests
    from fastapi.testclient import TestClient
    from equity.application import create_app
    from equity.lifecycle import health

    report = {"line_messages_sent": 0, "local_service": health(args.url), "api": {}}
    qa_root = ROOT / "var" / "qa"
    qa_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="shared-api-", dir=qa_root) as temporary:
        for setting, filename in [("EQUITY_AUTH_DB", "accounts.sqlite3"),
                                  ("EQUITY_USER_DB", "portfolio.sqlite3"),
                                  ("EQUITY_USER_KEY_FILE", "portfolio.key"),
                                  ("EQUITY_JWT_KEY_FILE", "jwt.key")]:
            os.environ[setting] = str(Path(temporary) / filename)
        os.environ["AUTO_REFRESH_MARKET_DATA_ON_START"] = "0"
        os.environ["AUTO_UPDATE_TW50_ON_START"] = "0"
        from core.accounts_database import initialize
        from repository.portfolio_repository import initialize as initialize_portfolio

        initialize()
        initialize_portfolio()
        with TestClient(create_app(), base_url="http://localhost", client=("127.0.0.1", 1000)) as client:
            payload = {"email": "synthetic-shared-api@example.invalid", "password": secrets.token_urlsafe(32) + "aA1!"}
            response = client.post("/api/setup", json=payload, headers={"X-Equity-Request": "1"})
            response.raise_for_status()
            login = client.post("/api/auth/login", json=payload)
            login.raise_for_status()
            # The deployed HTTPS cookie can be Secure; use the returned token
            # for this loopback-only synthetic client without weakening cookies.
            client.headers["Authorization"] = "Bearer " + login.json()["access_token"]
            for path in ("/", "/api/quotes/watchlist", "/api/quotes?mode=tw50", "/api/stock/2330/detail"):
                response = client.get(path)
                response.raise_for_status()
                item = {"status": response.status_code, "generation": response.headers.get("x-market-generation")}
                if path.startswith("/api/"):
                    body = response.json()
                    assert isinstance(body, dict)
                    if "rows" in body:
                        item["rows"] = len(body["rows"])
                        item["dates"] = sorted({str(row.get("data_date")) for row in body["rows"]})
                report["api"][path] = item
        # All temporary private DB handles are now closed by the TestClient lifespan.
    bot = requests.get(args.url + "/api/bot/market-data/2330/daily",
                       headers={"Authorization": "Bearer " + os.environ["BOT_MARKET_DATA_TOKEN"]}, timeout=90)
    bot.raise_for_status()
    report["bot_daily"] = {"status": bot.status_code, "generation": bot.headers.get("x-market-generation")}
    generations = {item["generation"] for item in report["api"].values() if item["generation"]}
    generations.add(report["bot_daily"]["generation"])
    report["same_market_generation"] = len(generations) == 1 and None not in generations
    body = b'{"events":[]}'
    signature = base64.b64encode(hmac.new(os.environ["LINE_CHANNEL_SECRET"].encode(), body, hashlib.sha256).digest()).decode()
    report["signed_empty_webhook"] = requests.post(args.url + "/line/webhook", data=body,
        headers={"x-line-signature": signature}, timeout=10).status_code
    report["invalid_webhook_signature"] = requests.post(args.url + "/line/webhook", data=body,
        headers={"x-line-signature": "invalid"}, timeout=10).status_code
    if args.public:
        from equity.public_endpoint import verify_public_access, LINE_API

        current = json.loads((ROOT / "var/services/public-endpoint.json").read_text(encoding="utf-8"))
        origin = current["origin"]
        report["public_access"] = verify_public_access(origin)
        headers = {"Authorization": "Bearer " + os.environ["LINE_CHANNEL_ACCESS_TOKEN"]}
        endpoint = requests.get(LINE_API + "/endpoint", headers=headers, timeout=20)
        endpoint.raise_for_status()
        state = endpoint.json()
        tested = requests.post(LINE_API + "/test", headers=headers,
                               json={"endpoint": origin + "/line/webhook"}, timeout=30)
        tested.raise_for_status()
        report["line"] = {"active": state.get("active"), "endpoint": state.get("endpoint"),
                          "empty_event_success": tested.json().get("success"),
                          "endpoint_matches": state.get("endpoint") == origin + "/line/webhook"}
    report["ok"] = (bool(report["local_service"]) and report["same_market_generation"]
                    and report["signed_empty_webhook"] == 200 and report["invalid_webhook_signature"] == 401
                    and (not args.public or all(report["line"][key] for key in
                                               ("active", "empty_event_success", "endpoint_matches"))))
    (qa_root / "shared-runtime-live.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
