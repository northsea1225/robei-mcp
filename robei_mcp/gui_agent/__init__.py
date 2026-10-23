"""Optional GUI bridge: the UIA driver and its Session-1 launcher.

Nothing here is imported at server start. ``robei_mcp.gui.available()`` probes
this package, and the 10 ``robei_gui_*`` tools are only advertised when the
probe succeeds (Windows + driver present).
"""
from __future__ import annotations

__all__ = ["driver", "job", "launcher", "paths", "ui"]
