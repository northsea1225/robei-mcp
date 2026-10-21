"""Session-1 screenshot helper for the Robei MCP GUI work.

Run from a scheduled task with /IT so it executes in the interactive desktop.
Prints one JSON line with session id, screen geometry and pixel statistics so a
black (Session 0) desktop is immediately visible.
"""
from __future__ import annotations

import ctypes
import json
import sys
import time
from pathlib import Path

from PIL import ImageGrab, ImageStat


def session_id() -> int | None:
    value = ctypes.c_ulong()
    if ctypes.windll.kernel32.ProcessIdToSessionId(ctypes.windll.kernel32.GetCurrentProcessId(), ctypes.byref(value)):
        return int(value.value)
    return None


def main() -> int:
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(r"C:\dev\robei-mcp\workspace\gui_shots")
    label = sys.argv[2] if len(sys.argv) > 2 else "shot"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = out_dir / f"{label}-{stamp}.png"

    image = ImageGrab.grab(all_screens=True)
    image.save(target)
    stat = ImageStat.Stat(image.convert("RGB"))
    extrema = image.convert("L").getextrema()
    payload = {
        "session_id": session_id(),
        "path": str(target),
        "size_bytes": target.stat().st_size,
        "width": image.width,
        "height": image.height,
        "mean_rgb": [round(v, 2) for v in stat.mean],
        "luma_min": extrema[0],
        "luma_max": extrema[1],
        "black_screen": extrema[1] < 8,
    }
    print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
