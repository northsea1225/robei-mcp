from __future__ import annotations
import json, time
from pathlib import Path

BUILD = Path(r"C:\dev\robei-mcp\workspace\robei_samples\exp1_and\build")


def run() -> dict:
    import robei_ui
    main, win = robei_ui.main_window()
    if main is None:
        return {"ok": False, "error": "Robei not running"}
    out: dict = {"ok": True, "active": robei_ui.active_model(main)}
    base = robei_ui.read_output(main)
    out["compile"] = robei_ui.click_menu(main, "Build", "Compile")
    time.sleep(6.0)
    mid = robei_ui.read_output(main)
    out["compile_delta"] = (mid[len(base):] if mid.startswith(base) else mid).strip()[:800]
    out["files_after_compile"] = sorted(p.name for p in BUILD.glob("*"))
    out["filelist"] = (BUILD / "filelist.vf").read_text(encoding="utf-8", errors="replace")[:400] if (BUILD / "filelist.vf").exists() else None

    base2 = mid
    out["run"] = robei_ui.click_menu(main, "Build", "Run")
    time.sleep(12.0)
    after = robei_ui.read_output(main)
    out["run_delta"] = (after[len(base2):] if after.startswith(base2) else after).strip()[:800]
    out["files_after_run"] = sorted(p.name for p in BUILD.glob("*"))
    return out


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
