"""Runtime paths for the GUI bridge.

Control files live under the workspace, never inside the installed package:
a wheel in site-packages is not a writable runtime directory.
"""
from __future__ import annotations

from pathlib import Path

from ..config import SETTINGS

RUN_DIR = SETTINGS.workspace / "gui_agent"
ARGS_FILE = RUN_DIR / "agent-args.txt"
JOB_FILE = RUN_DIR / "agent-job.json"
LOCK_FILE = RUN_DIR / "agent.lock"
SHOTS_DIR = SETTINGS.workspace / "gui_shots"
LAST_JSON = SHOTS_DIR / "last.json"


def ensure_run_dir() -> Path:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    return RUN_DIR
