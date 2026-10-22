"""Does a Type='test' model let Build>Run produce a waveform, and does Wave show it?"""
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
        path = OUT / time.strftime(f"p22-{label}-%H%M%S.png")
        ImageGrab.grab(all_screens=True).save(path)
        return str(path)

    report["refresh"] = robei_ui.refresh()
    report["tree"] = robei_ui.tree_items(dlg)
    report["open_model"] = robei_ui.open_model("tb_and_gate.model")

    before = robei_ui.read_output(dlg)
    report["run_click"] = robei_ui.click_menu(dlg, "Build", "Run")
    time.sleep(9.0)
    after = robei_ui.read_output(dlg)
    report["run_delta"] = (after[len(before):] if after.startswith(before) else after).strip()[:900]
    report["run_shot"] = snap("run")
    report["windows_after_run"] = [{"title": w["title"], "class": w["class"]} for w in robei_ui.windows()]
    report["tabs_after_run"] = robei_ui.tab_groups(dlg)

    before2 = robei_ui.read_output(dlg)
    report["wave_click"] = robei_ui.click_menu(dlg, "Build", "Wave")
    time.sleep(7.0)
    after2 = robei_ui.read_output(dlg)
    report["wave_delta"] = (after2[len(before2):] if after2.startswith(before2) else after2).strip()[:900]
    report["wave_shot"] = snap("wave")
    report["tabs_after_wave"] = robei_ui.tab_groups(dlg)
    report["windows_after_wave"] = [{"title": w["title"], "class": w["class"]} for w in robei_ui.windows()]
    return report


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
