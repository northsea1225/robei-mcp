"""Expand the sample project's model folder: focus + RIGHT, then Refresh fallback."""
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
    tree = robei_ui._file_tree(dlg)
    report: dict = {"ok": True, "steps": []}

    def items() -> list[str]:
        return [f'{e["text"]}[{e["state"]}]' for e in robei_ui._tree_entries(tree)]

    def shot(label: str) -> str:
        path = OUT / time.strftime(f"p18-{label}-%H%M%S.png")
        ImageGrab.grab(all_screens=True).save(path)
        return str(path)

    dlg.set_focus()
    time.sleep(0.6)
    report["start"] = items()
    report["foreground"] = shot("start")

    model = next((e for e in robei_ui._tree_entries(tree) if e["text"] == "model"), None)
    if model is None:
        return {"ok": False, "error": "model folder missing", "start": report["start"]}
    rect = robei_ui._rect(model["item"].element_info.rectangle)

    mouse.click(button="left", coords=robei_ui._center(rect))
    time.sleep(0.4)
    keyboard.send_keys("{RIGHT}")
    time.sleep(1.2)
    report["steps"].append({"step": "focus+click+RIGHT", "items": items(), "shot": shot("right")})

    if not any("top.model" in x for x in items()):
        refresh = robei_ui.click_menu(dlg, "Build", "Refresh")
        time.sleep(2.0)
        report["refresh"] = refresh
        report["steps"].append({"step": "refresh", "items": items(), "shot": shot("refresh")})
        mouse.click(button="left", coords=robei_ui._center(rect))
        time.sleep(0.4)
        keyboard.send_keys("{RIGHT}")
        time.sleep(1.2)
        report["steps"].append({"step": "click+RIGHT again", "items": items(), "shot": shot("right2")})

    target = next((e for e in robei_ui._tree_entries(tree) if e["text"] == "top.model"), None)
    if target is not None:
        trect = robei_ui._rect(target["item"].element_info.rectangle)
        mouse.double_click(button="left", coords=robei_ui._center(trect))
        time.sleep(2.5)
        report["models"] = robei_ui.model_tabs(dlg)
        report["active"] = robei_ui.active_model(dlg)
        report["shot_opened"] = shot("opened")
    else:
        report["ok"] = False
        report["error"] = "top.model never appeared"
    return report


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
