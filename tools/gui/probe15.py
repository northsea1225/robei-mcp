"""Inspect the model canvas 'Code' tab: which widget holds the Verilog text."""
from __future__ import annotations

import json
import time
from pathlib import Path

OUT = Path(r"C:\dev\robei-mcp\workspace\gui_shots")


def run() -> dict:
    import robei_ui
    from pywinauto import mouse
    from PIL import ImageGrab

    dlg, win = robei_ui.main_window()
    if dlg is None:
        return {"ok": False, "error": "Robei not running"}

    report: dict = {"ok": True, "tabs": robei_ui.tab_groups(dlg)}

    code_item = None
    for tab in dlg.descendants(control_type="Tab"):
        try:
            if "Design.qt_tabwidget_tabbar" not in (tab.element_info.automation_id or ""):
                continue
            for item in tab.children(control_type="TabItem"):
                if item.window_text().strip().lower() == "code":
                    code_item = item
        except Exception:
            continue
    if code_item is None:
        report["ok"] = False
        report["error"] = "Code tab item not found"
        return report

    rect = robei_ui._rect(code_item.element_info.rectangle)
    mouse.click(button="left", coords=robei_ui._center(rect))
    time.sleep(1.5)
    shot = OUT / time.strftime("p15-code-%H%M%S.png")
    ImageGrab.grab(all_screens=True).save(shot)
    report["screenshot"] = str(shot)

    controls = []
    for ctrl in dlg.descendants():
        try:
            info = ctrl.element_info
            if info.control_type not in ("Edit", "Document", "Text", "Pane", "Group", "Custom"):
                continue
            r = robei_ui._rect(info.rectangle)
            controls.append(
                {
                    "type": info.control_type,
                    "name": (info.name or "")[:40],
                    "auto_id": (info.automation_id or "")[:60],
                    "class": (info.class_name or "")[:40],
                    "rect": r,
                    "ctrl": ctrl,
                }
            )
        except Exception:
            continue
    report["control_count"] = len(controls)
    report["controls"] = [{k: v for k, v in c.items() if k != "ctrl"} for c in controls[:30]]

    probe_rows = []
    for c in controls:
        if c["type"] not in ("Edit", "Document"):
            continue
        entry = {"class": c["class"], "auto_id": c["auto_id"], "rect": c["rect"]}
        ctrl = c["ctrl"]
        for label, fn in (
            ("window_text", lambda: ctrl.window_text()),
            ("get_value", lambda: ctrl.get_value()),
            ("value_pattern", lambda: ctrl.iface_value.CurrentValue),
            ("text_pattern", lambda: ctrl.iface_text.DocumentText()),
        ):
            try:
                value = fn()
                entry[label] = {"len": len(value) if isinstance(value, str) else None, "head": (value or "")[:200] if isinstance(value, str) else repr(value)}
            except Exception as exc:
                entry[label] = f"{type(exc).__name__}"
        probe_rows.append(entry)
    report["text_probe"] = probe_rows
    return report


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
