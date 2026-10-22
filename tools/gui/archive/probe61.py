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
    # make sure the Files tab is the visible one
    for ctrl in main.descendants(control_type="TabItem"):
        try:
            if ctrl.window_text() == "Files":
                r = robei_ui._rect(ctrl.element_info.rectangle)
                mouse.click(button="left", coords=robei_ui._center(r))
                time.sleep(1.0)
                break
        except Exception:
            continue
    tree = robei_ui._file_tree(main)
    entries = robei_ui._tree_entries(tree)
    out["tree"] = [e["text"] for e in entries]
    folder = next((e for e in entries if e["text"] == "model"), None)
    if folder is None:
        return {"ok": False, "error": "model folder entry not found", "tree": out["tree"]}
    r = robei_ui._rect(folder["item"].element_info.rectangle)
    out["folder_rect"] = r
    mouse.click(button="left", coords=robei_ui._center(r))
    time.sleep(0.5)
    mouse.right_click(coords=robei_ui._center(r))
    time.sleep(1.5)
    items = [{"name": i["name"], "rect": i["rect"]} for p in robei_ui.popups() for i in p["items"] if i["type"] == "MenuItem"]
    out["menu"] = items
    out["windows"] = [w["title"] for w in robei_ui.windows()]
    if items:
        target = next((i for i in items if "add model" in i["name"].lower()), None)
        if target is not None:
            mouse.click(button="left", coords=robei_ui._center(target["rect"]))
            time.sleep(2.5)
            out["after_add_click"] = [w["title"] for w in robei_ui.windows()]
            # dump any new dialog
            for w in robei_ui.windows():
                if w["title"].lower() in ("robei  student",):
                    continue
                d = robei_ui.attach(w["hwnd"])
                rows = []
                for c in d.descendants()[:40]:
                    try:
                        i = c.element_info
                        rows.append({"type": i.control_type, "name": (i.name or "")[:40],
                                     "auto_id": (i.automation_id or "")[:60],
                                     "rect": robei_ui._rect(i.rectangle)})
                    except Exception:
                        continue
                out.setdefault("dialogs", []).append({"title": w["title"], "controls": rows})
    else:
        keyboard.send_keys("{ESC}")
    return out


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
