"""Stage-L1: open two_level.proj through the native dialog, compile, read the log.

Dialog strategy: the filename field is a ComboBox (auto id 1148); set its edit
text through UIA, then confirm with ENTER, falling back to the Open button.
"""
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

    return Application(backend="uia").connect(handle=hwnd).window(handle=hwnd)


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
            out.append(
                {
                    "title": w["title"],
                    "items": [
                        {
                            "type": c.element_info.control_type,
                            "name": (c.element_info.name or "")[:50],
                            "rect": rect_of(c.element_info.rectangle),
                        }
                        for c in p.descendants()[:80]
                    ],
                }
            )
        except Exception as exc:
            out.append({"title": w["title"], "error": f"{type(exc).__name__}: {exc}"})
    return out


def read_output(dlg) -> str:
    try:
        return dlg.child_window(auto_id=OUTPUT_ID, control_type="Edit").window_text()
    except Exception as exc:
        return f"ERR {type(exc).__name__}: {exc}"


def hashes() -> dict:
    return {
        str(p.relative_to(PROJ_DIR)): hashlib.sha256(p.read_bytes()).hexdigest()[:16]
        for p in sorted(PROJ_DIR.rglob("*"))
        if p.is_file()
    }


def click_menu(dlg, title: str, item: str) -> dict:
    from pywinauto import keyboard, mouse

    mb = dlg.child_window(auto_id=MENU_BAR_ID)
    headers = {i.element_info.name: rect_of(i.element_info.rectangle) for i in mb.descendants(control_type="MenuItem")}
    if title not in headers:
        return {"ok": False, "error": f"menu {title} not found"}
    tries = []
    for attempt in range(3):
        try:
            dlg.set_focus()
        except Exception:
            pass
        keyboard.send_keys("{ESC}")
        time.sleep(0.5)
        mouse.click(button="left", coords=center(headers[title]))
        time.sleep(1.3)
        popups = popup_items()
        tries.append({"attempt": attempt, "items": [i.get("name") for p in popups for i in p.get("items", [])]})
        for popup in popups:
            for it in popup.get("items", []):
                if it.get("name") == item and it.get("rect"):
                    mouse.click(button="left", coords=center(it["rect"]))
                    time.sleep(1.6)
                    return {"ok": True, "clicked": f"{title}>{item}", "tries": tries}
        keyboard.send_keys("{ESC}")
        time.sleep(0.4)
    return {"ok": False, "error": f"{item} not found in {title}", "tries": tries}


def dialog_controls(win: dict) -> list[dict]:
    d = attach(win["hwnd"])
    rows = []
    for c in d.descendants():
        i = c.element_info
        rows.append(
            {
                "type": i.control_type,
                "name": (i.name or "")[:50],
                "auto_id": (i.automation_id or "")[:40],
                "class": (i.class_name or "")[:30],
                "rect": rect_of(i.rectangle),
                "ctrl": c,
            }
        )
    return rows


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
    time.sleep(1.5)
    dialog = next((w for w in robie_windows() if w["class"] == "#32770"), None)
    if dialog is None:
        report["error"] = "no file dialog"
        return report
    report["dialog"] = dialog

    controls = dialog_controls(dialog)
    pick = [
        {k: v for k, v in c.items() if k != "ctrl"}
        for c in controls
        if c["type"] in {"ComboBox", "Edit", "Button"}
    ]
    report["controls"] = pick[:40]

    combo = next((c for c in controls if c["type"] == "ComboBox" and c["auto_id"] == "1148"), None)
    if combo is None:
        combo = next((c for c in controls if c["type"] == "ComboBox"), None)
    info = {}
    if combo is not None:
        info["combo"] = {"name": combo["name"], "auto_id": combo["auto_id"], "rect": combo["rect"]}
        try:
            combo["ctrl"].set_edit_text(PROJ)
            info["set_edit_text"] = "ok"
        except Exception as exc:
            info["set_edit_text"] = f"{type(exc).__name__}: {exc}"
        time.sleep(0.8)
        try:
            info["text_after_set"] = combo["ctrl"].window_text()
        except Exception as exc:
            info["text_after_set"] = f"{type(exc).__name__}: {exc}"
        if PROJ.lower() not in (info.get("text_after_set") or "").lower():
            x, y = center(combo["rect"])
            mouse.click(button="left", coords=(x, y))
            time.sleep(0.4)
            keyboard.send_keys("^a")
            keyboard.send_keys(PROJ, with_spaces=True, pause=0.005)
            time.sleep(0.8)
            try:
                info["text_after_typing"] = combo["ctrl"].window_text()
            except Exception as exc:
                info["text_after_typing"] = f"{type(exc).__name__}: {exc}"
    report["filename_fill"] = info

    keyboard.send_keys("{ENTER}")
    time.sleep(7.0)
    report["windows_after_enter"] = robie_windows()
    if any(w["class"] == "#32770" for w in report["windows_after_enter"]):
        btn = next((c for c in controls if c["type"] == "Button" and re.search(r"^打开|^Open", c["name"] or "")), None)
        if btn is not None:
            mouse.click(button="left", coords=center(btn["rect"]))
            time.sleep(7.0)
        report["windows_after_button"] = robie_windows()

    shot = OUT_DIR / time.strftime("l1-open-%Y%m%d-%H%M%S.png")
    ImageGrab.grab(all_screens=True).save(shot)
    report["open_screenshot"] = str(shot)
    report["output_after_open"] = read_output(dlg)
    report["files_after_open"] = hashes()
    main = next((w for w in robie_windows() if w["class"].startswith("Qt")), None)
    report["title_after_open"] = main["title"] if main else None

    if not any(w["class"] == "#32770" for w in robie_windows()):
        report["compile_click"] = click_menu(dlg, "Build", "Compile")
        time.sleep(8.0)
        shot2 = OUT_DIR / time.strftime("l1-compile-%Y%m%d-%H%M%S.png")
        ImageGrab.grab(all_screens=True).save(shot2)
        report["compile_screenshot"] = str(shot2)
        report["output_after_compile"] = read_output(dlg)
        report["files_after_compile"] = hashes()
        report["windows_after_compile"] = robie_windows()
    else:
        keyboard.send_keys("{ESC}")
        report["note"] = "dialog still open; ESC sent"
    return report


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
