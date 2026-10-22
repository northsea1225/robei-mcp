"""Can the Code tab be written? Read the editor, try SetValue, save, re-read."""
from __future__ import annotations

import json
import time
from pathlib import Path

OUT = Path(r"C:\dev\robei-mcp\workspace\gui_shots")
NEW_CODE = "assign y = ~(a & b);  // written by robei-mcp"


def _code_tab(dlg, label: str):
    for tab in dlg.descendants(control_type="Tab"):
        try:
            if "Design.qt_tabwidget_tabbar" not in (tab.element_info.automation_id or ""):
                continue
            for item in tab.children(control_type="TabItem"):
                if item.window_text().strip().lower() == label:
                    return item
        except Exception:
            continue
    return None


def _editor(dlg):
    for ctrl in dlg.descendants():
        try:
            if ctrl.element_info.class_name == "CodeEditor":
                return ctrl
        except Exception:
            continue
    return None


def run() -> dict:
    import robei_ui
    from pywinauto import keyboard, mouse
    from PIL import ImageGrab

    dlg, win = robei_ui.main_window()
    if dlg is None:
        return {"ok": False, "error": "Robei not running"}
    report: dict = {"ok": True}

    code_tab = _code_tab(dlg, "code")
    if code_tab is None:
        return {"ok": False, "error": "Code tab not found"}
    mouse.click(button="left", coords=robei_ui._center(robei_ui._rect(code_tab.element_info.rectangle)))
    time.sleep(1.2)

    editor = _editor(dlg)
    if editor is None:
        return {"ok": False, "error": "CodeEditor not found"}
    info = editor.element_info
    report["editor"] = {
        "auto_id": info.automation_id,
        "class": info.class_name,
        "rect": robei_ui._rect(info.rectangle),
        "readonly": info.element.CurrentIsReadOnly if hasattr(info.element, "CurrentIsReadOnly") else None,
    }
    report["before"] = editor.window_text()

    try:
        editor.iface_value.SetValue(NEW_CODE)
        report["set_value"] = "ok"
    except Exception as exc:
        report["set_value"] = f"{type(exc).__name__}: {exc}"
    time.sleep(0.8)
    report["after_set"] = editor.window_text()
    report["screenshot_after_set"] = str(OUT / "p16-after-set.png")
    ImageGrab.grab(all_screens=True).save(report["screenshot_after_set"])

    # try keyboard typing as a fallback whenever SetValue did not land
    if report["after_set"] != NEW_CODE:
        mouse.click(button="left", coords=robei_ui._center(robei_ui._rect(info.rectangle)))
        time.sleep(0.3)
        keyboard.send_keys("^a")
        keyboard.send_keys(NEW_CODE, with_spaces=True, pause=0.01)
        time.sleep(0.8)
        report["after_typing"] = editor.window_text()

    keyboard.send_keys("^s")
    time.sleep(2.0)
    report["output_after_save"] = robei_ui._tail(robei_ui.read_output(dlg), 6)

    graph_tab = _code_tab(dlg, "graph")
    if graph_tab is not None:
        mouse.click(button="left", coords=robei_ui._center(robei_ui._rect(graph_tab.element_info.rectangle)))
        time.sleep(1.2)
        mouse.click(button="left", coords=robei_ui._center(robei_ui._rect(code_tab.element_info.rectangle)))
        time.sleep(1.2)
        editor2 = _editor(dlg)
        report["after_tab_switch"] = editor2.window_text() if editor2 is not None else None
    report["screenshot_final"] = str(OUT / "p16-final.png")
    ImageGrab.grab(all_screens=True).save(report["screenshot_final"])
    return report


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
