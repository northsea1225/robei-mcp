"""What do Build>Run and Build>Wave actually do?"""
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
    report: dict = {"ok": True, "steps": []}

    def snap(label: str) -> str:
        path = OUT / time.strftime(f"p21-{label}-%H%M%S.png")
        ImageGrab.grab(all_screens=True).save(path)
        return str(path)

    for item in ("Run", "Wave"):
        before = robei_ui.read_output(dlg)
        click = robei_ui.click_menu(dlg, "Build", item)
        time.sleep(6.0)
        after = robei_ui.read_output(dlg)
        delta = after[len(before):] if after.startswith(before) else after
        report["steps"].append(
            {
                "item": item,
                "click": click,
                "delta": delta.strip()[:800],
                "windows": [{"title": w["title"], "class": w["class"]} for w in robei_ui.windows()],
                "tabs": robei_ui.tab_groups(dlg),
                "shot": snap(item.lower()),
            }
        )
    return report


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
