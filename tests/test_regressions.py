"""Regression tests for the 1.0.2 QA findings.

No Robei, GUI, Yosys or Verilator is required.  Icarus is only used by the
portable suite; everything here is parser/VCD/security/scaffolding logic.
"""
from __future__ import annotations

import asyncio
import hashlib
import shutil
import tempfile
import uuid
from pathlib import Path

import pytest

from robei_mcp import constraints, tools
from robei_mcp.config import SETTINGS
from robei_mcp.security import scan_source_paths
from robei_mcp.vcd import VcdParseError, parse_vcd, timescale_fs
from robei_mcp.verilog import generate_testbench, parse_module_ports, width_of
from robei_mcp.verilog_ast import parse_module
from robei_mcp import p1


@pytest.fixture()
def work() -> Path:
    root = SETTINGS.workspace / ".tmp" / f"pytest-{uuid.uuid4().hex[:8]}"
    root.mkdir(parents=True, exist_ok=False)
    yield root
    shutil.rmtree(root, ignore_errors=True)


def _vcd(scope: str = "qa", timescale: str = "1ns") -> str:
    return (
        f"$date QA $end\n$timescale {timescale} $end\n"
        f"$scope module {scope} $end\n"
        "$var wire 1 ! a $end\n$var wire 4 v bus [3:0] $end\n"
        "$upscope $end\n$enddefinitions $end\n"
        "#0\n0!\nb0000 v\n#10\n1!\nb1111 v\n#20\n0!\nb0010 v\n#30\n"
    )


def test_ansi_shorthand_ports() -> None:
    source = "module m(input wire [3:0] a, b, c, output wire [1:0] y); endmodule"
    ports = parse_module_ports(source, "m")
    assert [(p["name"], p["direction"], p["width"]) for p in ports] == [
        ("a", "input", "3:0"),
        ("b", "input", "3:0"),
        ("c", "input", "3:0"),
        ("y", "output", "1:0"),
    ]


def test_non_ansi_ports_and_width() -> None:
    source = (
        "module m(a,y);\n input wire [3:0] a;\n output reg y;\n"
        " assign y = a[0];\nendmodule\n"
    )
    ports = parse_module_ports(source, "m")
    assert [p["name"] for p in ports] == ["a", "y"]
    assert ports[0]["width"] == "3:0"
    assert width_of("3:0") == 4
    assert width_of("[3:0]") == 4
    assert width_of("") == 1


def test_width_of_folds_constant_expressions() -> None:
    assert width_of("4-1:0") == 4
    assert width_of("[4-1:0]") == 4
    assert width_of("3+1:0") == 5
    assert width_of("2*4-1:0") == 8
    # Anything that is not plain arithmetic must fall back to a single bit.
    assert width_of("WIDTH-1:0") == 1
    assert width_of("4-1:") == 1
    assert width_of("(4-1:0") == 1


def test_model_range_suffix_emits_literal_ranges() -> None:
    from robei_mcp.model import _range_suffix

    assert _range_suffix("4") == " [3:0]"
    assert _range_suffix("1") == ""
    assert _range_suffix("0") == ""
    assert _range_suffix("") == ""
    assert _range_suffix("3:0") == " [3:0]"
    # Datasize stores the width, so "4-1" means a 3-bit port.
    assert _range_suffix("4-1") == " [2:0]"
    assert _range_suffix("WIDTH") == " [WIDTH-1:0]"


def test_non_ansi_ports_on_one_line_are_scoped_per_module() -> None:
    source = (
        "module a(x,y); input x; output y; assign y=x; endmodule\n"
        "module b(p,q); input p; output q; assign q=p; endmodule\n"
    )
    assert [p["name"] for p in parse_module_ports(source, "a")] == ["x", "y"]
    assert [p["name"] for p in parse_module_ports(source, "b")] == ["p", "q"]


def test_instances_on_the_same_line() -> None:
    source = (
        "module top(input wire a, output wire y);\n"
        " wire t;\n"
        " and2 u1(.a(a),.y(t)); and2 u2(.a(t),.y(y));\n"
        "endmodule\n"
    )
    module = parse_module(source, "top")
    assert [i["instance"] for i in module["instances"]] == ["u1", "u2"]
    assert module["instances"][1]["connections"][0] == {"port": "a", "expr": "t"}


def test_full_combo_counts_bits_not_ports() -> None:
    ports = [
        {"name": "a", "direction": "input", "datatype": "wire", "width": "3:0"},
        {"name": "y", "direction": "output", "datatype": "wire", "width": "3:0"},
    ]
    tb = generate_testbench("bus4", ports, full_combo=True, max_combo_bits=8)
    assert tb.count('$display("combo ') == 16
    with pytest.raises(ValueError, match="max_combo_bits"):
        generate_testbench("bus4", ports, full_combo=True, max_combo_bits=3)


