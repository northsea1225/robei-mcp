"""Open File>New and dump the dialog so we can learn how a 'test' model is created."""
from __future__ import annotations

import json
import time
from pathlib import Path

OUT = Path(r"C:\dev\robei-mcp\workspace\gui_shots")


def run() -> dict:
    import robei_ui
    from PIL import ImageGrab

    dlg, win = robei_ui.main_window()
    if dlg is None:
        return {"ok": False, "error": "Robei not running"}
    report: dict = {"ok": True}

    def snap(label: str) -> str:
        path = OUT / time.strftime(f"p24-{label}-%H%M%S.png")
        ImageGrab.grab(all_screens=True).save(path)
        return str(path)

    existing = [w for w in robei_ui.windows() if w["hwnd"] != win["hwnd"] and w["title"]]
    if not any("New Project" in w["title"] for w in existing):
        report["new_click"] = robei_ui.click_menu(dlg, "File", "New")
        time.sleep(2.0)
    else:
        report["new_click"] = {"reused": [w["title"] for w in existing]}
    report["shot"] = snap("new")
    report["windows"] = [{"title": w["title"], "class": w["class"], "hwnd": w["hwnd"]} for w in robei_ui.windows()]

    dialogs = [w for w in robei_ui.windows() if w["hwnd"] != win["hwnd"] and w["title"]]
    report["dialogs"] = dialogs
    controls = []
    for d in dialogs:
        try:
            w = robei_ui.attach(d["hwnd"])
            for c in w.descendants()[:60]:
                i = c.element_info
                controls.append(
                    {
                        "dialog": d["class"],
                        "type": i.control_type,
                        "name": (i.name or "")[:40],
                        "class": i.class_name,
                        "auto_id": (i.automation_id or "")[:60],
                        "rect": robei_ui._rect(i.rectangle),
                    }
                )
        except Exception as exc:
            controls.append({"dialog": d["class"], "error": f"{type(exc).__name__}: {exc}"})
    report["controls"] = controls
    return report


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
