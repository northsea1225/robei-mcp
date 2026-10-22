"""Create the Type=test object with a robust dialog attach + keyboard type selection."""
from __future__ import annotations

import json
import time
from pathlib import Path

OUT = Path(r"C:\dev\robei-mcp\workspace\gui_shots")
PROJ_DIR = Path(r"C:\dev\robei-mcp\workspace\robei_samples\exp1_and")


def _wait_dialog(title_sub: str, timeout: float = 12.0):
    import robei_ui

    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        for w in robei_ui.windows():
            if title_sub in (w["title"] or ""):
                try:
                    d = robei_ui.attach(w["hwnd"])
                    d.descendants()  # forces a UIA round-trip; can raise COMError
                    return d, w
                except Exception as exc:
                    last = f"{type(exc).__name__}: {exc}"
        time.sleep(0.7)
    return None, {"error": last}


def run() -> dict:
    import robei_ui
    from pywinauto import keyboard, mouse
    from PIL import ImageGrab

    main, win = robei_ui.main_window()
    if main is None:
        return {"ok": False, "error": "Robei not running"}
    report: dict = {"ok": True}

    d, info = _wait_dialog("New Project")
    if d is None:
        robei_ui.click_menu(main, "File", "New")
        time.sleep(2.5)
        d, info = _wait_dialog("New Project")
    if d is None:
        return {"ok": False, "error": f"dialog not usable: {info}"}
    report["dialog"] = info.get("title") or "New Project Setting"

    edits = [c for c in d.descendants(control_type="Edit")]
    edits.sort(key=lambda c: robei_ui._rect(c.element_info.rectangle)[1])
    report["edit_count"] = len(edits)
    name_edit, loc_edit = edits[0], edits[-1]
    for edit, value in ((name_edit, "tb_and_gate"), (loc_edit, str(PROJ_DIR))):
        rect = robei_ui._rect(edit.element_info.rectangle)
        mouse.click(button="left", coords=robei_ui._center(rect))
        time.sleep(0.25)
        keyboard.send_keys("^a")
        keyboard.send_keys(value, with_spaces=True, pause=0.004)
        time.sleep(0.4)
    report["name"] = name_edit.window_text()
    report["location"] = loc_edit.window_text()

    combo = d.child_window(auto_id="QApplication.NewDialog.QComboBox", control_type="ComboBox")
    report["combo_before"] = combo.window_text()
    rect = robei_ui._rect(combo.element_info.rectangle)
    mouse.click(button="left", coords=robei_ui._center(rect))
    time.sleep(1.0)
    steps = []
    for _ in range(6):
        keyboard.send_keys("{DOWN}")
        time.sleep(0.25)
        steps.append(combo.window_text())
        if combo.window_text().strip().lower() == "test":
            break
    keyboard.send_keys("{ENTER}")
    time.sleep(0.8)
    report["combo_steps"] = steps
    report["combo_after"] = combo.window_text()

    path = OUT / time.strftime("p28-filled-%H%M%S.png")
    ImageGrab.grab(all_screens=True).save(path)
    report["shot_filled"] = str(path)

    buttons = [c for c in d.descendants(control_type="Button") if c.window_text() == "OK"]
    if not buttons:
        return {"ok": False, "error": "OK button missing", "report": report}
    mouse.click(button="left", coords=robei_ui._center(robei_ui._rect(buttons[0].element_info.rectangle)))
    time.sleep(4.0)
    report["windows_after_ok"] = [{"title": w["title"], "class": w["class"]} for w in robei_ui.windows()]
    report["tree"] = robei_ui.tree_items(main)
    report["tabs"] = robei_ui.tab_groups(main)
    report["output_tail"] = robei_ui._tail(robei_ui.read_output(main), 8)

    files = {}
    for sub in ("model", "test"):
        for p in sorted((PROJ_DIR / sub).glob("*.model")):
            files[f"{sub}/{p.name}"] = p.read_text(encoding="utf-8", errors="replace")[:1200]
    report["model_files"] = files
    shot = OUT / time.strftime("p28-after-%H%M%S.png")
    ImageGrab.grab(all_screens=True).save(shot)
    report["shot_after"] = str(shot)
    return report


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
