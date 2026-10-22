"""Stage-L1: enumerate every top menu through UIA, then trigger one real action.

Read-only: only opens menus (ESC afterwards) and clicks Compile once.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

OUT_DIR = Path(r"C:\dev\robei-mcp\workspace\gui_shots")
MENU_BAR_ID = "QApplication.Romain.QMenuBar"
TOOLBAR_ID = "QApplication.Romain.QToolBar"
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


def rect_of(obj) -> list[int]:
    try:
        return [int(obj.left), int(obj.top), int(obj.right), int(obj.bottom)]
    except AttributeError:
        return [int(v) for v in obj]


def main_window():
    from pywinauto import Application

    for win in robie_windows():
        if "Robei" in win["title"] and win["class"].startswith("Qt"):
            try:
                app = Application(backend="uia").connect(handle=win["hwnd"])
                dlg = app.window(handle=win["hwnd"])
                if dlg.descendants():
                    return dlg, win
            except Exception:
                continue
    return None, None


def popup_menus() -> list[dict]:
    from pywinauto import Application

    out = []
    for w in robie_windows():
        if "Popup" not in w["class"]:
            continue
        try:
            app = Application(backend="uia").connect(handle=w["hwnd"])
            p = app.window(handle=w["hwnd"])
            items = [
                {"type": c.element_info.control_type, "name": (c.element_info.name or "")[:50]}
                for c in p.descendants()[:80]
            ]
        except Exception as exc:
            items = [{"error": f"{type(exc).__name__}: {exc}"}]
        out.append({"title": w["title"], "class": w["class"], "items": items})
    return out


def read_output(dlg) -> str:
    try:
        edit = dlg.child_window(auto_id=OUTPUT_ID, control_type="Edit")
        return edit.window_text()
    except Exception as exc:
        return f"ERR {type(exc).__name__}: {exc}"


def run(open_menus: bool = True, do_compile: bool = True) -> dict:
    from pywinauto import keyboard, mouse

    dlg, win = main_window()
    if dlg is None:
        return {"ok": False, "error": "Robei main window not found"}

    report: dict = {"ok": True, "window": win}

    # toolbar inventory: QToolButtons surface as CheckBox in this Qt build
    toolbar = []
    try:
        bar = dlg.child_window(auto_id=TOOLBAR_ID)
        for ctrl in bar.descendants():
            info = ctrl.element_info
            if info.control_type in {"Button", "CheckBox"}:
                toolbar.append({"name": info.name, "type": info.control_type, "rect": rect_of(info.rectangle)})
    except Exception as exc:
        report["toolbar_error"] = f"{type(exc).__name__}: {exc}"
    report["toolbar_buttons"] = toolbar

    menus = []
    try:
        mb = dlg.child_window(auto_id=MENU_BAR_ID)
        menus = [
            {"name": i.element_info.name, "rect": rect_of(i.element_info.rectangle)}
            for i in mb.descendants(control_type="MenuItem")
        ]
    except Exception as exc:
        report["menu_error"] = f"{type(exc).__name__}: {exc}"
    report["menus"] = [m["name"] for m in menus]

    report["output_before"] = read_output(dlg)

    if open_menus:
        opened = []
        for m in menus:
            x = (m["rect"][0] + m["rect"][2]) // 2
            y = (m["rect"][1] + m["rect"][3]) // 2
            keyboard.send_keys("{ESC}")
            time.sleep(0.3)
            mouse.click(button="left", coords=(x, y))
            time.sleep(1.1)
            opened.append({"menu": m["name"], "popups": popup_menus()})
            keyboard.send_keys("{ESC}")
            time.sleep(0.4)
        report["open_menus"] = opened

    if do_compile:
        target = next((b for b in toolbar if b["name"] == "Compile"), None)
        action = {}
        if target:
            x = (target["rect"][0] + target["rect"][2]) // 2
            y = (target["rect"][1] + target["rect"][3]) // 2
            action = {"via": "toolbar", "coords": [x, y]}
            mouse.click(button="left", coords=(x, y))
        report["compile_action"] = action
        if action:
            time.sleep(3.0)
            from PIL import ImageGrab

            shot_path = OUT_DIR / time.strftime("l1-compile-%Y%m%d-%H%M%S.png")
            ImageGrab.grab(all_screens=True).save(shot_path)
            report["compile_screenshot"] = str(shot_path)
            report["output_after"] = read_output(dlg)
            report["windows_after"] = robie_windows()

    return report


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
