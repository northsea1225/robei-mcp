"""Diagnose whether Qt's accessibility bridge answers MSAA/UIA requests."""
from __future__ import annotations

import ctypes
import json

user32 = ctypes.windll.user32
WM_GETOBJECT = 0x004D
OBJID_WINDOW = 0
OBJID_CLIENT = -4


def _windows(exe: str) -> list[dict]:
    import win32gui
    import win32process
    import os

    rows: list[dict] = []

    def cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd)
        if not title:
            return True
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        kernel32 = ctypes.windll.kernel32
        h = kernel32.OpenProcess(0x1000, False, pid)
        name = ""
        if h:
            buf = ctypes.create_unicode_buffer(1024)
            size = ctypes.c_ulong(1024)
            if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                name = os.path.basename(buf.value)
            kernel32.CloseHandle(h)
        if name.lower() != exe.lower():
            return True
        rows.append({"hwnd": hwnd, "title": title, "class": win32gui.GetClassName(hwnd), "rect": list(win32gui.GetWindowRect(hwnd))})
        return True

    win32gui.EnumWindows(cb, None)
    return rows


def run() -> dict:
    results = []
    for win in _windows("Robei.exe"):
        hwnd = win["hwnd"]
        win["wm_getobject_window"] = int(user32.SendMessageW(hwnd, WM_GETOBJECT, 0, OBJID_WINDOW))
        win["wm_getobject_client"] = int(user32.SendMessageW(hwnd, WM_GETOBJECT, 0, OBJID_CLIENT))
        try:
            from pywinauto import Application

            app = Application(backend="uia").connect(handle=hwnd)
            dlg = app.window(handle=hwnd)
            kids = dlg.descendants()
            win["uia_count"] = len(kids)
            win["uia_sample"] = [
                (getattr(k.element_info, "control_type", "?"), (getattr(k.element_info, "name", "") or "")[:24])
                for k in kids[:8]
            ]
        except Exception as exc:
            win["uia_error"] = f"{type(exc).__name__}: {exc}"
        results.append(win)
    return {"ok": True, "windows": results}


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
