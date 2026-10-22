"""Stage 14: headless experiment — same .test model, our own iverilog, no IDE.

The expected count sequence is derived from the testbench semantics (10 ns clock
period, reset until t=12, 4-bit wrap), not copied from a previous run, so this
also checks that simulate_project really executes the design.
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

results: list[tuple[str, bool]] = []


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


def expected_lines() -> list[str]:
    """t=5,15,25 ... 255; count increments on each posedge after reset (t>12)."""
    out = []
    for t in range(5, 260, 10):
        n = 0 if t < 15 else (t - 15) // 10
        out.append(f"t={t} count={format(n % 16, '04b')}")
    return out


async def main() -> int:
    params = StdioServerParameters(command=str(PYTHON), args=[str(ROOT / "server.py")], cwd=str(ROOT))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = [t.name for t in (await session.list_tools()).tools]
            print("TOOL_COUNT=", len(names))
            check("registered simulate_project", "simulate_project" in names)

            await session.call_tool("robei_gui_close", {})
            await asyncio.sleep(2.0)
            data = payload(await session.call_tool("robei_gui_status", {}))
            check("IDE not required (Robei not running)", data.get("running") is False, data)

            data = payload(await session.call_tool("simulate_project", {
                "proj_path": str(PROJ), "test_name": "tb_counter4.test", "render": True,
            }))
            check("headless run ok", data.get("ok") is True and data.get("stage") == "run", data)
            display = data.get("display_output", "")
            got = [line for line in display.splitlines() if line.startswith("t=")]
            want = expected_lines()
            check("display matches design semantics", got == want, {"got": got[:4], "want": want[:4], "n": len(got)})
            print("   first:", got[0], "| last:", got[-1], "| lines:", len(got))

            vcds = data.get("vcd_paths") or []
            check("headless produced VCD", bool(vcds), data)
            check("generated tb source exists", Path(data["headless"]["tb_source"]).is_file(), data.get("headless"))
            print("   tb:", data["headless"]["tb_source_rel"], "deps:", data["headless"]["dependencies"])

            plot = data.get("plot") or {}
            check("render=True produced PNG", plot.get("ok") is True and Path(plot["path"]).is_file(), plot)

            if vcds:
                data = payload(await session.call_tool("find_events", {
                    "vcd_path": vcds[0], "signal": "tb_counter4.count", "condition": "== 4'b1111",
                }))
                check("same 1111 time as the IDE run (155)", data.get("event_count") == 1
                      and data["events"][0]["time"] == 155, data)

            data = payload(await session.call_tool("simulate_project", {
                "proj_path": str(PROJ), "test_name": "nope.test",
            }))
            check("unknown test reports available names", data.get("ok") is False
                  and "tb_counter4" in json.dumps(data, ensure_ascii=False), data)

    passed = sum(1 for _, ok in results if ok)
    print(f"\n--- summary ---\n{passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
