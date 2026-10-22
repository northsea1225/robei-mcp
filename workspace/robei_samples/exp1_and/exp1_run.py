"""Experiment 1 (2-input AND gate) driven entirely through the robei-mcp tools."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[3]
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
PROJ = ROOT / "workspace" / "robei_samples" / "exp1_and" / "exp1_and.proj"
MODEL = ROOT / "workspace" / "robei_samples" / "exp1_and" / "model" / "and_gate.model"
TB = "robei_samples/exp1_and/test/tb_and_gate.v"
DUT = "robei_samples/exp1_and/build/and_gate.v"


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
            names = [t.name for t in (await session.list_tools()).tools]
            print("TOOLS=" + str(len(names)))

            for port, direction in (("a", "input"), ("b", "input"), ("y", "output")):
                data = payload(await session.call_tool("set_robei_port", {
                    "model_path": str(MODEL), "name": port, "direction": direction, "width": 1,
                }))
                print(f"set_port {port:<3} ok={data.get('ok')} replaced={data.get('replaced')}")

            launch = payload(await session.call_tool("robei_gui_launch", {"wait_s": 30}))
            print("launch ok=", launch.get("ok"))
            opened = payload(await session.call_tool("robei_gui_open_project", {"proj_path": str(PROJ), "timeout_s": 120}))
            print("open_project", opened.get("ok"), opened.get("project"))
            model = payload(await session.call_tool("robei_gui_open_model", {"name": "and_gate.model", "timeout_s": 120}))
            print("open_model", model.get("ok"), model.get("active"))
            code = payload(await session.call_tool("robei_gui_code_write",
                                                   {"code": "assign y = a & b;", "save": True}))
            print("code_write ok=", code.get("ok"), "saved=", code.get("saved"))
            comp = payload(await session.call_tool("robei_gui_compile", {"timeout_s": 120}))
            print("compile ok=", comp.get("ok"), "errors=", comp.get("error_count"),
                  "generated=", comp.get("generated"))
            print("compile_delta=" + json.dumps(comp.get("delta"), ensure_ascii=False))
            payload(await session.call_tool("robei_gui_close", {}))

            sim = payload(await session.call_tool("simulate", {"files": [DUT, TB], "top": "tb_and_gate", "timeout_s": 60}))
            print("simulate ok=", sim.get("ok"))
            print("SIM_OUT=" + json.dumps(sim.get("display_output") or "", ensure_ascii=False))
            vcd = (sim.get("vcd_rel_paths") or [None])[0] or (sim.get("vcd_paths") or [None])[0]
            print("VCD=", vcd)
            print("SIM_KEYS=" + json.dumps(sorted(sim.keys())))

            if vcd:
                for t in (10000, 20000, 30000, 40000):
                    w = payload(await session.call_tool("read_waveform", {"vcd_path": vcd, "signals": ["tb_and_gate.a", "tb_and_gate.b", "tb_and_gate.y"], "time": t}))
                    print("WAVE@%s" % t, json.dumps(w.get("signals"), ensure_ascii=False))
                img = payload(await session.call_tool("render_waveform", {"vcd_path": vcd, "signals": ["tb_and_gate.a", "tb_and_gate.b", "tb_and_gate.y"], "out_name": "exp1_and.png", "title": "Experiment 1: y = a & b"}))
                print("render ok=", img.get("ok"), img.get("path"))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
