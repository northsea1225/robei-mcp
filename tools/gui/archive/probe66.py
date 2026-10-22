from __future__ import annotations
import json


def run() -> dict:
    import robei_ui
    main, win = robei_ui.main_window()
    if main is None:
        return {"ok": False, "error": "Robei not running"}
    rows = []
    for ctrl in main.descendants():
        try:
            i = ctrl.element_info
            r = robei_ui._rect(i.rectangle)
            if r[1] > 120 or r[3] < 40:
                continue
            rows.append({"type": i.control_type, "name": (i.name or "")[:40],
                         "class": (i.class_name or "")[:24], "rect": r})
        except Exception:
            continue
    return {"ok": True, "top_strip": rows[:60]}


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
