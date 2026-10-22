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


def items(tree):
    return [it.window_text() for it in tree.descendants(control_type="TreeItem")[:80]]


def run() -> dict:
    from pywinauto import mouse
    import robei_ui
    main, win = robei_ui.main_window()
    if main is None:
        return {"ok": False, "error": "Robei not running"}
    out: dict = {"ok": True}
    tree = wave_tree(main)
    out["before"] = items(tree) if tree is not None else []
    for _ in range(4):
        tree = wave_tree(main)
        if tree is None:
            break
        nodes = tree.descendants(control_type="TreeItem")
        changed = False
        for it in nodes[:40]:
            try:
                st = int(it.get_expand_state())
            except Exception:
                st = None
            if st == 0:
                r = robei_ui._rect(it.element_info.rectangle)
                mouse.double_click(button="left", coords=robei_ui._center(r))
                time.sleep(0.8)
                changed = True
        if not changed:
            break
        time.sleep(0.6)
    tree = wave_tree(main)
    out["expanded"] = items(tree) if tree is not None else []
    # single-click every leaf signal
    clicked = []
    if tree is not None:
        for it in tree.descendants(control_type="TreeItem")[:60]:
            try:
                txt = it.window_text()
                st = it.get_expand_state()
                r = robei_ui._rect(it.element_info.rectangle)
                if txt.lower() == "waves":
                    continue
                # click leaves only (no children)
                mouse.click(button="left", coords=robei_ui._center(r))
                time.sleep(0.45)
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
        out["table_texts"] = [c.window_text() for c in table.children() if c.window_text()][:40]
    out["output"] = robei_ui._tail(robei_ui.read_output(main), 6)
    return out


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
