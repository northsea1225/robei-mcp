from __future__ import annotations
import json
import time


def wave_tree_items(main):
    for ctrl in main.descendants(control_type="Tree"):
        try:
            if (ctrl.element_info.automation_id or "").endswith("WaveTree"):
                return ctrl, [it.window_text() for it in ctrl.descendants(control_type="TreeItem")[:60]]
        except Exception:
            continue
    return None, []


def run() -> dict:
    from pywinauto import mouse
    import robei_ui
    main, win = robei_ui.main_window()
    if main is None:
        return {"ok": False, "error": "Robei not running"}
    out: dict = {"ok": True}
    out["waveview_menu"] = robei_ui.click_menu(main, "Window", "Waveview")
    time.sleep(1.5)
    out["wave_menu"] = robei_ui.click_menu(main, "Build", "Wave")
    time.sleep(3.0)
    tree, items = wave_tree_items(main)
    out["items_1"] = items
    if tree is not None and items == ["Waves"]:
        it = tree.descendants(control_type="TreeItem")[0]
        r = robei_ui._rect(it.element_info.rectangle)
        mouse.double_click(button="left", coords=robei_ui._center(r))
        time.sleep(2.0)
        out["items_2"] = wave_tree_items(main)[1]
    out["output"] = robei_ui._tail(robei_ui.read_output(main), 10)
    out["windows"] = [{"title": w["title"], "class": w["class"]} for w in robei_ui.windows()]
    return out


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
