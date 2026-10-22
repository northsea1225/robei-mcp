from __future__ import annotations
import json
import time

PROJ = r"C:\dev\robei-mcp\workspace\robei_samples\hier_probe\hier_probe.proj"


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
    out["open"] = {k: v for k, v in robei_ui.open_project(PROJ).items() if k in ("ok", "project", "error")}
    time.sleep(2.0)
    out["refresh"] = robei_ui.refresh().get("ok")
    main, win = robei_ui.main_window()
    out["open_model"] = {k: v for k, v in robei_ui.open_model("top_two.model").items()
                         if k in ("ok", "active", "models", "error")}

    # ToolBox: click its tab, then dump every element inside the dock
    clicked = False
    for ctrl in main.descendants(control_type="TabItem"):
        try:
            if ctrl.window_text() == "ToolBox":
                r = robei_ui._rect(ctrl.element_info.rectangle)
                mouse.click(button="left", coords=robei_ui._center(r))
                time.sleep(1.2)
                clicked = True
                break
        except Exception:
            continue
    out["toolbox_tab_clicked"] = clicked
    rows = []
    for ctrl in main.descendants():
        try:
            i = ctrl.element_info
            aid = i.automation_id or ""
            if "ToolBox" not in aid:
                continue
            rows.append({"type": i.control_type, "name": (i.name or "")[:40],
                         "auto_id": aid[:70], "rect": robei_ui._rect(i.rectangle)})
        except Exception:
            continue
    out["toolbox"] = rows[:60]

    # context menu on the model folder
    tree = robei_ui._file_tree(main)
    entries = robei_ui._tree_entries(tree)
    out["tree"] = [e["text"] for e in entries]
    folder = next((e for e in entries if e["text"] == "model"), None)
    if folder is not None:
        r = robei_ui._rect(folder["item"].element_info.rectangle)
        mouse.click(button="left", coords=robei_ui._center(r))
        time.sleep(0.4)
        mouse.right_click(coords=robei_ui._center(r))
        time.sleep(1.2)
        out["model_folder_menu"] = [i["name"] for p in robei_ui.popups() for i in p["items"] if i["type"] == "MenuItem"]
        keyboard.send_keys("{ESC}")
    return out


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
