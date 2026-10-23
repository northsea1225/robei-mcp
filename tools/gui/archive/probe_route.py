"""Try Build > Route and Build > Package on the open constrain model; report output deltas."""
from __future__ import annotations


def run() -> dict:
    import time

    import robei_ui as ui

    dlg, win = ui.main_window()
    if dlg is None:
        return {"ok": False, "error": "Robei is not running"}
    out = {"ok": True, "active": ui.active_model(dlg), "steps": []}
    for item in ("Route", "Package"):
        before = ui.read_output(dlg)
        click = ui.click_menu(dlg, "Build", item, attempts=2)
        time.sleep(10.0)
        after = ui.read_output(dlg)
        delta = after[len(before):] if after.startswith(before) else after
        out["steps"].append(
            {
                "item": item,
                "click": click.get("ok"),
                "error": click.get("error"),
                "delta": delta.strip()[-1200:],
            }
        )
    return out
