"""Validate live local APIs and exact LINE payloads without sending messages."""

from pathlib import Path
import argparse, base64, hashlib, hmac, json, os, sys
from unittest.mock import patch
from contextlib import contextmanager
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from equity.__main__ import load_settings

load_settings()
from core.line_bot_config import env_text

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--url", required=True)
args = parser.parse_args()
report = {"line_messages_sent": 0}
session = requests.Session()
session.post(
    args.url + "/api/auth/login",
    json={"email": os.environ["EQUITY_QA_EMAIL"], "password": os.environ["EQUITY_QA_PASSWORD"]},
    timeout=20,
).raise_for_status()
response = session.get(args.url + "/api/stock/2330/detail", timeout=90)
report["stock_detail"] = {
    "http_status": response.status_code,
    "json_object": isinstance(response.json(), dict),
}
body = b'{"events":[]}'
signature = base64.b64encode(
    hmac.new(env_text("LINE_CHANNEL_SECRET").encode(), body, hashlib.sha256).digest()
).decode()
report["signed_webhook_status"] = requests.post(
    args.url + "/line/webhook", data=body, headers={"x-line-signature": signature}, timeout=10
).status_code
report["invalid_signature_status"] = requests.post(
    args.url + "/line/webhook", data=body, headers={"x-line-signature": "invalid"}, timeout=10
).status_code
# All portfolio mutations below use the caller's disposable QA database.
assert os.environ.get("EQUITY_USER_DB", "").startswith("var/qa/")
from services.line_portfolio_service import portfolio_command
from adapter.line_messaging import reply_text

event = {"source": {"type": "user", "userId": "U-synthetic-qa-only"}}
portfolio_command(event, "同意持股保存")
draft = portfolio_command(event, "新增持股 2330 1張 950")
token = draft.split("確認持股 ", 1)[1].splitlines()[0]
portfolio_command(event, "確認持股 " + token)
answer = portfolio_command(event, "我的持股")
captured = {}


@contextmanager
def capture(request, **kwargs):
    captured.update(json.loads(request.data))
    yield type("Response", (), {"status": 200})()


with patch("adapter.line_messaging.urlrequest.urlopen", capture):
    reply_text("synthetic-unused-reply-token", answer)
response = requests.post(
    "https://api.line.me/v2/bot/message/validate/reply",
    headers={"Authorization": "Bearer " + env_text("LINE_CHANNEL_ACCESS_TOKEN")},
    json={"messages": captured["messages"]},
    timeout=30,
)
report["line_payload_validation_status"] = response.status_code
report["exact_rendered_messages"] = captured["messages"]
report["ok"] = (
    report["stock_detail"]["http_status"] == 200
    and report["signed_webhook_status"] == 200
    and report["invalid_signature_status"] == 401
    and response.status_code == 200
)
(ROOT / "var" / "qa" / "line-api-report.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(json.dumps({k: v for k, v in report.items() if k != "exact_rendered_messages"}, ensure_ascii=False))
raise SystemExit(0 if report["ok"] else 1)
