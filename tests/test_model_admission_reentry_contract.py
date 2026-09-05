"""Same-controller worker reentry must fail fast regardless of category.

Only the isolated child imports production admission code. No production method,
clock, Future, or worker is replaced. The parent owns a hard watchdog and reaps
the child, so a real self-wait cannot wedge pytest or a running LINE process.
Optional ADMISSION_REENTRY_EVIDENCE_DIR is relative to PROJECT_ROOT (or absolute).
Each scenario's evidence subdirectory must not already exist.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import sysconfig
import time


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REJECTION_WINDOW_SECONDS = 0.25
SENTINEL_WINDOW_SECONDS = 0.25
CHILD_WATCHDOG_SECONDS = 5.0
REAP_TIMEOUT_SECONDS = 3.0
EXPECTED_REASON = "admission_reentry_rejected"
REPORT_PREFIX = "ADMISSION_REENTRY_REPORT "


def _child_probe(venv_site_packages: Path, scenario: str, cross_category: bool) -> None:
    import faulthandler
    import threading
    import traceback
    from concurrent.futures import TimeoutError as FutureTimeout

    forbidden_events: list[str] = []

    def deny_external_io(event: str, _args: tuple) -> None:
        if event in {"socket.connect", "socket.getaddrinfo", "sqlite3.connect", "subprocess.Popen"}:
            forbidden_events.append(event)
            raise AssertionError(f"offline admission probe forbids {event}")

    sys.addaudithook(deny_external_io)
    # Use the caller's dependency directory with its underlying interpreter.
    # Windows venv python.exe may be a redirector with a different child PID.
    sys.path.insert(0, str(venv_site_packages))
    sys.path.insert(0, str(PROJECT_ROOT / "review_src"))
    from services import model_admission_service as admission

    controller = admission._CONTROLLER
    controller_path = Path(admission.__file__).resolve()
    controller_hash = hashlib.sha256(controller_path.read_bytes()).hexdigest()
    child_started = time.monotonic()
    # Positive control: same real wrapper and worker, but no nested call.
    baseline = admission.run_shadow_model(lambda: "baseline-complete")
    print("ADMISSION_REENTRY_BASELINE " + json.dumps({
        "value": baseline.value, "controller_sha256": controller_hash,
        "child_pid": os.getpid(), "category": baseline.category,
    }), flush=True)

    inner_category = "interactive_stock_analysis" if scenario == "interactive" else "shadow_candidate"
    category = inner_category
    if cross_category or scenario == "external_cross":
        category = "shadow_candidate" if scenario == "interactive" else "interactive_stock_analysis"
    # Keep the existing same-category positive control; the dedicated external
    # case submits a DIFFERENT category while the worker is still held busy.
    external_category = "shadow_candidate" if scenario == "external_cross" else category
    busy_entered = threading.Event()
    busy_finished = threading.Event()
    release_busy = threading.Event()
    external_started = threading.Event()
    external_state: dict = {}

    def busy_work() -> str:
        external_state["busy_worker_ident"] = threading.get_ident()
        busy_entered.set()
        if not release_busy.wait(timeout=1.0):
            raise AssertionError("positive-control release missing")
        busy_finished.set()
        return "busy-complete"

    def external_work() -> str:
        external_state["external_worker_ident"] = threading.get_ident()
        external_state["busy_finished_before_external"] = busy_finished.is_set()
        external_started.set()
        return "external-same-category-complete" if scenario != "external_cross" else "external-cross-category-complete"

    busy = controller.submit(busy_work, priority=0, category=category, deadline_monotonic=None)
    if not busy_entered.wait(timeout=1.0):
        raise AssertionError("positive-control worker did not start")
    try:
        external = controller.submit(
            external_work, priority=0,
            category=external_category, deadline_monotonic=None,
        )
        external_state["submitter_is_worker"] = threading.current_thread() is controller._worker
        external_state["started_before_release"] = external_started.is_set()
        external_state["queued_while_busy"] = controller.snapshot()
    finally:
        release_busy.set()
    busy.result(timeout=1.0)
    external_value = external.result(timeout=1.0).value
    if scenario == "external_cross":
        print(REPORT_PREFIX + json.dumps({
            "scenario": scenario, "synthetic": True,
            "child_pid": os.getpid(), "python_version": sys.version,
            "controller_sha256": controller_hash,
            "baseline_value": baseline.value,
            "outer_category": category, "submitted_category": external_category,
            "external_value": external_value, "external_state": external_state,
            "controller_worker_ident": controller._worker.ident,
            "final_snapshot": controller.snapshot(),
            "external_io_attempts": forbidden_events,
        }, sort_keys=True), flush=True)
        return

    outer_entered = threading.Event()
    reentry_finished = threading.Event()
    allow_completion = threading.Event()
    inner_executed = threading.Event()
    inner_on_start_executed = threading.Event()
    sentinel_executed = threading.Event()
    state: dict = {"reason_code": None, "exception_class": None, "submit_returned_future": False}

    def inner_work() -> str:
        inner_executed.set()
        return "inner-must-not-run"

    def attempt_reentry() -> None:
        state["outer_worker_ident"] = threading.get_ident()
        state["nested_call_started"] = time.monotonic()
        outer_entered.set()
        try:
            if scenario == "interactive":
                admission.run_interactive_model(inner_work, category=inner_category)
            elif scenario == "direct_submit":
                nested = controller.submit(
                    inner_work, priority=10, category=inner_category, deadline_monotonic=None,
                    on_start=lambda _ms: inner_on_start_executed.set(),
                )
                state["submit_returned_future"] = True
                nested.result()
            else:
                admission.run_shadow_model(
                    inner_work, on_start=lambda _ms: inner_on_start_executed.set(),
                )
        except admission.ModelAdmissionError as exc:
            state["reason_code"] = exc.reason_code
            state["exception_class"] = type(exc).__name__
            state["exception_exact_type"] = type(exc) is admission.ModelAdmissionError
            state["snapshot_at_rejection"] = controller.snapshot()
        finally:
            state["nested_call_elapsed_ms"] = (
                time.monotonic() - state["nested_call_started"]
            ) * 1000
            reentry_finished.set()

    def outer_work() -> str:
        if scenario == "done_callback":
            if not allow_completion.wait(timeout=1.0):
                raise AssertionError("completion callback registration missing")
        elif scenario != "on_start":
            attempt_reentry()
        return "outer-complete"

    if category == "interactive_stock_analysis":
        outer = controller.submit(
            outer_work, priority=0, category=category, deadline_monotonic=None,
            on_start=(lambda _ms: attempt_reentry()) if scenario == "on_start" else None,
        )
    else:
        outer = admission.submit_shadow_model(
            outer_work, category=category,
            on_start=(lambda _ms: attempt_reentry()) if scenario == "on_start" else None,
        )
    if scenario == "done_callback":
        # Must register BEFORE completion: late callbacks run on the caller.
        outer.add_done_callback(lambda _future: attempt_reentry())
        allow_completion.set()
    if not outer_entered.wait(timeout=1.0):
        raise AssertionError("probe setup failed: outer callback never started")

    outer_wait_error = None
    if not reentry_finished.wait(timeout=REJECTION_WINDOW_SECONDS):
        outer_wait_error = "TimeoutError"
    else:
        outer.result(timeout=REJECTION_WINDOW_SECONDS)
    after_outer_wait = controller.snapshot()

    def sentinel_work() -> str:
        sentinel_executed.set()
        return "sentinel-complete"

    # A higher-priority job cannot help if the sole worker is waiting on itself.
    sentinel = controller.submit(
        sentinel_work, priority=0, category="offline_reentry_sentinel",
        deadline_monotonic=time.monotonic() + 1.0,
    )
    sentinel_wait_error = None
    try:
        sentinel.result(timeout=SENTINEL_WINDOW_SECONDS)
    except FutureTimeout as exc:
        sentinel_wait_error = type(exc).__name__

    worker_frame = sys._current_frames().get(controller._worker.ident)
    worker_stack = traceback.extract_stack(worker_frame) if worker_frame else []
    stack_functions = [frame.name for frame in worker_stack]
    report = {
        "scenario": scenario, "category": category, "synthetic": True,
        "inner_category": inner_category, "cross_category": cross_category,
        "child_pid": os.getpid(), "python_version": sys.version,
        "dependency_directory": str(venv_site_packages),
        "controller_sha256": controller_hash,
        "baseline_value": baseline.value,
        "external_same_category_value": external_value,
        "outer_on_controller_worker": state.get("outer_worker_ident") == controller._worker.ident,
        "outer_wait_error": outer_wait_error,
        "rejection_reason_code": state["reason_code"],
        "rejection_exception_class": state["exception_class"],
        "rejection_exact_type": state.get("exception_exact_type", False),
        "snapshot_at_rejection": state.get("snapshot_at_rejection"),
        "submit_returned_future": state["submit_returned_future"],
        "reentry_finished": reentry_finished.is_set(),
        "rejection_elapsed_ms": state.get("nested_call_elapsed_ms"),
        "rejection_window_ms": REJECTION_WINDOW_SECONDS * 1000,
        "inner_executed": inner_executed.is_set(),
        "inner_on_start_executed": inner_on_start_executed.is_set(),
        "outer_done": outer.done(), "sentinel_done": sentinel.done(),
        "sentinel_executed": sentinel_executed.is_set(),
        "sentinel_wait_error": sentinel_wait_error,
        "after_outer_wait": after_outer_wait,
        "final_snapshot": controller.snapshot(),
        "worker_alive": controller._worker.is_alive(),
        "worker_stack_functions": stack_functions,
        "external_io_attempts": forbidden_events,
        "observed_elapsed_ms": (time.monotonic() - child_started) * 1000,
    }
    print(REPORT_PREFIX + json.dumps(report, sort_keys=True), flush=True)
    if not reentry_finished.is_set():
        faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
        # Deliberately do not let daemon-worker exit hide the self-wait.
        # Only the parent watchdog may end this isolated process.
        reentry_finished.wait()


def _run_isolated_probe(evidence_dir: Path, scenario: str, cross_category: bool = False) -> dict:
    evidence_dir.mkdir(parents=True, exist_ok=False)
    safe_names = {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "LANG", "LC_ALL"}
    child_env = {key: value for key, value in os.environ.items() if key.upper() in safe_names}
    child_env.update({
        "LINE_MODEL_SHADOW_IDLE_GRACE_MS": "0",
        "LINE_MODEL_GPU_QUEUE_MAX": "8",
    })
    executable = getattr(sys, "_base_executable", None) or sys.executable
    command = [
        executable, "-I", "-B", "-u", str(Path(__file__).resolve()),
        "--reentry-child", sysconfig.get_path("purelib"), scenario,
        "cross" if cross_category else "same",
    ]
    started = time.monotonic()
    with subprocess.Popen(
        command, cwd=evidence_dir, env=child_env,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    ) as child:
        watchdog_fired = False
        try:
            stdout, stderr = child.communicate(timeout=CHILD_WATCHDOG_SECONDS)
        except subprocess.TimeoutExpired:
            watchdog_fired = True
            child.kill()  # Exact child owned by this Popen; never a service/PID lookup.
            stdout, stderr = child.communicate(timeout=REAP_TIMEOUT_SECONDS)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=REAP_TIMEOUT_SECONDS)
        parent = {
            "watchdog_fired": watchdog_fired,
            "watchdog_limit_seconds": CHILD_WATCHDOG_SECONDS,
            "child_pid": child.pid, "child_exit_code": child.returncode,
            "child_reaped": child.poll() is not None,
            "elapsed_ms": (time.monotonic() - started) * 1000,
        }
    # Save complete raw output before any assertion; no failed evidence is removed.
    (evidence_dir / "child_stdout.txt").write_text(stdout, encoding="utf-8")
    (evidence_dir / "child_stderr.txt").write_text(stderr, encoding="utf-8")
    (evidence_dir / "parent_watchdog.json").write_text(
        json.dumps(parent, indent=2), encoding="utf-8",
    )
    print(stdout, end="")
    print("ADMISSION_REENTRY_PARENT " + json.dumps(parent, sort_keys=True))
    print("ADMISSION_REENTRY_STDERR_BEGIN\n" + stderr + "ADMISSION_REENTRY_STDERR_END")
    reports = [json.loads(line[len(REPORT_PREFIX):]) for line in stdout.splitlines() if line.startswith(REPORT_PREFIX)]
    assert len(reports) == 1, f"Probe setup/transport failed, not a proven reentry: {parent}"
    return {"report": reports[0], "parent": parent}


def _assert_reentry_contract(tmp_path: Path, scenario: str, cross_category: bool = False) -> None:
    configured = os.environ.get("ADMISSION_REENTRY_EVIDENCE_DIR")
    evidence_dir = Path(configured) if configured else tmp_path / "reentry_probe"
    if not evidence_dir.is_absolute():
        evidence_dir = PROJECT_ROOT / evidence_dir
    case_id = scenario + ("_cross" if cross_category else "")
    observed = _run_isolated_probe(evidence_dir / case_id, scenario, cross_category)
    report, parent = observed["report"], observed["parent"]
    current_hash = hashlib.sha256(
        (PROJECT_ROOT / "review_src/services/model_admission_service.py").read_bytes(),
    ).hexdigest()
    assert report["controller_sha256"] == current_hash
    assert report["python_version"] == sys.version
    assert report["child_pid"] == parent["child_pid"], "Watchdog must own the actual Python PID"
    assert report["baseline_value"] == "baseline-complete"
    assert report["external_same_category_value"] == "external-same-category-complete"
    assert report["scenario"] == scenario
    assert report["cross_category"] is cross_category
    assert (report["category"] != report["inner_category"]) is cross_category
    assert report["outer_on_controller_worker"] is True
    assert report["external_io_attempts"] == []
    assert parent["child_reaped"] is True
    assert report["rejection_reason_code"] == EXPECTED_REASON, (
        "Same-controller reentry was not rejected before waiting: "
        f"reason={report['rejection_reason_code']!r}; "
        f"outer_done={report['outer_done']}; inner_executed={report['inner_executed']}; "
        f"queue_depth={report['final_snapshot']['queue_depth']}; "
        f"sentinel_done={report['sentinel_done']}; watchdog_fired={parent['watchdog_fired']}"
    )
    assert report["rejection_exception_class"] == "ModelAdmissionError"
    assert report["rejection_exact_type"] is True
    assert report["rejection_elapsed_ms"] <= REJECTION_WINDOW_SECONDS * 1000
    assert report["inner_executed"] is False
    assert report["inner_on_start_executed"] is False
    assert report["reentry_finished"] is True
    assert report["snapshot_at_rejection"]["queue_depth"] == 0
    assert report["snapshot_at_rejection"]["active_category"] == report["category"]
    assert report["snapshot_at_rejection"]["preemption_requests"] == 0
    if scenario == "direct_submit":
        assert report["submit_returned_future"] is False, "submit must raise synchronously"
    assert report["outer_done"] is True
    assert report["sentinel_done"] is True
    assert report["sentinel_executed"] is True
    assert report["final_snapshot"]["queue_depth"] == 0
    assert parent["watchdog_fired"] is False
    assert parent["child_exit_code"] == 0


def test_same_kind_reentry_must_reject_immediately_and_worker_remain_usable(tmp_path):
    """Original red contract; no xfail, skip, or weakened safety assertions."""
    _assert_reentry_contract(tmp_path, "shadow")


def test_interactive_wrapper_preserves_exact_reentry_reason(tmp_path):
    _assert_reentry_contract(tmp_path, "interactive")


def test_direct_submit_rejects_synchronously_before_returning_future(tmp_path):
    _assert_reentry_contract(tmp_path, "direct_submit")


def test_on_start_same_category_reentry_is_rejected(tmp_path):
    _assert_reentry_contract(tmp_path, "on_start")


def test_completion_callback_same_category_reentry_is_rejected(tmp_path):
    _assert_reentry_contract(tmp_path, "done_callback")


def test_cross_category_shadow_wrapper_reentry_is_rejected(tmp_path):
    """Original hazardous category pair: interactive_stock_analysis -> shadow_candidate."""
    _assert_reentry_contract(tmp_path, "shadow", cross_category=True)


def test_cross_category_interactive_wrapper_preserves_exact_reason(tmp_path):
    _assert_reentry_contract(tmp_path, "interactive", cross_category=True)


def test_cross_category_direct_submit_rejects_before_returning_future(tmp_path):
    _assert_reentry_contract(tmp_path, "direct_submit", cross_category=True)


def test_cross_category_on_start_reentry_is_rejected(tmp_path):
    _assert_reentry_contract(tmp_path, "on_start", cross_category=True)


def test_cross_category_completion_callback_reentry_is_rejected(tmp_path):
    _assert_reentry_contract(tmp_path, "done_callback", cross_category=True)


def test_external_thread_cross_category_submission_queues_and_executes_serially(tmp_path):
    configured = os.environ.get("ADMISSION_REENTRY_EVIDENCE_DIR")
    evidence_dir = Path(configured) if configured else tmp_path / "reentry_probe"
    if not evidence_dir.is_absolute():
        evidence_dir = PROJECT_ROOT / evidence_dir
    observed = _run_isolated_probe(evidence_dir / "external_cross", "external_cross")
    report, parent = observed["report"], observed["parent"]
    assert report["controller_sha256"] == hashlib.sha256(
        (PROJECT_ROOT / "review_src/services/model_admission_service.py").read_bytes(),
    ).hexdigest()
    assert report["python_version"] == sys.version
    assert report["child_pid"] == parent["child_pid"]
    assert report["scenario"] == "external_cross"
    assert report["baseline_value"] == "baseline-complete"
    assert report["outer_category"] != report["submitted_category"]
    state = report["external_state"]
    assert state["submitter_is_worker"] is False
    assert state["started_before_release"] is False
    assert state["queued_while_busy"]["active_category"] == report["outer_category"]
    assert state["queued_while_busy"]["queue_depth"] == 1
    assert state["queued_while_busy"]["queued_by_category"] == {report["submitted_category"]: 1}
    assert state["busy_finished_before_external"] is True
    assert state["busy_worker_ident"] == state["external_worker_ident"] == report["controller_worker_ident"]
    assert report["external_value"] == "external-cross-category-complete"
    assert report["final_snapshot"]["queue_depth"] == 0
    assert report["external_io_attempts"] == []
    assert parent["child_reaped"] is True
    assert parent["watchdog_fired"] is False
    assert parent["child_exit_code"] == 0


if __name__ == "__main__":
    if len(sys.argv) != 5 or sys.argv[1] != "--reentry-child":
        raise SystemExit("Use pytest to run this contract; --reentry-child is parent-managed only.")
    if sys.argv[3] not in {"shadow", "interactive", "direct_submit", "on_start", "done_callback", "external_cross"}:
        raise SystemExit("Unknown offline reentry scenario")
    if sys.argv[4] not in {"same", "cross"}:
        raise SystemExit("Unknown category relationship")
    _child_probe(Path(sys.argv[2]).resolve(), sys.argv[3], sys.argv[4] == "cross")
