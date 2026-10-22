"""Portable core checks: no Robei, no GUI, no Windows.

Runs on any host that has iverilog/vvp (Ubuntu CI installs them with apt).
Everything here goes through ``robei_mcp.tools`` directly, so it also proves the
package imports on a non-Windows platform.

    python tests/portable_core.py
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from robei_mcp import tools  # noqa: E402

results: list[tuple[str, str, str]] = []
WS = ROOT / "workspace"


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, "PASS" if ok else "FAIL", detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}"[:200])


def skip(name: str, why: str) -> None:
    results.append((name, "SKIP", why))
    print(f"SKIP  {name}  {why}")


def main() -> int:
    status = tools.runtime_status()
    print("tools:", json.dumps(
        {k: {"exists": v.get("exists"), "version": v.get("version")} for k, v in status["tools"].items()},
        ensure_ascii=False,
    ))
    check("runtime_status ok", status["ok"] is True)
    if not status["tools"]["iverilog"]["exists"] or not status["tools"]["vvp"]["exists"]:
        print("iverilog/vvp missing: install them (apt install iverilog / brew install icarus-verilog)")
        print(f"\n--- summary ---\n0 passed")
        return 1

    # --- simulate the plain Verilog fixture --------------------------------
    sim = tools.simulate(
        files=["counter4/counter4.v", "counter4/counter4_tb.v"], top="counter4_tb", timeout_s=60
    )
    display = sim.get("display_output", "")
    check("simulate ok", sim.get("ok") is True, sim.get("stage", ""))
    check("simulate prints MID line", "MID t=50 count=" in display, display.splitlines()[:2] and display.splitlines()[0])
    check("simulate produced VCD", bool(sim.get("vcd_paths")))

    vcd = (sim.get("vcd_paths") or [None])[0]
    if vcd:
        listing = tools.read_waveform(vcd)
        names = [s["name"] for s in listing["signals"]]
        check("read_waveform lists signals", listing["signal_count"] >= 3, str(names[:4]))
        values = tools.read_waveform(vcd, signals=["counter4_tb.count"], time=45)
        got = values["values"][0]
        check("read_waveform time lookup", got["found"] and got["value_int"] >= 1, json.dumps(got))
        events = tools.find_events(vcd, "counter4_tb.count", "== 4'd3", limit=3)
        check("find_events", events["event_count"] >= 1, json.dumps(events["events"][:2]))

    # --- generate_testbench round trip -------------------------------------
    tb_name = f"testbench/portable_{uuid.uuid4().hex[:6]}_tb.v"
    tb = tools.generate_testbench("counter4/counter4.v", top="counter4", out_path=tb_name)
    check("generate_testbench", tb["ok"] is True and Path(tb["path"]).is_file(), tb.get("relative_path", ""))
    hint = tb["simulate_hint"]
    gen = tools.simulate(files=hint["files"], top=hint["top"], timeout_s=60)
    check("generated tb simulates", gen.get("stage") == "run",
          str(gen.get("stage")) + " " + gen.get("display_output", "")[:60])

    # --- Robei XML (read-only) ---------------------------------------------
    model = tools.read_robei_model(str(WS / "robei_samples" / "exp1_and" / "model" / "and_gate.model"))
    check("read_robei_model", model["type"] == "model" and len(model["ports"]) == 3, model["name"])
    proj_path = WS / "robei_samples" / "exp1_and" / "exp1_and.proj"
    project = tools.read_robei_project(str(proj_path))
    check("read_robei_project", project["unchanged"] is True and len(project["models"]) == 1,
          f"models={len(project['models'])} tests={len(project['tests'])} constraints={len(project['constraints'])}")

    # --- headless experiment ----------------------------------------------
    headless = tools.simulate_project(
        str(WS / "counter4_native" / "counter4_native.proj"), "tb_counter4.test", timeout_s=60
    )
    lines = [line for line in headless.get("display_output", "").splitlines() if line.startswith("t=")]
    check("simulate_project ok", headless.get("ok") is True, headless.get("stage", ""))
    check("simulate_project 26 counts", len(lines) == 26 and lines[-1] == "t=255 count=1000", str(len(lines)))

    # --- lint (yosys is optional) -----------------------------------------
    if status["tools"]["yosys"]["exists"]:
        lint = tools.lint_verilog(files=["counter4/counter4.v"], top="counter4", timeout_s=60)
        check("lint_verilog clean", lint["ok"] is True, f"warnings={lint['iverilog']['warning_count']}")
        bad = tools.lint_verilog(files=["cases/syntax_error.v"], timeout_s=60)
        check("lint_verilog reports syntax errors", bad["ok"] is False and bad["iverilog"]["ok"] is False)
    else:
        skip("lint_verilog", "yosys not installed")

    passed = sum(1 for _, state, _ in results if state == "PASS")
    failed = sum(1 for _, state, _ in results if state == "FAIL")
    skipped = sum(1 for _, state, _ in results if state == "SKIP")
    print(f"\n--- summary ---\n{passed} passed, {failed} failed, {skipped} skipped")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
