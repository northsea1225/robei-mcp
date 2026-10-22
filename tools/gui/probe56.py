from __future__ import annotations
import json
import time


def run() -> dict:
    import robei_ui
    out: dict = {"ok": True}
    main, win = robei_ui.main_window()
    if main is None:
        out["launch"] = robei_ui.launch(r"D:\robei-lab\robei\bin\Robei.exe", 40)
        main, win = robei_ui.main_window()
    if main is None:
        return {"ok": False, "error": "Robei did not start"}
    out["new_menu"] = robei_ui.click_menu(main, "File", "New")
    time.sleep(2.0)
    out["windows"] = [{"title": w["title"], "class": w["class"], "rect": w["rect"]} for w in robei_ui.windows()]
    dlg = None
    for w in robei_ui.windows():
        if w["class"] == robei_ui.DIALOG_CLASS:
            dlg = w
    if dlg is not None:
        rows = []
        for c in robei_ui._open_dialog_controls(dlg)[:40]:
            rows.append({k: c[k] for k in ("type", "name", "auto_id", "class", "rect")})
        out["dialog"] = rows
    else:
        # Qt-based dialog lives inside the main window process
        rows = []
        for ctrl in main.descendants():
            try:
                i = ctrl.element_info
                if i.control_type in ("Edit", "ComboBox", "RadioButton", "Button", "ListItem", "CheckBox"):
                    rows.append({"type": i.control_type, "name": (i.name or "")[:40],
                                 "auto_id": (i.automation_id or "")[:60],
                                 "rect": robei_ui._rect(i.rectangle)})
            except Exception:
                continue
        out["qt_controls"] = rows[:40]
    out["shot"] = robei_ui.shot("new-dialog-const") if hasattr(robei_ui, "shot") else None
    return out


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
