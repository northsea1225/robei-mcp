"""Which input primitives actually work on this desktop?"""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes as wt


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
        ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("dwExtraInfo", wt.LPVOID),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("mi", MOUSEINPUT)]


def _send_abs(user32, x: int, y: int) -> int:
    sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    nx = int(round(x * 65535 / max(1, sw - 1)))
    ny = int(round(y * 65535 / max(1, sh - 1)))
    inp = INPUT(type=0, mi=MOUSEINPUT(nx, ny, 0, 0x0001 | 0x8000, 0, None))
    return int(user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT)))


def run() -> dict:
    import subprocess

    killed = []
    for name in ("AclosGameProxy.exe", "GameViewerServer.exe", "GameViewerHealthd.exe",
                 "MuMuRemoteBackend.exe", "MumuRemoteHealthd.exe"):
        proc = subprocess.run(["taskkill", "/F", "/IM", name], capture_output=True, text=True)
        if proc.returncode == 0:
            killed.append(name)

    user32 = ctypes.windll.user32
    out: dict = {}
    import win32api

    start = win32api.GetCursorPos()
    out["start"] = list(start)

    rc = wt.RECT()
    ok = user32.GetClipCursor(ctypes.byref(rc))
    out["clip"] = {"ok": bool(ok), "rect": [rc.left, rc.top, rc.right, rc.bottom],
                   "err": ctypes.get_last_error()}

    out["set_same_pos"] = bool(user32.SetCursorPos(start[0], start[1]))
    out["set_same_err"] = ctypes.get_last_error()

    out["set_plus10"] = bool(user32.SetCursorPos(start[0] + 10, start[1] + 10))
    time.sleep(0.3)
    out["after_plus10"] = list(win32api.GetCursorPos())

    out["sendinput_rc"] = _send_abs(user32, 600, 500)
    time.sleep(0.3)
    out["after_sendinput"] = list(win32api.GetCursorPos())

    out["cursor_info"] = None
    class CURSORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wt.DWORD), ("flags", wt.DWORD), ("hCursor", wt.LPVOID),
                    ("ptScreenPos", wt.POINT)]
    ci = CURSORINFO(); ci.cbSize = ctypes.sizeof(CURSORINFO)
    if user32.GetCursorInfo(ctypes.byref(ci)):
        out["cursor_info"] = {"flags": ci.flags, "pos": [ci.ptScreenPos.x, ci.ptScreenPos.y]}

    try:
        user32.SetCursorPos(start[0], start[1])
    except Exception:
        pass
    out["killed_first"] = killed
    return {"ok": True, **out}
