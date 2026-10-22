from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
CASES = ROOT / "workspace" / "cases"

FIXTURES = {
    "escape_include.v": """module escape_include (output wire y);
`include "../../../secret_defs.vh"
    assign y = 1'b0;
endmodule
""",
    "escape_fopen.v": """module escape_fopen;
    integer fd;
    initial begin
        fd = $fopen("C:/Windows/Temp/robei_escape_test.txt", "w");
        $fclose(fd);
        $display("opened outside");
        $finish;
    end
endmodule
""",
    "escape_readmemh.v": """module escape_readmemh;
    reg [7:0] mem [0:1];
    initial begin
        $readmemh("../../../outside.hex", mem);
        $display("mem0=%h", mem[0]);
        $finish;
    end
endmodule
""",
    "inside_paths.v": """module inside_paths;
    integer fd;
    initial begin
        fd = $fopen("inside_ok.txt", "w");
        $fdisplay(fd, "ok");
        $fclose(fd);
        $display("inside paths accepted");
        $finish;
    end
endmodule
""",
}


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
    summary = {k: data.get(k) for k in ("ok", "stage", "error", "reason") if k in data}
    print(f"\nCALL {name} {json.dumps(summary, ensure_ascii=False)}")
    return data


ARTIFACTS = (
    ROOT / "workspace" / "build" / "export" / "mux_len1_stage4.v",
    ROOT / "workspace" / "testbench" / "counter4_stage4_tb.v",
    ROOT / "workspace" / "build" / "waveforms" / "counter4_stage4.png",
)


def clean_artifacts() -> None:
    """Keep the test repeatable: it asserts the no-overwrite guard on second call."""
    removed = []
    for path in ARTIFACTS:
        if path.exists():
            path.unlink()
            removed.append(str(path))
    print("CLEANED=" + json.dumps(removed))


def write_fixtures() -> None:
    CASES.mkdir(parents=True, exist_ok=True)
    for name, content in FIXTURES.items():
        path = CASES / name
        if not path.exists():
            path.write_text(content, encoding="utf-8", newline="\n")