@pytest.mark.skipif(not Path(SETTINGS.iverilog).exists(), reason="iverilog not installed")
def test_truth_table_packs_mixed_width_inputs(work: Path) -> None:
    source = work / "mix.v"
    source.write_text(
        "module mix(input wire [1:0] a, input wire [3:0] b, output wire [5:0] y);\n"
        "  assign y = {b, a};\nendmodule\n",
        encoding="utf-8",
    )
    table = p1.truth_table(str(source), "mix", max_inputs=8)
    assert table["row_count"] == 64
    assert table["rows"][0] == {"a": "00", "b": "0000", "y": "000000"}
    # 6 bits: the last port (b) holds the lowest 4 bits of the counter, a the
    # upper 2.  y = {b, a}, so b=0001 and a=00 prints as 000100.
    assert table["rows"][1] == {"a": "00", "b": "0001", "y": "000100"}
    assert table["rows"][16] == {"a": "01", "b": "0000", "y": "000001"}
    assert table["rows"][-1] == {"a": "11", "b": "1111", "y": "111111"}


def test_vcd_rejects_non_vcd_input(work: Path) -> None:
    empty = work / "empty.vcd"
    empty.write_text("", encoding="utf-8")
    junk = work / "junk.vcd"
    junk.write_text("not a VCD file\n", encoding="utf-8")
    with pytest.raises(VcdParseError):
        parse_vcd(empty)
    with pytest.raises(VcdParseError):
        parse_vcd(junk)


def test_timescale_conversion() -> None:
    assert timescale_fs("1ns") == 1_000_000
    assert timescale_fs("10 ps") == 10_000
    assert timescale_fs("nonsense") is None


def test_compare_same_and_different(work: Path) -> None:
    golden = work / "golden.vcd"
    same = work / "same.vcd"
    different = work / "different.vcd"
    golden.write_text(_vcd(), encoding="utf-8")
    same.write_text(_vcd(), encoding="utf-8")
    different.write_text(_vcd().replace("#10\n1!", "#10\n0!"), encoding="utf-8")

    ok = tools.compare_waveforms(str(golden), str(same))
    assert ok["ok"] is True, ok
    assert ok["mismatch_total"] == 0

    bad = tools.compare_waveforms(str(golden), str(different))
    assert bad["ok"] is False
    assert bad["mismatch_total"] == 1
    assert bad["differing_signals"] == ["qa.a"]


def test_compare_selected_signals_no_longer_crashes(work: Path) -> None:
    golden = work / "golden.vcd"
    same = work / "same.vcd"
    golden.write_text(_vcd(), encoding="utf-8")
    same.write_text(_vcd(), encoding="utf-8")
    result = tools.compare_waveforms(str(golden), str(same), signals=["qa.a"])
    assert result["ok"] is True
    assert result["compared_signal_count"] == 1
    assert [row["signal"] for row in result["results"]] == ["qa.a"]


def test_compare_missing_signal_is_not_a_pass(work: Path) -> None:
    golden = work / "golden.vcd"
    trimmed = work / "trimmed.vcd"
    golden.write_text(_vcd(), encoding="utf-8")
    trimmed.write_text(
        "\n".join(
            line for line in _vcd().splitlines()
            if "wire 4" not in line and not line.startswith("b")
        ) + "\n",
        encoding="utf-8",
    )
    result = tools.compare_waveforms(str(golden), str(trimmed))
    assert result["ok"] is False
    assert result["missing_in_dut"] == ["qa.bus"]
    assert "missing in dut" in " ".join(result["reasons"])


def test_compare_no_common_signal_is_not_a_pass(work: Path) -> None:
    golden = work / "golden.vcd"
    other = work / "other.vcd"
    golden.write_text(_vcd(), encoding="utf-8")
    other.write_text(_vcd(scope="other"), encoding="utf-8")
    result = tools.compare_waveforms(str(golden), str(other))
    assert result["ok"] is False
    assert "no comparable signals" in result["reasons"]


def test_compare_timescale_and_width(work: Path) -> None:
    golden = work / "golden.vcd"
    ps = work / "ps.vcd"
    wide = work / "wide.vcd"
    golden.write_text(_vcd(), encoding="utf-8")
    ps.write_text(_vcd(timescale="1ps"), encoding="utf-8")
    wide.write_text(_vcd().replace("wire 4 v bus [3:0]", "wire 8 v bus [7:0]"), encoding="utf-8")

    scaled = tools.compare_waveforms(str(golden), str(ps))
    assert scaled["ok"] is False
    assert scaled["timescale_comparable"] is True

    widths = tools.compare_waveforms(str(golden), str(wide))
    assert widths["ok"] is False
    assert widths["incompatible_signals"] == ["qa.bus"]


def test_compare_max_time_mismatch(work: Path) -> None:
    golden = work / "golden.vcd"
    short = work / "short.vcd"
    golden.write_text(_vcd(), encoding="utf-8")
    short.write_text(_vcd().split("#30")[0], encoding="utf-8")
    result = tools.compare_waveforms(str(golden), str(short))
    assert result["ok"] is False
    assert result["max_time_matches"] is False


