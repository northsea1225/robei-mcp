"""Deprecated location. The driver moved to ``robei_mcp.gui_agent.driver``."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from robei_mcp.gui_agent.driver import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
