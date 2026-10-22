"""Stage-L1 probe: does Qt's compiled-in accessibility bridge answer MSAA/UIA?

Runs inside the interactive Windows session. Read-only: sends WM_GETOBJECT,
asks pywinauto/UIA for the control tree, and writes the tree to a text file.
"""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WM_GETOBJECT = 0x004D
SMTO_ABORTIFHUNG = 0x0002
SMTO_BLOCK = 0x0001
OBJIDS = {"window": 0, "client": -4, "nativeom": -16, "uia": -25}

OUT_DIR = Path(r"C:\dev\robei-mcp\workspace\gui_shots")


def exe_name(pid: int) -> str:
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return ""
    try:
        size = ctypes.c_ulong(1024)
        buf = ctypes.create_unicode_buffer(1024)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value)
        return ""
    finally:
        kernel32.CloseHandle(handle)


def windows(exe: str = "Robei.exe") -> list[dict]:
    import win32gui
    import win32process

    rows: list[dict] = []

    def cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd)
        if not title:
            return True
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if exe_name(pid).lower() != exe.lower():
            return True
        rows.append(
            {
                "hwnd": hwnd,
                "pid": pid,
                "title": title,
                "class": win32gui.GetClassName(hwnd),
                "rect": list(win32gui.GetWindowRect(hwnd)),
            }
        )
        return True

    win32gui.EnumWindows(cb, None)
    return rows


def wm_getobject(hwnd: int, objid: int, timeout_ms: int = 2000):
    result = ctypes.c_size_t(0)
    ok = user32.SendMessageTimeoutW(
        hwnd,
        WM_GETOBJECT,
        0,
        objid,
        SMTO_ABORTIFHUNG | SMTO_BLOCK,
        timeout_ms,
        ctypes.byref(result),
    )
    if not ok:
        return {"ok": False, "last_error": ctypes.get_last_error(), "value": None}
    return {"ok": True, "value": int(result.value)}


def uia_tree(hwnd: int, limit: int = 300):
    from pywinauto import Application

    app = Application(backend="uia").connect(handle=hwnd)
    dlg = app.window(handle=hwnd)
    kids = dlg.descendants()
    rows = []
    for ctrl in kids[:limit]:
        try:
            info = ctrl.element_info
            rows.append(
                {
                    "type": info.control_type,
                    "name": (info.name or "")[:70],
                    "auto_id": (info.automation_id or "")[:50],
                    "class": (info.class_name or "")[:50],
                    "rect": [int(v) for v in info.rectangle],
                    "enabled": bool(info.is_enabled),
                }
            )
        except Exception:
            continue
    return rows


def run(limit: int = 300) -> dict:
    rows = []
    out = []
    for win in windows():
        rec = dict(win)
        for name, objid in OBJIDS.items():
            rec[f"wm_getobject_{name}"] = wm_getobject(win["hwnd"], objid)
        for attempt in (1, 2):
            try:
                kids = uia_tree(win["hwnd"], limit)
                rec[f"uia_count_{attempt}"] = len(kids)
                if attempt == 2:
                    out = kids
            except Exception as exc:
                rec[f"uia_error_{attempt}"] = f"{type(exc).__name__}: {exc}"
        rows.append(rec)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tree_file = OUT_DIR / "uia_tree.json"
    tree_file.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"ok": True, "windows": rows, "tree_nodes": len(out), "tree_file": str(tree_file)}


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
