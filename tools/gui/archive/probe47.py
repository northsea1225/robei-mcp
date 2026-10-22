from __future__ import annotations
import json
import time


def run() -> dict:
    from pywinauto import keyboard, mouse
    import robei_ui
    main, win = robei_ui.main_window()
    if main is None:
        return {"ok": False, "error": "Robei not running"}
    out: dict = {"ok": True}
    tree = robei_ui._file_tree(main)
    if tree is None:
        return {"ok": False, "error": "no Files tree"}
    entries = robei_ui._tree_entries(tree)
    out["entries"] = [{"text": e["text"], "state": e["state"]} for e in entries]
    target = next((e for e in entries if "tb_and_gate" in e["text"].lower()), None)
    if target is None:
        return {"ok": False, "error": "test item missing", "entries": out["entries"]}
    r = robei_ui._rect(target["item"].element_info.rectangle)
    mouse.click(button="left", coords=robei_ui._center(r))
    time.sleep(0.6)
    mouse.right_click(coords=robei_ui._center(r))
    time.sleep(1.4)
    items = []
    for p in robei_ui.popups():
        for i in p["items"]:
            items.append({"type": i["type"], "name": i["name"], "rect": i["rect"]})
    out["context_menu"] = items
    keyboard.send_keys("{ESC}")
    time.sleep(0.4)
    # also dump the View menu after selecting the model (Codeview may only apply then)
    return out


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
