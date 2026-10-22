from __future__ import annotations
import json, time
from pathlib import Path
BUILD = Path(r"C:\dev\robei-mcp\workspace\robei_samples\exp1_and\build")


def run() -> dict:
    import robei_ui
    main, _ = robei_ui.main_window()
    if main is None:
        return {"ok": False, "error": "Robei not running"}
    out = {"ok": True, "active": robei_ui.active_model(main)}
    out["open"] = {k: v for k, v in robei_ui.open_model("tb_and_gate.test").items() if k in ("ok", "active")}
    for attempt in (1, 2):
        base = robei_ui.read_output(main)
        robei_ui.click_menu(main, "Build", "Run")
        time.sleep(12.0)
        after = robei_ui.read_output(main)
        out[f"run{attempt}"] = (after[len(base):] if after.startswith(base) else after).strip()[:700]
        out[f"files{attempt}"] = sorted(p.name for p in BUILD.glob("*"))
        fl = BUILD / "filelist.vf"
        out[f"filelist{attempt}"] = fl.read_text(encoding="utf-8", errors="replace")[:300] if fl.exists() else None
    return out


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
