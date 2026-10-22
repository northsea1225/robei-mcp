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
    entries = robei_ui._tree_entries(tree)
    target = next((e for e in entries if e["text"].lower() == "and_gate.model"), None)
    r = robei_ui._rect(target["item"].element_info.rectangle)
    mouse.click(button="left", coords=robei_ui._center(r))
    time.sleep(0.5)
    mouse.right_click(coords=robei_ui._center(r))
    time.sleep(1.3)
    entry = None
    for p in robei_ui.popups():
        for i in p["items"]:
            if i["name"].strip() == "Create Test":
                entry = i
    out["found"] = entry is not None
    if entry is None:
        keyboard.send_keys("{ESC}")
        return out
    mouse.click(button="left", coords=robei_ui._center(entry["rect"]))
    time.sleep(3.0)
    out["windows"] = [{"title": w["title"], "class": w["class"], "rect": w["rect"]} for w in robei_ui.windows()]
    dlg = None
    for w in robei_ui.windows():
        if w["class"] == robei_ui.DIALOG_CLASS:
            dlg = w
    if dlg is not None:
        rows = []
        for c in robei_ui._open_dialog_controls(dlg):
            rows.append({k: c[k] for k in ("type", "name", "auto_id", "class", "rect")})
        out["dialog"] = rows
    out["tabs"] = robei_ui.tab_groups(main)
    out["tree"] = [e["text"] for e in robei_ui._tree_entries(robei_ui._file_tree(main))]
    out["output"] = robei_ui._tail(robei_ui.read_output(main), 15)
    return out


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
