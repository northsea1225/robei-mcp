from __future__ import annotations
import json
import time
from pathlib import Path

VCD = r"C:\dev\robei-mcp\workspace\exp3_write\build\tb_and_gate.vcd"


def run() -> dict:
    import robei_ui
    main, win = robei_ui.main_window()
    if main is None:
        return {"ok": False, "error": "Robei not running"}
    out: dict = {"ok": True, "vcd_exists": Path(VCD).is_file()}
    before = robei_ui.read_output(main)
    out["open_menu"] = robei_ui.click_menu(main, "File", "Open")
    out["dialog"] = robei_ui.select_file_in_dialog(VCD)
    time.sleep(4.0)
    after = robei_ui.read_output(main)
    out["delta"] = (after[len(before):] if after.startswith(before) else after).strip()[:800]
    out["windows"] = [{"title": w["title"], "class": w["class"]} for w in robei_ui.windows()]
    out["tabs"] = robei_ui.tab_groups(main)
    tree = None
    for ctrl in main.descendants(control_type="Tree"):
        try:
            if (ctrl.element_info.automation_id or "").endswith("WaveTree"):
                tree = ctrl
                break
        except Exception:
            continue
    if tree is not None:
        out["wave_tree"] = [it.window_text() for it in tree.descendants(control_type="TreeItem")[:60]]
    table = None
    for ctrl in main.descendants():
        try:
            if (ctrl.element_info.automation_id or "").endswith("SignalTable"):
                table = ctrl
                break
        except Exception:
            continue
    if table is not None:
        out["table_texts"] = [c.window_text() for c in table.children() if c.window_text()][:40]
    return out


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
