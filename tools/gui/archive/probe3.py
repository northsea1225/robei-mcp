"""Stage-L1: read-only UIA dump of Robei's control tree (does not touch WM_GETOBJECT)."""
from __future__ import annotations

import json
import os
from pathlib import Path

OUT_DIR = Path(r"C:\dev\robei-mcp\workspace\gui_shots")


def exe_name(pid: int) -> str:
    import ctypes

    kernel32 = ctypes.windll.kernel32
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
        rows.append({"hwnd": hwnd, "pid": pid, "title": title, "class": win32gui.GetClassName(hwnd)})
        return True

    win32gui.EnumWindows(cb, None)
    return rows


def depth_of(ctrl, root) -> int:
    try:
        node = ctrl
        depth = 0
        while depth < 40:
            parent = node.parent()
            if parent is None:
                break
            depth += 1
            node = parent
        return depth
    except Exception:
        return -1


def rect_of(obj) -> list[int]:
    try:
        return [int(obj.left), int(obj.top), int(obj.right), int(obj.bottom)]
    except AttributeError:
        return [int(v) for v in obj]


def run(limit: int = 600) -> dict:
    from pywinauto import Application

    rows = []
    for win in windows():
        rec = dict(win)
        try:
            app = Application(backend="uia").connect(handle=win["hwnd"])
            dlg = app.window(handle=win["hwnd"])
            kids = dlg.descendants()
            rec["count"] = len(kids)
            rec["rect"] = rect_of(dlg.rectangle())
            items = []
            errors = []
            for ctrl in kids[:limit]:
                try:
                    info = ctrl.element_info
                    items.append(
                        {
                            "depth": depth_of(ctrl, dlg),
                            "type": info.control_type,
                            "name": (info.name or "")[:70],
                            "auto_id": (info.automation_id or "")[:60],
                            "class": (info.class_name or "")[:60],
                            "rect": rect_of(info.rectangle),
                            "enabled": bool(ctrl.is_enabled()),
                        }
                    )
                except Exception as exc:
                    if len(errors) < 3:
                        errors.append(f"{type(exc).__name__}: {exc}")
                    continue
            rec["items"] = items
            rec["item_errors"] = errors
        except Exception as exc:
            rec["error"] = f"{type(exc).__name__}: {exc}"
        rows.append(rec)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tree_file = OUT_DIR / "uia_tree.json"
    tree_file.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    return {
        "ok": True,
        "windows": [{k: v for k, v in r.items() if k != "items"} for r in rows],
        "tree_file": str(tree_file),
    }


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
