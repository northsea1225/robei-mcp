"""Why does the sample project's model folder refuse to expand?"""
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
    if tree is None:
        return {"ok": False, "error": "FileTree missing"}

    def snap(label: str) -> list[str]:
        rows = []
        for e in robei_ui._tree_entries(tree):
            rows.append(f'{e["text"]}[{e["state"]}]')
        path = OUT / time.strftime(f"p17-{label}-%H%M%S.png")
        ImageGrab.grab(all_screens=True).save(path)
        return rows

    report: dict = {"ok": True, "steps": []}
    report["start"] = snap("start")
    report["tree_rect"] = robei_ui._rect(tree.element_info.rectangle)

    model = next((e for e in robei_ui._tree_entries(tree) if e["text"] == "model"), None)
    if model is None:
        return {"ok": False, "error": "model folder not in tree", "start": report["start"]}

    rect = robei_ui._rect(model["item"].element_info.rectangle)
    report["model_rect"] = rect
    mouse.click(button="left", coords=robei_ui._center(rect))
    time.sleep(0.5)
    report["steps"].append({"step": "click", "items": snap("click")})
    keyboard.send_keys("{RIGHT}")
    time.sleep(1.0)
    report["steps"].append({"step": "right", "items": snap("right")})
    try:
        keyboard.send_keys("{DOWN}")
        time.sleep(0.6)
        report["steps"].append({"step": "down", "items": snap("down")})
    except Exception as exc:
        report["steps"].append({"step": "down", "error": f"{type(exc).__name__}: {exc}"})
    mouse.double_click(button="left", coords=robei_ui._center(rect))
    time.sleep(1.2)
    report["steps"].append({"step": "dblclick", "items": snap("dbl")})
    mouse.click(button="left", coords=(rect[0] + 7, (rect[1] + rect[3]) // 2))
    time.sleep(1.2)
    report["steps"].append({"step": "triangle", "items": snap("tri")})
    report["foreground"] = None
    return report


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
