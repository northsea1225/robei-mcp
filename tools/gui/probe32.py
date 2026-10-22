"""Write stimulus into the .test model, then Run and Wave."""
from __future__ import annotations

import json
import time

CODE = (
    "reg a; reg b; wire y;\n"
    "and_gate dut (.a(a), .b(b), .y(y));\n"
    "initial begin\n"
    "  a = 0; b = 0; #10;\n"
    "  a = 0; b = 1; #10;\n"
    "  a = 1; b = 0; #10;\n"
    "  a = 1; b = 1; #10;\n"
    "  $finish;\n"
    "end\n"
)


def run() -> dict:
    import robei_ui

    main, win = robei_ui.main_window()
    if main is None:
        return {"ok": False, "error": "Robei not running"}
    out: dict = {"ok": True}

    robei_ui.refresh()
    out["open"] = {k: v for k, v in robei_ui.open_model("tb_and_gate.test").items() if k in ("ok", "models", "active", "error")}
    before = robei_ui.read_output(main)
    out["code_write"] = {k: v for k, v in robei_ui.code_write(CODE).items() if k in ("ok", "written", "saved", "model", "error")}
    out["code_now"] = (robei_ui.code_read().get("text") or "")[:200]

    base = robei_ui.read_output(main)
    out["run_click"] = robei_ui.click_menu(main, "Build", "Run")
    time.sleep(14.0)
    after = robei_ui.read_output(main)
    out["run_delta"] = (after[len(base):] if after.startswith(base) else after).strip()[:1500]

    base2 = after
    out["wave_click"] = robei_ui.click_menu(main, "Build", "Wave")
    time.sleep(8.0)
    after2 = robei_ui.read_output(main)
    out["wave_delta"] = (after2[len(base2):] if after2.startswith(base2) else after2).strip()[:800]
    out["tabs"] = robei_ui.tab_groups(main)
    out["windows"] = [{"title": w["title"], "class": w["class"]} for w in robei_ui.windows()]
    return out


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
