"""Create a Type=test object properly and dump what Robei writes."""
from __future__ import annotations

import json
import time
from pathlib import Path

OUT = Path(r"C:\dev\robei-mcp\workspace\gui_shots")
PROJ_DIR = Path(r"C:\dev\robei-mcp\workspace\robei_samples\exp1_and")


def run() -> dict:
    import robei_ui
    from pywinauto import keyboard, mouse
    from PIL import ImageGrab

    main, _ = robei_ui.main_window()
    if main is None:
        return {"ok": False, "error": "Robei not running"}
    report: dict = {"ok": True}
    if not any("New Project" in (w["title"] or "") for w in robei_ui.windows()):
        robei_ui.click_menu(main, "File", "New")
        time.sleep(2.5)
    dialogs = [w for w in robei_ui.windows() if "New Project" in (w["title"] or "")]
    if not dialogs:
        return {"ok": False, "error": "dialog did not open"}
    d = robei_ui.attach(dialogs[0]["hwnd"])

    edits = [c for c in d.descendants(control_type="Edit")]
    edits.sort(key=lambda c: robei_ui._rect(c.element_info.rectangle)[1])
    name_edit, loc_edit = edits[0], edits[-1]
    for edit, value in ((name_edit, "tb_and_gate"), (loc_edit, str(PROJ_DIR))):
        rect = robei_ui._rect(edit.element_info.rectangle)
        mouse.click(button="left", coords=robei_ui._center(rect))
        time.sleep(0.2)
        keyboard.send_keys("^a")
        keyboard.send_keys(value, with_spaces=True, pause=0.005)
        time.sleep(0.4)
    report["name"] = name_edit.window_text()
    report["location"] = loc_edit.window_text()

    combo = d.child_window(auto_id="QApplication.NewDialog.QComboBox", control_type="ComboBox")
    selected = None
    try:
        combo.select("test")
        selected = "select()"
    except Exception as exc:
        selected = f"select failed: {type(exc).__name__}"
    time.sleep(0.6)
    report["combo_text"] = combo.window_text()
    if report["combo_text"].strip().lower() != "test":
        rect = robei_ui._rect(combo.element_info.rectangle)
        mouse.click(button="left", coords=robei_ui._center(rect))
        time.sleep(0.8)
        for _ in range(6):
            keyboard.send_keys("{DOWN}")
            time.sleep(0.1)
            if combo.window_text().strip().lower() == "test":
                break
        keyboard.send_keys("{ENTER}")
        time.sleep(0.6)
        report["combo_text_after_keys"] = combo.window_text()
    report["combo_selected_via"] = selected

    buttons = [c for c in d.descendants(control_type="Button") if c.window_text() == "OK"]
    if buttons:
        mouse.click(button="left", coords=robei_ui._center(robei_ui._rect(buttons[0].element_info.rectangle)))
        time.sleep(3.5)
    report["windows_after_ok"] = [{"title": w["title"], "class": w["class"]} for w in robei_ui.windows()]
    report["tree"] = robei_ui.tree_items(main)
    report["tabs"] = robei_ui.tab_groups(main)
    report["output_tail"] = robei_ui._tail(robei_ui.read_output(main), 6)

    files = {}
    for sub in ("model", "test"):
        folder = PROJ_DIR / sub
        for path in sorted(folder.glob("*.model")):
            files[f"{sub}/{path.name}"] = path.read_text(encoding="utf-8", errors="replace")[:900]
    report["model_files"] = files
    shot = OUT / time.strftime("p27-%H%M%S.png")
    ImageGrab.grab(all_screens=True).save(shot)
    report["shot"] = str(shot)
    return report


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
