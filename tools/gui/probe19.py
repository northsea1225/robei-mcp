"""Add a port through the canvas: toolbar 'Port' then click the canvas."""
from __future__ import annotations

import json
import time
from pathlib import Path

OUT = Path(r"C:\dev\robei-mcp\workspace\gui_shots")


def _toolbar(dlg):
    for ctrl in dlg.descendants():
        try:
            if ctrl.element_info.automation_id == "QApplication.Romain.QToolBar":
                return ctrl
        except Exception:
            continue
    return None


def _prop_rows(dlg) -> list[dict]:
    rows = []
    for ctrl in dlg.descendants(control_type="Table"):
        try:
            if ctrl.element_info.automation_id != "QApplication.Romain.Property.QTableView":
                continue
            import robei_ui as _ui

            for item in ctrl.descendants()[:40]:
                info = item.element_info
                rows.append(
                    {
                        "type": info.control_type,
                        "name": (info.name or "")[:40],
                        "rect": _ui._rect(info.rectangle),
                        "class": info.class_name,
                    }
                )
        except Exception:
            continue
    return rows


def run() -> dict:
    import robei_ui
    from pywinauto import mouse
    from PIL import ImageGrab

    dlg, win = robei_ui.main_window()
    if dlg is None:
        return {"ok": False, "error": "Robei not running"}
    report: dict = {"ok": True, "active": robei_ui.active_model(dlg)}

    def shot(label: str) -> str:
        path = OUT / time.strftime(f"p19-{label}-%H%M%S.png")
        ImageGrab.grab(all_screens=True).save(path)
        return str(path)

    dlg.set_focus()
    time.sleep(0.5)
    report["props_before"] = _prop_rows(dlg)
    report["shot_before"] = shot("before")

    bar = _toolbar(dlg)
    port_btn = None
    if bar is not None:
        for ctrl in bar.descendants():
            try:
                if ctrl.element_info.name == "Port":
                    port_btn = ctrl
                    break
            except Exception:
                continue
    if port_btn is None:
        return {"ok": False, "error": "Port toolbar button not found"}
    rect = robei_ui._rect(port_btn.element_info.rectangle)
    center = ((rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2)
    mouse.click(button="left", coords=center)
    time.sleep(1.0)
    report["port_button"] = {"rect": rect, "clicked": center}
    report["shot_tool"] = shot("tool")

    # click inside the canvas near the left edge so the new port lands on the border
    canvas_point = (420, 400)
    mouse.click(button="left", coords=canvas_point)
    time.sleep(1.5)
    report["canvas_click"] = canvas_point
    report["shot_after_click"] = shot("after-click")
    report["props_after"] = _prop_rows(dlg)
    report["models"] = robei_ui.model_tabs(dlg)
    return report


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
