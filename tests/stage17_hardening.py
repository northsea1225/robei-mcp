"""Stage 17: protocol + input hardening (annotations, error codes, XML guard, cancel, config).

Everything runs through a real stdio MCP client. The cancellation case cancels a
running ``simulate`` and checks both the returned state and that no ``vvp.exe``
survives; the config case re-imports the server in a child process pointed at a
throw-away robei.toml.
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = pathlib.Path(r"C:\dev\robei-mcp")
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
WS = ROOT / "workspace"
CASES = WS / "cases"

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
        print("      " + json.dumps(data, ensure_ascii=False)[:700])


def vvp_alive() -> bool:
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq vvp.exe"], capture_output=True, text=True).stdout
    return "vvp.exe" in out


async def main() -> int:
    params = StdioServerParameters(command=str(PYTHON), args=[str(ROOT / "server.py")], cwd=str(ROOT))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = {t.name: t for t in (await session.list_tools()).tools}
            print("TOOL_COUNT=", len(tools))

            # --- annotations -------------------------------------------------
            missing = [n for n, t in tools.items() if t.annotations is None]
            check("every tool declares annotations", not missing, missing)
            check(
                "read_waveform is read-only",
                tools["read_waveform"].annotations.read_only_hint is True,
            )
            check(
                "simulate is not read-only",
                tools["simulate"].annotations.read_only_hint is False,
            )
            check(
                "robei_gui_close is destructive",
                tools["robei_gui_close"].annotations.destructive_hint is True,
            )
            check(
                "gui tools are marked open-world",
                tools["robei_gui_compile"].annotations.open_world_hint is True,
            )

            # --- error codes --------------------------------------------------
            nf = payload(await session.call_tool("simulate", {"files": ["missing.v"], "top": "x"}))
            check("missing file -> code=not_found", nf.get("code") == "not_found", nf)
            check("error carries a hint", bool(nf.get("hint")), nf)

            escape = payload(await session.call_tool(
                "read_robei_model", {"path": r"C:\Windows\win.ini"}))
            check("path escape -> code=invalid_argument", escape.get("code") == "invalid_argument", escape)

            shutil.rmtree(WS / "stage17_hardening", ignore_errors=True)
            proj = payload(await session.call_tool("create_robei_project", {"name": "stage17_hardening", "models": []}))["proj"]
            first = payload(await session.call_tool("create_robei_model", {
                "proj_path": proj, "name": "leaf", "code": "  assign y = a;"}))
            check("project + leaf model created", first.get("ok") is True, first)
            dup = payload(await session.call_tool("create_robei_model", {
                "proj_path": proj, "name": "leaf", "code": "  assign y = a;"}))
            check("duplicate create -> code=already_exists", dup.get("code") == "already_exists", dup)

            # --- XML guard ----------------------------------------------------
            bomb = CASES / "entity_bomb.model"
            bomb.write_text(
                '<?xml version="1.0"?>\n<!DOCTYPE Robei [<!ENTITY a "aaaa">]>\n'
                '<Module Name="bomb" Type="module" Code="&a;"/>', encoding="utf-8")
            res = payload(await session.call_tool("read_robei_model", {"path": str(bomb)}))
            check("entity bomb -> code=unsafe_xml", res.get("code") == "unsafe_xml", res)
            bomb.unlink()

            plain = CASES / "doctype_plain.model"
            plain.write_text('<!DOCTYPE Robei>\n<Module Name="plain_ok" Type="module" Code=""/>', encoding="utf-8")
            res = payload(await session.call_tool("read_robei_model", {"path": str(plain)}))
            check("bare <!DOCTYPE Robei> still parses", res.get("ok") is True, res)
            plain.unlink()

            real = payload(await session.call_tool(
                "read_robei_model", {"path": r"C:\ProgramData\Robei\mux.model"}))
            check("real Robei model still parses", real.get("ok") is True and real.get("name") == "mux", real)

            # --- canvas negative cases -----------------------------------------
            canvas_args = {
                "proj_path": proj, "name": "neg",
                "ports": [{"name": "a", "direction": "input"}, {"name": "y", "direction": "output"}],
                "instances": [{"name": "u1", "model": "leaf"}],
                "wires": [{"from": "a", "to": "u1.a"}, {"from": "u1.y", "to": "y"}],
            }
            bad = payload(await session.call_tool("create_robei_canvas_model",
                                                 {**canvas_args, "instances": [{"name": "u1", "model": "leaf"},
                                                                               {"name": "u1", "model": "leaf"}]}))
            check("duplicate instance -> invalid_argument", bad.get("code") == "invalid_argument", bad)
            bad = payload(await session.call_tool("create_robei_canvas_model",
                                                 {**canvas_args, "wires": [{"from": "u1.zz", "to": "y"}]}))
            check("unknown instance port -> invalid_argument", bad.get("code") == "invalid_argument", bad)
            bad = payload(await session.call_tool("create_robei_canvas_model",
                                                 {**canvas_args, "wires": [{"from": "nope", "to": "y"}]}))
            check("unknown top port -> invalid_argument", bad.get("code") == "invalid_argument", bad)
            bad = payload(await session.call_tool("create_robei_canvas_model",
                                                 {**canvas_args, "instances": [{"name": "u1", "model": "ghost"}]}))
            check("missing model -> not_found", bad.get("code") == "not_found", bad)
            bad = payload(await session.call_tool("create_robei_canvas_model",
                                                 {**canvas_args, "name": 'evil" x="1'}))
            check("attribute injection -> invalid_argument", bad.get("code") == "invalid_argument", bad)

            # --- progress ------------------------------------------------------
            events: list[tuple] = []

            async def on_progress(progress, total, message):
                events.append((progress, total, message))

            sim = payload(await session.call_tool(
                "simulate",
                {"files": ["counter4/counter4.v", "counter4/counter4_tb.v"], "top": "counter4_tb", "timeout_s": 20},
                progress_callback=on_progress))
            check("simulate still ok with progress", sim.get("ok") is True, sim)
            check("progress events >= 4", len(events) >= 4, events[:5])

            # --- cancellation --------------------------------------------------
            task = asyncio.create_task(session.call_tool(
                "simulate", {"files": ["cases/timeout_tb.v"], "top": "timeout_tb", "timeout_s": 60}))
            await asyncio.sleep(2.5)
            running = vvp_alive()
            started = time.time()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            await asyncio.sleep(2.0)
            check("vvp was running before cancel", running)
            check("vvp killed after client cancel", not vvp_alive())
            check("cancel is immediate", time.time() - started < 5, round(time.time() - started, 2))
            alive = payload(await session.call_tool("runtime_status", {}))
            check("server survives a cancelled request", alive.get("ok") is True, alive)

            cfg = payload(await session.call_tool("runtime_status", {}))
            check("runtime_status exposes config sources", "config" in cfg and "sources" in cfg["config"], cfg.get("config"))
            check("default config path reported", str(cfg["config"]["file"]).endswith("robei.toml"), cfg.get("config"))

    # --- robei.toml ---------------------------------------------------------
    tmp_cfg = WS / ".tmp" / "stage17.toml"
    tmp_cfg.parent.mkdir(parents=True, exist_ok=True)
    tmp_cfg.write_text('[paths]\nrobei_home = "E:/Robei"\n[limits]\ndefault_timeout_s = 45\nmax_timeout_s = 91\n',
                       encoding="utf-8")
    code = (
        "import json,sys; sys.path.insert(0, r'" + str(ROOT) + "');"
        "from robei_mcp import tools; rs=tools.runtime_status();"
        "print(json.dumps({'src': rs['config']['sources'], 'lim': rs['limits']}))"
    )
    env = dict(os.environ, ROBEI_MCP_CONFIG=str(tmp_cfg), PYTHONPATH=str(ROOT))
    out = subprocess.run([str(PYTHON), "-c", code], env=env, capture_output=True, text=True)
    try:
        data = json.loads(out.stdout.strip().splitlines()[-1])
    except Exception:
        data = {"raw": out.stdout[-300:], "err": out.stderr[-300:]}
    check("toml limits applied", data.get("lim", {}).get("default_timeout_s") == 45, data)
    check("toml source is recorded per key", data.get("src", {}).get("robei_home") == "toml", data)
    tmp_cfg.unlink()

    passed = sum(1 for _, ok in results if ok)
    print(f"\n--- summary ---\n{passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
