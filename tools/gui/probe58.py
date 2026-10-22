from __future__ import annotations
import json
import time
from pathlib import Path

PROJ = Path(r"C:\dev\robei-mcp\workspace\robei_samples\two_level")


def run() -> dict:
    from pywinauto import keyboard, mouse
    import robei_ui
    out: dict = {"ok": True}
    main, win = robei_ui.main_window()
    if main is None:
        return {"ok": False, "error": "Robei not running"}
    st = robei_ui.status(5)
    out["project"] = st.get("project")
    if (st.get("project") or "").lower() != "two_level":
        out["open"] = {k: v for k, v in robei_ui.open_project(str(PROJ / "two_level.proj")).items() if k in ("ok", "project", "error")}
        time.sleep(2.0)
        main, win = robei_ui.main_window()

    out["new"] = robei_ui.click_menu(main, "File", "New")
    time.sleep(2.0)
    target = next((w for w in robei_ui.windows() if "new project" in w["title"].lower()), None)
    if target is None:
        return {"ok": False, "error": "no New Project dialog"}
    dlg = robei_ui.attach(target["hwnd"])
    edits = [c for c in dlg.descendants(control_type="Edit")]
    combo = dlg.children(control_type="ComboBox")[0]
    out["combo_rect"] = robei_ui._rect(combo.element_info.rectangle)
    mouse.click(button="left", coords=robei_ui._center(out["combo_rect"]))
    time.sleep(1.2)
    items = []
    for p in robei_ui.popups():
        for i in p["items"]:
            if i["type"] in ("ListItem", "MenuItem"):
                items.append({"name": i["name"], "rect": i["rect"]})
    out["combo_items"] = items
    out["combo_text_before"] = combo.window_text()
    if not items:
        return out
    pick = next((i for i in items if i["name"].strip().lower() == "const"), None)
    out["picked"] = pick["name"] if pick else None
    if pick is None:
        keyboard.send_keys("{ESC}")
        return out
    mouse.click(button="left", coords=robei_ui._center(pick["rect"]))
    time.sleep(1.0)
    out["combo_text_after"] = combo.window_text()
    # name + path
    mouse.click(button="left", coords=robei_ui._center(robei_ui._rect(edits[0].element_info.rectangle)))
    keyboard.send_keys("^a")
    keyboard.send_keys("pin_probe", pause=0.02)
    time.sleep(0.4)
    path_edit = edits[-1]
    mouse.click(button="left", coords=robei_ui._center(robei_ui._rect(path_edit.element_info.rectangle)))
    keyboard.send_keys("^a")
    keyboard.send_keys(str(PROJ) + "\\", pause=0.01)
    time.sleep(0.5)
    ok = next((c for c in dlg.descendants(control_type="Button") if c.window_text().strip().lower() == "ok"), None)
    out["ok_button"] = ok is not None
    if ok is not None:
        mouse.click(button="left", coords=robei_ui._center(robei_ui._rect(ok.element_info.rectangle)))
    time.sleep(3.0)
    out["windows_after"] = [w["title"] for w in robei_ui.windows()]
    out["const_files"] = [p.name for p in (PROJ / "const").glob("*")] if (PROJ / "const").is_dir() else []
    newest = None
    if (PROJ / "const").is_dir():
        files = sorted((PROJ / "const").glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
        if files:
            newest = files[0]
    out["file"] = str(newest) if newest else None
    out["content"] = newest.read_text(encoding="utf-8", errors="replace")[:800] if newest else None
    return out


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
