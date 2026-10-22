from __future__ import annotations
import json
import time
from pathlib import Path

PROJ = Path(r"C:\dev\robei-mcp\workspace\robei_samples\hier_probe")
MODEL = PROJ / "model" / "top_two.model"


def _open_add_model(main):
    from pywinauto import keyboard, mouse
    import robei_ui
    tree = robei_ui._file_tree(main)
    entries = robei_ui._tree_entries(tree)
    folder = next((e for e in entries if e["text"] == "model"), None)
    if folder is None:
        return {"ok": False, "error": "model folder not found"}
    r = robei_ui._rect(folder["item"].element_info.rectangle)
    mouse.click(button="left", coords=robei_ui._center(r))
    time.sleep(0.4)
    mouse.right_click(coords=robei_ui._center(r))
    time.sleep(1.2)
    item = next((i for p in robei_ui.popups() for i in p["items"]
                 if i["type"] == "MenuItem" and "add model" in i["name"].lower()), None)
    if item is None:
        keyboard.send_keys("{ESC}")
        return {"ok": False, "error": "Add model menu item missing"}
    mouse.click(button="left", coords=robei_ui._center(item["rect"]))
    time.sleep(2.0)
    return {"ok": True, "dialog": any("select file" in w["title"].lower() for w in robei_ui.windows())}


def run() -> dict:
    from pywinauto import keyboard
    import robei_ui
    main, win = robei_ui.main_window()
    if main is None:
        return {"ok": False, "error": "Robei not running"}
    out: dict = {"ok": True}
    out["before_size"] = MODEL.stat().st_size
    out["before_head"] = MODEL.read_text(encoding="utf-8", errors="replace")[:400]

    for attempt in range(2):
        has_dialog = any("select file" in w["title"].lower() for w in robei_ui.windows())
        if not has_dialog:
            out[f"open_menu_{attempt}"] = _open_add_model(main)
        else:
            out[f"dialog_reused_{attempt}"] = True
        pick = robei_ui.select_file_in_dialog(str(PROJ / "model" / "and2.model"))
        out[f"pick_{attempt}"] = pick
        time.sleep(3.0)
        out[f"windows_{attempt}"] = [w["title"] for w in robei_ui.windows()]
        out[f"tabs_{attempt}"] = robei_ui.model_tabs(main)

    try:
        keyboard.send_keys("^s")
        time.sleep(2.0)
    except Exception as exc:
        out["save_error"] = str(exc)
    out["output"] = robei_ui._tail(robei_ui.read_output(main), 12)
    out["after_size"] = MODEL.stat().st_size
    text = MODEL.read_text(encoding="utf-8", errors="replace")
    out["after_head"] = text[:1800]
    out["has_instance"] = "<Instance" in text
    return out


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
