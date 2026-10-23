"""Console-less launcher for the GUI agent (run by the scheduled task)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

# The task executes this file directly, so make ``robei_mcp`` importable first.
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from robei_mcp.gui_agent import driver  # noqa: E402
from robei_mcp.gui_agent.paths import ARGS_FILE, SHOTS_DIR  # noqa: E402


def main() -> int:
    raw = ARGS_FILE.read_text(encoding="utf-8", errors="replace").strip()
    # Backslashes are literal Windows path separators here; do not run shlex.
    argv = ["agent"] + [tok.strip('"') for tok in raw.split()]
    try:
        return driver.main(argv)
    except Exception as exc:  # keep failures visible in last.json
        SHOTS_DIR.mkdir(parents=True, exist_ok=True)
        (SHOTS_DIR / "last.json").write_text(
            json.dumps(
                {"ok": False, "error": f"{type(exc).__name__}: {exc}", "args": raw},
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
