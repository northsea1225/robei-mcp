"""Hypothesis: Robei resolves submodules from <project>/verilog. Copy the DUT .v there, then Run."""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

PROJ = Path(r"C:\dev\robei-mcp\workspace\robei_samples\exp1_and")
BUILD = PROJ / "build"


def run() -> dict:
    import robei_ui

    main, win = robei_ui.main_window()
    if main is None:
        return {"ok": False, "error": "Robei not running"}
    out: dict = {"ok": True}

    src = BUILD / "and_gate.v"
    dst = PROJ / "verilog" / "and_gate.v"
    if src.exists():
        shutil.copy2(src, dst)
    out["copied"] = str(dst) if dst.exists() else None

    robei_ui.refresh()
    out["open"] = {k: v for k, v in robei_ui.open_model("tb_and_gate.test").items() if k in ("ok", "active", "error")}

    base = robei_ui.read_output(main)
    robei_ui.click_menu(main, "Build", "Run")
    time.sleep(15.0)
    after = robei_ui.read_output(main)
    out["run_delta"] = (after[len(base):] if after.startswith(base) else after).strip()[:900]
    out["files"] = sorted(p.name for p in BUILD.glob("*"))
    vcds = sorted(p.name for p in BUILD.glob("*.vcd"))
    out["vcd"] = vcds

    base2 = after
    robei_ui.click_menu(main, "Build", "Wave")
    time.sleep(7.0)
    after2 = robei_ui.read_output(main)
    out["wave_delta"] = (after2[len(base2):] if after2.startswith(base2) else after2).strip()[:400]
    out["tabs"] = robei_ui.tab_groups(main)
    return out


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
