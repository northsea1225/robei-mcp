"""Independent 4-state goldens, checked against static widths and real Icarus."""
from __future__ import annotations

from pathlib import Path
import re
import shutil
import uuid

import pytest

from robei_mcp import tb_expr, tools
from robei_mcp.config import SETTINGS


# expr, self-determined width, input bits, target/context width, golden bits.
# Golden values are literal reference data, never evaluated by the Pratt parser.
CASES = [
    ("a+b", 4, ("1111", "0001", "0"), 5, "10000"),
    ("~a", 4, ("0001", "0000", "0"), 8, "11111110"),
    ("a-b", 4, ("0000", "0001", "0"), 4, "1111"),
    ("a*2", 32, ("1001", "0000", "0"), 4, "0010"),
    ("a<<1", 4, ("1001", "0000", "0"), 4, "0010"),
    ("a>>1", 4, ("1101", "0000", "0"), 4, "0110"),
    ("a<<4", 4, ("1001", "0000", "0"), 4, "0000"),
    ("sel ? a : b", 4, ("1010", "1001", "x"), 4, "10xx"),
    ("sel ? a : b", 4, ("1010", "1001", "z"), 4, "10xx"),
    ("{a,b}", 8, ("1010", "0101", "0"), 8, "10100101"),
    ("{a[3:2],b[1:0]}", 4, ("1101", "1011", "0"), 4, "1111"),
    ("a[3]", 1, ("x010", "0000", "0"), 1, "x"),
    ("a[3:1]", 3, ("1z01", "0000", "0"), 3, "1z0"),
    ("a==b", 1, ("x101", "x101", "0"), 1, "x"),
    ("a!=b", 1, ("0110", "1010", "0"), 1, "1"),
    ("a>b", 1, ("1001", "0011", "0"), 1, "1"),
    ("a+1==10", 1, ("1001", "0000", "0"), 1, "1"),
    ("a==b ? 7 : sel ? 5 : 2", 32, ("0000", "0001", "x"), 8, "00000xxx"),
]


@pytest.fixture()
def workdir():
    root = SETTINGS.workspace / "_qa_runs" / ("pytest110_icarus_" + uuid.uuid4().hex[:8])
    root.mkdir(parents=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.mark.parametrize("expr,static_width,inputs,target_width,golden", CASES,
                         ids=[f"crosscheck-{index:02d}" for index in range(1, len(CASES)+1)])
def test_static_width_and_four_state_value_against_icarus(workdir: Path, expr, static_width, inputs, target_width, golden):
    compiled = tb_expr.compile_expression(expr, {"a": 4, "b": 4, "sel": 1}, "expect.y")
    assert compiled.width == static_width
    a, b, sel = inputs
    text = f"""`timescale 1ns/1ps
module tb_expr_crosscheck;
  reg [3:0] a, b;
  reg sel;
  reg [{target_width-1}:0] observed;
  initial begin
    a = 4'b{a}; b = 4'b{b}; sel = 1'b{sel};
    #1;
    observed = {compiled.text};
    $display("CROSSCHECK width=%0d target={target_width} value=%b", $bits({compiled.text}), observed);
    if ($bits({compiled.text}) != {static_width}) $fatal(1, "width mismatch");
    if (observed !== {target_width}'b{golden}) $fatal(1, "value mismatch");
    $display("CROSSCHECK PASS");
    $finish;
  end
endmodule
"""
    source = workdir / "tb_expr_crosscheck.v"
    source.write_text(text, encoding="utf-8", newline="\n")
    result = tools.simulate([str(source)], top="tb_expr_crosscheck", base=workdir / "build")
    assert result["ok"] is True, result
    assert result["compile"]["returncode"] == result["simulation"]["returncode"] == 0
    display = result["display_output"]
    actual = re.search(r"CROSSCHECK width=(\d+) target=(\d+) value=([01xz]+)", display)
    assert actual and (int(actual[1]), int(actual[2]), actual[3]) == (compiled.width, target_width, golden)
    assert "CROSSCHECK PASS" in display
    print(f"{expr}: static={compiled.width}, target={target_width}, Icarus={actual[3]}")
