"""Dump every top-level menu and its entries (read-only)."""
from __future__ import annotations


def run() -> dict:
    import robei_ui as ui

    dlg, win = ui.main_window()
    if dlg is None:
        return {"ok": False, "error": "Robei is not running"}
    out = {}
    for title in ui.menu_headers(dlg):
        report = ui.click_menu(dlg, title, "__definitely_not_an_item__", attempts=1)
        items = []
        for attempt in report.get("tries") or []:
            for name in attempt.get("items") or []:
                if name not in items:
                    items.append(name)
        out[title] = items
    return {"ok": True, "menus": out}
