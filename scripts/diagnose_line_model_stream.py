from __future__ import annotations

"""Diagnose Ollama SSE framing without logging prompts or generated content."""

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = PROJECT_ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

load_dotenv(REVIEW_SRC / ".env", override=False)
load_dotenv(PROJECT_ROOT / ".env.line_bot", override=True)

from core.line_model_contract import (  # noqa: E402
    build_model_fact_packet_v2,
    compact_packet_to_token_budget,
    select_context_profile,
)
from core.public_payload import sanitize_public_market_payload  # noqa: E402
from services.line_model_benchmark_service import _benchmark_job  # noqa: E402
from services.line_model_shadow_service import (  # noqa: E402
    MODEL_ANALYSIS_SYSTEM_PROMPT,
    _AUDIT_PROFILE,
)
from services.line_request_planning_service import plan_line_request  # noqa: E402


def main() -> int:
    job, _question = _benchmark_job(
        scenario="fundamental_chip_technical_news",
        code="2330",
    )
    question = str(job["question"])
    model_facts = sanitize_public_market_payload(job["model_facts"])
    plans = plan_line_request(question, focus="overview", has_stock=True)
    execution = plans["execution_plan"]
    selection = select_context_profile(
        str(execution["effective_depth"]),
        int(os.getenv("QWEN_CONTEXT_TOKENS", "16384")),
        requested_profile=os.getenv("LINE_MODEL_V2_PROFILE") or None,
    )
    bounded = build_model_fact_packet_v2(
        model_facts,
        focus="overview",
        depth=str(execution["effective_depth"]),
        profile=selection.profile,
        requested_scopes=list(execution["effective_scopes"]),
    )
    compacted = compact_packet_to_token_budget(
        bounded.packet,
        system_prompt=MODEL_ANALYSIS_SYSTEM_PROMPT,
        question=question,
        profile=selection.profile,
        actual_context=int(os.getenv("QWEN_CONTEXT_TOKENS", "16384")),
        reserved_output_tokens=int(os.getenv("QWEN_MAX_OUTPUT_TOKENS", "900")),
    )
    packet_json = json.dumps(
        compacted.packet,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    user_prompt = f"使用者問題：{question}\nMODEL_FACT_PACKET_V2：{packet_json}"
    base_url = str(os.getenv("QWEN_BASE_URL", "http://127.0.0.1:8020/v1")).rstrip("/")
    body = {
        "model": os.getenv("QWEN_MODEL_ID", "taiwan-stock-qwen"),
        "messages": [
            {"role": "system", "content": MODEL_ANALYSIS_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": float(os.getenv("QWEN_TEMPERATURE", "0.2")),
        "top_p": float(os.getenv("QWEN_TOP_P", "0.8")),
        "max_tokens": int(os.getenv("QWEN_MAX_OUTPUT_TOKENS", "900")),
        "stream": True,
        "reasoning_effort": "none",
    }
    response = requests.post(
        f"{base_url}/chat/completions",
        headers={"Content-Type": "application/json"},
        json=body,
        timeout=120,
        stream=True,
    )
    response.raise_for_status()
    result: dict[str, Any] = {
        "contract": "qwen-stream-framing-diagnostic-v1",
        "http_status": response.status_code,
        "content_type": response.headers.get("content-type"),
        "nonempty_lines": 0,
        "data_lines": 0,
        "done_lines": 0,
        "other_sse_fields": {},
        "invalid_json": [],
        "delta_fields": {},
    }
    try:
        for raw_line in response.iter_lines(decode_unicode=False):
            line = (
                raw_line.decode("utf-8", errors="replace")
                if isinstance(raw_line, bytes)
                else str(raw_line)
            ).strip()
            if not line or line.startswith(":"):
                continue
            result["nonempty_lines"] += 1
            if not line.startswith("data:"):
                field = line.split(":", 1)[0][:32]
                fields = result["other_sse_fields"]
                fields[field] = int(fields.get(field, 0)) + 1
                continue
            result["data_lines"] += 1
            data = line[5:].strip()
            if data == "[DONE]":
                result["done_lines"] += 1
                continue
            try:
                payload = json.loads(data)
            except (TypeError, ValueError) as exc:
                result["invalid_json"].append(
                    {
                        "error_class": type(exc).__name__,
                        "length": len(data),
                        "sha256": hashlib.sha256(data.encode("utf-8")).hexdigest(),
                        "leading_codepoints": [ord(char) for char in data[:16]],
                    }
                )
                continue
            choices = payload.get("choices") if isinstance(payload, dict) else None
            if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                delta = choices[0].get("delta")
                if isinstance(delta, dict):
                    for key in delta:
                        fields = result["delta_fields"]
                        fields[key] = int(fields.get(key, 0)) + 1
    finally:
        response.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["invalid_json"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
