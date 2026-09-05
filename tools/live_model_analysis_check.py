"""Exercise the real local text model using a synthetic confirmed position."""

from pathlib import Path
from contextlib import contextmanager
import argparse, json, os, sys, time
from unittest.mock import patch
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--url", required=True, help="Local unified HTTP service")
parser.add_argument(
    "--validate-line",
    action="store_true",
    help="Validate the exact rendered answer through LINE without sending it",
)
args = parser.parse_args()
if urlsplit(args.url).hostname not in {"127.0.0.1", "localhost", "::1"}:
    parser.error("Use a loopback QA service")
os.environ["BOT_MARKET_DATA_BASE_URL"] = args.url.rstrip("/")
from equity.__main__ import load_settings

load_settings()
from services import line_bot_service

observed = {"model_called": False, "confirmed_position_in_prompt": False}
original = line_bot_service.qwen_chat


def model(system, user, **kwargs):
    observed["model_called"] = True
    if "FACTS：" in user:
        facts = json.JSONDecoder().raw_decode(user.split("FACTS：", 1)[1])[0]
        position = facts.get("user_confirmed_holding", {})
        observed["confirmed_position_in_prompt"] = (
            position.get("code") == "2330"
            and position.get("quantity") == "1000"
            and position.get("average_cost") == "950"
        )
    answer = original(system, user, **kwargs)
    observed["raw_model_answer"] = answer
    return answer


started = time.monotonic()
with patch.object(line_bot_service, "qwen_chat", model):
    result = line_bot_service._answer_stock_question_result(
        "2330 請結合我持有的部位，分析目前趨勢與需要留意的風險。",
        deadline_monotonic=time.monotonic() + 45,
        conversation_context={
            "code": "2330",
            "position_state": "holding",
            "confirmed_holdings": [{"code": "2330", "quantity": "1000", "average_cost": "950"}],
        },
    )
report = {
    **observed,
    "answer_path": result.answer_path,
    "policy_rejection_reason": result.policy_rejection_reason,
    "answer": result.text,
    "seconds": round(time.monotonic() - started, 2),
    "synthetic_position": True,
    "line_messages_sent": 0,
}
report["ok"] = observed["model_called"] and observed["confirmed_position_in_prompt"] and bool(result.text)
if args.validate_line:
    import requests
    from adapter.line_messaging import reply_text
    from core.line_bot_config import env_text

    captured = {}

    @contextmanager
    def capture(request, **kwargs):
        captured.update(json.loads(request.data))
        yield type("Response", (), {"status": 200})()

    with patch("adapter.line_messaging.urlrequest.urlopen", capture):
        reply_text("synthetic-unused-reply-token", result.text)
    response = requests.post(
        "https://api.line.me/v2/bot/message/validate/reply",
        headers={"Authorization": "Bearer " + env_text("LINE_CHANNEL_ACCESS_TOKEN")},
        json={"messages": captured["messages"]},
        timeout=30,
    )
    report["line_payload_validation_status"] = response.status_code
    report["rendered_messages"] = captured["messages"]
    report["ok"] = report["ok"] and response.status_code == 200
location = ROOT / "var/qa/model-analysis-report.json"
location.parent.mkdir(parents=True, exist_ok=True)
location.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(
    json.dumps(
        {
            key: value
            for key, value in report.items()
            if key not in {"answer", "raw_model_answer", "rendered_messages"}
        },
        ensure_ascii=False,
    )
)
raise SystemExit(0 if report["ok"] else 1)
