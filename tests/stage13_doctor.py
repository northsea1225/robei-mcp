"""Stage 13: robei_doctor over MCP + the negative path (misconfigured ROBEI_HOME).

Checks that the self-check reports the real toolchain, that deep mode actually
compiles and runs something, and that a broken config produces a useful hint
instead of a crash.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(r"C:\dev\robei-mcp")
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"

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


async def main() -> int:
    params = StdioServerParameters(command=str(PYTHON), args=[str(ROOT / "server.py")], cwd=str(ROOT))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = [t.name for t in (await session.list_tools()).tools]
            print("TOOL_COUNT=", len(names))
            check("registered robei_doctor", "robei_doctor" in names)

            data = payload(await session.call_tool("robei_doctor", {}))
            check("doctor ok", data.get("ok") is True, data)
            by_name = {c["name"]: c for c in data.get("checks", [])}
            print("   checks:", {k: v["ok"] for k, v in by_name.items()})
            for want in ("python", "mcp_sdk", "workspace", "robei_exe", "iverilog", "vvp", "yosys", "gui_task"):
                check(f"check {want}", want in by_name)
            check("gui task registered", by_name.get("gui_task", {}).get("ok") is True, by_name.get("gui_task"))

            data = payload(await session.call_tool("robei_doctor", {"deep": True}))
            deep = {c["name"]: c for c in data.get("checks", [])}.get("deep_simulate", {})
            check("deep simulate", deep.get("ok") is True, deep)
            print("   deep:", json.dumps(deep.get("detail"))[:160])

    # --- .const parsing (real file produced by Robei) -----------------------
    proc = subprocess.run(
        [str(PYTHON), "-c",
         "import json;from robei_mcp import tools;"
         "r=tools.read_robei_project(r\"C:\\dev\\robei-mcp\\workspace\\robei_samples\\two_level\\two_level.proj\");"
         "print(json.dumps({'n':len(r['constraints']),'c':r['constraints'][0]['name'] if r['constraints'] else None,"
         "'t':r['constraints'][0]['type'] if r['constraints'] else None,'u':r['unchanged']}))"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=120,
    )
    try:
        cdata = json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception:
        cdata = {"error": proc.stderr[-300:]}
    print("   const:", cdata)
    check("parses .const", cdata.get("n") == 1 and cdata.get("t") == "const", cdata)
    check("proj untouched while reading .const", cdata.get("u") is True, cdata)

    # --- negative path: a bogus ROBEI_HOME must fail loudly with a hint ------
    env = os.environ.copy()
    env["ROBEI_HOME"] = r"C:\definitely-not-robei"
    env["ROBEI_EXE"] = r"C:\definitely-not-robei\bin\Robei.exe"
    env["PYTHONPATH"] = str(ROOT)
    proc = subprocess.run(
        [str(PYTHON), "-c", "import json;from robei_mcp import doctor;print(json.dumps(doctor.run()))"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=120, env=env,
    )
    try:
        bad = json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception:
        bad = {"error": proc.stderr[-400:]}
    print("   bogus ROBEI_HOME ->", bad.get("failures"))
    check("bogus ROBEI_HOME fails", bad.get("ok") is False, bad)
    check("bogus ROBEI_HOME names robei_exe", "robei_exe" in (bad.get("failures") or []), bad)
    hint = next((c["hint"] for c in bad.get("checks", []) if c["name"] == "robei_exe"), None)
    check("bogus ROBEI_HOME gives a hint", bool(hint and "ROBEI_HOME" in hint), bad)

    # --- setup.py --check is green ------------------------------------------
    proc = subprocess.run(
        [str(PYTHON), str(ROOT / "tools" / "setup.py"), "--check"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=180,
    )
    check("setup.py --check exit 0", proc.returncode == 0, proc.stdout[-400:])

    passed = sum(1 for _, ok in results if ok)
    print(f"\n--- summary ---\n{passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
