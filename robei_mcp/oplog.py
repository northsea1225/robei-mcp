"""Append-only operation log for the MCP server.

Every subprocess and every GUI round-trip writes one JSON line under
``workspace/logs/mcp-YYYYMMDD.log``. It exists so a failed run can be explained
after the fact: the tool only returns the last chunk of output to the client.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import SETTINGS

MAX_FIELD_CHARS = 400


def _trim(value: Any) -> Any:
    if isinstance(value, str):
        return value if len(value) <= MAX_FIELD_CHARS else value[:MAX_FIELD_CHARS] + "…"
    return value


def log_path() -> Path:
    return SETTINGS.workspace / "logs" / f"mcp-{datetime.now():%Y%m%d}.log"


def log_event(kind: str, **fields: Any) -> None:
    """Best effort: logging must never break a tool call."""
    try:
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": datetime.now().isoformat(timespec="milliseconds"),
            "pid": os.getpid(),
            "kind": kind,
            **{k: _trim(v) for k, v in fields.items()},
        }
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        return


def tail(lines: int = 50) -> list[dict[str, Any]]:
    """Last N records, newest last; used by tests and manual inspection."""
    path = log_path()
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines()[-max(1, lines):]:
        try:
            rows.append(json.loads(raw))
        except Exception:
            continue
    return rows


def since(seconds: float) -> list[dict[str, Any]]:
    cutoff = time.time() - seconds
    out = []
    for row in tail(500):
        try:
            stamp = datetime.fromisoformat(row["ts"]).timestamp()
        except Exception:
            continue
        if stamp >= cutoff:
            out.append(row)
    return out
