from __future__ import annotations
import json
import time


def click_at(x, y, button="left"):
    from pywinauto import mouse
    mouse.click(button=button, coords=(x, y))
    time.sleep(1.2)


def menu_dump(tag):
    import robei_ui
    items = []
    for p in robei_ui.popups():
        for i in p["items"]:
            if i["type"] in ("MenuItem", "ListItem"):
                items.append({"name": i["name"], "rect": i["rect"]})
    return {"tag": tag, "items": items}


def run() -> dict:
    from pywinauto import keyboard
    import robei_ui
    main, win = robei_ui.main_window()
    if main is None:
        return {"ok": False, "error": "Robei not running"}
    out = {"ok": True}
    # 1) right-click the empty SignalTable
    click_at(220, 1150, "right")
    out["signaltable_menu"] = menu_dump("signal")
    out["shot1"] = robei_ui.__dict__.get("shot") and None
    keyboard.send_keys("{ESC}")
    time.sleep(0.6)
    # 2) right-click the waveform canvas
    click_at(1400, 1150, "right")
    out["waveform_menu"] = menu_dump("wave")
    keyboard.send_keys("{ESC}")
    time.sleep(0.4)
    return out


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
