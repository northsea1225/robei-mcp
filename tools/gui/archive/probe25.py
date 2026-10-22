"""What does the New dialog offer, and what's in the Files-tree context menu?"""
from __future__ import annotations

import json
import time
from pathlib import Path

OUT = Path(r"C:\dev\robei-mcp\workspace\gui_shots")


def run() -> dict:
    import robei_ui
    from pywinauto import keyboard, mouse
    from PIL import ImageGrab

    report: dict = {"ok": True}
    dialogs = [w for w in robei_ui.windows() if "New Project" in (w["title"] or "")]
    if dialogs:
        d = robei_ui.attach(dialogs[0]["hwnd"])
        combo = d.child_window(auto_id="QApplication.NewDialog.QComboBox", control_type="ComboBox")
        info = {"text": combo.window_text()}
        try:
            info["value"] = combo.get_value()
        except Exception as exc:
            info["value_error"] = type(exc).__name__
        rect = robei_ui._rect(combo.element_info.rectangle)
        mouse.click(button="left", coords=robei_ui._center(rect))
        time.sleep(1.0)
        items = []
        for w in robei_ui.windows():
            if "Popup" in w["class"]:
                try:
                    p = robei_ui.attach(w["hwnd"])
                    items += [(c.element_info.control_type, c.element_info.name) for c in p.descendants()[:12]]
                except Exception:
                    pass
        info["popup_items"] = items
        path = OUT / time.strftime("p25-combo-%H%M%S.png")
        ImageGrab.grab(all_screens=True).save(path)
        info["shot"] = str(path)
        keyboard.send_keys("{ESC}")
        report["new_dialog"] = info

    # context menu on the "test" folder in the Files tree
    dlg, win = robei_ui.main_window()
    if dlg is not None:
        tree = robei_ui._file_tree(dlg)
        entry = next((e for e in robei_ui._tree_entries(tree) if e["text"] == "test"), None)
        if entry is not None:
            rect = robei_ui._rect(entry["item"].element_info.rectangle)
            mouse.right_click(coords=robei_ui._center(rect))
            time.sleep(1.2)
            items = []
            for w in robei_ui.windows():
                if "Popup" in w["class"]:
                    try:
                        p = robei_ui.attach(w["hwnd"])
                        items += [(c.element_info.control_type, c.element_info.name) for c in p.descendants()[:15]]
                    except Exception:
                        pass
            path = OUT / time.strftime("p25-ctx-%H%M%S.png")
            ImageGrab.grab(all_screens=True).save(path)
            report["ctx_menu"] = {"items": items, "shot": str(path)}
            keyboard.send_keys("{ESC}")
    return report


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
