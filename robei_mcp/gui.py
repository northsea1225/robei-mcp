"""Session-aware bridge between the MCP server and the Robei IDE.

The MCP server may run in Session 0 (SSH) while the desktop lives in Session 1.
In that case every request is queued to the scheduled task ``RobeiMCP_GuiAgent``,
which runs ``tools/gui/agent_launch.py`` with pythonw.exe inside the interactive
session. When the server itself already runs on the interactive desktop the
same operations are executed in-process.
"""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from .config import SETTINGS
from .security import resolve_project_path

TASK_NAME = "RobeiMCP_GuiAgent"
GUI_DIR = SETTINGS.project_root / "tools" / "gui"
JOB_FILE = GUI_DIR / "agent-job.json"
ARGS_FILE = GUI_DIR / "agent-args.txt"
LAST_JSON = SETTINGS.workspace / "gui_shots" / "last.json"

DEFAULT_TIMEOUT_S = 90
MAX_TIMEOUT_S = 180


def _session_id() -> int:
    try:
        value = ctypes.c_ulong()
        ok = ctypes.windll.kernel32.ProcessIdToSessionId(
            ctypes.windll.kernel32.GetCurrentProcessId(), ctypes.byref(value)
        )
        return int(value.value) if ok else -1
    except Exception:
        return -1


def _console_session() -> int:
    try:
        return int(ctypes.windll.kernel32.WTSGetActiveConsoleSessionId())
    except Exception:
        return -1


def transport() -> dict[str, Any]:
    """Describe how GUI requests will be executed right now."""
    if os.name != "nt":
        return {"mode": "unsupported", "reason": "GUI tools require Windows"}
    session = _session_id()
    console = _console_session()
    task = _task_query()
    return {
        "mode": "direct" if session == console and console not in (-1, 0xFFFFFFFF) else "queued",
        "server_session": session,
        "console_session": console,
        "task": TASK_NAME if task.get("exists") else None,
        "task_state": task.get("state"),
        "task_error": task.get("error"),
        "gui_dir": str(GUI_DIR),
    }


def _task_query() -> dict[str, Any]:
    try:
        proc = subprocess.run(
            ["schtasks", "/Query", "/TN", TASK_NAME, "/FO", "LIST"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception as exc:
        return {"exists": False, "error": f"{type(exc).__name__}: {exc}"}
    if proc.returncode != 0:
        return {"exists": False, "error": (proc.stderr or proc.stdout).strip()[:200]}
    state = None
    for line in proc.stdout.splitlines():
        if line.lower().startswith("status") or line.startswith("状态"):
            state = line.split(":", 1)[-1].strip()
    return {"exists": True, "state": state}


def _read_last() -> dict[str, Any] | None:
    try:
        return json.loads(LAST_JSON.read_text(encoding="utf-8"))
    except Exception:
        return None


def _run_direct(op: str, args: dict[str, Any]) -> dict[str, Any]:
    if str(GUI_DIR) not in sys.path:
        sys.path.insert(0, str(GUI_DIR))
    import importlib

    module = importlib.import_module("robei_ui")
    importlib.reload(module)
    if op == "status":
        payload = module.status(int(args.get("output_lines", 20)))
    elif op == "open_project":
        payload = module.open_project(args["path"])
    elif op == "compile":
        payload = module.compile_model(float(args.get("timeout_s", 25)))
    elif op == "output":
        payload = module.output(int(args.get("lines", 80)))
    elif op == "open_model":
        payload = module.open_model(args["name"])
    elif op == "launch":
        payload = module.launch(args["exe"], float(args.get("wait_s", 20)))
    elif op == "close":
        payload = module.close()
    else:
        return {"ok": False, "error": f"unknown op: {op}"}
    payload["transport"] = "direct"
    return payload


def _run_queued(op: str, args: dict[str, Any], timeout_s: int) -> dict[str, Any]:
    task = _task_query()
    if not task.get("exists"):
        return {
            "ok": False,
            "error": "scheduled task RobeiMCP_GuiAgent is missing; register it before using GUI tools",
            "detail": task.get("error"),
            "transport": "schtasks",
        }
    job_id = uuid.uuid4().hex[:12]
    GUI_DIR.mkdir(parents=True, exist_ok=True)
    JOB_FILE.write_text(
        json.dumps({"job_id": job_id, "op": op, "args": args}, ensure_ascii=False),
        encoding="utf-8",
    )
    ARGS_FILE.write_text("job\n", encoding="utf-8")
    try:
        proc = subprocess.run(
            ["schtasks", "/Run", "/TN", TASK_NAME], capture_output=True, text=True, timeout=20
        )
    except Exception as exc:
        return {"ok": False, "error": f"schtasks failed: {type(exc).__name__}: {exc}", "transport": "schtasks"}
    if proc.returncode != 0:
        return {
            "ok": False,
            "error": f"schtasks /Run failed: {(proc.stderr or proc.stdout).strip()[:300]}",
            "transport": "schtasks",
        }
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        time.sleep(0.4)
        payload = _read_last()
        if payload and payload.get("job_id") == job_id:
            payload["transport"] = "schtasks"
            return payload
    return {
        "ok": False,
        "error": f"GUI job {op!r} timed out after {timeout_s}s",
        "job_id": job_id,
        "transport": "schtasks",
    }


def call(op: str, args: dict[str, Any] | None = None, timeout_s: int | None = None) -> dict[str, Any]:
    args = args or {}
    timeout_s = int(min(max(timeout_s or DEFAULT_TIMEOUT_S, 10), MAX_TIMEOUT_S))
    info = transport()
    if info["mode"] == "unsupported":
        return {"ok": False, "error": "GUI tools require Windows", "transport": info}
    if info["mode"] == "direct":
        return _run_direct(op, args)
    return _run_queued(op, args, timeout_s)


# --------------------------------------------------------------------------
# tool wrappers
# --------------------------------------------------------------------------
def status(output_lines: int = 20) -> dict[str, Any]:
    payload = call("status", {"output_lines": output_lines})
    payload["bridge"] = transport()
    return payload


def launch(wait_s: int = 30) -> dict[str, Any]:
    return call("launch", {"exe": str(SETTINGS.robei_gui_exe), "wait_s": wait_s}, timeout_s=wait_s + 30)


def open_project(proj_path: str, timeout_s: int = 90) -> dict[str, Any]:
    path = resolve_project_path(proj_path)
    return call("open_project", {"path": str(path)}, timeout_s=timeout_s)


def open_model(name: str, timeout_s: int = 90) -> dict[str, Any]:
    return call("open_model", {"name": name}, timeout_s=timeout_s)


def compile_model(timeout_s: int = 90) -> dict[str, Any]:
    return call("compile", {"timeout_s": 25}, timeout_s=timeout_s)


def output(lines: int = 80) -> dict[str, Any]:
    return call("output", {"lines": lines})


def close(timeout_s: int = 60) -> dict[str, Any]:
    return call("close", {}, timeout_s=timeout_s)
