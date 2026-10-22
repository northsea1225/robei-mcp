"""Stage 11: drive the whole Robei loop through the MCP stdio protocol.

Nothing here imports the package: every step is a real `call_tool` request, so
this is the evidence that the new tools work for an MCP client (Codex/Claude).
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(r"C:\dev\robei-mcp")
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
PROJ = ROOT / "workspace" / "counter4_native" / "counter4_native.proj"
TEST = "tb_counter4.test"


def payload(result) -> dict:
    if result.content and hasattr(result.content[0], "text"):
        return json.loads(result.content[0].text)
    return {}


async def main() -> int:
    ok = 0
    failed = 0

    def check(name: str, condition: bool, data=None) -> None:
        nonlocal ok, failed
        if condition:
            ok += 1
            print(f"PASS  {name}")
        else:
            failed += 1
            print(f"FAIL  {name}")
            if data is not None:
                print(json.dumps(data, ensure_ascii=False)[:800])

    params = StdioServerParameters(command=str(PYTHON), args=[str(ROOT / "server.py")], cwd=str(ROOT))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            print("TOOL_COUNT=", len(names))
            for want in ("sync_robei_verilog", "create_robei_test", "robei_gui_experiment", "robei_gui_wave_load"):
                check(f"registered {want}", want in names)

            data = payload(await session.call_tool("sync_robei_verilog", {"proj_path": str(PROJ)}))
            check("sync_robei_verilog", data.get("ok") is True, data)
            print("   wrote:", [w["relative_path"] for w in data.get("written", [])])

            data = payload(await session.call_tool("read_robei_project", {"proj_path": str(PROJ)}))
            check("read_robei_project unchanged", data.get("unchanged") is True, data)
            check("read_robei_project sees .test", len(data.get("tests", [])) >= 1, data)
            print("   tests:", [(t["name"], t["type"], t["timescale"]) for t in data.get("tests", [])])

            data = payload(await session.call_tool("create_robei_test", {
                "proj_path": str(PROJ),
                "name": "tb_counter4_mcp",
                "code": "reg clk; reg rst_n; wire [3:0] count;\n"
                        "counter4 dut (.clk(clk), .rst_n(rst_n), .count(count));\n"
                        "initial begin clk = 0; rst_n = 0; #12 rst_n = 1; #60 $finish; end\n"
                        "always #5 clk = ~clk;\n"
                        "always @(posedge clk) $display(\"t=%0t count=%b\", $time, count);",
                "overwrite": True,
            }))
            check("create_robei_test", data.get("ok") is True, data)
            check("create_robei_test registers", data.get("registered_in_project") is True, data)

            data = payload(await session.call_tool("read_robei_model", {"path": str(ROOT / "workspace" / "counter4_native" / "model" / "counter4.model")}))
            check("read_robei_model", data.get("ok") is True and data.get("type") == "model", data)

            data = payload(await session.call_tool("robei_gui_experiment", {
                "proj_path": str(PROJ), "test_name": "tb_counter4_mcp.test", "restart": True, "timeout_s": 240,
            }))
            check("robei_gui_experiment", data.get("ok") is True, data)
            run_delta = (data.get("steps", {}).get("run", {}) or {}).get("delta", "")
            print("   run output:", run_delta.replace("\n", " | ")[:300])
            vcds = data.get("vcd") or []
            check("experiment produced VCD", bool(vcds), data)

            if vcds:
                data = payload(await session.call_tool("read_waveform", {"vcd_path": vcds[0]}))
                check("read_waveform on Robei VCD", data.get("ok") is True and data.get("signal_count", 0) >= 4, data)
                data = payload(await session.call_tool("find_events", {
                    "vcd_path": vcds[0], "signal": "tb_counter4_mcp.count", "condition": "== 4'b0011",
                }))
                check("find_events on Robei VCD", data.get("event_count", 0) >= 1, data)
                print("   events:", json.dumps(data.get("events"), ensure_ascii=False))

    print(f"\n--- summary ---\n{ok}/{ok + failed} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
