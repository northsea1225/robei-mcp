"""Stage 5 regression: drive the Robei IDE through the MCP GUI tools.

Run with the project venv:
    .venv\\Scripts\\python.exe tests\\stage5_gui_cases.py

The script launches Robei, opens the two_level sample, opens and2.model,
compiles it, reads the Output dock and finally closes the IDE.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
PROJECT = ROOT / "workspace" / "robei_samples" / "two_level" / "two_level.proj"

results: list[tuple[str, bool]] = []


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


def check(name: str, data: dict, expect: bool) -> None:
    ok = bool(data.get("ok")) is expect
    results.append((name, ok))
    print(f"\n=== {name} === ok={data.get('ok')} expected={expect} -> {'PASS' if ok else 'FAIL'}")
    print(json.dumps(data, ensure_ascii=False, indent=2)[:1800])


async def main() -> int:
    params = StdioServerParameters(command=str(PYTHON), args=[str(ROOT / "server.py")], cwd=str(ROOT))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            print("TOOLS=" + json.dumps(names, ensure_ascii=False))
            gui_tools = [n for n in names if n.startswith("robei_gui_")]
            results.append(("tool_registration", len(gui_tools) == 7))
            print("GUI tools:", gui_tools)

            data = payload(await session.call_tool("robei_gui_status", {}))
            print("\n=== robei_gui_status (before launch) ===")
            print(json.dumps({k: data.get(k) for k in ("ok", "running", "hint", "transport")}, ensure_ascii=False))
            results.append(("status_before_launch", data.get("ok") is True))

            check("robei_gui_launch", payload(await session.call_tool("robei_gui_launch", {"wait_s": 30})), True)
            check("robei_gui_status", payload(await session.call_tool("robei_gui_status", {})), True)
            check(
                "robei_gui_open_project",
                payload(await session.call_tool("robei_gui_open_project", {"proj_path": str(PROJECT), "timeout_s": 120})),
                True,
            )
            check(
                "robei_gui_open_model",
                payload(await session.call_tool("robei_gui_open_model", {"name": "and2.model", "timeout_s": 120})),
                True,
            )
            compile_data = payload(await session.call_tool("robei_gui_compile", {"timeout_s": 120}))
            check("robei_gui_compile", compile_data, True)
            check("robei_gui_output", payload(await session.call_tool("robei_gui_output", {"lines": 20})), True)
            check("robei_gui_close", payload(await session.call_tool("robei_gui_close", {})), True)

            data = payload(await session.call_tool("robei_gui_status", {}))
            print("\n=== robei_gui_status (after close) ===")
            print(json.dumps({k: data.get(k) for k in ("ok", "running")}, ensure_ascii=False))
            results.append(("status_after_close", data.get("running") is False))

    failed = [name for name, ok in results if not ok]
    print("\n--- summary ---")
    for name, ok in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    print(f"{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
