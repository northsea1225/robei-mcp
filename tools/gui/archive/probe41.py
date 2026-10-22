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
            if r[1] < 560:
                continue
            rows.append({
                "type": i.control_type,
                "name": (i.name or "")[:60],
                "auto_id": (i.automation_id or "")[:70],
                "class": (i.class_name or "")[:50],
                "rect": r,
            })
        except Exception:
            continue
    return {"ok": True, "count": len(rows), "bottom": rows[:120], "tabs": robei_ui.tab_groups(main)}


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
