from __future__ import annotations
import json
import traceback


def run() -> dict:
    import robei_ui
    main, win = robei_ui.main_window()
    out = {"ok": True, "win": win}
    try:
        allc = main.descendants()
        out["descendants"] = len(allc)
    except Exception:
        out["descendants_error"] = traceback.format_exc()[-800:]
        allc = []
    trees = []
    for c in allc:
        try:
            if c.element_info.control_type == "Tree":
                trees.append({"auto_id": c.element_info.automation_id, "class": c.element_info.class_name,
                              "rect": [int(v) for v in c.element_info.rectangle]})
        except Exception as exc:
            trees.append({"error": f"{type(exc).__name__}: {exc}"})
    out["trees"] = trees
    try:
        t2 = main.descendants(control_type="Tree")
        out["trees_filtered"] = len(t2)
    except Exception:
        out["trees_filtered_error"] = traceback.format_exc()[-600:]
    try:
        t3 = main.descendants(control_type="TreeItem")
        out["treeitems_all"] = [i.window_text() for i in t3[:40]]
    except Exception:
        out["treeitems_error"] = traceback.format_exc()[-600:]
    return out


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
