"""Job dispatcher executed inside Session 1 by the RobeiMCP_GuiAgent task.

Reads tools/gui/agent-job.json, runs one Robei UI operation and returns the
payload so that agent_launch.py can persist it as workspace/gui_shots/last.json.
"""
from __future__ import annotations

import json
from pathlib import Path

import robei_ui

JOB_FILE = Path(r"C:\dev\robei-mcp\tools\gui\agent-job.json")

OPS = {
    "status": lambda a: robei_ui.status(int(a.get("output_lines", 20))),
    "open_project": lambda a: robei_ui.open_project(a["path"]),
    "compile": lambda a: robei_ui.compile_model(float(a.get("timeout_s", 25))),
    "output": lambda a: robei_ui.output(int(a.get("lines", 80))),
    "open_model": lambda a: robei_ui.open_model(a["name"]),
    "code_read": lambda a: robei_ui.code_read(),
    "code_write": lambda a: robei_ui.code_write(a["code"], bool(a.get("save", True))),
    "launch": lambda a: robei_ui.launch(a["exe"], float(a.get("wait_s", 20))),
    "close": lambda a: robei_ui.close(),
}


def run() -> dict:
    try:
        job = json.loads(JOB_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "error": f"cannot read job file: {type(exc).__name__}: {exc}"}

    job_id = job.get("job_id")
    op = job.get("op")
    handler = OPS.get(op)
    if handler is None:
        return {"ok": False, "error": f"unknown op: {op!r}", "job_id": job_id, "op": op}
    try:
        payload = handler(job.get("args") or {})
    except Exception as exc:
        payload = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    payload["job_id"] = job_id
    payload["op"] = op
    return payload
