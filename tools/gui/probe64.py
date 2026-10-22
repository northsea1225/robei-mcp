from __future__ import annotations
import json
import time

PROJ = r"C:\dev\robei-mcp\workspace\robei_samples\two_level\two_level.proj"


def menu_items():
    import robei_ui
    return [{"name": i["name"], "rect": i["rect"]} for p in robei_ui.popups() for i in p["items"] if i["type"] == "MenuItem"]


def run() -> dict:
    from pywinauto import keyboard, mouse
    import robei_ui
    out: dict = {"ok": True}
    main, win = robei_ui.main_window()
    if main is None:
        out["launch"] = robei_ui.launch(r"D:\robei-lab\robei\bin\Robei.exe", 40)
        main, win = robei_ui.main_window()
    if main is None:
        return {"ok": False, "error": "Robei did not start"}
    out["open"] = {k: v for k, v in robei_ui.open_project(PROJ).items() if k in ("ok", "project")}
    time.sleep(2.0)
    robei_ui.refresh()
    main, win = robei_ui.main_window()
    out["model"] = {k: v for k, v in robei_ui.open_model("top_two.model").items() if k in ("ok", "active")}

    # 1) right-click the model tab
    tab = None
    for ctrl in main.descendants(control_type="TabItem"):
        try:
            if ctrl.window_text() == "top_two":
                tab = robei_ui._rect(ctrl.element_info.rectangle)
                break
        except Exception:
            continue
    if tab:
        mouse.click(button="left", coords=robei_ui._center(tab))
        time.sleep(0.4)
        mouse.right_click(coords=robei_ui._center(tab))
        time.sleep(1.3)
        out["tab_menu"] = menu_items()
        keyboard.send_keys("{ESC}")
        time.sleep(0.5)

    # 2) right-click the canvas (Graph tab first)
    for ctrl in main.descendants(control_type="TabItem"):
        try:
            if ctrl.window_text() == "Graph":
                mouse.click(button="left", coords=robei_ui._center(robei_ui._rect(ctrl.element_info.rectangle)))
                time.sleep(1.2)
                break
        except Exception:
            continue
    mouse.right_click(coords=(1150, 500))
    time.sleep(1.5)
    out["canvas_menu"] = menu_items()
    keyboard.send_keys("{ESC}")
    time.sleep(0.4)

    # 3) Edit menu with canvas focused
    headers = robei_ui.menu_headers(main)
    if "Edit" in headers:
        mouse.click(button="left", coords=robei_ui._center(headers["Edit"]))
        time.sleep(1.3)
        out["edit_menu"] = [i["name"] for p in robei_ui.popups() for i in p["items"] if i["type"] == "MenuItem"]
        keyboard.send_keys("{ESC}")
    return out


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
