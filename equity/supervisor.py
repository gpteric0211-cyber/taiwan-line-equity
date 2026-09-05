"""Run web, model runtime and daily scheduler under one local supervisor."""

from __future__ import annotations
from contextlib import ExitStack
import json
import os
import sys
import time
from urllib.request import urlopen
from equity import ROOT
from equity.processes import spawn, stop, run_bounded
from equity.scheduler import scheduler_lock


def endpoint_ready(url):
    try:
        with urlopen(url, timeout=2) as response:
            return response.status == 200
    except (OSError, ValueError):
        return False


def main(args):
    location = ROOT / "var" / "services"
    location.mkdir(parents=True, exist_ok=True)
    children = []
    with scheduler_lock(location / "supervisor.lock"), ExitStack() as stack:

        def launch(name, arguments):
            log = stack.enter_context((location / (name + ".log")).open("a", encoding="utf-8"))
            child = spawn([sys.executable, "-m", "equity", *arguments], cwd=ROOT, stdout=log, stderr=log)
            children.append((name, child))
            return child

        try:
            if not args.no_model:
                endpoint = os.getenv("QWEN_BASE_URL", "http://127.0.0.1:8020/v1").rstrip("/") + "/models"
                if not endpoint_ready(endpoint):
                    child = launch("model", ["model-server"])
                    deadline = time.monotonic() + 45
                    while not endpoint_ready(endpoint):
                        if child.poll() is not None or time.monotonic() > deadline:
                            raise RuntimeError(
                                "Model runtime did not become ready; inspect var/services/model.log"
                            )
                        time.sleep(1)
                if os.getenv("EQUITY_MODEL_WARMUP", "1").lower() not in {"0", "false", "off"}:
                    log = stack.enter_context((location / "warmup.log").open("a", encoding="utf-8"))
                    code = run_bounded(
                        [sys.executable, "-m", "equity", "warmup"],
                        cwd=ROOT,
                        stdout=log,
                        stderr=log,
                        timeout=2 * int(os.getenv("EQUITY_MODEL_LOAD_TIMEOUT_SECONDS", "180")) + 20,
                    )
                    if code:
                        raise RuntimeError("Model preload failed; inspect var/services/warmup.log")
            launch("web", ["serve", "--host", args.host, "--port", str(args.port)])
            tunnel_mode = os.getenv("EQUITY_TUNNEL_MODE", "off").lower()
            if tunnel_mode not in {"off", "external", "quick"}:
                raise ValueError("EQUITY_TUNNEL_MODE must be off, external, or quick")
            if tunnel_mode == "quick":
                launch("tunnel", ["tunnel", "--port", str(args.port)])
            if not args.no_schedule:
                launch("scheduler", ["schedule"])
            print(
                json.dumps(
                    {
                        "started": [name for name, _ in children],
                        "open": f"http://127.0.0.1:{args.port}/portfolio",
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            while True:
                for name, child in children:
                    if child.poll() is not None:
                        raise RuntimeError(
                            f"{name} stopped (exit {child.returncode}); inspect var/services/{name}.log"
                        )
                time.sleep(1)
        except KeyboardInterrupt:
            return 0
        finally:
            for _, child in reversed(children):
                stop(child)
