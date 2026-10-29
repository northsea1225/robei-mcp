from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

import pytest

from robei_mcp import fpga, lifecycle as lc, p1, tools
from robei_mcp.config import SETTINGS


@pytest.fixture()
def project():
    name = f"pytest17_{uuid.uuid4().hex[:8]}"
    proj = Path(tools.create_robei_project(name)["proj"])
    yield proj
    shutil.rmtree(SETTINGS.workspace / name, ignore_errors=True)


def _vcd(project: Path) -> Path:
    path = project.parent / "build" / "sim-fixture" / "tb.vcd"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "$timescale 1ps $end\n$scope module tb $end\n"
        "$var wire 1 ! clk $end\n$var wire 1 \" en $end\n"
        "$var wire 4 # cnt [3:0] $end\n$upscope $end\n"
        "$enddefinitions $end\n#0\n0!\n1\"\nb1001 #\n"
        "#100000\n1!\n#105000\n0!\nb0000 #\n",
        encoding="utf-8",
    )
    return path


def test_waveform_stats_uses_timescale_fs_for_1ps() -> None:
    root = SETTINGS.workspace / ".tmp" / f"stats17-{uuid.uuid4().hex[:8]}"
    root.mkdir(parents=True, exist_ok=False)
    path = root / "one_ps.vcd"
    try:
        path.write_text(
            "$timescale 1ps $end\n$scope module tb $end\n"
            "$var wire 1 ! clk $end\n$upscope $end\n$enddefinitions $end\n"
            "#0\n0!\n#5000\n1!\n#10000\n0!\n#15000\n1!\n#20000\n0!\n#25000\n1!\n#30000\n0!\n",
            encoding="utf-8",
        )
        result = p1.waveform_stats(str(path.relative_to(SETTINGS.workspace)).replace("\\", "/"))
        assert result["timescale_fs"] == 1_000
        assert result["clock"]["period"] == 10_000
        assert result["clock"]["frequency_mhz"] == 100.0
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_lifecycle_status_exposes_current_run_id(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "lifecycle.log"
    monkeypatch.setattr(lc, "log_path", lambda: path)
    monkeypatch.setattr(lc, "_ACTIVE", lc.Session("current-17"))
    lc.event("start", pid=1234, run_id="previous-17")
    lc.event("clean_exit", pid=1234, run_id="previous-17")
    lc.event("start", pid=5678, run_id="current-17")
    result = lc.status()
    assert result["current_run_id"] == "current-17"
    assert result["previous_exit"]["run_id"] == "previous-17"


def test_export_vcd_window_edges_and_alias(project: Path) -> None:
    source = _vcd(project)
    result = tools.export_vcd_window(
        vcd=str(source.relative_to(SETTINGS.workspace)).replace("\\", "/"),
        signals=["cnt", "tb.clk"],
        t_start=95_000,
        t_end=105_000,
        format="csv",
    )
    assert result["ok"] is True
    assert result["timescale"] == "1ps"
    text = Path(result["path"]).read_text(encoding="utf-8")
    assert "95000" in text and "105000" in text
    assert "1001" in text and "0000" in text
    with pytest.raises(FileExistsError):
        tools.export_vcd_window(
            vcd=str(source.relative_to(SETTINGS.workspace)).replace("\\", "/"),
            signals=["cnt"], t_start=95_000, t_end=105_000,
            format="csv", out=Path(result["path"]).name,
        )


def test_build_report_writes_report_and_evidence(project: Path) -> None:
    tools.create_robei_model(
        proj_path=str(project),
        name="and2",
        ports=[
            {"name": "a", "direction": "input", "width": 1},
            {"name": "b", "direction": "input", "width": 1},
            {"name": "y", "direction": "output", "width": 1},
        ],
        code="assign y = a & b;",
    )
    tools.create_robei_test(
        proj_path=str(project),
        name="tb_and2",
        code=(
            "reg a, b; wire y; and2 dut(.a(a), .b(b), .y(y));\n"
            "initial begin a=0; b=0; #1; $display(\"SELFTEST PASS\"); $finish; end"
        ),
        timescale="1ns/1ns",
    )
    relative_v = str(project.parent.relative_to(SETTINGS.workspace) / "verilog" / "and2.v")
    report = tools.build_report(
        str(project),
        include=["simulation", "display", "assertions", "truth_table"],
        truth_map={"tb_and2": {"top": "and2", "verilog_path": relative_v}},
    )
    assert report["ok"] is True
    assert Path(report["report_path"]).is_file()
    assert Path(report["evidence_path"]).is_file()
    text = Path(report["report_path"]).read_text(encoding="utf-8")
    assert "SELFTEST PASS" in text
    assert "Evidence:" in text
    with pytest.raises(FileExistsError):
        tools.build_report(str(project), include=["simulation"])


def test_capacity_parser_is_conservative(tmp_path: Path) -> None:
    path = tmp_path / "part.yaml"
    path.write_text("resources:\n  LUT: 100\n  FF: 200\n", encoding="utf-8")
    capacity, source, warnings = fpga._capacity_from_part_yaml(path)
    assert capacity == {"LUT": 100, "FF": 200}
    assert source["lines"]["LUT"] == 2
    assert warnings == []
    assert fpga._utilization_percent({"LUT": 10, "FF": 20}, capacity) == {"LUT": 10.0, "FF": 10.0}


def test_lifecycle_status_keeps_current_run_id_on_read_failure(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(lc, "log_path", lambda: tmp_path / "missing.log")
    monkeypatch.setattr(lc, "_ACTIVE", lc.Session("current-error-17"))
    monkeypatch.setattr(lc, "read_events", lambda: (_ for _ in ()).throw(PermissionError()))
    result = lc.status()
    assert result["available"] is False
    assert result["current_run_id"] == "current-error-17"
