"""Open Configure Settings and try to read the device ComboBox items, then cancel."""
from __future__ import annotations


def run() -> dict:
    import time

    import robei_ui as ui
    from pywinauto import mouse

    dlg, win = ui.main_window()
    if dlg is None:
        return {"ok": False, "error": "Robei is not running"}
    before = {w["hwnd"] for w in ui.windows()}
    ui.click_menu(dlg, "Setting", "Configure", attempts=2)
    time.sleep(2.5)
    fresh = [w for w in ui.windows() if w["hwnd"] not in before]
    if not fresh:
        return {"ok": False, "error": "configure dialog did not appear"}
    d = ui.attach(fresh[0]["hwnd"])
    combos = d.descendants(control_type="ComboBox")
    out = {"ok": True, "combo_count": len(combos), "attempts": []}
    for index, combo in enumerate(combos[:2]):
        try:
            rect = ui._rect(combo.element_info.rectangle)
            mouse.click(button="left", coords=ui._center(rect))
            time.sleep(1.2)
            items = []
            for win2 in ui.windows():
                try:
                    d2 = ui.attach(win2["hwnd"])
                    for li in d2.descendants(control_type="ListItem")[:30]:
                        name = li.element_info.name
                        if name and name not in items:
                            items.append(name)
                except Exception:
                    continue
            out["attempts"].append({"combo": index, "items": items, "value": combo.window_text()})
            from pywinauto import keyboard

            keyboard.send_keys("{ESC}")
            time.sleep(0.5)
        except Exception as exc:
            out["attempts"].append({"combo": index, "error": f"{type(exc).__name__}: {exc}"})
    from pywinauto import keyboard

    keyboard.send_keys("{ESC}")
    time.sleep(0.5)
    return out
