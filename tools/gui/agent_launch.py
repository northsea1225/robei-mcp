"""Console-less launcher for the GUI agent (used by the scheduled task)."""
from __future__ import annotations

import sys
from pathlib import Path

import agent

ARGS_FILE = Path(r"C:\dev\robei-mcp\tools\gui\agent-args.txt")


def main() -> int:
    raw = ARGS_FILE.read_text(encoding="utf-8", errors="replace").strip()
    # Backslashes are literal Windows path separators here; do not run shlex.
    argv = ["agent"] + [tok.strip('"') for tok in raw.split()]
    try:
        return agent.main(argv)
    except Exception as exc:  # keep failures visible in last.json
        import json

        out = Path(r"C:\dev\robei-mcp\workspace\gui_shots")
        out.mkdir(parents=True, exist_ok=True)
        (out / "last.json").write_text(
            json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}", "args": raw}, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
