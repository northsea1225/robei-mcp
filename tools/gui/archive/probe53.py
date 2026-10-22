from __future__ import annotations
import json
import time


def rows(dlg):
    for ctrl in dlg.descendants(control_type="Tree"):
        try:
            if (ctrl.element_info.automation_id or "").endswith("WaveTree"):
                out = []
                for it in ctrl.descendants(control_type="TreeItem")[:80]:
                    out.append({"text": it.window_text(), "state": it.get_expand_state(),
                                "rect": [int(v) for v in it.element_info.rectangle]})
                return out
        except Exception:
            continue
    return []


def table(dlg):
    for ctrl in dlg.descendants():
        try:
            if (ctrl.element_info.automation_id or "").endswith("SignalTable"):
                return [c.window_text() for c in ctrl.children(control_type="DataItem")]
        except Exception:
            continue
    return None


def run() -> dict:
    from pywinauto import mouse
    import robei_ui
    main, _ = robei_ui.main_window()
    out = {"ok": True}
    out["rows"] = rows(main)
    clicked = []
    for name in ("y", "a", "b", "dut.y"):
        main, _ = robei_ui.main_window()
        rs = rows(main)
        leaf = next((r for r in rs if r["text"] == name), None)
        if leaf is None:
            clicked.append({"name": name, "found": False, "tree": [r["text"] for r in rs]})
            continue
        x = (leaf["rect"][0] + leaf["rect"][2]) // 2
        y = (leaf["rect"][1] + leaf["rect"][3]) // 2
        mouse.click(button="left", coords=(x, y))
        time.sleep(0.7)
        clicked.append({"name": name, "found": True, "at": [x, y]})
    out["clicked"] = clicked
    main, _ = robei_ui.main_window()
    time.sleep(1.0)
    out["table_after"] = table(main)
    return out


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
