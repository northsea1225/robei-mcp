"""Stage 6 regression: read/write the model Code tab through the MCP GUI tools.

Run with the project venv:
    .venv\\Scripts\\python.exe tests\\stage6_gui_code.py
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
MODEL = "and2.model"

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


def record(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


async def main() -> int:
    params = StdioServerParameters(command=str(PYTHON), args=[str(ROOT / "server.py")], cwd=str(ROOT))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = [t.name for t in (await session.list_tools()).tools]
            print("TOOLS=" + json.dumps(names, ensure_ascii=False))
            record("tool_registration", "robei_gui_code_read" in names and "robei_gui_code_write" in names,
                   f"({len(names)} tools)")

            launch = payload(await session.call_tool("robei_gui_launch", {"wait_s": 30}))
            record("launch", launch.get("ok") is True)

            opened = payload(await session.call_tool("robei_gui_open_project", {"proj_path": str(PROJECT), "timeout_s": 120}))
            record("open_project", opened.get("ok") is True and opened.get("project") == "two_level",
                   str(opened.get("project")))

            model = payload(await session.call_tool("robei_gui_open_model", {"name": MODEL, "timeout_s": 120}))
            record("open_model", model.get("ok") is True and model.get("active") == "and2",
                   f"active={model.get('active')}")

            first = payload(await session.call_tool("robei_gui_code_read", {}))
            original = first.get("text") or ""
            record("code_read", first.get("ok") is True and len(original) > 0, repr(original)[:60])

            modified = original + "  // stage6 probe"
            wrote = payload(await session.call_tool("robei_gui_code_write", {"code": modified, "save": True}))
            record("code_write", wrote.get("ok") is True and wrote.get("saved") is True,
                   (wrote.get("save_log") or "")[:80])

            second = payload(await session.call_tool("robei_gui_code_read", {}))
            record("code_read_after_write", second.get("text") == modified, repr(second.get("text"))[:60])

            restored = payload(await session.call_tool("robei_gui_code_write", {"code": original, "save": True}))
            record("code_restore", restored.get("ok") is True and restored.get("saved") is True)

            third = payload(await session.call_tool("robei_gui_code_read", {}))
            record("code_read_after_restore", third.get("text") == original, repr(third.get("text"))[:60])

            compiled = payload(await session.call_tool("robei_gui_compile", {"timeout_s": 120}))
            record("compile", compiled.get("ok") is True, str(compiled.get("generated"))[:80])

            closed = payload(await session.call_tool("robei_gui_close", {}))
            record("close", closed.get("ok") is True)

    failed = [n for n, ok in results if not ok]
    print("\n--- summary ---")
    print(f"{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
