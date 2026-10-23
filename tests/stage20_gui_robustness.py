"""Stage 20: GUI robustness — marker+quiescence waiting, unknown-state retry, focus retry.

Unit part runs in-process against a fake Output dock (no GUI). The GUI part
uses the real IDE: compile_all must report state/marker/output_tail per model,
and compiling a constrain model must report the constrain marker.
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(r"C:\dev\robei-mcp")
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
sys.path.insert(0, str(ROOT / "tools" / "gui"))
sys.path.insert(0, str(ROOT))

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

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
        print("      " + json.dumps(data, ensure_ascii=False)[:600])


class FakeDock:
    """Feeds _wait_for a scripted Output dock, then repeats the last frame."""

    def __init__(self, frames: list[str]) -> None:
        self.frames = frames
        self.last = frames[-1]
        self.polls = 0

    def __call__(self, dlg) -> str:  # signature of robei_ui.read_output
        self.polls += 1
        if self.frames:
            self.last = self.frames.pop(0)
        return self.last


def unit_tests() -> None:
    # The real module, not the tools/gui/robei_ui shim: monkeypatching a shim
    # would not touch the globals that _wait_for actually reads.
    from robei_mcp.gui_agent import ui

    base = "|>Welcome\n"
    frames = [
        base + "|>Check complete with 0 warnings.\n",
        base + "|>Check complete with 0 warnings.\n|>Compile done.\n",
        base + "|>Check complete with 0 warnings.\n|>Compile done.\n|>late warning: race\n",
    ]
    dock = FakeDock(frames)
    original = ui.read_output
    ui.read_output = dock
    try:
        started = time.time()
        after, delta, marker, stopped = ui._wait_for(None, base, ui.COMPILE_MARKERS, 10, quiet_s=0.4)
        elapsed = time.time() - started
        check("marker detected after the output settles", marker == "Compile done", marker)
        check("lines that arrive after the marker are kept", "late warning" in delta, delta[-80:])
        check("waiting stops early once quiescent", elapsed < 5, round(elapsed, 2))

        err_dock = FakeDock([base, base + "|>Error: boom\n"])
        ui.read_output = err_dock
        started = time.time()
        after, delta, marker, stopped = ui._wait_for(
            None, base, ui.SYNTH_MARKERS, 30, quiet_s=0.4, stop_markers=("Error:",)
        )
        check("stop marker returns without waiting the timeout", stopped is True and time.time() - started < 5,
              {"stopped": stopped, "elapsed": round(time.time() - started, 2)})
        check("stop marker reports no success marker", marker is None, marker)

        quiet_dock = FakeDock([base + "|>nothing happens\n"])
        ui.read_output = quiet_dock
        started = time.time()
        after, delta, marker, stopped = ui._wait_for(None, base, ui.COMPILE_MARKERS, 1.2, quiet_s=0.4)
        check("timeout returns marker=None instead of hanging",
              marker is None and 1.0 <= time.time() - started < 6, round(time.time() - started, 2))
        check("marker tables are centralised",
              "Finished to generate constrain file" in ui.CONSTRAIN_MARKERS
              and "Synthesis complete" in ui.SYNTH_MARKERS)
    finally:
        ui.read_output = original


async def gui_tests() -> None:
    params = StdioServerParameters(command=str(PYTHON), args=[str(ROOT / "server.py")], cwd=str(ROOT))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            await session.call_tool("robei_gui_close", {})
            await session.call_tool("robei_gui_launch", {"wait_s": 45})
            opened = payload(await session.call_tool(
                "robei_gui_open_project", {"proj_path": "canvas_stage16/canvas_stage16.proj"}))
            check("project opens for compile_all", opened.get("ok") is True, opened)

            ca = payload(await session.call_tool("robei_gui_compile_all", {"models": []}))
            rows = ca.get("results") or []
            check("compile_all reports every model", ca.get("total") == 2 and len(rows) == 2, ca)
            check("every model carries state/marker/output_tail",
                  all(row.get("state") and row.get("marker") and row.get("output_tail") for row in rows), rows)
            check("compile_all is green", ca.get("ok") is True, rows)

            opened = payload(await session.call_tool(
                "robei_gui_open_project", {"proj_path": "const_demo/const_demo.proj"}))
            check("constraint project opens", opened.get("ok") is True, opened)
            model = payload(await session.call_tool(
                "robei_gui_open_model", {"name": "light_constrain.const", "timeout_s": 60}))
            check("constrain model opens", model.get("ok") is True, model)
            await session.call_tool("robei_gui_save_project", {})
            compiled = payload(await session.call_tool("robei_gui_compile", {"timeout_s": 90}))
            check("constrain compile reports the constrain marker",
                  compiled.get("state") == "ok"
                  and compiled.get("marker") == "Finished to generate constrain file", compiled)
            await session.call_tool("robei_gui_close", {})


def main() -> int:
    unit_tests()
    asyncio.run(gui_tests())
    passed = sum(1 for _, ok in results if ok)
    print(f"\n--- summary ---\n{passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
