"""Stage 10: 4-bit counter, built and simulated entirely inside Robei.

Uses only the MCP tools: create_robei_project -> create_robei_model ->
create_robei_test (+ verilog library sync) -> robei_gui_experiment ->
read_waveform / find_events / render_waveform.
"""
from __future__ import annotations

import json
import shutil
import traceback
from pathlib import Path

from robei_mcp import gui, tools

PROJECT = "counter4_native"
TEST = "tb_counter4.test"

CODE = """  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) count <= 4'b0000;
    else count <= count + 4'b0001;
  end"""

TB = """reg clk; reg rst_n; wire [3:0] count;
counter4 dut (.clk(clk), .rst_n(rst_n), .count(count));
initial begin
  clk = 0; rst_n = 0;
  #12 rst_n = 1;
end
always #5 clk = ~clk;
always @(posedge clk) $display("t=%0t count=%b", $time, count);
initial begin
  #260 $finish;
end"""


def main() -> None:
    report: dict = {}
    try:
        root = tools.SETTINGS.workspace / PROJECT
        if root.exists():
            shutil.rmtree(root)
        project = tools.create_robei_project(PROJECT, [])
        proj = project["proj"]
        report["model"] = {
            k: v
            for k, v in tools.create_robei_model(
                proj_path=proj,
                name="counter4",
                ports=[
                    {"name": "clk", "direction": "input"},
                    {"name": "rst_n", "direction": "input"},
                    {"name": "count", "direction": "output", "width": 4, "datatype": "reg"},
                ],
                code=CODE,
                comment="4-bit binary counter",
            ).items()
            if k in ("ok", "path", "ports", "registered_in_project")
        }
        report["test"] = {
            k: v
            for k, v in tools.create_robei_test(proj_path=proj, name="tb_counter4", code=TB).items()
            if k in ("ok", "path", "verilog_dependencies")
        }
        report["export"] = tools.export_robei_verilog(
            str(root / "model" / "counter4.model"), out_name="counter4_native.v"
        ) if not (tools.SETTINGS.workspace / "build" / "export" / "counter4_native.v").exists() else "exists"
        report["experiment"] = gui.experiment(proj, TEST, restart=True, timeout_s=240)
        vcds = report["experiment"].get("vcd") or []
        if vcds:
            vcd = vcds[0]
            report["vcd"] = vcd
            report["signals"] = [s["name"] for s in tools.read_waveform(vcd)["signals"]]
            report["at_40"] = tools.read_waveform(vcd, signals=["tb_counter4.count"], time=40)
            report["first_full"] = tools.find_events(vcd, "tb_counter4.count", "== 4'b1111", limit=3)
            png = tools.render_waveform(
                vcd,
                signals=["tb_counter4.clk", "tb_counter4.count"],
                title="counter4 (Robei vvp)",
            )
            report["png"] = {k: png.get(k) for k in ("ok", "path", "relative_path")}
    except Exception:
        report["fatal"] = traceback.format_exc()[-2500:]
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
