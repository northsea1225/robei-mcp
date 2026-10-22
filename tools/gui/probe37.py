"""Compile the DUT, drop its .v into <proj>/verilog (iverilog -y path), then Run the test."""
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

    robei_ui.refresh()
    out["open_dut"] = {k: v for k, v in robei_ui.open_model("and_gate.model").items() if k in ("ok", "active")}
    base = robei_ui.read_output(main)
    robei_ui.click_menu(main, "Build", "Compile")
    time.sleep(6.0)
    mid = robei_ui.read_output(main)
    out["dut_compile"] = (mid[len(base):] if mid.startswith(base) else mid).strip()[:300]
    src = BUILD / "and_gate.v"
    dst = PROJ / "verilog" / "and_gate.v"
    if src.exists():
        shutil.copy2(src, dst)
    out["copied"] = dst.exists()
    out["dut_v"] = src.read_text(encoding="utf-8", errors="replace")[:300] if src.exists() else None

    out["open_tb"] = {k: v for k, v in robei_ui.open_model("tb_and_gate.test").items() if k in ("ok", "active")}
    base2 = robei_ui.read_output(main)
    robei_ui.click_menu(main, "Build", "Run")
    time.sleep(16.0)
    after = robei_ui.read_output(main)
    out["run_delta"] = (after[len(base2):] if after.startswith(base2) else after).strip()[:900]
    out["files"] = sorted(p.name for p in BUILD.glob("*"))
    out["vcd"] = sorted(p.name for p in BUILD.glob("*.vcd"))

    base3 = after
    robei_ui.click_menu(main, "Build", "Wave")
    time.sleep(8.0)
    after2 = robei_ui.read_output(main)
    out["wave_delta"] = (after2[len(base3):] if after2.startswith(base3) else after2).strip()[:400]
    out["tabs"] = robei_ui.tab_groups(main)
    return out


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
