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
            for t in (5000, 15000, 25000, 35000):
                data = payload(await session.call_tool("read_waveform", {
                    "vcd_path": VCD,
                    "signals": ["tb_and_gate.a", "tb_and_gate.b", "tb_and_gate.y"],
                    "time": t,
                }))
                vals = {v["name"].split(".")[-1]: v["value"] for v in data.get("values", [])}
                ok = vals.get("y") == str(int(vals.get("a") == "1" and vals.get("b") == "1"))
                print(f"t={t:>5}ps a={vals.get('a')} b={vals.get('b')} y={vals.get('y')} and_ok={ok}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
