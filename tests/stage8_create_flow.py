"""Stage 8 regression: project + model scaffolding, Robei compile, simulation.

Run:  .venv\\Scripts\\python.exe tests\\stage8_create_flow.py
"""
from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
SANDBOX = ROOT / "workspace" / "mcp_sandbox"
PROJ = SANDBOX / "mcp_sandbox.proj"

results: list[tuple[str, bool]] = []

TB = """`timescale 1ns/1ps
module tb_half_adder;
  reg a;
  reg b;
  wire s;
  wire c;
  half_adder dut (.a(a), .b(b), .s(s), .c(c));
  initial begin
    $dumpfile("half_adder.vcd");
    $dumpvars(0, tb_half_adder);
    a = 1'b0; b = 1'b0; #10;
    $display("t=%0t a=%b b=%b s=%b c=%b", $time, a, b, s, c);
    a = 1'b0; b = 1'b1; #10;
    $display("t=%0t a=%b b=%b s=%b c=%b", $time, a, b, s, c);
    a = 1'b1; b = 1'b0; #10;
    $display("t=%0t a=%b b=%b s=%b c=%b", $time, a, b, s, c);
    a = 1'b1; b = 1'b1; #10;
    $display("t=%0t a=%b b=%b s=%b c=%b", $time, a, b, s, c);
    $finish;
  end
endmodule
"""


def payload(result):
    if getattr(result, "structured_content", None) is not None:
        return result.structured_content
    for item in getattr(result, "content", []):
        text = getattr(item, "text", None)
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"text": text}
    return {"raw": str(result)}


def record(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


async def main() -> int:
    if SANDBOX.exists():
        shutil.rmtree(SANDBOX)

    params = StdioServerParameters(command=str(PYTHON), args=[str(ROOT / "server.py")], cwd=str(ROOT))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = [t.name for t in (await session.list_tools()).tools]
            print("TOOLS=" + str(len(names)))
            record(
                "tool_registration",
                {"create_robei_project", "create_robei_model", "robei_gui_status"} <= set(names),
                str(len(names)),
            )

            created = payload(await session.call_tool("create_robei_project", {"name": "mcp_sandbox", "models": []}))
            record("create_project", created.get("ok") is True, created.get("proj", ""))

            model = payload(await session.call_tool("create_robei_model", {
                "proj_path": str(PROJ),
                "name": "half_adder",
                "ports": [
                    {"name": "a", "direction": "input"},
                    {"name": "b", "direction": "input"},
                    {"name": "s", "direction": "output"},
                    {"name": "c", "direction": "output"},
                ],
                "code": "assign s = a ^ b;\nassign c = a & b;",
                "comment": "half adder built by robei-mcp",
            }))
            record("create_model", model.get("ok") is True, f"registered={model.get('registered_in_project')}")

            proj = payload(await session.call_tool("read_robei_project", {"proj_path": str(PROJ)}))
            model_names = [m.get("name") for m in (proj.get("models") or [])]
            record("project_lists_model", "half_adder" in model_names, str(model_names))

            tb_path = SANDBOX / "test" / "tb_half_adder.v"
            tb_path.write_text(TB, encoding="utf-8")

            launch = payload(await session.call_tool("robei_gui_launch", {"wait_s": 30}))
            record("launch", launch.get("ok") is True)
            # a freshly started IDE needs a moment before the File menu answers
            for _ in range(15):
                status = payload(await session.call_tool("robei_gui_status", {}))
                if status.get("running") and status.get("project"):
                    break
                await asyncio.sleep(1.0)
            opened = payload(await session.call_tool("robei_gui_open_project", {"proj_path": str(PROJ), "timeout_s": 120}))
            record("open_project", opened.get("ok") is True, str(opened.get("project")))
            gui_model = payload(await session.call_tool("robei_gui_open_model", {"name": "half_adder.model", "timeout_s": 120}))
            record("open_model", gui_model.get("ok") is True, f"active={gui_model.get('active')}")
            compiled = payload(await session.call_tool("robei_gui_compile", {"timeout_s": 120}))
            record("compile", compiled.get("ok") is True, str(compiled.get("generated"))[:100])
            payload(await session.call_tool("robei_gui_close", {}))

            sim = payload(await session.call_tool("simulate", {
                "files": ["mcp_sandbox/build/half_adder.v", "mcp_sandbox/test/tb_half_adder.v"],
                "top": "tb_half_adder",
                "timeout_s": 60,
            }))
            record("simulate", sim.get("ok") is True)
            print("DISPLAY=" + json.dumps(sim.get("display_output"), ensure_ascii=False))
            vcd = (sim.get("vcd_rel_paths") or [None])[0]
            if vcd:
                table = []
                for t in (5000, 15000, 25000, 35000):
                    wave = payload(await session.call_tool("read_waveform", {
                        "vcd_path": vcd,
                        "signals": ["tb_half_adder.a", "tb_half_adder.b", "tb_half_adder.s", "tb_half_adder.c"],
                        "time": t,
                    }))
                    vals = {v["name"].split(".")[-1]: v["value"] for v in wave.get("values", [])}
                    table.append((vals.get("a"), vals.get("b"), vals.get("s"), vals.get("c")))
                expected = [("0", "0", "0", "0"), ("0", "1", "1", "0"), ("1", "0", "1", "0"), ("1", "1", "0", "1")]
                record("half_adder_truth_table", table == expected, str(table))

    failed = [n for n, ok in results if not ok]
    print("\n--- summary ---")
    print(f"{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
