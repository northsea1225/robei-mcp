from __future__ import annotations
import json
import time


def dump_menu(title):
    from pywinauto import keyboard, mouse
    import robei_ui
    main, _ = robei_ui.main_window()
    headers = robei_ui.menu_headers(main)
    if title not in headers:
        return None
    try:
        main.set_focus()
    except Exception:
        pass
    keyboard.send_keys("{ESC}")
    time.sleep(0.35)
    mouse.click(button="left", coords=robei_ui._center(headers[title]))
    time.sleep(1.3)
    items = [i["name"] for p in robei_ui.popups() for i in p["items"] if i["type"] == "MenuItem"]
    keyboard.send_keys("{ESC}")
    time.sleep(0.4)
    return items


def rclick_item(text_part):
    from pywinauto import keyboard, mouse
    import robei_ui
    main, _ = robei_ui.main_window()
    tree = robei_ui._file_tree(main)
    entries = robei_ui._tree_entries(tree) if tree is not None else []
    target = next((e for e in entries if text_part in e["text"].lower()), None)
    if target is None:
        return None
    r = robei_ui._rect(target["item"].element_info.rectangle)
    mouse.click(button="left", coords=robei_ui._center(r))
    time.sleep(0.5)
    mouse.right_click(coords=robei_ui._center(r))
    time.sleep(1.3)
    items = [i["name"] for p in robei_ui.popups() for i in p["items"] if i["type"] == "MenuItem"]
    keyboard.send_keys("{ESC}")
    time.sleep(0.4)
    return items


def run() -> dict:
    out = {"ok": True, "menus": {}, "ctx": {}}
    for title in ("File", "Edit", "Tool", "Setting", "Help"):
        out["menus"][title] = dump_menu(title)
    for name in ("and_gate.model", "exp3_write", "model"):
        out["ctx"][name] = rclick_item(name)
    return out


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
