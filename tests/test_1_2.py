"""1.2.0 additions: canvas port ops, batch test runs, Verilator --timing."""
from __future__ import annotations

import hashlib
import shutil
import uuid
from pathlib import Path

import pytest

from robei_mcp import tools
from robei_mcp.config import SETTINGS
from robei_mcp.security import resolve_model_path

IVERILOG_MISSING = not Path(SETTINGS.iverilog).is_file()
VERILATOR_MISSING = not Path(SETTINGS.verilator).is_file()

needs_iverilog = pytest.mark.skipif(
    IVERILOG_MISSING, reason=f"iverilog not found at {SETTINGS.iverilog}"
)
needs_verilator = pytest.mark.skipif(
    VERILATOR_MISSING, reason=f"verilator not found at {SETTINGS.verilator}"
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture()
def project():
    name = f"pytest_12_{uuid.uuid4().hex[:8]}"
    created = tools.create_robei_project(name, models=["inv"])
    proj = created["proj"]
    tools.create_robei_model(
        proj,
        "inv",
        ports=[
            {"name": "a", "direction": "input", "width": 1},
            {"name": "y", "direction": "output", "width": 1},
        ],
        code="assign y = ~a;",
        overwrite=True,  # create_robei_project already dropped a blank model
    )
    yield proj
    shutil.rmtree(Path(proj).parent, ignore_errors=True)


def _canvas(project: str) -> str:
    proj = Path(project)
    tools.create_robei_model(proj.as_posix(), "canvas_top")
    return (proj.parent / "model" / "canvas_top.model").as_posix()


# ---------------------------------------------------------------------------
# edit_robei_canvas: add_port / remove_port
# ---------------------------------------------------------------------------
def test_add_port_dry_run_leaves_file_untouched(project: str) -> None:
    model = _canvas(project)
    before = _sha(resolve_model_path(model))
    result = tools.edit_robei_canvas(
        model,
        [{"op": "add_port", "name": "a", "direction": "input", "width": 4}],
        dry_run=True,
    )
    assert result["ok"] is True
    assert result["dry_run"] is True
    assert [(port["name"], port["width"], port["direction"]) for port in result["ports"]] == [
        ("a", 4, "input")
    ]
    assert _sha(resolve_model_path(model)) == before


def test_add_port_writes_model(project: str) -> None:
    model = _canvas(project)
    tools.edit_robei_canvas(
        model,
        [
            {"op": "add_port", "name": "a", "direction": "input", "width": 4},
            {"op": "add_port", "name": "y", "direction": "output", "width": 1},
        ],
    )
    parsed = tools.read_robei_model(model)
    widths = {port["name"]: int(port["datasize"] or 1) for port in parsed["ports"]}
    assert widths == {"a": 4, "y": 1}
    assert {port["inout"] for port in parsed["ports"]} == {"input", "output"}


def test_add_port_rejects_duplicate(project: str) -> None:
    model = _canvas(project)
    with pytest.raises(ValueError):
        tools.edit_robei_canvas(
            model,
            [
                {"op": "add_port", "name": "a", "direction": "input", "width": 1},
                {"op": "add_port", "name": "a", "direction": "input", "width": 1},
            ],
        )


def test_remove_port_drops_connected_wires(project: str) -> None:
    model = _canvas(project)
    tools.edit_robei_canvas(
        model,
        [
            {"op": "add_port", "name": "a", "direction": "input", "width": 1},
            {"op": "add_port", "name": "y", "direction": "output", "width": 1},
            {"op": "add_instance", "name": "u1", "model": "inv"},
            {"op": "connect", "from": "u1.a", "to": "a"},
            {"op": "connect", "from": "u1.y", "to": "y"},
        ],
    )
    result = tools.edit_robei_canvas(model, [{"op": "remove_port", "name": "a"}])
    assert result["ops_applied"][-1] == {"op": "remove_port", "port": "a", "wires_dropped": 1}
    parsed = tools.read_robei_model(model)
    assert [port["name"] for port in parsed["ports"]] == ["y"]
    assert len(parsed["wires"]) == 1


def test_remove_port_rejects_unknown(project: str) -> None:
    model = _canvas(project)
    with pytest.raises(ValueError):
        tools.edit_robei_canvas(model, [{"op": "remove_port", "name": "nope"}], dry_run=True)


def test_add_port_rejects_bad_width(project: str) -> None:
    model = _canvas(project)
    with pytest.raises(ValueError):
        tools.edit_robei_canvas(
            model, [{"op": "add_port", "name": "a", "direction": "input", "width": 0}]
        )


# ---------------------------------------------------------------------------
# run_all_tests
# ---------------------------------------------------------------------------
@needs_iverilog
def test_run_all_tests_aggregates_pass_and_fail(project: str) -> None:
    tools.create_robei_test(
        project,
        "tb_ok",
        code=(
            "reg a; wire y; inv u(.a(a), .y(y));\n"
            "initial begin a = 0; #5; $display(\"y=%b\", y); $finish; end"
        ),
    )
    tools.create_robei_test(project, "tb_broken", code="this is not verilog;")
    result = tools.run_all_tests(project, timeout_s=30)
    assert result["total"] == 2
    assert result["passed"] == 1
    assert result["failed"] == 1
    by_name = {row["test"]: row for row in result["tests"]}
    assert by_name["tb_ok"]["ok"] is True
    assert by_name["tb_ok"]["vcd_paths"]
    assert by_name["tb_broken"]["ok"] is False
    assert by_name["tb_broken"]["error_tail"]
    assert result["ok"] is False


@needs_iverilog
def test_run_all_tests_without_tests_is_not_ok(project: str) -> None:
    result = tools.run_all_tests(project, timeout_s=15)
    assert result["total"] == 0
    assert result["ok"] is False


# ---------------------------------------------------------------------------
# Verilator --timing
# ---------------------------------------------------------------------------
@needs_verilator
def test_verilator_timing_flag_is_detected() -> None:
    supported, version = tools._verilator_timing_support()
    assert version, "verilator --version must print a version"
    assert isinstance(supported, bool)


@needs_verilator
@needs_iverilog
def test_clock_model_lints_ok_with_timing(project: str) -> None:
    clock = (
        "module clock_tb();\n"
        "  reg clk = 0;\n"
        "  always #5 clk = ~clk;\n"
        "  initial begin #20 $finish; end\n"
        "endmodule\n"
    )
    path = Path(project).parent / "verilog" / "clock_tb.v"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(clock, encoding="utf-8", newline="\n")
    rel = path.relative_to(SETTINGS.workspace).as_posix()
    result = tools.lint_verilog(files=[rel], top="clock_tb", timeout_s=60)
    verilator = result["verilator"]
    assert verilator["timing"] is True
    assert verilator["error_count"] == 0, verilator["errors"]
    assert verilator["ok"] is True
