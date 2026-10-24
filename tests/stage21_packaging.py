"""Stage 21: the GUI driver ships inside the package, and the GUI tools are optional.

Nothing here touches the desktop, so it runs on any host:

  1. the driver lives in ``robei_mcp/gui_agent`` (a wheel can ship it)
  2. control files live under ``workspace``, never inside the package
  3. ``ROBEI_MCP_GUI=0`` removes every ``robei_gui_*`` tool from ``tools/list``
  4. the default (Windows + driver present) still advertises 11 of them
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(r"C:\dev\robei-mcp")
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

results: list[tuple[str, bool]] = []


def check(name: str, ok: bool, data=None) -> None:
    results.append((name, bool(ok)))
    print(f"{'PASS' if ok else 'FAIL'}  {name}")
    if not ok and data is not None:
        print("      " + str(data)[:400])


async def tool_names(env_extra: dict[str, str]) -> list[str]:
    env = {**os.environ, **env_extra}
    params = StdioServerParameters(
        command=str(PYTHON), args=[str(ROOT / "server.py")], cwd=str(ROOT), env=env
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return [t.name for t in (await session.list_tools()).tools]


async def main() -> int:
    import robei_mcp.gui_agent as pkg
    from robei_mcp import config
    from robei_mcp.gui_agent import paths

    pkg_dir = Path(pkg.__file__).resolve().parent
    print("package dir:", pkg_dir)
    for rel in ("ui.py", "driver.py", "job.py", "launcher.py", "paths.py"):
        check(f"package ships {rel}", (pkg_dir / rel).is_file(), pkg_dir)
    check("package is inside robei_mcp", pkg_dir.name == "gui_agent", pkg_dir)

    check(
        "control files under workspace",
        str(paths.RUN_DIR).startswith(str(config.SETTINGS.workspace)),
        (str(paths.RUN_DIR), str(config.SETTINGS.workspace)),
    )
    check("job/args/lock all in that dir",
          paths.JOB_FILE.parent == paths.ARGS_FILE.parent == paths.LOCK_FILE.parent == paths.RUN_DIR)

    off = await tool_names({"ROBEI_MCP_GUI": "0"})
    gui_off = [n for n in off if n.startswith("robei_gui_")]
    check("GUI=0 hides every robei_gui_* tool", not gui_off, gui_off)
    check("GUI=0 keeps 33 tools", len(off) == 33, len(off))
    check("GUI=0 keeps the engine", "simulate_project" in off and "read_waveform" in off)
    for name in (
        "list_library_models",
        "import_library_model",
        "edit_robei_canvas",
        "set_robei_code",
        "set_robei_parameter",
    ):
        check(f"GUI=0 exposes {name}", name in off)

    on = await tool_names({})
    gui_on = [n for n in on if n.startswith("robei_gui_")]
    if pkg_dir.parent.name == "robei_mcp" and os.name == "nt":
        check("default advertises 11 gui tools", len(gui_on) == 11, gui_on)
        check("default total 44", len(on) == 44, len(on))
    else:
        print("skip  default GUI count (not Windows)")

    passed = sum(1 for _, ok in results if ok)
    print(f"\n--- summary ---\n{passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
