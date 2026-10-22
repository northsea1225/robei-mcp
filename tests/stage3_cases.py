from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"


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


async def call(session, name, arguments):
    result = await session.call_tool(name, arguments)
    data = payload(result)
    print(f"\nCALL {name} is_error={getattr(result, 'is_error', None)} ok={data.get('ok')}")
    return data


async def main():
    params = StdioServerParameters(
        command=str(PYTHON),
        args=[str(ROOT / "server.py")],
        cwd=str(ROOT),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = [tool.name for tool in tools.tools]
            print("TOOLS=" + json.dumps(names))
            required = [
                "runtime_status",
                "lint_verilog",
                "simulate",
                "read_waveform",
                "find_events",
                "render_waveform",
                "read_robei_model",
                "read_robei_project",
                "set_robei_port",
                "create_robei_project",
                "create_robei_model",
                "export_robei_verilog",
                "generate_testbench",
                "robei_gui_status",
                "robei_gui_launch",
                "robei_gui_open_project",
                "robei_gui_open_model",
                "robei_gui_compile",
                "robei_gui_output",
                "robei_gui_refresh",
                "robei_gui_run",
                "robei_gui_wave",
                "robei_gui_code_read",
                "robei_gui_code_write",
                "robei_gui_close",
                "sync_robei_verilog",
                "create_robei_test",
                "robei_gui_experiment",
                "robei_gui_wave_load",
                "compare_waveforms",
                "report_lab",
                "simulate_project",
                "robei_doctor",
                "list_projects",
                "robei_gui_health",
                "robei_gui_compile_all",
            ]
            for name in required:
                assert name in names, f"missing tool: {name}"
            unexpected = [n for n in names if n not in required]
            assert not unexpected, f"unexpected tools registered: {unexpected}"

            runtime = await call(session, "runtime_status", {})
            assert runtime["ok"] is True
            print("RUNTIME=" + json.dumps({
                "mcp": runtime.get("mcp_version"),
                "robei": runtime["tools"]["robei"],
                "iverilog": runtime["tools"]["iverilog"]["version"],
                "vvp": runtime["tools"]["vvp"]["version"],
                "yosys_path": runtime["tools"]["yosys"]["path"],
            }, ensure_ascii=False))

            model = await call(session, "read_robei_model", {"path": r"C:\ProgramData\Robei\mux.model"})
            assert model["ok"] is True
            print("MODEL=" + json.dumps({
                "name": model["name"],
                "ports": [p["name"] for p in model["ports"]],
                "code": model["code"],
            }, ensure_ascii=False))

            clean_lint = await call(session, "lint_verilog", {
                "files": ["counter4/counter4.v"],
                "top": "counter4",
                "timeout_s": 20,
            })
            assert clean_lint["ok"] is True
            assert clean_lint["iverilog"]["warning_count"] == 0
            print("CLEAN_LINT=" + json.dumps({
                "iverilog_ok": clean_lint["iverilog"]["ok"],
                "warning_count": clean_lint["iverilog"]["warning_count"],
                "yosys_ok": clean_lint["yosys"]["ok"],
                "yosys_summary": clean_lint["yosys"]["summary"],
            }, ensure_ascii=False))

            sim = await call(session, "simulate", {
                "files": ["counter4/counter4.v", "counter4/counter4_tb.v"],
                "top": "counter4_tb",
                "timeout_s": 20,
            })
            assert sim["ok"] is True
            assert sim["vcd_rel_paths"]
            print("DISPLAY=" + json.dumps(sim["display_output"], ensure_ascii=False))
            print("VCD=" + sim["vcd_rel_paths"][0])

            listing = await call(session, "read_waveform", {"vcd_path": sim["vcd_rel_paths"][0]})
            assert listing["ok"] is True and listing["signal_count"] == 8
            print("SIGNALS=" + json.dumps([s["name"] for s in listing["signals"]], ensure_ascii=False))

            values = await call(session, "read_waveform", {
                "vcd_path": sim["vcd_rel_paths"][0],
                "signals": ["counter4_tb.clk", "counter4_tb.rst_n", "counter4_tb.dut.count"],
                "time": 45,
            })
            assert values["ok"] is True
            print("VALUES=" + json.dumps(values["values"], ensure_ascii=False))
            assert values["values"][2]["value_int"] == 4
            assert values["values"][2]["value"] == "0100"

            width = await call(session, "lint_verilog", {
                "files": ["cases/warning_width_unused.v"],
                "top": "warning_width_unused",
                "timeout_s": 20,
            })
            assert width["ok"] is True
            assert width["iverilog"]["warning_count"] >= 1
            print("WIDTH_WARNINGS=" + json.dumps(width["iverilog"]["warnings"], ensure_ascii=False))
            print("WIDTH_YOSYS=" + json.dumps(width["yosys"]["summary"], ensure_ascii=False))

            portbind = await call(session, "lint_verilog", {
                "files": ["cases/warning_portbind.v"],
                "top": "warning_portbind",
                "timeout_s": 20,
            })
            assert portbind["ok"] is True
            assert portbind["iverilog"]["warning_count"] >= 1
            print("PORTBIND_WARNINGS=" + json.dumps(portbind["iverilog"]["warnings"], ensure_ascii=False))

            syntax = await call(session, "lint_verilog", {
                "files": ["cases/syntax_error.v"],
                "top": "syntax_error",
                "timeout_s": 20,
            })
            assert syntax["ok"] is False
            assert syntax["iverilog"]["ok"] is False
            assert "syntax error" in syntax["iverilog"]["stderr"].lower()
            print("SYNTAX_IVERILOG=" + json.dumps(syntax["iverilog"]["stderr"], ensure_ascii=False))
            print("SYNTAX_YOSYS=" + json.dumps(syntax["yosys"]["stderr"], ensure_ascii=False))

            timeout = await call(session, "simulate", {
                "files": ["cases/timeout_tb.v"],
                "top": "timeout_tb",
                "timeout_s": 1,
            })
            assert timeout["ok"] is False
            assert timeout["simulation"]["timeout"] is True
            print("TIMEOUT=" + json.dumps({
                "duration_ms": timeout["simulation"]["duration_ms"],
                "returncode": timeout["simulation"]["returncode"],
                "stderr": timeout["simulation"]["stderr"],
                "vcd_rel_paths": timeout["vcd_rel_paths"],
            }, ensure_ascii=False))
            tasklist = subprocess.run(["tasklist"], capture_output=True, text=True, errors="replace").stdout
            residual = tasklist.lower().count("vvp.exe")
            print("RESIDUAL_VVP=" + str(residual))
            assert residual == 0

            print("\nSTAGE3_OK")


if __name__ == "__main__":
    asyncio.run(main())
