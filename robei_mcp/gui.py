"""Session-aware bridge between the MCP server and the Robei IDE.

The MCP server may run in Session 0 (SSH) while the desktop lives in Session 1.
In that case every request is queued to the scheduled task ``RobeiMCP_GuiAgent``,
which runs ``robei_mcp/gui_agent/launcher.py`` with pythonw.exe inside the
interactive session. When the server itself already runs on the interactive
desktop the same operations are executed in-process.

The driver ships inside the package, so an installed wheel works too; when it is
missing (or the host is not Windows) ``available()`` returns False and the
server never advertises the ``robei_gui_*`` tools.
"""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from .config import SETTINGS
from .gui_agent.paths import ARGS_FILE, JOB_FILE, LAST_JSON, LOCK_FILE, ensure_run_dir
from .security import resolve_project_path

TASK_NAME = "RobeiMCP_GuiAgent"
GUI_DIR = Path(__file__).resolve().parent / "gui_agent"

DEFAULT_TIMEOUT_S = 90
MAX_TIMEOUT_S = 300


def available() -> bool:
    """True when the GUI bridge can actually run on this host.

    ``ROBEI_MCP_GUI=0`` forces it off even on Windows, which hides the tools
    from ``tools/list`` entirely (useful for CI and for file-level-only use).
    The optional GUI dependencies are checked explicitly: importing
    ``gui_agent.ui`` alone succeeds because pywinauto/Pillow are imported
    lazily, which used to advertise GUI tools that could never run.
    """
    if os.environ.get("ROBEI_MCP_GUI", "").strip().lower() in {"0", "false", "off", "no"}:
        return False
    if os.name != "nt":
        return False
    try:
        import importlib.util

        for module in ("pywinauto", "PIL", "win32gui"):
            if importlib.util.find_spec(module) is None:
                return False
    except Exception:
        return False
    try:
        from .gui_agent import driver, ui  # noqa: F401
    except Exception:
        return False
    return True


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


def task_status() -> dict[str, Any]:
    """Is the Session-1 helper task registered, and in which state?"""
    return {"task": TASK_NAME, **_task_query()}


def _read_last() -> dict[str, Any] | None:
    try:
        return json.loads(LAST_JSON.read_text(encoding="utf-8"))
    except Exception:
        return None


def _run_direct(op: str, args: dict[str, Any]) -> dict[str, Any]:
    from .gui_agent import ui as module

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
    elif op == "health":
        payload = module.uia_health()
    elif op == "compile_all":
        payload = module.compile_all(float(args.get("compile_timeout", 45)), args.get("models"))
    elif op == "save_project":
        payload = module.save_project()
    elif op == "synthesis":
        payload = module.synthesis(float(args.get("timeout_s", 300)))
    elif op == "close":
        payload = module.close()
    elif op == "screenshot":
        from .gui_agent import driver

        payload = driver.shot(str(args.get("label") or "shot"))
    else:
        return {"ok": False, "error": f"unknown op: {op}"}
    payload["transport"] = "direct"
    return payload


def _run_queued(op: str, args: dict[str, Any], timeout_s: int) -> dict[str, Any]:
    if not _acquire_lock():
        return {
            "ok": False,
            "error": "another GUI job is still running; try again in a few seconds",
            "transport": "schtasks",
        }
    try:
        return _run_queued_locked(op, args, timeout_s)
    finally:
        _release_lock()


def _run_queued_locked(op: str, args: dict[str, Any], timeout_s: int) -> dict[str, Any]:
    task = _task_query()
    if not task.get("exists"):
        return {
            "ok": False,
            "error": "scheduled task RobeiMCP_GuiAgent is missing; register it before using GUI tools",
            "detail": task.get("error"),
            "transport": "schtasks",
        }
    job_id = uuid.uuid4().hex[:12]
    ensure_run_dir()
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