def test_backups_do_not_collide(work: Path) -> None:
    name = f"regress_{uuid.uuid4().hex[:8]}"
    project = tools.create_robei_project(name, models=[])
    try:
        model = tools.create_robei_model(
            proj_path=project["proj"],
            name="m",
            ports=[
                {"name": "a", "direction": "input"},
                {"name": "y", "direction": "output"},
            ],
            code="assign y = a;",
        )
        first = tools.set_port(model_path=model["path"], name="y", width=2)
        second = tools.set_port(model_path=model["path"], name="y", width=1)
        assert first["backup"] != second["backup"]
        assert hashlib.sha256(Path(first["backup"]).read_bytes()).hexdigest() == first["sha256_before"]
        assert hashlib.sha256(Path(second["backup"]).read_bytes()).hexdigest() == second["sha256_before"]
    finally:
        shutil.rmtree(SETTINGS.workspace / name, ignore_errors=True)


def test_strict_scan_rejects_dynamic_and_transitive_paths(work: Path) -> None:
    outside = Path(tempfile.gettempdir()) / f"robei-regress-{uuid.uuid4().hex[:6]}.hex"
    outside.write_text("5a\n", encoding="ascii")
    try:
        dynamic = work / "dynamic.v"
        dynamic.write_text(
            "module d; reg [7:0] m[0:0]; initial $readmemh(PATH, m); endmodule\n",
            encoding="utf-8",
        )
        strict = scan_source_paths([dynamic])
        assert strict["ok"] is False
        assert strict["unresolved"] and strict["rejected"]

        loose = scan_source_paths([dynamic], strict=False)
        assert loose["ok"] is True
        assert loose["unresolved"]

        header = work / "evil.vh"
        header.write_text(
            f'initial begin $readmemh("{outside.as_posix()}", mem); end\n',
            encoding="utf-8",
        )
        top = work / "nested.v"
        top.write_text(
            'module n; reg [7:0] mem[0:0];\n`include "evil.vh"\nendmodule\n',
            encoding="utf-8",
        )
        nested = scan_source_paths([top])
        assert nested["ok"] is False
        assert any(item["kind"] == "include" for item in nested["findings"])
        assert any(item["kind"] == "$readmemh" for item in nested["rejected"])
    finally:
        outside.unlink(missing_ok=True)


def test_simulate_rejects_dynamic_path_before_compile(work: Path) -> None:
    source = work / "dynamic.v"
    source.write_text(
        "module d; reg [7:0] m[0:0]; initial $readmemh(PATH, m); endmodule\n",
        encoding="utf-8",
    )
    result = tools.simulate([str(source)], "d", timeout_s=5)
    assert result["ok"] is False
    assert result["stage"] == "security_scan"
    assert result["security"]["strict"] is True


def test_duplicate_pins_and_tcl_quoting() -> None:
    name = f"regress_{uuid.uuid4().hex[:8]}"
    project = tools.create_robei_project(name, models=[])
    try:
        tools.create_robei_model(
            proj_path=project["proj"],
            name="light",
            ports=[
                {"name": "swt", "direction": "input", "width": 4},
                {"name": "led", "direction": "output", "width": 4},
            ],
            code="assign led = ~swt;",
        )
        with pytest.raises(ValueError, match="duplicate FPGA pin"):
            constraints.create_const(
                project["proj"],
                "bad",
                "light",
                {
                    "swt": ["G15", "G15", "W13", "T16"],
                    "led": ["M14", "M15", "G14", "D18"],
                },
            )
        pins = {
            "swt": ["G15", "P15", "W13", "T16"],
            "led": ["M14", "M15", "G14", "D18"],
        }
        constraints.create_const(project["proj"], "ok", "light", pins)
        standard = constraints.export_xdc(project["proj"], "ok")
        text = Path(standard["path"]).read_text(encoding="utf-8")
        assert "[get_ports {swt[0]}]" in text
        legacy = constraints.export_xdc(project["proj"], "ok", out_name="legacy", quote_bus=False)
        legacy_text = Path(legacy["path"]).read_text(encoding="utf-8")
        assert "[get_ports swt[0]]" in legacy_text
    finally:
        shutil.rmtree(SETTINGS.workspace / name, ignore_errors=True)


def test_progress_is_monotonic() -> None:
    from robei_mcp import server_app

    events: list[tuple[float, float, str]] = []

    class Ctx:
        async def report_progress(self, done, total, message):
            events.append((done, total, message))

    def fn(progress=None):
        progress(0, 2, "compile")
        progress(1, 2, "run")
        progress(2, 2, "finished")
        return {"ok": True}

    result = asyncio.run(server_app._invoke(fn, Ctx(), "simulate"))
    assert result == {"ok": True}
    assert events[0] == (0.0, 1.0, "simulate: started")
    dones = [item[0] for item in events]
    totals = [item[1] for item in events]
    assert dones == sorted(dones)
    assert totals == sorted(totals)
    assert events[-1][0] == events[-1][1]


def test_console_entry_point_exists() -> None:
    from robei_mcp.__main__ import main

    assert callable(main)
