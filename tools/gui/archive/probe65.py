from __future__ import annotations
import json
import time
from pathlib import Path

MODEL = Path(r"C:\dev\robei-mcp\workspace\robei_samples\two_level\model\top_two.model")


def items():
    import robei_ui
    return [{"name": i["name"], "rect": i["rect"]} for p in robei_ui.popups() for i in p["items"] if i["type"] == "MenuItem"]


def run() -> dict:
    from pywinauto import keyboard, mouse
    import robei_ui
    main, win = robei_ui.main_window()
    if main is None:
        return {"ok": False, "error": "Robei not running"}
    out: dict = {"ok": True, "size_before": MODEL.stat().st_size}
    found = None
    for spot in ((400, 250), (700, 950), (2200, 900)):
        mouse.right_click(coords=spot)
        time.sleep(1.4)
        menu = items()
        out[f"menu_{spot[0]}_{spot[1]}"] = menu
        hit = next((i for i in menu if "graph" in i["name"].lower()), None)
        if hit:
            found = hit
            break
        keyboard.send_keys("{ESC}")
        time.sleep(0.4)

    out["found"] = found["name"] if found else None
    if found:
        mouse.click(button="left", coords=robei_ui._center(found["rect"]))
        time.sleep(4.0)
        out["windows"] = [w["title"] for w in robei_ui.windows()]
        out["output"] = robei_ui._tail(robei_ui.read_output(main), 10)
        keyboard.send_keys("^s")
        time.sleep(2.0)
        text = MODEL.read_text(encoding="utf-8", errors="replace")
        out["size_after"] = MODEL.stat().st_size
        out["tags"] = sorted({line.strip().split(" ")[0] for line in text.splitlines() if line.strip().startswith("<")})
        out["head"] = text[:1200]
    return out


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
