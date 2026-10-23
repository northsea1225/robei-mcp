"""Save the project, then run Build > Synthesis on the open constraint model."""
from __future__ import annotations


def run() -> dict:
    import time

    import robei_ui as ui

    dlg, win = ui.main_window()
    if dlg is None:
        return {"ok": False, "error": "Robei is not running"}
    out = {"ok": True}
    out["active"] = ui.active_model(dlg)
    out["save"] = ui.click_menu(dlg, "File", "Save", attempts=2)
    time.sleep(2.0)
    out["output_after_save"] = ui._tail(ui.read_output(dlg), 12)
    out["synthesis"] = ui.click_menu(dlg, "Build", "Synthesis", attempts=2)
    time.sleep(12.0)
    out["output_after_synthesis"] = ui._tail(ui.read_output(dlg), 30)
    out["active_after"] = ui.active_model(dlg)
    return out
