"""Open Setting > Configure and dump the dialog controls, then cancel out."""
from __future__ import annotations


def run() -> dict:
    import time

    import robei_ui as ui

    dlg, win = ui.main_window()
    if dlg is None:
        return {"ok": False, "error": "Robei is not running"}
    before = {w["hwnd"] for w in ui.windows()}
    click = ui.click_menu(dlg, "Setting", "Configure", attempts=2)
    time.sleep(2.5)
    fresh = [w for w in ui.windows() if w["hwnd"] not in before]
    dialogs = []
    for item in fresh:
        entry = {"window": item, "controls": []}
        try:
            d = ui.attach(item["hwnd"])
            for ctrl in d.descendants()[:150]:
                try:
                    entry["controls"].append(
                        {
                            "type": ctrl.element_info.control_type,
                            "name": ctrl.element_info.name,
                            "class": ctrl.element_info.class_name,
                        }
                    )
                except Exception:
                    continue
        except Exception as exc:
            entry["error"] = f"{type(exc).__name__}: {exc}"
        dialogs.append(entry)
    # leave without saving
    from pywinauto import keyboard

    keyboard.send_keys("{ESC}")
    time.sleep(0.5)
    return {"ok": True, "click": click, "dialogs": dialogs}
