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
    edit = None
    for ctrl in main.descendants(control_type="Edit"):
        try:
            i = ctrl.element_info
            r = robei_ui._rect(i.rectangle)
            if r[0] > 2000 and r[1] < 200:
                edit = ctrl
                out["search_edit"] = {"auto_id": i.automation_id, "rect": r}
                break
        except Exception:
            continue
    if edit is None:
        return {"ok": False, "error": "search box not found"}
    mouse.click(button="left", coords=robei_ui._center(out["search_edit"]["rect"]))
    time.sleep(0.3)
    keyboard.send_keys("^a")
    keyboard.send_keys("top_two", pause=0.03)
    keyboard.send_keys("{ENTER}")
    time.sleep(3.0)
    results = []
    for ctrl in main.descendants(control_type="ListItem"):
        try:
            i = ctrl.element_info
            results.append({"name": i.name, "rect": robei_ui._rect(i.rectangle)})
        except Exception:
            continue
    out["results"] = results[:20]
    if results:
        r = results[0]["rect"]
        mouse.click(button="left", coords=robei_ui._center(r))
        time.sleep(0.4)
        mouse.right_click(coords=robei_ui._center(r))
        time.sleep(1.4)
        out["result_menu"] = [{"name": i["name"], "rect": i["rect"]}
                              for p in robei_ui.popups() for i in p["items"] if i["type"] == "MenuItem"]
        keyboard.send_keys("{ESC}")
    return out


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
