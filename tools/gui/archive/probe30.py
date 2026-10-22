"""Pure-keyboard creation of a Type=test object (no UIA attach on the dialog)."""
from __future__ import annotations

import json
import time
from pathlib import Path

OUT = Path(r"C:\dev\robei-mcp\workspace\gui_shots")
PROJ_DIR = Path(r"C:\dev\robei-mcp\workspace\robei_samples\exp1_and")


def run() -> dict:
    import robei_ui
    import win32clipboard
    from pywinauto import keyboard
    from PIL import ImageGrab

    main, win = robei_ui.main_window()
    if main is None:
        return {"ok": False, "error": "Robei not running"}
    report: dict = {"ok": True}

    if not any("New Project" in (w["title"] or "") for w in robei_ui.windows()):
        robei_ui.click_menu(main, "File", "New")
        time.sleep(2.5)

    def paste(text: str) -> None:
        win32clipboard.OpenClipboard()
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
        win32clipboard.CloseClipboard()
        keyboard.send_keys("^v")

    # name -> type combo -> location -> OK
    keyboard.send_keys("^a")
    keyboard.send_keys("tb_and_gate", with_spaces=True, pause=0.02)
    time.sleep(0.4)
    keyboard.send_keys("{TAB}")
    time.sleep(0.3)
    keyboard.send_keys("t")
    time.sleep(0.4)
    keyboard.send_keys("{TAB}")
    time.sleep(0.3)
    keyboard.send_keys("^a")
    paste(str(PROJ_DIR))
    time.sleep(0.6)

    shot1 = OUT / time.strftime("p30-filled-%H%M%S.png")
    ImageGrab.grab(all_screens=True).save(shot1)
    report["shot_filled"] = str(shot1)

    keyboard.send_keys("{ENTER}")
    time.sleep(4.0)
    report["windows_after_enter"] = [{"title": w["title"], "class": w["class"]} for w in robei_ui.windows()]
    if any("Location" in (w["title"] or "") for w in robei_ui.windows()):
        keyboard.send_keys("{ENTER}")
        time.sleep(1.0)
        report["note"] = "location complaint dismissed"

    report["tree"] = robei_ui.tree_items(main)
    report["tabs"] = robei_ui.tab_groups(main)
    report["output_tail"] = robei_ui._tail(robei_ui.read_output(main), 8)

    files = {}
    for sub in ("model", "test"):
        for p in sorted((PROJ_DIR / sub).glob("*.model")):
            files[f"{sub}/{p.name}"] = p.read_text(encoding="utf-8", errors="replace")[:1500]
    report["model_files"] = files
    proj = PROJ_DIR / "exp1_and.proj"
    report["proj"] = proj.read_text(encoding="utf-8", errors="replace")[:800]

    shot2 = OUT / time.strftime("p30-after-%H%M%S.png")
    ImageGrab.grab(all_screens=True).save(shot2)
    report["shot_after"] = str(shot2)
    return report


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
