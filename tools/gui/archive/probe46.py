from __future__ import annotations
import json
import time


def run() -> dict:
    from pywinauto import keyboard, mouse
    import robei_ui
    main, win = robei_ui.main_window()
    if main is None:
        return {"ok": False, "error": "Robei not running"}
    out: dict = {"ok": True, "menus": {}}
    headers = robei_ui.menu_headers(main)
    for title in ("Build", "View", "Window"):
        if title not in headers:
            continue
        try:
            main.set_focus()
        except Exception:
            pass
        keyboard.send_keys("{ESC}")
        time.sleep(0.4)
        mouse.click(button="left", coords=robei_ui._center(headers[title]))
        time.sleep(1.4)
        items = []
        for p in robei_ui.popups():
            for i in p["items"]:
                if i["type"] == "MenuItem":
                    items.append({"name": i["name"], "rect": i["rect"]})
        out["menus"][title] = items
        keyboard.send_keys("{ESC}")
        time.sleep(0.5)
    return out


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
