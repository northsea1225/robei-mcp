"""Capture the exact iverilog command line Robei uses during Build>Run."""
from __future__ import annotations

import json
import subprocess
import threading
import time

PS = (
    "Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'iverilog|ivl|vvp' } "
    "| Select-Object Name,CommandLine | Format-List"
)


def run() -> dict:
    import robei_ui

    main, win = robei_ui.main_window()
    if main is None:
        return {"ok": False, "error": "Robei not running"}

    seen: list[str] = []
    stop = threading.Event()

    def poll() -> None:
        while not stop.is_set():
            try:
                out = subprocess.run(
                    ["powershell", "-NoProfile", "-Command", PS],
                    capture_output=True, text=True, timeout=10,
                ).stdout
                for line in out.splitlines():
                    line = line.strip()
                    if line.startswith("CommandLine") and "iverilog" in line.lower() or "ivl" in line.lower():
                        if line not in seen:
                            seen.append(line[:800])
            except Exception:
                pass
            time.sleep(0.12)

    t = threading.Thread(target=poll, daemon=True)
    t.start()

    robei_ui.refresh()
    opened = robei_ui.open_model("tb_and_gate.test")
    base = robei_ui.read_output(main)
    robei_ui.click_menu(main, "Build", "Run")
    time.sleep(18.0)
    stop.set()
    t.join(timeout=3)
    after = robei_ui.read_output(main)
    return {
        "ok": True,
        "open": {k: v for k, v in opened.items() if k in ("ok", "active")},
        "run_delta": (after[len(base):] if after.startswith(base) else after).strip()[:800],
        "commands": seen[:10],
    }


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
