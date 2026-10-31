"""Stage 12: discovery, bridge health, IDE compile, project-wide compile.

Every step is a real MCP stdio call, so this is also the client-facing
regression for the four tools added in this stage.
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
WS = ROOT / "workspace"
COUNTER = WS / "counter4_scratch" / "counter4_scratch.proj"
TWO_LEVEL = WS / "robei_samples" / "two_level" / "two_level.proj"

results: list[tuple[str, bool]] = []


def payload(result) -> dict:
    if result.content and hasattr(result.content[0], "text"):
        try:
            return json.loads(result.content[0].text)
        except Exception:
            return {"raw": result.content[0].text[:500]}
    return {}


def check(name: str, ok: bool, data=None) -> None:
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}")
    if not ok and data is not None:
        print("      " + json.dumps(data, ensure_ascii=False)[:700])


def _fixture_hint(entry: dict) -> str | None:
    if "tb_counter4_mcp" not in (entry.get("tests") or []):
        return "缺少 tb_counter4_mcp：先跑 stage11（tests/stage11_mcp_native.py）；stage10 会重建 scratch。"
    return None


async def main() -> int:
    params = StdioServerParameters(command=str(PYTHON), args=[str(ROOT / "server.py")], cwd=str(ROOT))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            doctor = payload(await session.call_tool("gui_doctor", {}))
            print("GUI_DOCTOR", json.dumps(doctor, ensure_ascii=True))
            if not doctor.get("ok"):
                print("GUI_DOCTOR_FAILED", json.dumps(doctor.get("failed_checks") or [], ensure_ascii=False))
                return 2
            names = [t.name for t in (await session.list_tools()).tools]
            print("TOOL_COUNT=", len(names))
            for want in ("list_projects", "robei_gui_status", "robei_gui_compile_all"):
                check(f"registered {want}", want in names)

            await session.call_tool("robei_gui_close", {})
            await asyncio.sleep(2.0)

            # --- 1. discovery ------------------------------------------------
            data = payload(await session.call_tool("list_projects", {}))
            rels = [p["relative"] for p in data.get("projects", [])]
            check("list_projects ok", data.get("ok") is True, data)
            check("finds counter4_scratch", any("counter4_scratch" in r for r in rels), rels)
            entry = next((p for p in data["projects"] if "counter4_scratch" in p["relative"]), {})
            check("sees tests", entry.get("tests") == ["tb_counter4", "tb_counter4_mcp"],
                  {"hint": _fixture_hint(entry), "project": entry})
            # 0.11: the project's build/ no longer receives a VCD, because Robei's own
            # Build > Run is gone; simulations land under workspace/build/sim-*.
            check("sees verilog export", "counter4.v" in entry.get("verilog", []), entry)

            # --- 2. status while the IDE is closed ---------------------------
            data = payload(await session.call_tool("robei_gui_status", {"probe": True}))
            check(
                "status: not running",
                data.get("running") is False and (data.get("bridge_health") or {}).get("ok") is False,
                data,
            )
            print("   bridge:", (data.get("bridge_health") or {}).get("reason"))

            # --- 3. open + compile inside the IDE, simulate headlessly --------
            data = payload(await session.call_tool("robei_gui_launch", {"wait_s": 60}))
            check("launch", data.get("ok") is True, data)
            data = payload(await session.call_tool("robei_gui_open_project", {"proj_path": str(COUNTER)}))
            check("open project", data.get("ok") is True, data)
            data = payload(await session.call_tool("robei_gui_open_model", {"name": "tb_counter4.test"}))
            check("open test model", data.get("ok") is True, data)
            data = payload(await session.call_tool("robei_gui_compile", {"timeout_s": 90}))
            check("compile test model", data.get("ok") is True, data)
            print("   output tail:", (data.get("delta") or "").replace("\n", " | ")[:200])

            data = payload(await session.call_tool("simulate_project", {
                "proj_path": str(COUNTER), "test_name": "tb_counter4.test",
            }))
            check("simulate_project ok", data.get("ok") is True, data)
            vcds = data.get("vcd_paths") or []
            check("simulate_project VCD", bool(vcds), data)
            if vcds:
                data = payload(await session.call_tool("render_waveform", {
                    "vcd_path": vcds[0],
                    "signals": ["tb_counter4.clk", "tb_counter4.count"],
                }))
                check("render_waveform png", data.get("ok") is True and bool(data.get("path")), data)
                print("   png:", data.get("path"))

            # --- 5. project-wide compile (needs the -y library) --------------
            data = payload(await session.call_tool("sync_robei_verilog", {"proj_path": str(TWO_LEVEL)}))
            check("sync two_level verilog", data.get("ok") is True and len(data.get("written", [])) == 2, data)
            data = payload(await session.call_tool("robei_gui_open_project", {"proj_path": str(TWO_LEVEL)}))
            check("open two_level", data.get("ok") is True, data)
            data = payload(await session.call_tool("robei_gui_compile_all", {}))
            check("compile_all 2/2", data.get("ok") is True and data.get("compiled") == 2, data)
            print("   per model:", [(r["model"], r["ok"], r.get("missing_modules")) for r in data.get("results", [])])

            await session.call_tool("robei_gui_close", {})

    passed = sum(1 for _, ok in results if ok)
    print(f"\n--- summary ---\n{passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
