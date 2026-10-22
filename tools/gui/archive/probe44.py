from __future__ import annotations
import json
import time


def run() -> dict:
    from pywinauto import mouse
    import robei_ui
    main, win = robei_ui.main_window()
    if main is None:
        return {"ok": False, "error": "Robei not running"}
    out: dict = {"ok": True, "window_rect": win["rect"]}
    trees = []
    for ctrl in main.descendants(control_type="Tree"):
        try:
            i = ctrl.element_info
            trees.append({"auto_id": (i.automation_id or "")[:80], "class": (i.class_name or ""),
                          "rect": robei_ui._rect(i.rectangle)})
        except Exception:
            continue
    out["trees"] = trees
    # The wave tree lives in the right-hand Signal dock.
    target = None
    for ctrl in main.descendants(control_type="Tree"):
        try:
            i = ctrl.element_info
            r = robei_ui._rect(i.rectangle)
            if "WaveTree" in (i.automation_id or "") or (r[0] > 2000 and r[1] > 100 and r[3] < 950):
                target = ctrl
                out["target_auto_id"] = i.automation_id
                break
        except Exception:
            continue
    if target is None:
        return out
    items = target.descendants(control_type="TreeItem")
    out["items"] = [{"text": it.window_text(), "rect": robei_ui._rect(it.element_info.rectangle)} for it in items[:60]]
    # expand the root if needed
    for it in items[:1]:
        r = robei_ui._rect(it.element_info.rectangle)
        mouse.click(button="left", coords=robei_ui._center(r))
        time.sleep(0.6)
        try:
            it.expand()
        except Exception:
            pass
        time.sleep(1.2)
    items = target.descendants(control_type="TreeItem")
    out["items_after_expand"] = [{"text": it.window_text(), "rect": robei_ui._rect(it.element_info.rectangle)} for it in items[:60]]
    # single-click every leaf to add it to the wave window
    clicked = []
    for it in items:
        try:
            txt = it.window_text()
            if not txt or txt.lower() == "waves":
                continue
            r = robei_ui._rect(it.element_info.rectangle)
            mouse.click(button="left", coords=robei_ui._center(r))
            time.sleep(0.5)
            clicked.append(txt)
        except Exception:
            continue
    out["clicked"] = clicked
    time.sleep(1.5)
    table = None
    for ctrl in main.descendants():
        try:
            if (ctrl.element_info.automation_id or "").endswith("SignalTable"):
                table = ctrl
                break
        except Exception:
            continue
    if table is not None:
        out["table_children"] = [c.element_info.control_type for c in table.children()[:60]]
        out["table_texts"] = [c.window_text() for c in table.children() if c.window_text()][:40]
    return out


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
