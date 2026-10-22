"""Stage-L1: complete one drive cycle - open two_level.proj, compile, read the log."""
from __future__ import annotations

import hashlib
import json
import re
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


def attach(hwnd: int):
    from pywinauto import Application

    app = Application(backend="uia").connect(handle=hwnd)
    return app.window(handle=hwnd)


def main_window():
    for win in robie_windows():
        if "Robei" in win["title"] and win["class"].startswith("Qt"):
            try:
                dlg = attach(win["hwnd"])
                if dlg.descendants():
                    return dlg, win
            except Exception:
                continue
    return None, None


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


def hashes() -> dict:
    out = {}
    for p in sorted(PROJ_DIR.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(PROJ_DIR))] = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
    return out


def menu_headers(dlg) -> dict:
    mb = dlg.child_window(auto_id=MENU_BAR_ID)
    return {i.element_info.name: rect_of(i.element_info.rectangle) for i in mb.descendants(control_type="MenuItem")}


def click_menu(dlg, title: str, item: str) -> dict:
    from pywinauto import keyboard, mouse

    headers = menu_headers(dlg)
    if title not in headers:
        return {"ok": False, "error": f"menu {title} not found", "headers": list(headers)}
    tries = []
    for attempt in range(3):
        try:
            dlg.set_focus()
        except Exception:
            pass
        keyboard.send_keys("{ESC}")
        time.sleep(0.5)
        x, y = center(headers[title])
        mouse.click(button="left", coords=(x, y))
        time.sleep(1.3)
        popups = popup_items()
        names = [i.get("name") for p in popups for i in p["items"] if i.get("type") == "MenuItem"]
        tries.append({"attempt": attempt, "coords": [x, y], "popup_items": names})
        for popup in popups:
            for it in popup["items"]:
                if it.get("name") == item and it.get("rect"):
                    mouse.click(button="left", coords=center(it["rect"]))
                    time.sleep(1.5)
                    return {"ok": True, "clicked": f"{title}>{item}", "tries": tries}
        keyboard.send_keys("{ESC}")
        time.sleep(0.4)
    return {"ok": False, "error": f"item {item} not found in {title}", "tries": tries}


def pick_filename_edit(win: dict):
    d = attach(win["hwnd"])
    best = None
    for c in d.descendants():
        i = c.element_info
        if i.control_type != "Edit":
            continue
        rec = {"auto_id": i.automation_id, "class": i.class_name, "rect": rect_of(i.rectangle), "ctrl": c}
        if best is None:
            best = rec
            continue
        if rec["auto_id"] == "1148" and best["auto_id"] != "1148":
            best = rec
        elif best["auto_id"] != "1148" and rec["rect"][3] > best["rect"][3]:
            best = rec
    return best


def run() -> dict:
    from pywinauto import keyboard, mouse
    from PIL import ImageGrab

    dlg, win = main_window()
    if dlg is None:
        return {"ok": False, "error": "Robei main window not found"}
    report: dict = {"ok": True, "window": win, "title_before": win["title"]}
    report["files_before"] = hashes()
    report["output_before"] = read_output(dlg)

    report["open_click"] = click_menu(dlg, "File", "Open")
    time.sleep(1.2)
    dialog = next((w for w in robie_windows() if w["class"] == "#32770"), None)
    report["dialog"] = dialog
    if dialog is None:
        report["error"] = "no file dialog"
        return report

    field = pick_filename_edit(dialog)
    if field is None:
        report["error"] = "filename edit not found"
        keyboard.send_keys("{ESC}")
        return report
    report["filename_field"] = {k: v for k, v in field.items() if k != "ctrl"}

    mouse.click(button="left", coords=center(field["rect"]))
    time.sleep(0.4)
    keyboard.send_keys("^a")
    keyboard.send_keys(PROJ, with_spaces=True, pause=0.005)
    time.sleep(1.2)
    report["field_value_after_typing"] = field["ctrl"].window_text()
    # autocomplete list may be covering the OK button; close it but keep the text
    keyboard.send_keys("{ESC}")
    time.sleep(0.5)
    dialog = next((w for w in robie_windows() if w["class"] == "#32770"), None)
    if dialog is None:
        report["note"] = "dialog closed by ESC"
        return report
    # re-apply text in case ESC reverted the field, then click the Open button
    field = pick_filename_edit(dialog)
    report["field_value_after_esc"] = field["ctrl"].window_text() if field else None
    if field and not field["ctrl"].window_text().strip():
        mouse.click(button="left", coords=center(field["rect"]))
        keyboard.send_keys(PROJ, with_spaces=True, pause=0.005)
        time.sleep(0.8)
        report["field_value_after_retype"] = field["ctrl"].window_text()
    buttons = []
    d = attach(dialog["hwnd"])
    for c in d.descendants():
        i = c.element_info
        if i.control_type == "Button":
            buttons.append({"name": (i.name or ""), "rect": rect_of(i.rectangle)})
    report["dialog_buttons"] = buttons[:8]
    target = next((b for b in buttons if re.search(r"^打开|^Open", b["name"])), None)
    keyboard.send_keys("{ENTER}")
    time.sleep(6.0)
    report["windows_after_enter"] = robie_windows()
    if any(w["class"] == "#32770" for w in report["windows_after_enter"]):
        if target:
            mouse.click(button="left", coords=center(target["rect"]))
            time.sleep(6.0)
        report["windows_after_button"] = robie_windows()
    shot = OUT_DIR / time.strftime("l1-opened-%Y%m%d-%H%M%S.png")
    ImageGrab.grab(all_screens=True).save(shot)
    report["opened_screenshot"] = str(shot)
    report["output_after_open"] = read_output(dlg)
    report["files_after_open"] = hashes()
    win2 = next((w for w in robie_windows() if w["class"].startswith("Qt")), None)
    report["title_after_open"] = win2["title"] if win2 else None

    if not any(w["class"] == "#32770" for w in robie_windows()):
        report["compile_click"] = click_menu(dlg, "Build", "Compile")
        time.sleep(7.0)
        shot2 = OUT_DIR / time.strftime("l1-compiled-%Y%m%d-%H%M%S.png")
        ImageGrab.grab(all_screens=True).save(shot2)
        report["compiled_screenshot"] = str(shot2)
        report["output_after_compile"] = read_output(dlg)
        report["files_after_compile"] = hashes()
        report["windows_after_compile"] = robie_windows()
    return report


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
