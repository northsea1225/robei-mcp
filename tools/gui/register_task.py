"""Register (or remove) the Session-1 helper task used by the robei_gui_* tools.

The MCP server often runs in Session 0 (SSH), where Windows refuses to attach to
the desktop. The task re-runs robei_mcp/gui_agent/launcher.py with pythonw.exe
inside the logged-in session, so UIA can reach the Robei window.

Usage:
    python tools/gui/register_task.py            # create / refresh the task
    python tools/gui/register_task.py --remove   # delete the task
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYTHONW = ROOT / ".venv" / "Scripts" / "pythonw.exe"
LAUNCHER = ROOT / "robei_mcp" / "gui_agent" / "launcher.py"
TASK = "RobeiMCP_GuiAgent"


def create() -> int:
    user = os.environ.get("USERNAME") or os.environ.get("USER")
    if not user:
        print("cannot determine the interactive user name", file=sys.stderr)
        return 2
    for path in (PYTHONW, LAUNCHER):
        if not path.is_file():
            print(f"missing: {path}", file=sys.stderr)
            return 2
    command = f'"{PYTHONW}" "{LAUNCHER}"'
    args = [
        "schtasks", "/Create", "/TN", TASK,
        "/SC", "ONCE", "/ST", "23:58",
        "/RU", user, "/IT", "/F",
        "/TR", command,
    ]
    proc = subprocess.run(args, capture_output=True, text=True)
    print(proc.stdout.strip() or proc.stderr.strip())
    return proc.returncode


def remove() -> int:
    proc = subprocess.run(["schtasks", "/Delete", "/TN", TASK, "/F"], capture_output=True, text=True)
    print(proc.stdout.strip() or proc.stderr.strip())
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(remove() if "--remove" in sys.argv[1:] else create())
