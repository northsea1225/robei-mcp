"""Place a port on the module edge and inspect the Property dock."""
from __future__ import annotations

import json
import time
from pathlib import Path

OUT = Path(r"C:\dev\robei-mcp\workspace\gui_shots")


def _toolbar_button(dlg, name: str):
    for ctrl in dlg.descendants():
        try:
            info = ctrl.element_info
            if info.name == name and "QToolBar" in (info.automation_id or ""):
                return ctrl
        except Exception:
            continue
    return None


def _prop_dump(dlg) -> list[dict]:
    rows = []
    for ctrl in dlg.descendants(control_type="Window"):
        try:
            if ctrl.element_info.automation_id != "QApplication.Romain.Property":
                continue
            for child in ctrl.descendants()[:40]:
                info = child.element_info
                rows.append({"type": info.control_type, "name": (info.name or "")[:40], "class": info.class_name})
        except Exception:
            continue
    return rows


def run() -> dict:
    import robei_ui
    from pywinauto import keyboard, mouse
    from PIL import ImageGrab

    dlg, win = robei_ui.main_window()
    if dlg is None:
        return {"ok": False, "error": "Robei not running"}
    report: dict = {"ok": True}

    def shot(label: str) -> str:
        path = OUT / time.strftime(f"p20-{label}-%H%M%S.png")
        ImageGrab.grab(all_screens=True).save(path)
        return str(path)

    dlg.set_focus()
    time.sleep(0.5)
    btn = _toolbar_button(dlg, "Port")
    if btn is None:
        return {"ok": False, "error": "Port button not found"}
    rect = robei_ui._rect(btn.element_info.rectangle)
    mouse.click(button="left", coords=robei_ui._center(rect))
    time.sleep(0.8)
    report["shot_tool"] = shot("tool")

    clicks = []
    for point in ((516, 300), (516, 380), (1014, 300)):
        mouse.click(button="left", coords=point)
        time.sleep(1.2)
        clicks.append({"point": point, "props": _prop_dump(dlg)})
    report["clicks"] = clicks
    report["shot_after"] = shot("after")

    out_before = robei_ui.read_output(dlg)
    keyboard.send_keys("^s")
    time.sleep(2.0)
    out = robei_ui.read_output(dlg)
    report["save_log"] = (out[len(out_before):] if out.startswith(out_before) else out).strip()[:300]
    return report


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
