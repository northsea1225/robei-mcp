from __future__ import annotations

import hashlib
import shutil
import uuid
from pathlib import Path

import pytest

from robei_mcp import errors, server_app, tb_expr, tools
from robei_mcp.config import SETTINGS


@pytest.fixture()
def project():
    root = SETTINGS.workspace / "_qa_runs" / ("pytest110_expr_" + uuid.uuid4().hex[:8])
    proj = tools.create_robei_project("_qa_runs/" + root.name, models=[], skeleton=False)["proj"]

    def model(name="dut_expr", width=8, code="assign y = a + b;"):
        tools.create_robei_model(proj, name, ports=[
            {"name": "a", "direction": "input", "width": 4},
            {"name": "b", "direction": "input", "width": 4},
            {"name": "sel", "direction": "input"},
            {"name": "y", "direction": "output", "width": width},
        ], code=code)
        return str(root / "model" / f"{name}.model")

    try:
        yield proj, root, model
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _vector(value, a=9, b=3, sel=1):
    return [{"drive": {"a": a, "b": b, "sel": sel}, "expect": {"y": value}}]


def _passed(result):
    assert result["ok"] is True, result
    row = result["run"]["tests"][0]
    assert row["returncode"] == 0
    assert "SELFTEST PASS" in row["display_output"]


def test_pratt_operators_precedence_and_explicit_constants(project):
    proj, _, model = project
    cases = [("a+b*2", 15), ("(a+b)*2", 24), ("a-b", 6), ("a*b", 27),
             ("a&b", 1), ("a|b", 11), ("a^b", 10), ("~a", 246),
             ("a<<1", 18), ("a>>1", 4), ("a==b", 0), ("a!=b", 1),
             ("a<b", 0), ("a>b", 1), ("sel ? b : a", 3),
             ("a==b ? 7 : sel ? 5 : 2", 5)]
    for index, (expr, want) in enumerate(cases):
        # The DUT is a literal independent oracle, not a copy of the expression.
        path = model(f"oracle{index}", code=f"assign y = 8'd{want};")
        result = tools.create_testbench(proj, f"tb_op{index}", path, _vector(expr), run=True)
        _passed(result)
        assert "reg [7:0] __robei_expected_y" in result["code"]
        assert "y !== __robei_expected_y" in result["code"]
        assert result["semantic"]["expression_count"] == 1
    parsed = tb_expr.compile_expression("a+2", {"a": 4}, "expect.y")
    assert "32'd2" in parsed.text and parsed.width == 32


def test_constant_selects_and_plain_concatenation(project):
    proj, _, model = project
    for index, (expr, width, want) in enumerate([
        ("a[3]", 1, 1), ("a[3:1]", 3, 4), ("{a[3:2],b[1:0]}", 4, 11),
        ("{a,b}", 8, 147),
    ]):
        path = model(f"select{index}", width, f"assign y = {width}'d{want};")
        _passed(tools.create_testbench(proj, f"tb_sel{index}", path, _vector(expr), run=True))


def test_expression_rejections_are_structured_and_write_nothing(project):
    proj, root, model = project
    path = model()
    before = hashlib.sha256(Path(proj).read_bytes()).hexdigest()
    denied = ["-1", "a/2", "a%2", "!a", "a&&b", "a||b", "f(a)", "$random",
              "dut.a", "missing+1", "y+1", "a[sel]", "a[4]", "a[1:3]", "{2{a}}",
              "__import__('os')", "a; $finish", ""]
    for expr in denied:
        result = server_app.create_testbench(proj, "tb_denied", path, _vector(expr))
        assert result["ok"] is False, expr
        assert result["code"] == "bad_argument", result
        assert result["field"] == "vectors[0].expect.y"
        assert isinstance(result["offset"], int)
        assert result["reason"] and result["hint"]
    assert not (root / "test" / "tb_denied.test").exists()
    assert hashlib.sha256(Path(proj).read_bytes()).hexdigest() == before


