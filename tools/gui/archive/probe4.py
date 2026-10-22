"""Stage-L1: prove the UIA bridge can drive Robei (open a menu, click a toolbar action)
and that widget state (Output log) can be read back. Read-only w.r.t. project files.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

OUT_DIR = Path(r"C:\dev\robei-mcp\workspace\gui_shots")

MENU_BAR_ID = "QApplication.Romain.QMenuBar"
OUTPUT_ID = "QApplication.Romain.Output.TextEdit"
TOOLBAR_ID = "QApplication.Romain.QToolBar"


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
    import win32gui

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


def read_output(dlg) -> dict:
    result: dict = {}
    try:
        edit = dlg.child_window(auto_id=OUTPUT_ID, control_type="Edit")
        attempts = {}
        for label, fn in (
            ("window_text", lambda: edit.window_text()),
            ("get_value", lambda: edit.get_value()),
            ("texts", lambda: edit.texts()),
            ("value_pattern", lambda: edit.iface_value.CurrentValue),
            ("text_pattern", lambda: edit.iface_text.DocumentText()),
        ):
            try:
                val = fn()
                attempts[label] = val if isinstance(val, (str, list)) else repr(val)
            except Exception as exc:
                attempts[label] = f"{type(exc).__name__}: {exc}"
        result["edit_found"] = True
        result["attempts"] = attempts
    except Exception as exc:
        result["edit_found"] = False
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def run() -> dict:
    from pywinauto import keyboard, mouse

    dlg, win = main_window()
    if dlg is None:
        return {"ok": False, "error": "Robei main window not found"}

    report: dict = {"ok": True, "window": win}

    # 1. toolbar + menu bar inventory through UIA (not coordinates)
    toolbar = []
    try:
        bar = dlg.child_window(auto_id=TOOLBAR_ID)
        for btn in bar.descendants(control_type="Button"):
            info = btn.element_info
            toolbar.append({"name": info.name, "rect": rect_of(info.rectangle)})
    except Exception as exc:
        report["toolbar_error"] = f"{type(exc).__name__}: {exc}"
    report["toolbar_buttons"] = toolbar

    menus = []
    try:
        mb = dlg.child_window(auto_id=MENU_BAR_ID)
        for item in mb.descendants(control_type="MenuItem"):
            info = item.element_info
            menus.append({"name": info.name, "rect": rect_of(info.rectangle)})
    except Exception as exc:
        report["menu_error"] = f"{type(exc).__name__}: {exc}"
    report["menus"] = menus

    # 2. output text before any action
    report["output_before"] = read_output(dlg)

    # 3. open the File menu and read the popup items (popup is a separate hwnd)
    file_item = next((m for m in menus if m["name"] == "File"), None)
    if file_item:
        x = (file_item["rect"][0] + file_item["rect"][2]) // 2
        y = (file_item["rect"][1] + file_item["rect"][3]) // 2
        mouse.click(button="left", coords=(x, y))
        time.sleep(1.2)
        popups = []
        from pywinauto import Application

        for w in robie_windows():
            if w["hwnd"] == win["hwnd"]:
                continue
            try:
                app = Application(backend="uia").connect(handle=w["hwnd"])
                p = app.window(handle=w["hwnd"])
                items = [
                    {"type": c.element_info.control_type, "name": (c.element_info.name or "")[:60]}
                    for c in p.descendants()[:40]
                ]
            except Exception as exc:
                items = [{"error": f"{type(exc).__name__}: {exc}"}]
            popups.append({"window": w, "items": items})
        report["file_menu_click"] = {"coords": [x, y], "popup_windows": popups}
        from PIL import ImageGrab

        shot_path = OUT_DIR / time.strftime("l1-filemenu-%Y%m%d-%H%M%S.png")
        ImageGrab.grab(all_screens=True).save(shot_path)
        report["file_menu_click"]["screenshot"] = str(shot_path)
        keyboard.send_keys("{ESC}")
        time.sleep(0.6)

    # 4. click the Compile toolbar button through coordinates reported by UIA
    compile_btn = next((b for b in toolbar if b["name"] == "Compile"), None)
    if compile_btn:
        x = (compile_btn["rect"][0] + compile_btn["rect"][2]) // 2
        y = (compile_btn["rect"][1] + compile_btn["rect"][3]) // 2
        mouse.click(button="left", coords=(x, y))
        time.sleep(2.5)
        from PIL import ImageGrab

        shot_path = OUT_DIR / time.strftime("l1-compile-click-%Y%m%d-%H%M%S.png")
        ImageGrab.grab(all_screens=True).save(shot_path)
        report["compile_click"] = {"coords": [x, y], "screenshot": str(shot_path)}
        report["output_after"] = read_output(dlg)
        report["windows_after"] = robie_windows()

    return report


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
