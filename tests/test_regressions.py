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
from robei_mcp.model import model_to_verilog, parse_robei_model


@pytest.fixture()
def work() -> Path:
    root = SETTINGS.workspace / ".tmp" / f"pytest-{uuid.uuid4().hex[:8]}"
    root.mkdir(parents=True, exist_ok=False)
    yield root
    shutil.rmtree(root, ignore_errors=True)


def _vcd(scope: str = "qa", timescale: str = "1ns", end: int = 30, extra: bool = False) -> str:
    extra_var = "$var wire 1 w extra $end\n" if extra else ""
    extra_change = "#5\n1w\n" if extra else ""
    return (
        f"$date QA $end\n$timescale {timescale} $end\n"
        f"$scope module {scope} $end\n"
        "$var wire 1 ! a $end\n$var wire 4 v bus [3:0] $end\n"
        f"{extra_var}"
        "$upscope $end\n$enddefinitions $end\n"
        f"#0\n0!\nb0000 v\n{extra_change}#10\n1!\nb1111 v\n#20\n0!\nb0010 v\n#{end}\n"
    )


def _vcd_static(timescale: str, end: int) -> str:
    """One signal that never changes: isolates the run-length comparison."""
    return (
        f"$timescale {timescale} $end\n$scope module qa $end\n"
        "$var wire 1 ! a $end\n$upscope $end\n$enddefinitions $end\n"
        f"#0\n1!\n#{end}\n"
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


def test_const_eval_uses_verilog_integer_semantics() -> None:
    from robei_mcp.verilog import const_eval

    assert const_eval("4-1") == 3
    assert const_eval("-1/2") == 0      # truncates toward zero, not floor
    assert const_eval("-1/2*2") == 0
    assert const_eval("-3/2") == -1
    assert const_eval("-1%2") == -1     # remainder keeps the dividend's sign
    assert const_eval("1/0") is None


def test_parameterised_widths_are_not_silently_one_bit() -> None:
    from robei_mcp.verilog import width_info

    info = width_info("LEN-1:0")
    assert info["resolved"] is False
    assert info["width"] is None
    assert width_info("4-1:0")["width"] == 4
    ports = [
        {"name": "a", "direction": "input", "datatype": "wire", "width": "LEN-1:0"},
        {"name": "y", "direction": "output", "datatype": "wire", "width": "LEN-1:0"},
    ]
    with pytest.raises(ValueError, match="parameterised width"):
        generate_testbench("param", ports, full_combo=True)


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


def test_parameterised_header_on_one_line() -> None:
    source = (
        "module m #(parameter WIDTH=4)"
        "(input [WIDTH-1:0] a, output [WIDTH-1:0] y);\n"
        "  assign y = a;\nendmodule\n"
    )
    ports = parse_module_ports(source, "m")
    assert [(p["name"], p["direction"], p["width"]) for p in ports] == [
        ("a", "input", "WIDTH-1:0"),
        ("y", "output", "WIDTH-1:0"),
    ]
    module = parse_module(source, "m")
    assert [p["name"] for p in module["ports"]] == ["a", "y"]


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


def test_compare_same_length_across_timescales(work: Path) -> None:
    """#20 at 1ns and #20000 at 1ps are the same instant and must not fail."""
    golden = work / "golden.vcd"
    pico = work / "pico.vcd"
    golden.write_text(_vcd_static("1ns", 20), encoding="utf-8")
    pico.write_text(_vcd_static("1ps", 20_000), encoding="utf-8")
    result = tools.compare_waveforms(str(golden), str(pico))
    assert result["ok"] is True, result["reasons"]
    assert result["max_time_matches"] is True
    assert result["max_time_fs"] == {"golden": 20_000_000, "dut": 20_000_000}
    assert result["mismatch_total"] == 0


def test_compare_reports_unknown_requested_signals(work: Path) -> None:
    golden = work / "golden.vcd"
    same = work / "same.vcd"
    golden.write_text(_vcd(), encoding="utf-8")
    same.write_text(_vcd(), encoding="utf-8")
    result = tools.compare_waveforms(str(golden), str(same), signals=["does_not_exist"])
    assert result["ok"] is False
    assert result["unknown_signals"] == ["does_not_exist"]
    assert "not found" in " ".join(result["reasons"])


def test_compare_can_ignore_one_sided_internal_signals(work: Path) -> None:
    golden = work / "golden.vcd"
    dut = work / "dut.vcd"
    golden.write_text(_vcd(), encoding="utf-8")
    dut.write_text(_vcd(extra=True), encoding="utf-8")
    strict = tools.compare_waveforms(str(golden), str(dut))
    assert strict["ok"] is False
    assert strict["missing_in_golden"] == ["qa.extra"]
    loose = tools.compare_waveforms(str(golden), str(dut), allow_extra_signals=True)
    assert loose["ok"] is True, loose["reasons"]
    assert loose["missing_in_golden"] == ["qa.extra"]


def test_workspace_prefix_is_not_doubled(work: Path) -> None:
    from robei_mcp.security import resolve_workspace_path

    source = work / "bus.v"
    source.write_text("module bus; endmodule\n", encoding="utf-8")
    relative = source.relative_to(SETTINGS.workspace)
    assert resolve_workspace_path(str(relative)) == source
    assert resolve_workspace_path(f"workspace/{relative.as_posix()}") == source
    # Creation paths follow the same rule instead of nesting workspace/workspace.
    fresh = f"workspace/{relative.parent.as_posix()}/new-model.model"
    assert resolve_workspace_path(fresh, must_exist=False) == source.parent / "new-model.model"


def test_workspace_prefix_strips_before_the_directory_exists(work: Path) -> None:
    from robei_mcp.security import resolve_workspace_path

    relative = work.relative_to(SETTINGS.workspace)
    fresh = f"workspace/{relative.as_posix()}/brand-new/new.v"
    resolved = resolve_workspace_path(fresh, must_exist=False)
    assert resolved == work / "brand-new" / "new.v"
    assert resolved.relative_to(SETTINGS.workspace).parts[0] != "workspace"


def test_workspace_prefix_accepts_windows_separators(work: Path) -> None:
    from robei_mcp.security import resolve_workspace_path

    relative = work.relative_to(SETTINGS.workspace)
    raw = "Workspace\\" + str(relative).replace("/", "\\") + "\\new.v"
    assert resolve_workspace_path(raw, must_exist=False) == work / "new.v"


def test_workspace_prefix_cannot_escape(work: Path) -> None:
    from robei_mcp.security import resolve_workspace_path

    with pytest.raises(ValueError, match="escapes workspace"):
        resolve_workspace_path("workspace/../outside.v", must_exist=False)


def test_nested_workspace_directory_is_ambiguous(work: Path) -> None:
    from robei_mcp.security import resolve_workspace_path

    relative = work.relative_to(SETTINGS.workspace)
    nested_root = SETTINGS.workspace / "workspace"
    created_root = not nested_root.exists()
    nested = nested_root / relative / "amb"
    try:
        nested.mkdir(parents=True, exist_ok=True)
        (nested / "same.v").write_text("module same; endmodule\n", encoding="utf-8")
        (work / "amb").mkdir(exist_ok=True)
        (work / "amb" / "same.v").write_text("module same; endmodule\n", encoding="utf-8")
        with pytest.raises(ValueError, match="ambiguous path"):
            resolve_workspace_path(f"workspace/{relative.as_posix()}/amb/same.v")
    finally:
        shutil.rmtree(nested_root / relative, ignore_errors=True)
        if created_root:
            shutil.rmtree(nested_root, ignore_errors=True)


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
    # The decisive lists must also be visible at the top level.
    assert result["rejected"] and result["rejected"] == result["security"]["rejected"]
    assert isinstance(result["unresolved"], list)
    assert result["strict"] is True


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
        assert legacy["tcl_quoting"] == "legacy"
        assert standard["tcl_quoting"] == "standard"
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


# ---------------------------------------------------------------------------
# 1.0.3 — hierarchical export, path codes, QA filtering, constraints
# ---------------------------------------------------------------------------


def _param_model_xml(name: str, parameters: str, ports: list[tuple[str, str, str]], code: str) -> str:
    lines = [
        "<!DOCTYPE Robei>",
        f'<Module Category="Current" Class="module" Code="{code}" Comment="" Height="600" '
        f'Name="{name}" Parameters="{parameters}" Parent="0" Type="model" Width="900" X="0" Y="0">',
    ]
    for index, (port_name, direction, width) in enumerate(ports):
        lines.append(
            f' <Port Color="#faebd7" Connect="" Datasize="{width}" Datatype="wire" '
            f'Description="" Height="30" Inout="{direction}" Name="{port_name}" Parent="{name}" '
            f'Side="left" Width="30" X="-0.02" Y="{0.2 + index * 0.2:.3f}"/>'
        )
    lines.append("</Module>")
    return "\n".join(lines) + "\n"


@pytest.mark.skipif(not Path(SETTINGS.iverilog).exists(), reason="iverilog not installed")
def test_canvas_bus_chain_keeps_width_and_simulates(work: Path) -> None:
    name = f"regress_chain_{uuid.uuid4().hex[:8]}"
    project = tools.create_robei_project(name, models=[])
    try:
        proj = project["proj"]
        tools.create_robei_model(
            proj_path=proj,
            name="buf4",
            ports=[
                {"name": "a", "direction": "input", "width": 4},
                {"name": "y", "direction": "output", "width": 4},
            ],
            code="assign y = a;",
        )
        canvas = tools.create_robei_canvas_model(
            proj_path=proj,
            name="chain",
            ports=[
                {"name": "a", "direction": "input", "width": 4},
                {"name": "y", "direction": "output", "width": 4},
            ],
            instances=[{"name": "u1", "model": "buf4"}, {"name": "u2", "model": "buf4"}],
            wires=[
                {"from": "a", "to": "u1.a", "net": "a_n", "datasize": 4},
                {"from": "u1.y", "to": "u2.a", "net": "mid", "datasize": 4},
                {"from": "u2.y", "to": "y", "net": "y_n", "datasize": 4},
            ],
        )
        verilog = model_to_verilog(parse_robei_model(Path(canvas["path"])))
        assert "wire [3:0] mid;" in verilog
        assert "buf4 u1(" in verilog and "buf4 u2(" in verilog

        dut = work / "chain.v"
        dut.write_text(verilog, encoding="utf-8")
        child = model_to_verilog(parse_robei_model(Path(canvas["path"]).parent / "buf4.model"))
        child_path = work / "buf4.v"
        child_path.write_text(child, encoding="utf-8")
        tb = work / "tb.v"
        tb.write_text(
            "module tb;\n"
            "  reg [3:0] a;\n  wire [3:0] y;\n"
            "  chain dut(.a(a), .y(y));\n"
            "  initial begin\n"
            "    a = 4'b1011; #10;\n"
            '    $display("Y=%b", y);\n'
            '    if (y !== 4\'b1011) $fatal(1, "MISMATCH");\n'
            "    $finish;\n"
            "  end\n"
            "endmodule\n",
            encoding="utf-8",
        )
        sim = tools.simulate([str(child_path), str(dut), str(tb)], "tb", timeout_s=30)
        assert sim["ok"] is True, sim
        assert "Y=1011" in (sim.get("display_output") or "")
    finally:
        shutil.rmtree(SETTINGS.workspace / name, ignore_errors=True)


def test_canvas_mixes_single_bit_and_bus_wires(work: Path) -> None:
    name = f"regress_mixw_{uuid.uuid4().hex[:8]}"
    project = tools.create_robei_project(name, models=[])
    try:
        proj = project["proj"]
        tools.create_robei_model(
            proj_path=proj, name="buf1",
            ports=[{"name": "a", "direction": "input"}, {"name": "y", "direction": "output"}],
            code="assign y = a;",
        )
        tools.create_robei_model(
            proj_path=proj, name="buf4",
            ports=[{"name": "a", "direction": "input", "width": 4},
                   {"name": "y", "direction": "output", "width": 4}],
            code="assign y = a;",
        )
        canvas = tools.create_robei_canvas_model(
            proj_path=proj, name="mix",
            ports=[
                {"name": "a", "direction": "input", "width": 4},
                {"name": "y", "direction": "output", "width": 4},
                {"name": "bit", "direction": "input"},
                {"name": "bout", "direction": "output"},
            ],
            instances=[
                {"name": "u1", "model": "buf4"}, {"name": "u2", "model": "buf4"},
                {"name": "u3", "model": "buf1"}, {"name": "u4", "model": "buf1"},
            ],
            wires=[
                {"from": "a", "to": "u1.a", "net": "a_n", "datasize": 4},
                {"from": "u1.y", "to": "u2.a", "net": "mid", "datasize": 4},
                {"from": "u2.y", "to": "y", "net": "y_n", "datasize": 4},
                {"from": "bit", "to": "u3.a", "net": "bit_n", "datasize": 1},
                {"from": "u3.y", "to": "u4.a", "net": "t1", "datasize": 1},
                {"from": "u4.y", "to": "bout", "net": "bout_n", "datasize": 1},
            ],
        )
        verilog = model_to_verilog(parse_robei_model(Path(canvas["path"])))
        assert "wire [3:0] mid;" in verilog
        assert "wire t1;" in verilog
        assert "wire [3:0] t1;" not in verilog
    finally:
        shutil.rmtree(SETTINGS.workspace / name, ignore_errors=True)


def test_instance_parameter_overrides_are_emitted(work: Path) -> None:
    name = f"regress_param_{uuid.uuid4().hex[:8]}"
    project = tools.create_robei_project(name, models=[])
    try:
        proj = Path(project["proj"])
        model_dir = proj.parent / "model"
        (model_dir / "pw.model").write_text(
            _param_model_xml(
                "pw",
                "W = 1; // width",
                [("a", "input", "W-1:0"), ("y", "output", "W-1:0")],
                "assign y = a;",
            ),
            encoding="utf-8",
        )
        tools._register_model_in_project(proj, "pw")

        override = tools.create_robei_canvas_model(
            proj_path=str(proj), name="top_override",
            ports=[{"name": "a", "direction": "input", "width": 4},
                   {"name": "y", "direction": "output", "width": 4}],
            instances=[{"name": "u1", "model": "pw", "parameters": "W = 4;"}],
            wires=[{"from": "a", "to": "u1.a", "net": "a_n", "datasize": 4},
                   {"from": "u1.y", "to": "y", "net": "y_n", "datasize": 4}],
        )
        text = model_to_verilog(parse_robei_model(Path(override["path"])))
        assert "pw #(.W(4)) u1(" in text
        assert parse_robei_model(Path(override["path"]))["instances"][0]["parameters"] == "W = 4;"

        same_default = tools.create_robei_canvas_model(
            proj_path=str(proj), name="top_default",
            ports=[{"name": "a", "direction": "input"},
                   {"name": "y", "direction": "output"}],
            instances=[{"name": "u1", "model": "pw", "parameters": "W = 1;"}],
            wires=[{"from": "a", "to": "u1.a", "net": "a_n", "datasize": 1},
                   {"from": "u1.y", "to": "y", "net": "y_n", "datasize": 1}],
        )
        plain = model_to_verilog(parse_robei_model(Path(same_default["path"])))
        assert "pw u1(" in plain
        assert "#(" not in plain
    finally:
        shutil.rmtree(SETTINGS.workspace / name, ignore_errors=True)


def test_malformed_instance_parameters_warn_and_emit_nothing(work: Path) -> None:
    from robei_mcp.model import model_to_verilog_with_warnings

    name = f"regress_badparam_{uuid.uuid4().hex[:8]}"
    project = tools.create_robei_project(name, models=[])
    try:
        proj = Path(project["proj"])
        model_dir = proj.parent / "model"
        (model_dir / "pw.model").write_text(
            _param_model_xml(
                "pw",
                "W = 1; // width",
                [("a", "input", "W-1:0"), ("y", "output", "W-1:0")],
                "assign y = a;",
            ),
            encoding="utf-8",
        )
        tools._register_model_in_project(proj, "pw")
        canvas = tools.create_robei_canvas_model(
            proj_path=str(proj), name="top_bad",
            ports=[{"name": "a", "direction": "input", "width": 4},
                   {"name": "y", "direction": "output", "width": 4}],
            instances=[{"name": "u1", "model": "pw", "parameters": "W = ;"}],
            wires=[{"from": "a", "to": "u1.a", "net": "a_n", "datasize": 4},
                   {"from": "u1.y", "to": "y", "net": "y_n", "datasize": 4}],
        )
        text, warnings = model_to_verilog_with_warnings(parse_robei_model(Path(canvas["path"])))
        assert "#(" not in text
        assert warnings and "cannot parse Parameters" in warnings[0]

        synced = tools.sync_robei_verilog(str(proj), overwrite=True)
        assert synced["ok"] is True
        assert any("cannot parse Parameters" in item for item in synced["warnings"])
        exported = (proj.parent / "verilog" / "top_bad.v").read_text(encoding="utf-8")
        assert "#(" not in exported
    finally:
        shutil.rmtree(SETTINGS.workspace / name, ignore_errors=True)


def test_path_escape_has_its_own_code() -> None:
    from robei_mcp.errors import PathEscape, code_for
    from robei_mcp.security import resolve_workspace_path

    with pytest.raises(PathEscape):
        resolve_workspace_path("../outside.v", must_exist=False)
    assert code_for(PathEscape("x")) == "path_escape"


def test_bare_workspace_resolves_to_the_root() -> None:
    from robei_mcp.security import resolve_workspace_path

    assert resolve_workspace_path("workspace", must_exist=False) == SETTINGS.workspace
    assert resolve_workspace_path("Workspace", must_exist=False) == SETTINGS.workspace


def test_model_roots_are_deduplicated() -> None:
    from robei_mcp.config import _dedupe_paths
    from robei_mcp.security import _unique_roots

    workspace = SETTINGS.workspace
    assert len(_dedupe_paths([workspace, workspace, workspace / "."])) == 1
    combined = _unique_roots((workspace, workspace, *SETTINGS.model_roots))
    assert len(combined) == len({str(item).lower() for item in combined})


def test_modules_without_ports_parse_consistently() -> None:
    assert parse_module_ports("module m; endmodule", "m") == []
    assert parse_module_ports("module m(); endmodule", "m") == []
    assert parse_module("module m; endmodule", "m")["ports"] == []
    with pytest.raises(ValueError, match="not found"):
        parse_module_ports("module n; endmodule", "m")


def test_list_projects_skips_qa_trees() -> None:
    tag = uuid.uuid4().hex[:8]
    qa_dir = SETTINGS.workspace / f"qa_regress_{tag}"
    runs_dir = SETTINGS.workspace / "_qa_runs" / f"regress_{tag}"
    qa_dir.mkdir(parents=True)
    runs_dir.mkdir(parents=True)
    try:
        (qa_dir / f"qa_regress_{tag}.proj").write_text(
            f'<qa_regress_{tag} name="qa_regress_{tag}" parent="0" type="proj"/>\n',
            encoding="utf-8",
        )
        (runs_dir / f"runs_{tag}.proj").write_text(
            f'<runs_{tag} name="runs_{tag}" parent="0" type="proj"/>\n',
            encoding="utf-8",
        )
        default = tools.list_projects(include_qa=False)
        names = {item["name"] for item in default["projects"]}
        assert f"qa_regress_{tag}" not in names
        assert f"runs_{tag}" not in names
        wide = tools.list_projects(include_qa=True)
        wide_names = {item["name"] for item in wide["projects"]}
        assert f"qa_regress_{tag}" in wide_names
        assert f"runs_{tag}" in wide_names
    finally:
        shutil.rmtree(qa_dir, ignore_errors=True)
        shutil.rmtree(SETTINGS.workspace / "_qa_runs" / f"regress_{tag}", ignore_errors=True)


def test_const_drive_pull_and_voltage_reach_xdc(work: Path) -> None:
    name = f"regress_const_{uuid.uuid4().hex[:8]}"
    project = tools.create_robei_project(name, models=[])
    try:
        proj = Path(project["proj"])
        (proj.parent / "model" / "tiny.model").write_text(
            _param_model_xml(
                "tiny", "", [("a", "input", "1"), ("y", "output", "1")], "assign y = a;"
            ),
            encoding="utf-8",
        )
        tools._register_model_in_project(proj, "tiny")
        const_dir = proj.parent / "const"
        const_dir.mkdir(exist_ok=True)
        (const_dir / "pins.const").write_text(
            "<!DOCTYPE Robei>\n"
            '<Module Height="547" Type="const" Vendor="AMD" Device="" Family="" Width="1185" '
            'Class="module" Code="" Color="#d3d3d3" Comment="" Name="pins" Parent="0" '
            'File="Current/pins.const">\n'
            ' <Module Type="model" Class="tiny" Name="pins_inst" Parent="pins">\n'
            '  <Port Name="a" Inout="input" Datasize="1" Datatype="wire" Parent="pins_inst"/>\n'
            '  <Port Name="y" Inout="output" Datasize="1" Datatype="wire" Parent="pins_inst"/>\n'
            " </Module>\n"
            ' <Port Name="G15" Inout="input" Datasize="1" Datatype="wire" Parent="pins" '
            'Drive="8" Pull="up" Voltage="3.3"/>\n'
            ' <Port Name="M14" Inout="output" Datasize="1" Datatype="wire" Parent="pins" '
            'Drive="4" Pull="down" Voltage=""/>\n'
            ' <Wire Name="0" From="pins>G15" To="pins#pins_inst>a" Datasize="1"/>\n'
            ' <Wire Name="0" From="pins#pins_inst>y" To="pins>M14" Datasize="1"/>\n'
            "</Module>\n",
            encoding="utf-8",
        )
        info = constraints.read_const(str(const_dir / "pins.const"))
        assert info["pin_options"]["G15"]["drive"] == "8"
        assert info["pin_options"]["G15"]["pull"] == "up"

        exported = constraints.export_xdc(str(proj), "pins")
        text = Path(exported["path"]).read_text(encoding="utf-8")
        # get_ports targets the model port; the pin name only keys the options.
        assert "set_property DRIVE 8 [get_ports a]" in text
        assert "set_property PULLUP TRUE [get_ports a]" in text
        assert "set_property DRIVE 4 [get_ports y]" in text
        assert "set_property PULLDOWN TRUE [get_ports y]" in text
        assert "3.3" not in text
        assert exported["voltage_hints"] == [{"port": "a", "voltage": "3.3"}]
    finally:
        shutil.rmtree(SETTINGS.workspace / name, ignore_errors=True)
