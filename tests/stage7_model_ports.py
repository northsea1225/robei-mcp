"""Stage 7 regression: build a model from ports with set_robei_port, then compile it in Robei."""
from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
# Scratch project: built from scratch on every run so the tracked samples stay clean.
PROJECT = ROOT / "workspace" / "port_scratch" / "port_scratch.proj"
MODEL = ROOT / "workspace" / "port_scratch" / "model" / "blank.model"

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
            record("tool_registration", "set_robei_port" in names, f"({len(names)} tools)")

            if PROJECT.parent.exists():
                shutil.rmtree(PROJECT.parent)
            made = payload(await session.call_tool("create_robei_project", {"name": "port_scratch", "models": []}))
            record("create_project", made.get("ok") is True, str(made.get("proj")))
            created = payload(await session.call_tool("create_robei_model", {
                "proj_path": str(PROJECT), "name": "blank", "ports": [], "code": "",
            }))
            record("create_model", created.get("ok") is True, str(created.get("path")))

            for port, direction in (("a", "input"), ("clk", "input"), ("y", "output")):
                data = payload(
                    await session.call_tool(
                        "set_robei_port",
                        {"model_path": str(MODEL), "name": port, "direction": direction, "width": 1},
                    )
                )
                record(f"set_port_{port}", data.get("ok") is True, f"replaced={data.get('replaced')}")

            model = payload(await session.call_tool("read_robei_model", {"path": str(MODEL)}))
            got = {p["name"]: p["inout"] for p in (model.get("ports") or [])}
            record("model_ports", got.get("a") == "input" and got.get("y") == "output" and got.get("clk") == "input", json.dumps(got))

            launch = payload(await session.call_tool("robei_gui_launch", {"wait_s": 30}))
            record("launch", launch.get("ok") is True)
            opened = payload(await session.call_tool("robei_gui_open_project", {"proj_path": str(PROJECT), "timeout_s": 120}))
            record("open_project", opened.get("ok") is True, str(opened.get("project")))
            gui_model = payload(await session.call_tool("robei_gui_open_model", {"name": "blank.model", "timeout_s": 120}))
            record("open_model", gui_model.get("ok") is True, f"active={gui_model.get('active')}")
            compiled = payload(await session.call_tool("robei_gui_compile", {"timeout_s": 120}))
            record("compile", compiled.get("ok") is True, str(compiled.get("generated"))[:90])
            closed = payload(await session.call_tool("robei_gui_close", {}))
            record("close", closed.get("ok") is True)

    failed = [n for n, ok in results if not ok]
    print("\n--- summary ---")
    print(f"{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
