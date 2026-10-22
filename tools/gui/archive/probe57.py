from __future__ import annotations
import json


def run() -> dict:
    import robei_ui
    out: dict = {"ok": True}
    target = None
    for w in robei_ui.windows():
        if "new project" in w["title"].lower():
            target = w
    if target is None:
        return {"ok": False, "error": "New Project Setting dialog not found", "windows": [w["title"] for w in robei_ui.windows()]}
    dlg = robei_ui.attach(target["hwnd"])
    rows = []
    for ctrl in dlg.descendants()[:60]:
        try:
            i = ctrl.element_info
            rows.append({"type": i.control_type, "name": (i.name or "")[:50],
                         "auto_id": (i.automation_id or "")[:60],
                         "class": (i.class_name or "")[:30],
                         "rect": robei_ui._rect(i.rectangle)})
        except Exception:
            continue
    out["dialog_rect"] = target["rect"]
    out["controls"] = rows
    return out


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
