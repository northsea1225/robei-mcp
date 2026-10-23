"""Report whether this process can move the mouse (active-desktop diagnosis)."""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt


def _obj_name(handle, index: int = 2) -> str:
    user32 = ctypes.windll.user32
    buf = ctypes.create_unicode_buffer(256)
    need = wt.DWORD()
    if user32.GetUserObjectInformationW(handle, index, buf, 512, ctypes.byref(need)):
        return buf.value
    return f"<err {ctypes.get_last_error()}>"


def run() -> dict:
    out: dict = {}
    user32 = ctypes.windll.user32
    k32 = ctypes.windll.kernel32
    user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    user32.SetCursorPos.restype = wt.BOOL

    try:
        out["window_station"] = _obj_name(user32.GetProcessWindowStation())
        out["thread_desktop_name"] = _obj_name(user32.GetThreadDesktop(k32.GetCurrentThreadId()))
        out["process_session"] = ctypes.c_ulong(
            k32.WTSGetActiveConsoleSessionId()
        ).value
    except Exception as exc:
        out["station_error"] = f"{type(exc).__name__}: {exc}"

    try:
        import win32api

        pos = win32api.GetCursorPos()
        out["cursor"] = list(pos)
        ctypes.set_last_error(0)
        out["set_cursor_pos"] = bool(user32.SetCursorPos(int(pos[0]), int(pos[1])))
        out["set_cursor_err"] = ctypes.get_last_error()
    except Exception as exc:
        out["cursor_error"] = f"{type(exc).__name__}: {exc}"

    try:
        out["monitors"] = int(user32.GetSystemMetrics(80))
        out["screen"] = [int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))]
        hdesk = user32.OpenInputDesktop(0, False, 0x0100)
        out["input_desktop"] = _obj_name(hdesk) if hdesk else f"null({ctypes.get_last_error()})"
    except Exception as exc:
        out["display_error"] = f"{type(exc).__name__}: {exc}"

    try:
        class LII(ctypes.Structure):
            _fields_ = [("cbSize", wt.UINT), ("dwTime", wt.DWORD)]
        li = LII(); li.cbSize = ctypes.sizeof(LII)
        user32.GetLastInputInfo(ctypes.byref(li))
        out["last_input_ms"] = int(li.dwTime)
        out["tick_ms"] = int(ctypes.windll.kernel32.GetTickCount())
    except Exception as exc:
        out["last_input_error"] = f"{type(exc).__name__}: {exc}"

    try:
        hwnd = user32.GetForegroundWindow()
        title = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, title, 256)
        out["foreground"] = {"hwnd": hwnd, "title": title.value[:40]}
    except Exception as exc:
        out["foreground_error"] = f"{type(exc).__name__}: {exc}"

    return {"ok": True, **out}
