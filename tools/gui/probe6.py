"""Stage-L1: drive Robei through UIA - File > Open a project, then Build > Compile.

Records the file dialog tree, the compile log, and file hashes before/after.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

OUT_DIR = Path(r"C:\dev\robei-mcp\workspace\gui_shots")
PROJ = r"C:\dev\robei-mcp\workspace\robei_samples\two_level\two_level.proj"
PROJ_DIR = Path(r"C:\dev\robei-mcp\workspace\robei_samples\two_level")
MENU_BAR_ID = "QApplication.Romain.QMenuBar"
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


def center(rect: list[int]) -> tuple[int, int]:
    return ((rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2)


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


def attach(hwnd: int):
    from pywinauto import Application

    app = Application(backend="uia").connect(handle=hwnd)
    return app.window(handle=hwnd)


def popup_items() -> list[dict]:
    out = []
    for w in robie_windows():
        if "Popup" not in w["class"]:
            continue
        try:
            p = attach(w["hwnd"])
            items = [
                {
                    "type": c.element_info.control_type,
                    "name": (c.element_info.name or "")[:50],
                    "rect": rect_of(c.element_info.rectangle),
                }
                for c in p.descendants()[:80]
            ]
        except Exception as exc:
            items = [{"error": f"{type(exc).__name__}: {exc}"}]
        out.append({"title": w["title"], "class": w["class"], "items": items})
    return out


def read_output(dlg) -> str:
    try:
        return dlg.child_window(auto_id=OUTPUT_ID, control_type="Edit").window_text()
    except Exception as exc:
        return f"ERR {type(exc).__name__}: {exc}"


def dump_tree(win: dict, limit: int = 120) -> dict:
    try:
        w = attach(win["hwnd"])
        kids = w.descendants()
        items = []
        for c in kids[:limit]:
            info = c.element_info
            items.append(
                {
                    "type": info.control_type,
                    "name": (info.name or "")[:60],
                    "auto_id": (info.automation_id or "")[:70],
                    "class": (info.class_name or "")[:40],
                    "rect": rect_of(info.rectangle),
                }
            )
        return {"count": len(kids), "items": items}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def hashes() -> dict:
    out = {}
    for p in sorted(PROJ_DIR.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(PROJ_DIR))] = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
    return out


def click_menu(dlg, title: str, item: str) -> dict:
    from pywinauto import keyboard, mouse

    mb = dlg.child_window(auto_id=MENU_BAR_ID)
    headers = {i.element_info.name: rect_of(i.element_info.rectangle) for i in mb.descendants(control_type="MenuItem")}
    if title not in headers:
        return {"ok": False, "error": f"menu {title} not found"}
    keyboard.send_keys("{ESC}")
    time.sleep(0.3)
    mouse.click(button="left", coords=center(headers[title]))
    time.sleep(1.0)
    for popup in popup_items():
        for it in popup["items"]:
            if it.get("name") == item and it.get("rect"):
                mouse.click(button="left", coords=center(it["rect"]))
                time.sleep(1.5)
                return {"ok": True, "clicked": f"{title}>{item}", "rect": it["rect"]}
    keyboard.send_keys("{ESC}")
    return {"ok": False, "error": f"item {item} not found in {title}", "headers": list(headers)}


def fill_dialog_and_ok(win: dict, path: str) -> dict:
    from pywinauto import keyboard, mouse

    d = attach(win["hwnd"])
    info = {"window": win, "tree": dump_tree(win, 80)}
    edits = []
    for c in d.descendants():
        i = c.element_info
        if i.control_type == "Edit":
            edits.append({"auto_id": i.automation_id, "class": i.class_name, "rect": rect_of(i.rectangle), "value": c.window_text()})
    info["edits"] = edits
    target = None
    for e in edits:
        if "fileNameEdit" in (e["auto_id"] or "") or (e["class"] == "QLineEdit" and e["rect"][3] > 400):
            target = e
            break
    if target is None and edits:
        target = edits[-1]
    if target is None:
        info["error"] = "no edit field in dialog"
        return info
    x, y = center(target["rect"])
    mouse.click(button="left", coords=(x, y))
    time.sleep(0.3)
    keyboard.send_keys("^a")
    keyboard.send_keys(path, with_spaces=True, pause=0.005)
    time.sleep(0.5)
    info["typed_into"] = target["auto_id"]
    keyboard.send_keys("{ENTER}")
    time.sleep(4.0)
    return info


def run() -> dict:
    from PIL import ImageGrab

    dlg, win = main_window()
    if dlg is None:
        return {"ok": False, "error": "Robei main window not found"}
    report: dict = {"ok": True, "window": win}
    report["project_files_before"] = hashes()
    report["output_before"] = read_output(dlg)

    opened = click_menu(dlg, "File", "Open")
    report["open_menu_click"] = opened
    time.sleep(0.5)
    shot = OUT_DIR / time.strftime("l1-opendialog-%Y%m%d-%H%M%S.png")
    ImageGrab.grab(all_screens=True).save(shot)
    report["dialog_screenshot"] = str(shot)
    report["windows_with_dialog"] = robie_windows()

    dialog = next((w for w in robie_windows() if w["hwnd"] != win["hwnd"] and "Popup" not in w["class"]), None)
    if dialog:
        report["dialog"] = fill_dialog_and_ok(dialog, PROJ)
        shot2 = OUT_DIR / time.strftime("l1-afteropen-%Y%m%d-%H%M%S.png")
        ImageGrab.grab(all_screens=True).save(shot2)
        report["after_open_screenshot"] = str(shot2)
        report["windows_after_open"] = robie_windows()
        report["output_after_open"] = read_output(dlg)
        report["project_files_after_open"] = hashes()

        report["compile_click"] = click_menu(dlg, "Build", "Compile")
        time.sleep(4.0)
        shot3 = OUT_DIR / time.strftime("l1-aftercompile-%Y%m%d-%H%M%S.png")
        ImageGrab.grab(all_screens=True).save(shot3)
        report["after_compile_screenshot"] = str(shot3)
        report["output_after_compile"] = read_output(dlg)
        report["project_files_after_compile"] = hashes()
        report["windows_after_compile"] = robie_windows()
    return report


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
