from __future__ import annotations
import json, time
from pathlib import Path

def run() -> dict:
    import robei_ui
    main, win = robei_ui.main_window()
    if main is None:
        return {"ok": False, "error": "Robei not running"}
    out = {"ok": True}
    out["refresh"] = {k: v for k, v in robei_ui.refresh().items() if k in ("ok", "tree", "models")}
    out["open"] = {k: v for k, v in robei_ui.open_model("tb_and_gate.test").items() if k in ("ok", "models", "active", "error")}
    before = robei_ui.read_output(main)
    out["run_click"] = robei_ui.click_menu(main, "Build", "Run")
    time.sleep(12.0)
    after = robei_ui.read_output(main)
    out["run_delta"] = (after[len(before):] if after.startswith(before) else after).strip()[:1200]
    before2 = after
    out["wave_click"] = robei_ui.click_menu(main, "Build", "Wave")
    time.sleep(6.0)
    after2 = robei_ui.read_output(main)
    out["wave_delta"] = (after2[len(before2):] if after2.startswith(before2) else after2).strip()[:600]
    out["tabs"] = robei_ui.tab_groups(main)
    return out

if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