def test_literal_precedence_padding_and_range_compatibility():
    for value, width, want in [(True, 1, "1'b1"), (False, 2, "2'b00"),
                               ("10", 4, "4'b0010"), (10, 4, "4'b1010"),
                               ("15", 4, "4'b1111"), (" xZ ", 4, "4'b00xz")]:
        assert tb_expr.expected(value, width, {}, "expected") == (want, False)
    long_bits = "1" * (tb_expr.MAX_LENGTH + 1)
    assert tb_expr.expected(long_bits, len(long_bits), {}, "expected") == (f"{len(long_bits)}'b{long_bits}", False)
    assert tb_expr.expected("0" * 5000 + "2", 4, {}, "expected") == ("4'b0010", False)
    for value in [-1, 16, "10000", None, {}, "-1"]:
        with pytest.raises(errors.BadRequest) as caught:
            tb_expr.expected(value, 4, {}, "expected")
        assert errors.payload(caught.value)["code"] == "bad_argument"


def test_expression_width_extension_truncation_and_subtraction(project):
    proj, _, model = project
    for index, (width, expr, a, b, want) in enumerate([
        (4, "a+b", 15, 1, 0), (5, "a+b", 15, 1, 16),
        (4, "a-b", 0, 1, 15), (4, "a*2", 9, 0, 2),
    ]):
        path = model(f"sized{index}", width, f"assign y = {width}'d{want};")
        _passed(tools.create_testbench(proj, f"tb_size{index}", path,
                                       _vector(expr, a=a, b=b), run=True))


def test_xz_literals_expressions_and_real_failure(project):
    proj, _, model = project
    path = model(width=4, code="assign y = a;")
    _passed(tools.create_testbench(proj, "tb_xz", path, _vector("xz", a="xz"), run=True))
    inverse = model("inverse", 4, "assign y = ~a;")
    _passed(tools.create_testbench(proj, "tb_xexpr", inverse, _vector("~a", a="xz"), run=True))
    result = tools.create_testbench(proj, "tb_wrong", path, _vector("a+b+1"), run=True)
    assert result["ok"] is False
    assert result["run"]["code"] == "selftest_failed"
    assert "SELFTEST FAIL" in result["run"]["tests"][0]["display_output"]


def test_timed_mode_uses_expression_temporaries(project):
    proj, _, model = project
    path = model(width=4, code="assign y = a;")
    result = tools.create_testbench(proj, "tb_expr_timed", path, mode="timed",
        clock={"signal": "sel", "period": 10},
        stimuli=[{"at": 0, "drive": {"a": 9, "b": 2}}],
        assertions=[{"at": 1, "expect": {"y": "a|0"}},
                    {"after_edge": 2, "expect": {"y": "a+0"}}], run=True)
    _passed(result)
    assert result["semantic"]["expression_count"] == 2


def test_expression_bounds_and_signed_ports_rejected_before_write(project):
    proj, root, model = project
    path = model()
    expressions = ["a" * (tb_expr.MAX_LENGTH + 1),
                   "(" * (tb_expr.MAX_DEPTH + 1) + "a" + ")" * (tb_expr.MAX_DEPTH + 1),
                   "{" + ",".join(["a"] * (tb_expr.MAX_NODES + 1)) + "}"]
    for expr in expressions:
        with pytest.raises(errors.BadRequest) as caught:
            tools.create_testbench(proj, "tb_limits", path, _vector(expr))
        assert errors.payload(caught.value)["code"] == "bad_argument"
    xml = Path(path).read_text(encoding="utf-8")
    Path(path).write_text(xml.replace('<Port ', '<Port Signed="true" ', 1), encoding="utf-8")
    result = server_app.create_testbench(proj, "tb_signed", path, _vector("a+0"))
    assert result["code"] == "bad_argument" and "signed" in result["reason"]
    assert not list((root / "test").glob("tb_limits.test"))
    assert not list((root / "test").glob("tb_signed.test"))