async def main():
    write_fixtures()
    clean_artifacts()
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
                "simulate_project",
                "robei_doctor",
                "list_projects",
                "robei_gui_health",
                "robei_gui_compile_all",
            ]
            for name in required:
                assert name in names, f"missing tool: {name}"
            unexpected = [n for n in names if n not in required]
            assert not unexpected, f"unexpected tools: {unexpected}"

            runtime = await call(session, "runtime_status", {})
            assert runtime["ok"] is True
            assert runtime["verilator_available"] is True
            assert runtime["tools"]["verilator"]["version"]
            print("VERILATOR=" + json.dumps({
                "path": runtime["tools"]["verilator"]["path"],
                "version": runtime["tools"]["verilator"]["version"],
                "root": runtime["tools"]["verilator"]["root"],
            }, ensure_ascii=False))

            # 1. unused signal detection (Verilator + Yosys)
            width = await call(session, "lint_verilog", {
                "files": ["cases/warning_width_unused.v"],
                "top": "warning_width_unused",
                "timeout_s": 20,
            })
            unused_names = [item["name"] for item in width.get("unused_signals") or []]
            print("UNUSED=" + json.dumps(unused_names, ensure_ascii=False))
            assert "unused_signal" in unused_names, unused_names
            assert width["verilator"]["skipped"] is False
            vcodes = [w["code"] for w in width["verilator"]["warnings"]]
            print("VERILATOR_WARNINGS=" + json.dumps(vcodes))
            assert "Warning-SELRANGE" in vcodes

            clean = await call(session, "lint_verilog", {
                "files": ["counter4/counter4.v"],
                "top": "counter4",
                "timeout_s": 20,
            })
            assert clean["ok"] is True
            assert clean["iverilog"]["warning_count"] == 0
            assert clean["verilator"]["ok"] is True
            assert clean["unused_signals"] == []

            syntax = await call(session, "lint_verilog", {
                "files": ["cases/syntax_error.v"],
                "top": "syntax_error",
                "timeout_s": 20,
            })
            assert syntax["ok"] is False
            assert syntax["verilator"]["errors"]
            print("SYNTAX_ERROR=" + json.dumps(syntax["verilator"]["errors"][0], ensure_ascii=False))

            # 2. hierarchical / unknown-node project parsing
            project = await call(session, "read_robei_project", {
                "proj_path": "robei_samples/_synthetic/sample.proj",
            })
            assert project["ok"] is True and project["unchanged"] is True
            assert len(project["models"]) == 2
            unknown_tags = [node["tag"] for node in project["unknown_nodes"]]
            print("UNKNOWN_NODES=" + json.dumps(unknown_tags))
            assert "netlist" in unknown_tags
            instance_nodes = [
                child["tag"]
                for model in project["models"]
                for child in model["other_children"]
            ]
            print("MODEL_UNKNOWN_CHILDREN=" + json.dumps(instance_nodes))
            assert "Instance" in instance_nodes

            counter_project = await call(session, "read_robei_project", {
                "proj_path": "robei_counter4_project/counter4_project.proj",
            })
            assert counter_project["unchanged"] is True
            print("PROJECT_SHA=" + counter_project["sha256_before"])

            # 3. export .model -> Verilog, then lint
            export = await call(session, "export_robei_verilog", {
                "model_path": r"C:\ProgramData\Robei\mux.model",
                "out_name": "mux_len1_stage4.v",
                "parameter_overrides": {"LEN": 1},
            })
            assert export["ok"] is True
            assert export["lint"]["ok"] is True
            assert export["model_sha256_before"] == export["model_sha256_after"]
            print("EXPORT=" + json.dumps({
                "path": export["relative_path"],
                "sha256": export["verilog_sha256"],
                "lint_iverilog": export["lint"]["iverilog"]["ok"],
                "lint_verilator": export["lint"]["verilator"]["ok"],
            }, ensure_ascii=False))

            again = await call(session, "export_robei_verilog", {
                "model_path": r"C:\ProgramData\Robei\mux.model",
                "out_name": "mux_len1_stage4.v",
                "parameter_overrides": {"LEN": 1},
            })
            assert again["ok"] is False and "FileExistsError" in again["error"]
            print("EXPORT_OVERWRITE_REFUSED=" + again["error"][:80])

            # 4. generated testbench -> simulate
            tb = await call(session, "generate_testbench", {
                "verilog_path": "counter4/counter4.v",
                "top": "counter4",
                "out_path": "testbench/counter4_stage4_tb.v",
            })
            assert tb["ok"] is True
            assert tb["clock"] == "clk" and tb["reset"] == "rst_n" and tb["reset_active_low"] is True
            print("TB=" + json.dumps({
                "path": tb["relative_path"],
                "clock": tb["clock"],
                "reset": tb["reset"],
                "ports": [p["name"] for p in tb["ports"]],
            }, ensure_ascii=False))

            tb_again = await call(session, "generate_testbench", {
                "verilog_path": "counter4/counter4.v",
                "top": "counter4",
                "out_path": "testbench/counter4_stage4_tb.v",
            })
            assert tb_again["ok"] is False and "FileExistsError" in tb_again["error"]

            sim = await call(session, "simulate", {
                "files": ["counter4/counter4.v", "testbench/counter4_stage4_tb.v"],
                "top": "counter4_tb",
                "timeout_s": 20,
            })
            assert sim["ok"] is True, sim
            assert "SIM start" in sim["display_output"]
            vcd = sim["vcd_rel_paths"][0]
            print("SIM_DISPLAY=" + json.dumps(sim["display_output"]))
            print("VCD=" + vcd)

            # 5. waveform event search + PNG render
            events = await call(session, "find_events", {
                "vcd_path": vcd,
                "signal": "counter4_tb.dut.count",
                "condition": "== 4'b1111",
                "limit": 3,
            })
            assert events["ok"] is True and events["event_count"] >= 1
            assert events["events"][0]["value_int"] == 15
            print("EVENTS=" + json.dumps(events["events"], ensure_ascii=False))

            bad_condition = await call(session, "find_events", {
                "vcd_path": vcd,
                "signal": "counter4_tb.dut.count",
                "condition": "__import__('os').system('echo pwn')",
            })
            assert bad_condition["ok"] is False
            print("BAD_CONDITION_REFUSED=" + bad_condition["error"][:80])

            png = await call(session, "render_waveform", {
                "vcd_path": vcd,
                "signals": ["counter4_tb.clk", "counter4_tb.dut.count"],
                "out_name": "counter4_stage4.png",
                "title": "counter4 stage4",
            })
            assert png["ok"] is True
            assert png["size_bytes"] > 1000
            print("PNG=" + json.dumps({
                "path": png["relative_path"],
                "size_bytes": png["size_bytes"],
                "signals": png["signals"],
            }, ensure_ascii=False))

            png_again = await call(session, "render_waveform", {
                "vcd_path": vcd,
                "signals": ["counter4_tb.clk"],
                "out_name": "counter4_stage4.png",
            })
            assert png_again["ok"] is False and "FileExistsError" in png_again["error"]

            # 6. security: workspace escapes must be refused before compiling
            for case, top in (
                ("escape_include.v", "escape_include"),
                ("escape_fopen.v", "escape_fopen"),
                ("escape_readmemh.v", "escape_readmemh"),
            ):
                blocked = await call(session, "simulate", {
                    "files": [f"cases/{case}"],
                    "top": top,
                    "timeout_s": 10,
                })
                assert blocked["ok"] is False
                assert blocked["stage"] == "security_scan", blocked.get("stage")
                rejected = blocked["security"]["rejected"]
                print("REJECTED=" + json.dumps(
                    [{"kind": r.get("kind"), "raw": r.get("raw")} for r in rejected],
                    ensure_ascii=False,
                ))

            inside = await call(session, "simulate", {
                "files": ["cases/inside_paths.v"],
                "top": "inside_paths",
                "timeout_s": 10,
            })
            assert inside["ok"] is True, inside
            print("INSIDE_PATHS_OK=" + json.dumps(inside["display_output"]))

            # 7. timeout regression: vvp must be killed, no residual process
            timeout = await call(session, "simulate", {
                "files": ["cases/timeout_tb.v"],
                "top": "timeout_tb",
                "timeout_s": 1,
            })
            assert timeout["ok"] is False
            assert timeout["simulation"]["timeout"] is True
            tasklist = subprocess.run(
                ["tasklist"], capture_output=True, text=True, errors="replace"
            ).stdout
            residual = tasklist.lower().count("vvp.exe")
            print("TIMEOUT_RESIDUAL_VVP=" + str(residual))
            assert residual == 0

            print("\nSTAGE4_OK")


if __name__ == "__main__":
    asyncio.run(main())
