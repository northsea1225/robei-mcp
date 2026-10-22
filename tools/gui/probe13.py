"""Diagnose the Robei Files tree: expansion states, patterns, child enumeration."""
from __future__ import annotations

import json
import time


def run() -> dict:
    import robei_ui
    from pywinauto import mouse

    dlg, win = robei_ui.main_window()
    if dlg is None:
        return {"ok": False, "error": "Robei not running"}

    tree = None
    for ctrl in dlg.descendants(control_type="Tree"):
        try:
            if ctrl.element_info.class_name == "FileTree":
                tree = ctrl
                break
        except Exception:
            continue
    if tree is None:
        return {"ok": False, "error": "FileTree not found"}

    def snapshot(label: str) -> list[dict]:
        rows = []
        for item in tree.descendants(control_type="TreeItem")[:40]:
            try:
                state = None
                for api in ("get_expand_state", "is_expanded", "is_collapsed"):
                    fn = getattr(item, api, None)
                    if fn is None:
                        continue
                    try:
                        state = {api: fn()}
                        break
                    except Exception as exc:
                        state = {api: f"err {type(exc).__name__}"}
                rows.append(
                    {
                        "text": item.window_text(),
                        "state": state,
                        "rect": robei_ui._rect(item.element_info.rectangle),
                    }
                )
            except Exception as exc:
                rows.append({"error": f"{type(exc).__name__}: {exc}"})
        return rows

    report = {"ok": True, "before": snapshot("before")}

    expand_errors = []
    expand = getattr(tree, "expand", None)
    if callable(expand):
        try:
            expand()
            time.sleep(0.5)
        except Exception as exc:
            expand_errors.append(f"tree.expand: {type(exc).__name__}: {exc}")
    for item in tree.descendants(control_type="TreeItem"):
        for api in ("expand", "collapse"):
            fn = getattr(item, api, None)
            if not callable(fn):
                continue
        try:
            fn = getattr(item, "expand")
            fn()
            time.sleep(0.1)
        except Exception as exc:
            expand_errors.append(f"{item.window_text()}: {type(exc).__name__}: {exc}")
    report["expand_errors"] = expand_errors[:6]
    report["after_expand"] = snapshot("after")

    # double click the "model" folder as a fallback route
    model_folder = next((i for i in tree.descendants(control_type="TreeItem") if i.window_text() == "model"), None)
    if model_folder is not None:
        rect = robei_ui._rect(model_folder.element_info.rectangle)
        center = ((rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2)
        mouse.double_click(button="left", coords=center)
        time.sleep(1.0)
        report["after_dblclick_model"] = snapshot("dbl")
    return report


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
