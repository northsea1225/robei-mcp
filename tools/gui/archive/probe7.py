"""Stage-L1: File > Open (native #32770 dialog) then Build > Compile, all via UIA."""
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


def dump_tree(win: dict, limit: int = 140) -> dict:
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


def click_menu(dlg, title: str, item: str | None) -> dict:
    from pywinauto import keyboard, mouse

    mb = dlg.child_window(auto_id=MENU_BAR_ID)
    headers = {i.element_info.name: rect_of(i.element_info.rectangle) for i in mb.descendants(control_type="MenuItem")}
    if title not in headers:
        return {"ok": False, "error": f"menu {title} not found"}
    keyboard.send_keys("{ESC}")
    time.sleep(0.3)
    mouse.click(button="left", coords=center(headers[title]))
    time.sleep(1.0)
    if item is None:
        return {"ok": True, "opened": title}
    for popup in popup_items():
        for it in popup["items"]:
            if it.get("name") == item and it.get("rect"):
                mouse.click(button="left", coords=center(it["rect"]))
                time.sleep(1.5)
                return {"ok": True, "clicked": f"{title}>{item}", "rect": it["rect"]}
    keyboard.send_keys("{ESC}")
    return {"ok": False, "error": f"item {item} not found in {title}"}


def fill_open_dialog(dlg_win: dict, path: str) -> dict:
    from pywinauto import keyboard, mouse

    d = attach(dlg_win["hwnd"])
    info: dict = {"window": dlg_win, "tree": dump_tree(dlg_win, 120)}
    controls = []
    for c in d.descendants():
        i = c.element_info
        controls.append(
            {
                "type": i.control_type,
                "name": (i.name or "")[:40],
                "auto_id": (i.automation_id or "")[:40],
                "class": (i.class_name or "")[:30],
                "rect": rect_of(i.rectangle),
                "ctrl": c,
            }
        )
    edits = [c for c in controls if c["type"] == "Edit"]
    if not edits:
        info["error"] = "no Edit control"
        return info
    # filename field: lowest + widest edit, or the classic auto id 1148
    edits.sort(key=lambda c: (0 if c["auto_id"] == "1148" else 1, -c["rect"][3], -(c["rect"][2] - c["rect"][0])))
    target = edits[0]
    x, y = center(target["rect"])
    mouse.click(button="left", coords=(x, y))
    time.sleep(0.3)
    keyboard.send_keys("^a")
    keyboard.send_keys(path, with_spaces=True, pause=0.005)
    time.sleep(0.6)
    info["typed_into"] = {"type": target["type"], "auto_id": target["auto_id"], "class": target["class"], "rect": target["rect"]}
    open_btn = next(
        (c for c in controls if c["type"] == "Button" and re.search(r"打开|Open|确定|OK", c["name"] or "")),
        None,
    )
    if open_btn:
        mouse.click(button="left", coords=center(open_btn["rect"]))
        info["clicked_button"] = open_btn["name"]
    else:
        keyboard.send_keys("{ENTER}")
        info["clicked_button"] = "ENTER"
    time.sleep(5.0)
    return info


def run() -> dict:
    from PIL import ImageGrab

    dlg, win = main_window()
    if dlg is None:
        return {"ok": False, "error": "Robei main window not found"}
    report: dict = {"ok": True, "window": win}
    report["files_before"] = hashes()
    report["output_before"] = read_output(dlg)

    report["open_click"] = click_menu(dlg, "File", "Open")
    time.sleep(1.0)
    shot = OUT_DIR / time.strftime("l1-dlg-%Y%m%d-%H%M%S.png")
    ImageGrab.grab(all_screens=True).save(shot)
    report["dialog_screenshot"] = str(shot)
    report["windows_dialog"] = robie_windows()

    dialog = next((w for w in robie_windows() if w["class"] == "#32770"), None)
    if dialog is None:
        report["error"] = "no #32770 dialog found"
        return report
    report["fill"] = fill_open_dialog(dialog, PROJ)
    shot2 = OUT_DIR / time.strftime("l1-opened-%Y%m%d-%H%M%S.png")
    ImageGrab.grab(all_screens=True).save(shot2)
    report["opened_screenshot"] = str(shot2)
    report["windows_after_open"] = robie_windows()
    report["output_after_open"] = read_output(dlg)
    report["files_after_open"] = hashes()

    report["compile_click"] = click_menu(dlg, "Build", "Compile")
    time.sleep(5.0)
    shot3 = OUT_DIR / time.strftime("l1-compiled-%Y%m%d-%H%M%S.png")
    ImageGrab.grab(all_screens=True).save(shot3)
    report["compiled_screenshot"] = str(shot3)
    report["output_after_compile"] = read_output(dlg)
    report["files_after_compile"] = hashes()
    report["windows_after_compile"] = robie_windows()
    return report


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
