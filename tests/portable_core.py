"""Portable core checks: no Robei, no GUI, no Windows.

Runs on any host with iverilog/vvp (the Ubuntu CI installs them with apt).
Everything goes through ``robei_mcp.tools`` directly, so it also proves the
package imports on a non-Windows platform.

    python tests/portable_core.py            # every group
    python tests/portable_core.py lint       # one group (used by CI for precise failures)
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

WS = ROOT / "workspace"
results: list[tuple[str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, "PASS" if ok else "FAIL"))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {str(detail)[:160]}")


def skip(name: str, why: str) -> None:
    results.append((name, "SKIP"))
    print(f"SKIP  {name}  {why}")


def group_simulate() -> None:
    status = tools.runtime_status()
    print("tools:", json.dumps(
        {k: {"exists": v.get("exists"), "version": v.get("version")} for k, v in status["tools"].items()},
        ensure_ascii=False,
    ))
    # runtime_status is only "ok" when Robei itself is present; CI has no Robei.
    check(
        "runtime_status sees iverilog/vvp",
        bool(status["tools"]["iverilog"]["exists"] and status["tools"]["vvp"]["exists"]),
    )
    sim = tools.simulate(files=["counter4/counter4.v", "counter4/counter4_tb.v"], top="counter4_tb", timeout_s=60)
    check("simulate ok", sim.get("ok") is True, str(sim.get("stage")))
    display = sim.get("display_output", "")
    check("simulate prints MID line", "MID t=50 count=" in display, display.splitlines()[-2] if display else "")
    check("simulate produced VCD", bool(sim.get("vcd_paths")))

    vcd = (sim.get("vcd_paths") or [None])[0]
    if not vcd:
        return
    listing = tools.read_waveform(vcd)
    check("read_waveform lists signals", listing["signal_count"] >= 3,
          str([s["name"] for s in listing["signals"]][:4]))
    values = tools.read_waveform(vcd, signals=["counter4_tb.count"], time=45)
    got = values["values"][0]
    check("read_waveform time lookup", bool(got["found"]) and int(got["value_int"] or 0) >= 1, json.dumps(got))
    events = tools.find_events(vcd, "counter4_tb.count", "== 4'd3", limit=3)
    check("find_events", events["event_count"] >= 1, json.dumps(events["events"][:2]))


def group_testbench() -> None:
    tb_name = f"testbench/portable_{uuid.uuid4().hex[:6]}_tb.v"
    tb = tools.generate_testbench("counter4/counter4.v", top="counter4", out_path=tb_name)
    check("generate_testbench", bool(tb.get("ok")) and Path(tb["path"]).is_file(), str(tb.get("relative_path")))
    hint = tb["simulate_hint"]
    gen = tools.simulate(files=hint["files"], top=hint["top"], timeout_s=60)
    check("generated tb simulates", gen.get("stage") == "run", str(gen.get("stage")))


def group_robei_xml() -> None:
    model = tools.read_robei_model(str(WS / "robei_samples" / "exp1_and" / "model" / "and_gate.model"))
    check("read_robei_model", model["type"] == "model" and len(model["ports"]) == 3, str(model["name"]))
    project = tools.read_robei_project(str(WS / "robei_samples" / "exp1_and" / "exp1_and.proj"))
    check("read_robei_project unchanged", project["unchanged"] is True)
    check("read_robei_project models", len(project["models"]) == 1,
          f"models={len(project['models'])} tests={len(project['tests'])} const={len(project['constraints'])}")


def group_headless() -> None:
    headless = tools.simulate_project(
        str(WS / "counter4_native" / "counter4_native.proj"), "tb_counter4.test", timeout_s=60
    )
    lines = [line for line in headless.get("display_output", "").splitlines() if line.startswith("t=")]
    check("simulate_project ok", headless.get("ok") is True, str(headless.get("stage")))
    check("simulate_project 26 counts", len(lines) == 26 and lines[-1] == "t=255 count=1000", str(len(lines)))


def group_lint() -> None:
    status = tools.runtime_status()
    if not status["tools"]["yosys"]["exists"]:
        skip("lint_verilog", "yosys not installed")
        return
    lint = tools.lint_verilog(files=["counter4/counter4.v"], top="counter4", timeout_s=60)
    check("lint_verilog clean", lint["ok"] is True, f"iverilog warnings={lint['iverilog']['warning_count']}")
    bad = tools.lint_verilog(files=["cases/syntax_error.v"], timeout_s=60)
    check("lint_verilog reports syntax errors", bad["ok"] is False and bad["iverilog"]["ok"] is False)


GROUPS = {
    "simulate": group_simulate,
    "testbench": group_testbench,
    "robei-xml": group_robei_xml,
    "headless": group_headless,
    "lint": group_lint,
}


def main(argv: list[str]) -> int:
    wanted = [arg for arg in argv if arg in GROUPS] or list(GROUPS)
    unknown = [arg for arg in argv if arg not in GROUPS and not arg.startswith("-")]
    if unknown:
        print(f"unknown group(s): {unknown}; known: {list(GROUPS)}")
        return 2
    for name in wanted:
        print(f"\n=== {name} ===")
        try:
            GROUPS[name]()
        except Exception as exc:
            check(f"{name} crashed", False, f"{type(exc).__name__}: {exc}")
    passed = sum(1 for _, state in results if state == "PASS")
    failed = sum(1 for _, state in results if state == "FAIL")
    skipped = sum(1 for _, state in results if state == "SKIP")
    print(f"\n--- summary ---\n{passed} passed, {failed} failed, {skipped} skipped")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
