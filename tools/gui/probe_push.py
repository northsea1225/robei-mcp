"""Push the repo from the interactive session, where the Windows credential store works."""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = r"C:\dev\robei-mcp"


def _redact(text: str) -> str:
    text = re.sub(r"https://[^@\s]*@", "https://***@", text)
    text = re.sub(r"gh[pousr]_[A-Za-z0-9]+", "***", text)
    text = re.sub(r"github_pat_[A-Za-z0-9_]+", "***", text)
    return text


def run(ref: str | None = None) -> dict:
    if not ref:
        try:
            raw = (Path(ROOT) / "workspace" / "gui_agent" / "agent-args.txt").read_text(encoding="utf-8").strip().split()
            ref = raw[1] if len(raw) > 1 and raw[0] == "probe_push" else "master"
        except OSError:
            ref = "master"
    if ref != "master" and not re.fullmatch(r"refs/tags/v[0-9][0-9A-Za-z.-]*", ref):
        return {"ok": False, "error": "ref must be master or refs/tags/v*"}
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"
    env["GIT_ASKPASS"] = "echo"
    verify = ["git", "rev-parse", "--short", "origin/master"] if ref == "master" else ["git", "rev-parse", "--short", f"{ref}^{{commit}}"]
    steps = []
    for args in (
        ["git", "rev-parse", "--short", "HEAD"],
        ["git", "push", "origin", ref],
        verify,
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
    return {"ok": ok, "ref": ref, "steps": steps}


if __name__ == "__main__":
    import json

    ref = sys.argv[1] if len(sys.argv) > 1 else "master"
    print(json.dumps(run(ref), ensure_ascii=False, indent=1))
