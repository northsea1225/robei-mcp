"""Stage-L1: read-only snapshot of Robei's UI state (files tree, tabs, output)."""
from __future__ import annotations

import json
import time
from pathlib import Path

OUT_DIR = Path(r"C:\dev\robei-mcp\workspace\gui_shots")
FILE_TREE_ID = "QApplication.Romain.FileManager.FileTree"
OUTPUT_ID = "QApplication.Romain.Output.TextEdit"


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

    tree_rows = []
    try:
        tree = dlg.child_window(auto_id=FILE_TREE_ID)
        for item in tree.descendants(control_type="TreeItem"):
            try:
                tree_rows.append(item.window_text())
            except Exception:
                tree_rows.append("<err>")
    except Exception as exc:
        report["tree_error"] = f"{type(exc).__name__}: {exc}"
    report["file_tree"] = tree_rows

    tabs = []
    for tab in dlg.descendants(control_type="Tab"):
        try:
            tabs.append({"auto_id": tab.element_info.automation_id, "items": [
                t.window_text() for t in tab.children(control_type="TabItem")
            ]})
        except Exception:
            continue
    report["tabs"] = tabs

    try:
        report["output"] = dlg.child_window(auto_id=OUTPUT_ID, control_type="Edit").window_text()
    except Exception as exc:
        report["output"] = f"ERR {type(exc).__name__}: {exc}"

    shot = OUT_DIR / time.strftime("l1-state-%Y%m%d-%H%M%S.png")
    ImageGrab.grab(all_screens=True).save(shot)
    report["screenshot"] = str(shot)
    return report


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
