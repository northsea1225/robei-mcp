from __future__ import annotations
import json, time
from pathlib import Path
BUILD = Path(r"C:\dev\robei-mcp\workspace\robei_samples\exp1_and\build")


def run() -> dict:
    import robei_ui
    main, _ = robei_ui.main_window()
    if main is None:
        return {"ok": False, "error": "Robei not running"}
    out = {"ok": True}
    out["open"] = {k: v for k, v in robei_ui.open_model("tb_and_gate.test").items() if k in ("ok", "active")}
    out["active"] = robei_ui.active_model(main)
    if out["active"] != "tb_and_gate":
        return {"ok": False, "error": f"tb not active: {out['active']}"}
    base = robei_ui.read_output(main)
    robei_ui.click_menu(main, "Build", "Compile")
    time.sleep(8.0)
    mid = robei_ui.read_output(main)
    out["compile"] = (mid[len(base):] if mid.startswith(base) else mid).strip()[:500]
    out["files_after_compile"] = sorted(p.name for p in BUILD.glob("*"))
    fl = BUILD / "filelist.vf"
    out["filelist"] = fl.read_text(encoding="utf-8", errors="replace")[:300] if fl.exists() else None
    base2 = mid
    robei_ui.click_menu(main, "Build", "Run")
    time.sleep(14.0)
    after = robei_ui.read_output(main)
    out["run"] = (after[len(base2):] if after.startswith(base2) else after).strip()[:700]
    out["files_after_run"] = sorted(p.name for p in BUILD.glob("*"))
    out["vcd"] = sorted(p.name for p in BUILD.glob("*.vcd"))
    return out


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
