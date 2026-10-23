"""Deprecated location. The UI driver moved to ``robei_mcp.gui_agent.ui``.

Kept as a shim so the archived probes in ``tools/gui/archive`` keep working.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from robei_mcp.gui_agent.ui import *  # noqa: E402,F401,F403
