"""Stage-L1: which project is loaded? Dump every Tree control and the status bars."""
from __future__ import annotations

import json
import time
from pathlib import Path

OUT_DIR = Path(r"C:\dev\robei-mcp\workspace\gui_shots")


def robie_windows() -> list[dict]:
    import ctypes
    import os

    import win32gui
    import win32process

    kernel32 = ctypes.windll.kernel32
    rows: list[dict] = []

    def exe(pid: int) -> str:
        h = kernel32.OpenProcess(0x1000, False, pid)
        if not h:
            return ""
        try:
            size = ctypes.c_ulong(1024)
            buf = ctypes.create_unicode_buffer(1024)
            if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return os.path.basename(buf.value)
            return ""
        finally:
            kernel32.CloseHandle(h)

    def cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd)
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if exe(pid).lower() != "robei.exe":
            return True
        rows.append({"hwnd": hwnd, "title": title, "class": win32gui.GetClassName(hwnd)})
        return True

    win32gui.EnumWindows(cb, None)
    return rows


def attach(hwnd: int):
    from pywinauto import Application

    return Application(backend="uia").connect(handle=hwnd).window(handle=hwnd)


def run() -> dict:
    from PIL import ImageGrab

    win = next((w for w in robie_windows() if w["class"].startswith("Qt")), None)
    if win is None:
        return {"ok": False, "error": "no Robei window"}
    dlg = attach(win["hwnd"])
    report: dict = {"ok": True, "window": win}

    trees = []
    for tree in dlg.descendants(control_type="Tree"):
        try:
            info = tree.element_info
            items = []
            for item in tree.descendants(control_type="TreeItem")[:40]:
                items.append(item.window_text())
            trees.append(
                {
                    "auto_id": info.automation_id,
                    "class": info.class_name,
                    "rect": [int(info.rectangle.left), int(info.rectangle.top), int(info.rectangle.right), int(info.rectangle.bottom)],
                    "items": items,
                }
            )
        except Exception as exc:
            trees.append({"error": f"{type(exc).__name__}: {exc}"})
    report["trees"] = trees

    texts = []
    for ctrl in dlg.descendants(control_type="Text"):
        try:
            t = ctrl.window_text().strip()
            if t and len(t) < 120:
                texts.append(t)
        except Exception:
            continue
    report["texts"] = texts[:40]

    shot = OUT_DIR / time.strftime("l1-trees-%Y%m%d-%H%M%S.png")
    ImageGrab.grab(all_screens=True).save(shot)
    report["screenshot"] = str(shot)
    return report


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
