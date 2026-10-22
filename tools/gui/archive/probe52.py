from __future__ import annotations
import json
import time


def wave_tree(main):
    for ctrl in main.descendants(control_type="Tree"):
        try:
            if (ctrl.element_info.automation_id or "").endswith("WaveTree"):
                return ctrl
        except Exception:
            continue
    return None


def snapshot(main):
    tree = wave_tree(main)
    if tree is None:
        return []
    rows = []
    for it in tree.descendants(control_type="TreeItem")[:80]:
        try:
            rows.append({"text": it.window_text(), "state": it.get_expand_state(),
                         "rect": robei_ui._rect(it.element_info.rectangle)})
        except Exception:
            continue
    return rows


def run() -> dict:
    from pywinauto import mouse
    import robei_ui
    main, win = robei_ui.main_window()
    if main is None:
        return {"ok": False, "error": "Robei not running"}
    out: dict = {"ok": True}
    for _ in range(3):
        rows = snapshot(main)
        todo = [r for r in rows if r["state"] == 0]
        if not todo:
            break
        for r in todo:
            mouse.double_click(button="left", coords=robei_ui._center(r["rect"]))
            time.sleep(0.7)
    out["tree"] = [{"text": r["text"], "state": r["state"]} for r in snapshot(main)]
    clicked = []
    for name in ("y", "a", "b", "dut.a", "dut.b", "dut.y"):
        rows = snapshot(main)
        root = [r for r in rows if r["text"] == "Waves"]
        if root:
            # make sure top node is expanded (cheap: double click if collapsed)
            pass
        leaf = next((r for r in rows if r["text"] == name.split(".")[-1] and r["state"] != 1), None)
        if leaf is None:
            # try expanding 'dut' first
            dut = next((r for r in rows if r["text"] == "dut" and r["state"] == 0), None)
            if dut is not None:
                mouse.double_click(button="left", coords=robei_ui._center(dut["rect"]))
                time.sleep(0.8)
                rows = snapshot(main)
                leaf = next((r for r in rows if r["text"] == name.split(".")[-1] and r["state"] != 1), None)
        if leaf is None:
            clicked.append({"name": name, "clicked": False})
            continue
        mouse.click(button="left", coords=robei_ui._center(leaf["rect"]))
        time.sleep(0.6)
        clicked.append({"name": name, "clicked": True})
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
        out["table_texts"] = [c.window_text() for c in table.children() if c.window_text()][:60]
    return out


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
