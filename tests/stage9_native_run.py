"""Stage 9: build a project with the file-level tools, then run it inside Robei.

Verifies that a hand-written .test file (no GUI "New" dialog) is accepted by the
IDE, that sync_robei_verilog supplies the ``-y`` library directory, and that the
VCD produced by Robei's own vvp run is readable by our parser.
"""
from __future__ import annotations

import json
import shutil
import traceback

from robei_mcp import gui, tools

PROJECT = "exp3_write"
TEST = "tb_and_gate.test"

TB_CODE = """reg a; reg b; wire y;
and_gate dut (.a(a), .b(b), .y(y));
initial begin
  a = 0; b = 0; #10;
  a = 0; b = 1; #10;
  a = 1; b = 0; #10;
  a = 1; b = 1; #10;
  $display("t=%0t a=%b b=%b y=%b", $time, a, b, y);
  #10 $finish;
end"""


def build_project() -> dict:
    root = tools.SETTINGS.workspace / PROJECT
    if root.exists():
        shutil.rmtree(root)
    report: dict = {}
    report["project"] = tools.create_robei_project(PROJECT, [])
    proj = report["project"]["proj"]
    report["model"] = tools.create_robei_model(
        proj_path=proj,
        name="and_gate",
        ports=[
            {"name": "a", "direction": "input"},
            {"name": "b", "direction": "input"},
            {"name": "y", "direction": "output"},
        ],
        code="  assign y = a & b;",
        comment="stage9 2-input AND",
    )
    report["test"] = tools.create_robei_test(proj_path=proj, name="tb_and_gate", code=TB_CODE)
    report["read_back"] = {
        k: v
        for k, v in tools.read_robei_project(proj).items()
        if k in ("project_name", "folder_names", "unchanged", "model_errors")
    }
    report["verilog"] = sorted(p.name for p in (root / "verilog").glob("*"))
    return report


def main() -> None:
    report: dict = {}
    try:
        report["build"] = build_project()
        proj = report["build"]["project"]["proj"]
        report["experiment"] = gui.experiment(proj, TEST, restart=True, timeout_s=240)
        vcds = report["experiment"].get("vcd") or []
        if vcds:
            vcd = vcds[0]
            report["vcd_abs"] = vcd
            report["waveform"] = tools.read_waveform(vcd)
            report["values"] = tools.read_waveform(
                vcd, signals=["tb_and_gate.a", "tb_and_gate.b", "tb_and_gate.dut.y"], time=25
            )
            report["events"] = tools.find_events(vcd, "tb_and_gate.dut.y", "== 1'b1", limit=5)
    except Exception:
        report["fatal"] = traceback.format_exc()[-3000:]
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
