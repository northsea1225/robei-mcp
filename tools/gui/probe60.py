from __future__ import annotations
import json
import time


def dump_list(main, label):
    rows = []
    for ctrl in main.descendants(control_type="List"):
        try:
            items = ctrl.children(control_type="ListItem")
            if not items:
                continue
            rows.append({
                "rect": [int(v) for v in ctrl.element_info.rectangle],
                "items": [{"name": i.window_text(), "rect": [int(v) for v in i.element_info.rectangle]}
                          for i in items[:40]],
            })
        except Exception:
            continue
    return {label: rows}


def run() -> dict:
    from pywinauto import mouse
    import robei_ui
    main, win = robei_ui.main_window()
    if main is None:
        return {"ok": False, "error": "Robei not running"}
    out: dict = {"ok": True}
    out.update(dump_list(main, "current_items"))
    # switch to the System category
    for ctrl in main.descendants(control_type="Button"):
        try:
            if ctrl.window_text() == "System":
                r = robei_ui._rect(ctrl.element_info.rectangle)
                mouse.click(button="left", coords=robei_ui._center(r))
                time.sleep(1.2)
                break
        except Exception:
            continue
    out.update(dump_list(main, "system_items"))
    return out


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