def _acquire_lock(wait_s: float = 5.0) -> bool:
    """One GUI job at a time: two clients share one agent-job.json."""
    deadline = time.time() + wait_s
    while time.time() < deadline:
        try:
            fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, f"{os.getpid()} {time.time():.0f}".encode())
            finally:
                os.close(fd)
            return True
        except FileExistsError:
            try:
                if time.time() - LOCK_FILE.stat().st_mtime > MAX_TIMEOUT_S:
                    LOCK_FILE.unlink()  # stale lock from a killed server
                    continue
            except OSError:
                pass
            time.sleep(0.2)
        except OSError:
            return True  # cannot lock at all -> do not block every call
    return False


def _release_lock() -> None:
    try:
        LOCK_FILE.unlink()
    except OSError:
        pass


def call(op: str, args: dict[str, Any] | None = None, timeout_s: int | None = None) -> dict[str, Any]:
    args = args or {}
    timeout_s = int(min(max(timeout_s or DEFAULT_TIMEOUT_S, 10), MAX_TIMEOUT_S))
    started = time.monotonic()
    info = transport()
    if info["mode"] == "unsupported":
        payload: dict[str, Any] = {"ok": False, "error": "GUI tools require Windows", "transport": info}
    elif info["mode"] == "direct":
        payload = _run_direct(op, args)
    else:
        payload = _run_queued(op, args, timeout_s)
    try:
        from . import oplog

        oplog.log_event(
            "gui",
            op=op,
            transport=(payload.get("transport") if isinstance(payload, dict) else None),
            ok=(payload.get("ok") if isinstance(payload, dict) else None),
            ms=round((time.monotonic() - started) * 1000),
            error=(payload.get("error") if isinstance(payload, dict) else None),
        )
    except Exception:
        pass
    return payload


# --------------------------------------------------------------------------
# tool wrappers
# --------------------------------------------------------------------------
def status(output_lines: int = 20, probe: bool = False) -> dict[str, Any]:
    payload = call("status", {"output_lines": output_lines})
    payload["bridge"] = transport()
    if probe:
        payload["bridge_health"] = health()
    return payload


def launch(wait_s: int = 30) -> dict[str, Any]:
    return call("launch", {"exe": str(SETTINGS.robei_gui_exe), "wait_s": wait_s}, timeout_s=wait_s + 30)


def open_project(
    proj_path: str, timeout_s: int = 90, discard_unsaved: bool = False
) -> dict[str, Any]:
    path = resolve_project_path(proj_path)
    return call(
        "open_project",
        {"path": str(path), "discard_unsaved": discard_unsaved},
        timeout_s=timeout_s,
    )


def open_model(name: str, timeout_s: int = 90) -> dict[str, Any]:
    return call("open_model", {"name": name}, timeout_s=timeout_s)


def compile_model(timeout_s: int = 90) -> dict[str, Any]:
    return call("compile", {"timeout_s": 25}, timeout_s=timeout_s)


def output(lines: int = 80) -> dict[str, Any]:
    return call("output", {"lines": lines})


def close(timeout_s: int = 60) -> dict[str, Any]:
    return call("close", {}, timeout_s=timeout_s)


def health() -> dict[str, Any]:
    """Check whether Robei's UIA bridge is alive (it goes silent after Wave)."""
    return call("health", {}, timeout_s=45)


def compile_all(models: list[str] | None = None, timeout_s: int = 240) -> dict[str, Any]:
    """Compile every .model of the open project inside Robei."""
    return call("compile_all", {"models": models, "compile_timeout": 45}, timeout_s=timeout_s)


def save_project() -> dict[str, Any]:
    """File > Save in the IDE (required before Build > Synthesis)."""
    return call("save_project", {}, timeout_s=90)


def synthesis(timeout_s: int = 300) -> dict[str, Any]:
    """Run Build > Synthesis on the open constrain model and wait for it to finish."""
    return call("synthesis", {"timeout_s": timeout_s}, timeout_s=timeout_s + 60)


def screenshot(label: str = "shot", timeout_s: int = 60) -> dict[str, Any]:
    """Grab the whole interactive desktop and save it under workspace/gui_shots."""
    return call("screenshot", {"label": label}, timeout_s=timeout_s)


