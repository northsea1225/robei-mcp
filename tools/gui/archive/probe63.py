from __future__ import annotations
import json
import time
from pathlib import Path

PROJ = Path(r"C:\dev\robei-mcp\workspace\robei_samples\hier_probe")
MODEL = PROJ / "model" / "top_two.model"


def shot(label):
    import robei_ui
    try:
        return robei_ui.__dict__.get("_shot")
    except Exception:
        return None


def run() -> dict:
    from pywinauto import mouse
    import robei_ui
    main, win = robei_ui.main_window()
    if main is None:
        return {"ok": False, "error": "Robei not running"}
    out: dict = {"ok": True, "size_before": MODEL.stat().st_size}

    # 1. ToolBox tab + Current category
    for ctrl in main.descendants(control_type="TabItem"):
        try:
            if ctrl.window_text() == "ToolBox":
                mouse.click(button="left", coords=robei_ui._center(robei_ui._rect(ctrl.element_info.rectangle)))
                time.sleep(1.0)
                break
        except Exception:
            continue
    for ctrl in main.descendants(control_type="Button"):
        try:
            if ctrl.window_text() == "Current":
                mouse.click(button="left", coords=robei_ui._center(robei_ui._rect(ctrl.element_info.rectangle)))
                time.sleep(1.0)
                break
        except Exception:
            continue

    # 2. drag the first toolbox entry onto the canvas
    src = (170, 205)
    dst = (1150, 620)
    try:
        mouse.move(coords=src)
        time.sleep(0.3)
        mouse.press(button="left", coords=src)
        time.sleep(0.4)
        for step in range(1, 11):
            x = src[0] + (dst[0] - src[0]) * step // 10
            y = src[1] + (dst[1] - src[1]) * step // 10
            mouse.move(coords=(x, y))
            time.sleep(0.08)
        mouse.release(button="left", coords=dst)
        out["dragged"] = True
    except Exception as exc:
        out["drag_error"] = f"{type(exc).__name__}: {exc}"

    time.sleep(2.0)
    out["tabs"] = robei_ui.model_tabs(main)
    out["output"] = robei_ui._tail(robei_ui.read_output(main), 8)

    from pywinauto import keyboard
    keyboard.send_keys("^s")
    time.sleep(2.0)
    text = MODEL.read_text(encoding="utf-8", errors="replace")
    out["size_after"] = MODEL.stat().st_size
    out["has_instance"] = "<Instance" in text
    out["children"] = [line.strip()[:120] for line in text.splitlines() if line.strip().startswith("<") and not line.startswith("<!DOCTYPE")]
    return out


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
