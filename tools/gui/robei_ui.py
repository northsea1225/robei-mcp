"""Deprecated location. The UI driver moved to ``robei_mcp.gui_agent.ui``.

Kept as a shim so ``tools/gui/archive`` probes keep working. Every name is
re-exported, including private helpers such as ``_wait_for``.

Note: these are copies. Monkeypatching here (``robei_ui.read_output = x``) has
no effect on the real module - import ``robei_mcp.gui_agent.ui`` for that.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import robei_mcp.gui_agent.ui as _ui  # noqa: E402

_SKIP = {"__name__", "__file__", "__loader__", "__spec__", "__package__", "__builtins__", "__doc__"}
globals().update({k: v for k, v in vars(_ui).items() if k not in _SKIP})
