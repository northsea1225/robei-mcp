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
    "refresh": lambda a: robei_ui.refresh(),
    "run": lambda a: robei_ui.run_simulation(float(a.get("timeout_s", 25))),
    "wave": lambda a: robei_ui.open_wave(float(a.get("timeout_s", 15))),
    "open_model": lambda a: robei_ui.open_model(a["name"]),
    "code_read": lambda a: robei_ui.code_read(),
    "code_write": lambda a: robei_ui.code_write(a["code"], bool(a.get("save", True))),
    "launch": lambda a: robei_ui.launch(a["exe"], float(a.get("wait_s", 20))),
    "close": lambda a: robei_ui.close(),
    "experiment": lambda a: robei_ui.experiment(
        a.get("exe"),
        a["path"],
        a["test"],
        bool(a.get("restart", False)),
        bool(a.get("wave", True)),
        float(a.get("compile_timeout", 45)),
        float(a.get("run_timeout", 45)),
        a.get("signals"),
        bool(a.get("heal", True)),
    ),
    "wave_load": lambda a: robei_ui.wave_load(a.get("signals"), int(a.get("max_signals", 12))),
    "health": lambda a: robei_ui.uia_health(),
    "compile_all": lambda a: robei_ui.compile_all(float(a.get("compile_timeout", 45)), a.get("models")),
    "save_project": lambda a: robei_ui.save_project(),
    "synthesis": lambda a: robei_ui.synthesis(float(a.get("timeout_s", 300))),
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
