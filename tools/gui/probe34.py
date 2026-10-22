"""Compile the .test model, inject the DUT into filelist.vf, then Run + Wave."""
from __future__ import annotations

import json
import time
from pathlib import Path

BUILD = Path(r"C:\dev\robei-mcp\workspace\robei_samples\exp1_and\build")
DUT = "C:/dev/robei-mcp/workspace/robei_samples/exp1_and/build/and_gate.v"


def run() -> dict:
    import robei_ui

    main, win = robei_ui.main_window()
    if main is None:
        return {"ok": False, "error": "Robei not running"}
    out: dict = {"ok": True}
    robei_ui.refresh()
    out["open"] = {k: v for k, v in robei_ui.open_model("tb_and_gate.test").items() if k in ("ok", "active", "error")}
    out["active"] = robei_ui.active_model(main)

    base = robei_ui.read_output(main)
    robei_ui.click_menu(main, "Build", "Compile")
    time.sleep(6.0)
    mid = robei_ui.read_output(main)
    out["compile_delta"] = (mid[len(base):] if mid.startswith(base) else mid).strip()[:600]

    fl = BUILD / "filelist.vf"
    before = fl.read_text(encoding="utf-8", errors="replace") if fl.exists() else ""
    if DUT not in before:
        fl.write_text(before.rstrip() + "\n" + DUT + "\n", encoding="utf-8")
    out["filelist"] = fl.read_text(encoding="utf-8", errors="replace")[:300]

    base2 = robei_ui.read_output(main)
    robei_ui.click_menu(main, "Build", "Run")
    time.sleep(14.0)
    after = robei_ui.read_output(main)
    out["run_delta"] = (after[len(base2):] if after.startswith(base2) else after).strip()[:900]
    out["files_after_run"] = sorted(p.name for p in BUILD.glob("*"))

    base3 = after
    robei_ui.click_menu(main, "Build", "Wave")
    time.sleep(7.0)
    after2 = robei_ui.read_output(main)
    out["wave_delta"] = (after2[len(base3):] if after2.startswith(base3) else after2).strip()[:500]
    out["tabs"] = robei_ui.tab_groups(main)
    out["windows"] = [{"title": w["title"], "class": w["class"]} for w in robei_ui.windows()]
    return out


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
