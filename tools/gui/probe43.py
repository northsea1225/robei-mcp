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
    tb = []
    for ctrl in main.descendants(control_type="Button"):
        try:
            i = ctrl.element_info
            r = robei_ui._rect(i.rectangle)
            if r[1] < 120:
                tb.append({"name": (i.name or "")[:40], "auto_id": (i.automation_id or "")[:60], "rect": r})
        except Exception:
            continue
    out["toolbar"] = tb
    # 1) click the Wave tab item (bottom left)
    for ctrl in main.descendants(control_type="TabItem"):
        try:
            if ctrl.window_text() == "Wave" and robei_ui._rect(ctrl.element_info.rectangle)[1] > 1200:
                r = robei_ui._rect(ctrl.element_info.rectangle)
                mouse.click(button="left", coords=robei_ui._center(r))
                out["wave_tab_clicked"] = True
                break
        except Exception:
            continue
    time.sleep(1.0)
    # 2) Build > Run while the Wave tab is open
    before = robei_ui.read_output(main)
    out["run_click"] = robei_ui.click_menu(main, "Build", "Run")
    time.sleep(12.0)
    after = robei_ui.read_output(main)
    out["delta"] = (after[len(before):] if after.startswith(before) else after).strip()[:800]
    # 3) dump wave viewer contents
    rows = []
    for ctrl in main.descendants():
        try:
            i = ctrl.element_info
            if "WaveViewer" not in (i.automation_id or ""):
                continue
            rows.append({"type": i.control_type, "name": (i.name or "")[:60], "auto_id": (i.automation_id or "")[:70],
                         "class": (i.class_name or "")[:40], "rect": robei_ui._rect(i.rectangle)})
        except Exception:
            continue
    out["wave_controls"] = rows
    table = None
    for ctrl in main.descendants():
        try:
            if (ctrl.element_info.automation_id or "").endswith("SignalTable"):
                table = ctrl
                break
        except Exception:
            continue
    if table is not None:
        out["table_rows"] = [r.window_text() for r in table.children(control_type="DataItem")[:40]]
        out["table_children"] = [c.element_info.control_type for c in table.children()[:40]]
    return out


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
