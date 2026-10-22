"""Stage 12: discovery, UIA health, self-heal, project-wide compile, one-call plot.

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
COUNTER = WS / "counter4_native" / "counter4_native.proj"
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


async def main() -> int:
    params = StdioServerParameters(command=str(PYTHON), args=[str(ROOT / "server.py")], cwd=str(ROOT))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = [t.name for t in (await session.list_tools()).tools]
            print("TOOL_COUNT=", len(names))
            for want in ("list_projects", "robei_gui_health", "robei_gui_compile_all"):
                check(f"registered {want}", want in names)

            await session.call_tool("robei_gui_close", {})
            await asyncio.sleep(2.0)

            # --- 1. discovery ------------------------------------------------
            data = payload(await session.call_tool("list_projects", {}))
            rels = [p["relative"] for p in data.get("projects", [])]
            check("list_projects ok", data.get("ok") is True, data)
            check("finds counter4_native", any("counter4_native" in r for r in rels), rels)
            entry = next((p for p in data["projects"] if "counter4_native" in p["relative"]), {})
            check("sees tests", entry.get("tests") == ["tb_counter4", "tb_counter4_mcp"], entry)
            check("sees VCD", bool(entry.get("build", {}).get("vcd")), entry)

            # --- 2. health while the IDE is closed ---------------------------
            data = payload(await session.call_tool("robei_gui_health", {}))
            check("health: not running", data.get("ok") is False and not data.get("running"), data)

            # --- 3. one call: open -> compile -> run -> plot -----------------
            data = payload(await session.call_tool("robei_gui_experiment", {
                "proj_path": str(COUNTER),
                "test_name": "tb_counter4.test",
                "restart": True,
                "timeout_s": 240,
            }))
            check("experiment ok", data.get("ok") is True, data)
            print("   run:", (data.get("steps", {}).get("run", {}) or {}).get("delta", "").replace("\n", " | ")[:200])
            plot = data.get("plot") or {}
            check("experiment plotted PNG", plot.get("ok") is True and bool(plot.get("path")), plot)
            print("   png:", plot.get("path"), "signals:", (plot.get("signals") or [])[:4])

            data = payload(await session.call_tool("robei_gui_health", {}))
            check("health: ok while idle", data.get("ok") is True, data)
            print("   health:", {k: data.get(k) for k in ("widgets", "menu_items", "pid")})

            # --- 4. Wave dock vs UIA bridge + self-heal ----------------------
            data = payload(await session.call_tool("robei_gui_wave_load", {"signals": ["count", "clk"]}))
            print("   wave_load:", {k: data.get(k) for k in ("ok", "rendered", "clicked")})
            data = payload(await session.call_tool("robei_gui_health", {}))
            broken = data.get("ok") is False
            print("   health after wave:", {k: data.get(k) for k in ("ok", "running", "widgets", "reason")})
            check("health detects broken bridge", broken, data)

            data = payload(await session.call_tool("robei_gui_experiment", {
                "proj_path": str(COUNTER),
                "test_name": "tb_counter4.test",
                "timeout_s": 240,
            }))
            healed = (data.get("steps", {}) or {}).get("heal")
            check("experiment self-heals", data.get("ok") is True, data)
            check("heal triggered after breakage", bool(healed) if broken else True, {"heal": healed, "broken": broken})
            print("   heal:", healed)

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
