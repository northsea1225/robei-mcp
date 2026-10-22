"""Find a working way to expand the Robei Files tree and open a model."""
from __future__ import annotations

import json
import time
from pathlib import Path

OUT = Path(r"C:\dev\robei-mcp\workspace\gui_shots")


def run() -> dict:
    import robei_ui
    from pywinauto import keyboard, mouse
    from PIL import ImageGrab

    dlg, win = robei_ui.main_window()
    if dlg is None:
        return {"ok": False, "error": "Robei not running"}

    tree = None
    for ctrl in dlg.descendants(control_type="Tree"):
        try:
            if ctrl.element_info.class_name == "FileTree":
                tree = ctrl
                break
        except Exception:
            continue
    if tree is None:
        return {"ok": False, "error": "FileTree not found"}

    report: dict = {"ok": True, "steps": []}

    def texts() -> list[str]:
        out = []
        for item in tree.descendants(control_type="TreeItem")[:40]:
            try:
                state = item.get_expand_state()
            except Exception:
                state = None
            try:
                out.append(f"{item.window_text()}[{state}]")
            except Exception:
                out.append("<?>")
        return out

    def shot(label: str) -> str:
        path = OUT / time.strftime(f"p14-{label}-%H%M%S.png")
        ImageGrab.grab(all_screens=True).save(path)
        return str(path)

    dlg.set_focus()
    time.sleep(0.5)
    report["start"] = texts()

    def item_named(name: str):
        for i in tree.descendants(control_type="TreeItem"):
            try:
                if i.window_text() == name:
                    return i
            except Exception:
                continue
        return None

    model = item_named("model")
    if model is not None:
        rect = robei_ui._rect(model.element_info.rectangle)
        # 1. single click on the item, then RIGHT arrow
        mouse.click(button="left", coords=((rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2))
        time.sleep(0.4)
        keyboard.send_keys("{RIGHT}")
        time.sleep(0.8)
        report["steps"].append({"step": "click+RIGHT", "items": texts(), "shot": shot("right")})

        # 2. click the expander triangle at the left edge
        tri = (rect[0] + 6, (rect[1] + rect[3]) // 2)
        mouse.click(button="left", coords=tri)
        time.sleep(0.8)
        report["steps"].append({"step": "click-triangle", "items": texts(), "shot": shot("tri")})

        # 3. Build > Refresh, then look again
        refresh = robei_ui.click_menu(dlg, "Build", "Refresh")
        time.sleep(1.5)
        report["refresh"] = refresh
        report["steps"].append({"step": "refresh", "items": texts(), "shot": shot("refresh")})

    target = item_named("top_two.model")
    if target is None:
        report["ok"] = False
        report["error"] = "top_two.model still not visible in the Files tree"
        report["items"] = texts()
        return report

    rect = robei_ui._rect(target.element_info.rectangle)
    center = ((rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2)
    mouse.double_click(button="left", coords=center)
    time.sleep(2.5)
    report["models"] = robei_ui.model_tabs(dlg)
    report["shot_opened"] = shot("opened")
    report["ok"] = bool(report["models"])
    return report


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
