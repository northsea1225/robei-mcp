"""Audit batch B: one error shape for every "outside the workspace" refusal."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from robei_mcp import constraints, errors, fpga, p1, security, tools
from robei_mcp.config import SETTINGS

PROJ_XML = '<?xml version="1.0" encoding="UTF-8"?>\n<Project name="p"/>\n'
MODEL_XML = "<!DOCTYPE Robei>\n<Module Name=\"m\" Type=\"model\" Code=\"\"/>\n"


@pytest.fixture
def outside(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A readable model root that is *not* the workspace: reads resolve, writes must be refused."""
    ws = tmp_path / "ws"
    root = tmp_path / "robei_projects"
    (root / "p" / "model").mkdir(parents=True)
    ws.mkdir()
    proj = root / "p" / "p.proj"
    proj.write_text(PROJ_XML, encoding="utf-8")
    model = root / "p" / "model" / "m.model"
    model.write_text(MODEL_XML, encoding="utf-8")
    settings = replace(SETTINGS, workspace=ws, model_roots=(root,))
    for module in (constraints, fpga, p1, security, tools):
        monkeypatch.setattr(module, "SETTINGS", settings, raising=False)
    return str(proj), str(model)


def _cases(proj: str, model: str):
    return {
        "create_const": lambda: constraints.create_const(proj, "c", "m", {"a": ["A1"]}),
        "export_xdc": lambda: constraints.export_xdc(proj),
        "synthesize_project": lambda: fpga.synthesize_project(proj),
        "build_bitstream": lambda: fpga.build_bitstream(proj, "xc7a35tcsg324-1"),
        "import_verilog_canvas": lambda: p1.import_verilog_canvas(proj, "x.v"),
        "simulate_project": lambda: tools.simulate_project(proj, "t"),
        "run_all_tests": lambda: tools.run_all_tests(proj),
        "build_report": lambda: tools.build_report(proj),
        "set_port": lambda: tools.set_port(model, "a"),
        "sync_robei_verilog": lambda: tools.sync_robei_verilog(proj),
        "create_robei_canvas_model": lambda: tools.create_robei_canvas_model(proj, "n", [], [], []),
        "edit_robei_canvas": lambda: tools.edit_robei_canvas(model, [{"op": "x"}]),
        "set_robei_code": lambda: tools.set_robei_code(model, "// c"),
        "set_robei_parameter": lambda: tools.set_robei_parameter(model, "LEN", 4),
        "create_robei_test": lambda: tools.create_robei_test(proj, "t", "// c"),
        "create_robei_model": lambda: tools.create_robei_model(proj, "n"),
        "import_library_model": lambda: tools.import_library_model("and2", project=proj),
    }


NAMES = list(_cases("p", "m"))


@pytest.mark.parametrize("name", NAMES)
def test_every_write_outside_workspace_is_one_path_escape(outside, name: str) -> None:
    proj, model = outside
    with pytest.raises(errors.PathEscape) as caught:
        _cases(proj, model)[name]()
    assert errors.code_for(caught.value) == "path_escape"
    body = errors.payload(caught.value)
    assert body["ok"] is False and body["code"] == "path_escape"
    assert "workspace" in body["error"] and body["hint"]
    assert "refusing to" in str(caught.value)


def test_legacy_code_is_still_reported(outside) -> None:
    proj, model = outside
    with pytest.raises(errors.PathEscape) as value_site:
        tools.run_all_tests(proj)
    with pytest.raises(errors.PathEscape) as perm_site:
        tools.set_robei_code(model, "// c")
    assert errors.payload(value_site.value)["legacy_code"] == "invalid_argument"
    assert errors.payload(perm_site.value)["legacy_code"] == "permission_denied"


def test_generate_testbench_path_check_is_case_insensitive_and_prefix_safe(tmp_path, monkeypatch) -> None:
    """Simulate a case-insensitive filesystem: ``WS`` and ``ws`` are the same folder."""
    ws = tmp_path / "WS"
    ws.mkdir()
    src = ws / "dut.v"
    src.write_text("module dut(input a, output y); assign y = a; endmodule\n", encoding="utf-8")
    settings = replace(SETTINGS, workspace=ws)
    for module in (security, tools):
        monkeypatch.setattr(module, "SETTINGS", settings, raising=False)
    monkeypatch.setattr(security.os.path, "normcase", lambda value: str(value).lower())
    result = tools.generate_testbench(str(src), "dut", out_path=str(tmp_path / "ws" / "tb" / "dut_tb.v"))
    assert result["ok"] is True
    # a sibling that merely shares the prefix is still outside
    evil = tmp_path / "WS_evil"
    with pytest.raises(errors.PathEscape) as caught:
        tools.generate_testbench(str(src), "dut", out_path=str(evil / "x.v"))
    assert errors.code_for(caught.value) == "path_escape"
    assert caught.value.legacy_code == "invalid_argument"
    with pytest.raises(errors.PathEscape):
        tools.generate_testbench(str(src), "dut", out_path=str(tmp_path / "ws" / ".." / "elsewhere.v"))


def test_is_within_handles_a_filesystem_root(monkeypatch) -> None:
    assert security.is_within(Path("/tmp/x"), Path("/"))
    assert not security.is_within(Path("/tmp/x"), Path("/tmpfoo"))


# --- tb_expr: "x"/"z" that is also an input name --------------------------------------------

from robei_mcp import tb_expr  # noqa: E402


def test_bare_x_or_z_without_a_same_named_input_is_still_a_literal() -> None:
    assert tb_expr.expected("x", 1, {"a": 1}, "f") == ("1'bx", False)
    assert tb_expr.expected("z", 4, {"a": 1}, "f") == ("4'b000z", False)  # documented: zero-extended, not x-extended


@pytest.mark.parametrize("text", ["x", "z", "xz", "x0"])
def test_literal_that_is_also_an_input_name_is_ambiguous(text: str) -> None:
    with pytest.raises(errors.BadRequest) as caught:
        tb_expr.expected(text, max(len(text), 2), {text: 2}, "expected.y")
    exc = caught.value
    assert errors.code_for(exc) == "bad_argument" and exc.reason == "ambiguous_literal_or_port"
    assert f"{len(text) if len(text) > 1 else 2}'b" in exc.hint and f"({text})" in exc.hint


def test_ambiguity_escape_hatches() -> None:
    inputs = {"x": 1, "z": 1}
    assert tb_expr.expected("1'bx", 1, inputs, "f") == ("1'bx", False)
    assert tb_expr.expected("4'b00zx", 4, inputs, "f") == ("4'b00zx", False)
    assert tb_expr.expected("(x)", 1, inputs, "f")[1] is True  # parsed as the input port, not a literal
    with pytest.raises(errors.BadRequest):
        tb_expr.expected("2'bx", 4, inputs, "f")  # a sized literal must match the port width


def test_binary_string_versus_decimal_integer_is_unchanged() -> None:
    assert tb_expr.expected(10, 4, {}, "f") == ("4'b1010", False)     # JSON integer: decimal
    assert tb_expr.expected("10", 4, {}, "f") == ("4'b0010", False)   # string of 0/1 digits: binary
    assert tb_expr.expected("12", 4, {}, "f") == ("4'b1100", False)   # other digit strings: decimal
