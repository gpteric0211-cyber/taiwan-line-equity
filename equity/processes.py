"""Bounded process trees owned by this application; never stop unrelated services."""

from __future__ import annotations
import os
import signal
import subprocess
from pathlib import Path


def spawn(command, **kwargs):
    options = (
        {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
        if os.name == "nt"
        else {"start_new_session": True}
    )
    return subprocess.Popen(command, **options, **kwargs)


def stop(process, *, timeout=10):
    if process.poll() is not None:
        return
    if os.name == "nt":
        # taskkill targets this exact child PID and its descendants, never a process name.
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=timeout,
        )
    else:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=timeout)
            return
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
    process.wait(timeout=timeout)


def run_bounded(command, *, timeout, **kwargs):
    process = spawn(command, **kwargs)
    try:
        return process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        stop(process)
        return 124
    except BaseException:
        stop(process)
        raise
