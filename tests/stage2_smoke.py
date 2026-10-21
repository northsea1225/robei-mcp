from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"


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


def show(name, result):
    print(f"\n=== {name} ===")
    print(json.dumps(payload(result), ensure_ascii=False, indent=2))
    print(f"is_error={getattr(result, 'is_error', None)}")
    return payload(result)


async def main():
    params = StdioServerParameters(
        command=str(PYTHON),
        args=[str(ROOT / "server.py")],
        cwd=str(ROOT),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("TOOLS=" + json.dumps([t.name for t in tools.tools], ensure_ascii=False))

            show("runtime_status", await session.call_tool("runtime_status", {}))

            show(
                "read_robei_model",
                await session.call_tool(
                    "read_robei_model",
                    {"path": r"C:\ProgramData\Robei\mux.model"},
                ),
            )

            show(
                "lint_verilog",
                await session.call_tool(
                    "lint_verilog",
                    {
                        "files": ["counter4/counter4.v"],
                        "top": "counter4",
                        "timeout_s": 30,
                    },
                ),
            )

            sim = show(
                "simulate",
                await session.call_tool(
                    "simulate",
                    {
                        "files": [
                            "counter4/counter4.v",
                            "counter4/counter4_tb.v",
                        ],
                        "top": "counter4_tb",
                        "timeout_s": 30,
                    },
                ),
            )

            vcd = (sim.get("vcd_rel_paths") or [None])[0]
            if not vcd:
                print("NO_VCD_PRODUCED")
                return

            show(
                "read_waveform:list",
                await session.call_tool(
                    "read_waveform",
                    {"vcd_path": vcd},
                ),
            )
            show(
                "read_waveform:values",
                await session.call_tool(
                    "read_waveform",
                    {
                        "vcd_path": vcd,
                        "signals": [
                            "counter4_tb.clk",
                            "counter4_tb.rst_n",
                            "counter4_tb.dut.count",
                        ],
                        "time": 45,
                    },
                ),
            )


if __name__ == "__main__":
    asyncio.run(main())
