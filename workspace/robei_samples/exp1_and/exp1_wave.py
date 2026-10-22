from __future__ import annotations

import asyncio
import json
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[3]
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
VCD = "build/sim-20261022-104619-1c50e9fe/and_gate.vcd"


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


async def main() -> int:
    params = StdioServerParameters(command=str(PYTHON), args=[str(ROOT / "server.py")], cwd=str(ROOT))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listing = payload(await session.call_tool("read_waveform", {"vcd_path": VCD}))
            print("LIST=" + json.dumps(listing, ensure_ascii=False)[:1200])
            one = payload(await session.call_tool("read_waveform", {"vcd_path": VCD, "signals": ["tb_and_gate.a", "tb_and_gate.b", "tb_and_gate.y"], "time": 15000}))
            print("ONE=" + json.dumps(one, ensure_ascii=False)[:1200])
            img = payload(await session.call_tool("render_waveform", {"vcd_path": VCD, "signals": ["tb_and_gate.a", "tb_and_gate.b", "tb_and_gate.y"], "out_name": "exp1_and.png", "title": "Experiment 1: y = a & b"}))
            print("IMG=" + json.dumps(img, ensure_ascii=False)[:800])
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
