"""Stage 16: canvas-level models — parse, generate, compile, simulate.

The schema (nested <Module> = placed instance, <Wire From/To> = net) was taken
from a published Robei project on GitHub and verified here end to end:
  1. generate a two-instance model with create_robei_canvas_model
  2. Robei compiles it to Verilog instantiation (GUI)
  3. headless simulation proves the wiring is electrically correct (y = a&b&c)
"""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(r"C:\dev\robei-mcp")
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
WS = ROOT / "workspace"

results: list[tuple[str, bool]] = []

TB = """reg a; reg b; reg c; wire y;
top_canvas dut (.a(a), .b(b), .c(c), .y(y));
initial begin
  a = 0; b = 0; c = 0; #10;
  a = 0; b = 0; c = 1; #10;
  a = 0; b = 1; c = 0; #10;
  a = 0; b = 1; c = 1; #10;
  a = 1; b = 0; c = 0; #10;
  a = 1; b = 0; c = 1; #10;
  a = 1; b = 1; c = 0; #10;
  a = 1; b = 1; c = 1; #10;
  $finish;
end
always @(a or b or c) $strobe("t=%0t a=%b b=%b c=%b y=%b", $time, a, b, c, y);"""


def payload(result) -> dict:
    if result.content and hasattr(result.content[0], "text"):
        try:
            return json.loads(result.content[0].text)
        except Exception:
            return {"raw": result.content[0].text[:400]}
    return {}


def check(name: str, ok: bool, data=None) -> None:
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}")
    if not ok and data is not None:
        print("      " + json.dumps(data, ensure_ascii=False)[:700])


async def main() -> int:
    params = StdioServerParameters(command=str(PYTHON), args=[str(ROOT / "server.py")], cwd=str(ROOT))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = [t.name for t in (await session.list_tools()).tools]
            print("TOOL_COUNT=", len(names))
            check("registered create_robei_canvas_model", "create_robei_canvas_model" in names)

            root = WS / "canvas_stage16"
            if root.exists():
                shutil.rmtree(root)
            proj = payload(await session.call_tool("create_robei_project", {"name": "canvas_stage16", "models": []}))["proj"]

            leaf = payload(await session.call_tool("create_robei_model", {
                "proj_path": proj, "name": "and2",
                "ports": [{"name": "a", "direction": "input"}, {"name": "b", "direction": "input"},
                          {"name": "y", "direction": "output"}],
                "code": "  assign y = a & b;",
            }))
            check("leaf model created", leaf.get("ok") is True, leaf)

            canvas = payload(await session.call_tool("create_robei_canvas_model", {
                "proj_path": proj, "name": "top_canvas",
                "ports": [{"name": "a", "direction": "input"}, {"name": "b", "direction": "input"},
                          {"name": "c", "direction": "input"}, {"name": "y", "direction": "output"}],
                "instances": [{"name": "u1", "model": "and2"}, {"name": "u2", "model": "and2", "x": 520}],
                "wires": [{"from": "a", "to": "u1.a"}, {"from": "b", "to": "u1.b"},
                          {"from": "u1.y", "to": "u2.a", "net": "t1"}, {"from": "c", "to": "u2.b"},
                          {"from": "u2.y", "to": "y"}],
            }))
            check("canvas model created", canvas.get("ok") is True, canvas)
            check("two instances parsed", len(canvas.get("instances", [])) == 2, canvas.get("instances"))
            nets = (canvas.get("netlist") or {}).get("nets") or {}
            t1 = nets.get("t1") or []
            check("t1 connects u1.y -> u2.a",
                  {e["instance"] for e in t1} == {"u1", "u2"} and {e["port"] for e in t1} == {"y", "a"}, t1)
            check("no dangling instance ports", not (canvas.get("netlist") or {}).get("dangling_instance_ports"),
                  canvas.get("netlist"))

            # --- Robei compiles the generated canvas model -------------------
            await session.call_tool("robei_gui_launch", {"wait_s": 40})
            opened = payload(await session.call_tool("robei_gui_open_project", {"proj_path": proj}))
            check("project opens in Robei", opened.get("ok") is True, opened)
            compiled = payload(await session.call_tool("robei_gui_compile_all", {}))
            check("Robei compiles canvas model", compiled.get("ok") is True and compiled.get("compiled") == 2, compiled)
            generated = root / "build" / "top_canvas.v"
            text = generated.read_text(encoding="utf-8", errors="replace") if generated.is_file() else ""
            check("Robei emitted instantiations", "and2 u1(" in text and ".y(t1)" in text, text[:300])

            # --- electrical proof: y == a & b & c ---------------------------
            await session.call_tool("robei_gui_close", {})
            test = payload(await session.call_tool("create_robei_test", {
                "proj_path": proj, "name": "tb_top", "code": TB,
            }))
            check("testbench created", test.get("ok") is True, test)
            sim = payload(await session.call_tool("simulate_project", {
                "proj_path": proj, "test_name": "tb_top.test",
            }))
            lines = [ln for ln in (sim.get("display_output") or "").splitlines() if ln.startswith("t=")]
            expected = []
            for t in range(0, 80, 10):
                idx = t // 10
                a, b, c = (idx >> 2) & 1, (idx >> 1) & 1, idx & 1
                expected.append(f"a={a} b={b} c={c} y={a & b & c}")
            got = [ln.split("t=", 1)[1].split(" ", 1)[1] for ln in lines]
            check("headless simulation of canvas model", sim.get("ok") is True, sim)
            check("truth table y = a&b&c", got == expected, {"got": got, "expected": expected})

    passed = sum(1 for _, ok in results if ok)
    print(f"\n--- summary ---\n{passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
