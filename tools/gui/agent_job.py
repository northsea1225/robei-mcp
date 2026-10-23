"""Deprecated location. The job dispatcher moved to ``robei_mcp.gui_agent.job``."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from robei_mcp.gui_agent.job import run  # noqa: E402

if __name__ == "__main__":
    import json

    print(json.dumps(run(), ensure_ascii=False, indent=1))
