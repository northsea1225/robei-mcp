"""Push the repo from the interactive session, where the Windows credential store works."""
from __future__ import annotations

import os
import re
import subprocess

ROOT = r"C:\dev\robei-mcp"


def _redact(text: str) -> str:
    text = re.sub(r"https://[^@\s]*@", "https://***@", text)
    text = re.sub(r"gh[pousr]_[A-Za-z0-9]+", "***", text)
    text = re.sub(r"github_pat_[A-Za-z0-9_]+", "***", text)
    return text


def run() -> dict:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"
    env["GIT_ASKPASS"] = "echo"
    steps = []
    for args in (
        ["git", "rev-parse", "--short", "HEAD"],
        ["git", "push", "origin", "master"],
        ["git", "rev-parse", "--short", "origin/master"],
    ):
        try:
            proc = subprocess.run(
                args, cwd=ROOT, capture_output=True, text=True, timeout=90, env=env
            )
            steps.append(
                {
                    "cmd": " ".join(args[:3]),
                    "rc": proc.returncode,
                    "out": _redact((proc.stdout + proc.stderr).strip())[-800:],
                }
            )
        except subprocess.TimeoutExpired:
            steps.append({"cmd": " ".join(args[:3]), "rc": "timeout", "out": ""})
    ok = all(s["rc"] == 0 for s in steps)
    return {"ok": ok, "steps": steps}


if __name__ == "__main__":
    import json

    print(json.dumps(run(), ensure_ascii=False, indent=1))
