"""Create a Type=test object through File>New and watch what Robei writes."""
from __future__ import annotations

import json
import time
from pathlib import Path

OUT = Path(r"C:\dev\robei-mcp\workspace\gui_shots")


def _popup_items() -> list[dict]:
    out = []
    for w in __import__("robei_ui").windows():
        if "Popup" not in w["class"]:
            continue
        try:
            p = __import__("robei_ui").attach(w["hwnd"])
            for c in p.descendants()[:20]:
                i = c.element_info
                out.append(
                    {
                        "type": i.control_type,
                        "name": i.name or "",
                        "rect": __import__("robei_ui")._rect(i.rectangle),
                    }
                )
        except Exception:
            continue
    return out


def run() -> dict:
    import robei_ui
    from pywinauto import keyboard, mouse
    from PIL import ImageGrab

    report: dict = {"ok": True}
    dialogs = [w for w in robei_ui.windows() if "New Project" in (w["title"] or "")]
    if not dialogs:
        main, _ = robei_ui.main_window()
        if main is None:
            return {"ok": False, "error": "Robei not running"}
        robei_ui.click_menu(main, "File", "New")
        time.sleep(2.5)
        dialogs = [w for w in robei_ui.windows() if "New Project" in (w["title"] or "")]
    if not dialogs:
        return {"ok": False, "error": "New Project Setting dialog did not open"}
    d = robei_ui.attach(dialogs[0]["hwnd"])

    name_edit = d.child_window(auto_id="QApplication.NewDialog.QLineEdit", control_type="Edit", found_index=0)
    rect = robei_ui._rect(name_edit.element_info.rectangle)
    mouse.click(button="left", coords=robei_ui._center(rect))
    time.sleep(0.2)
    keyboard.send_keys("^a")
    keyboard.send_keys("tb_and_gate")
    time.sleep(0.4)
    report["name"] = name_edit.window_text()

    combo = d.child_window(auto_id="QApplication.NewDialog.QComboBox", control_type="ComboBox")
    crect = robei_ui._rect(combo.element_info.rectangle)
    mouse.click(button="left", coords=robei_ui._center(crect))
    time.sleep(1.0)
    item = next((i for i in _popup_items() if i["name"] == "test"), None)
    report["combo_items"] = [i["name"] for i in _popup_items()]
    if item:
        mouse.click(button="left", coords=robei_ui._center(item["rect"]))
        time.sleep(0.8)
    report["combo_after"] = combo.window_text()

    ok_btn = d.child_window(auto_id="QApplication.NewDialog.QPushButton", control_type="Button", found_index=0)
    buttons = [c for c in d.descendants(control_type="Button") if c.window_text() in ("OK", "Cancel")]
    target = next((b for b in buttons if b.window_text() == "OK"), None)
    path = OUT / time.strftime("p26-filled-%H%M%S.png")
    ImageGrab.grab(all_screens=True).save(path)
    report["shot_filled"] = str(path)
    if target is not None:
        mouse.click(button="left", coords=robei_ui._center(robei_ui._rect(target.element_info.rectangle)))
        time.sleep(3.5)
    report["windows_after_ok"] = [{"title": w["title"], "class": w["class"]} for w in robei_ui.windows()]
    dlg, win = robei_ui.main_window()
    if dlg is not None:
        report["tabs"] = robei_ui.tab_groups(dlg)
        report["tree"] = robei_ui.tree_items(dlg)
        report["output_tail"] = robei_ui._tail(robei_ui.read_output(dlg), 8)
    path2 = OUT / time.strftime("p26-after-%H%M%S.png")
    ImageGrab.grab(all_screens=True).save(path2)
    report["shot_after"] = str(path2)
    return report


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
