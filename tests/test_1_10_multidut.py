from __future__ import annotations

from dataclasses import replace
import hashlib
import inspect
import itertools
import shutil
import uuid
from pathlib import Path

import pytest

from robei_mcp import errors, server_app, tb_dut, tools
from robei_mcp.config import SETTINGS
from robei_mcp.model import parse_robei_model


@pytest.fixture()
def project():
    root = SETTINGS.workspace / "_qa_runs" / ("pytest110_multi_" + uuid.uuid4().hex[:8])
    proj = tools.create_robei_project("_qa_runs/" + root.name, models=[], skeleton=False)["proj"]

    def model(name, ports, code):
        tools.create_robei_model(proj, name, ports=ports, code=code)
        return str(root / "model" / f"{name}.model")

    try:
        yield proj, root, model
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _muxes(project):
    _, _, model = project
    ports = [{"name": p, "direction": "input"} for p in ("a", "b", "sel")] + [{"name": "y", "direction": "output"}]
    bh = model("mux_bh", ports, "assign y = sel ? b : a;")
    st = model("mux_st", ports, "assign y = (a & ~sel) | (b & sel);")
    return [{"instance": instance, "model_path": path,
             "connections": {"a": "a", "b": "b", "sel": "sel", "y": output}}
            for instance, path, output in [("u_bh", bh, "y_bh"), ("u_st", st, "y_st")]]


def _passed(result):
    assert result["ok"] is True, result
    row = result["run"]["tests"][0]
    assert row["returncode"] == 0
    assert "SELFTEST PASS" in row["display_output"]


def test_two_mux_duts_exhaustive_expression_and_comparisons(project):
    proj, _, _ = project
    duts = _muxes(project)
    vectors = [{"drive": dict(zip(("a", "b", "sel"), bits)),
                "expect": {"u_bh.y": "sel ? b : a", "u_st.y": "sel ? b : a"}}
               for bits in itertools.product((0, 1), repeat=3)]
    result = tools.create_testbench(proj, "tb_mux", duts=duts, vectors=vectors,
        comparisons=[{"left": "u_bh.y", "right": "u_st.y"}], run=True)
    _passed(result)
    assert result["semantic"]["input_bits"] == 3  # shared inputs counted once
    assert result["semantic"]["comparison_count"] == 8
    assert len(result["semantic"]["duts"]) == 2
    assert ".y(y_bh)" in result["code"] and ".y(y_st)" in result["code"]
    prefix = "workspace/" + Path(duts[0]["model_path"]).relative_to(SETTINGS.workspace).as_posix()
    _passed(tools.create_testbench(proj, "tb_old_prefix", prefix, vectors=[
        {"drive": {"a": 0, "b": 1, "sel": 1}, "expect": {"y": "sel ? b : a"}}], run=True))


def test_two_adders_all_512_vectors_preserve_carry_context(project):
    proj, _, model = project
    ports = [{"name": "a", "direction": "input", "width": 4},
             {"name": "b", "direction": "input", "width": 4},
             {"name": "cin", "direction": "input"},
             {"name": "s", "direction": "output", "width": 4},
             {"name": "cout", "direction": "output"}]
    bh = model("adder_bh", ports, "assign {cout,s} = {1'b0,a} + {1'b0,b} + cin;")
    gates = ["wire [4:0] carry; assign carry[0] = cin;"]
    for bit in range(4):
        gates.append(f"assign s[{bit}] = a[{bit}] ^ b[{bit}] ^ carry[{bit}];")
        gates.append(f"assign carry[{bit+1}] = (a[{bit}] & b[{bit}]) | ((a[{bit}] ^ b[{bit}]) & carry[{bit}]);")
    gates.append("assign cout = carry[4];")
    st = model("adder_st", ports, "\n".join(gates))
    duts = [{"instance": inst, "model_path": path,
             "connections": {"a": "a", "b": "b", "cin": "cin", "s": "s_"+inst, "cout": "co_"+inst}}
            for inst, path in [("u_bh", bh), ("u_st", st)]]
    vectors = [{"drive": {"a": a, "b": b, "cin": cin},
                "expect": {"{u_bh.cout,u_bh.s}": "a+b+cin", "{u_st.cout,u_st.s}": "a+b+cin"}}
               for a, b, cin in itertools.product(range(16), range(16), range(2))]
    result = tools.create_testbench(proj, "tb_adder", duts=duts, vectors=vectors,
        comparisons=[{"left": "u_bh.s", "right": "u_st.s"},
                     {"left": "u_bh.cout", "right": "u_st.cout"}], run=True)
    _passed(result)
    assert result["semantic"]["vector_count"] == 512
    assert result["semantic"]["comparison_count"] == 1024
    assert "reg [4:0] __robei_expected_" in result["code"]


def test_wrong_expression_returns_selftest_failed(project):
    proj, _, _ = project
    result = tools.create_testbench(proj, "tb_wrong", duts=_muxes(project), vectors=[
        {"drive": {"a": 0, "b": 1, "sel": 1}, "expect": {"u_bh.y": "sel ? a : b", "u_st.y": "sel ? a : b"}}
    ], comparisons=[{"left": "u_bh.y", "right": "u_st.y"}], run=True)
    assert result["ok"] is False
    assert result["run"]["code"] == "selftest_failed"
    assert parse_robei_model(Path(result["path"]))["code"] == result["code"]
    assert "SELFTEST FAIL" in result["run"]["tests"][0]["display_output"]


def test_timed_two_duts_share_clock_reset_and_check_expressions(project):
    proj, _, model = project
    ports = [{"name": p, "direction": "input"} for p in ("clk", "rst", "en")] + [{"name": "cnt", "direction": "output", "width": 4}]
    path = model("counter", ports, "reg [3:0] q; always @(posedge clk) if (rst) q <= 0; else if (en) q <= q+1'b1; assign cnt=q;")
    duts = [{"instance": inst, "model_path": path,
             "connections": {"clk": "clk", "rst": "rst", "en": "en", "cnt": "cnt_"+inst}}
            for inst in ("u1", "u2")]
    result = tools.create_testbench(proj, "tb_counters", duts=duts, mode="timed",
        clock={"signal": "clk", "period": 10}, reset={"signal": "rst", "active": 1, "duration": 10},
        stimuli=[{"at": 0, "drive": {"en": 1}}, {"at": 20, "drive": {"en": 0}}],
        assertions=[{"after_edge": 2, "expect": {"u1.cnt": "en+0", "u2.cnt": 1}},
                    {"after_edge": 3, "expect": {"u1.cnt": "en+1", "u2.cnt": 1}}],
        comparisons=[{"left": "u1.cnt", "right": "u2.cnt"}], run=True)
    _passed(result)
    assert result["code"].count("always #5 clk") == 1
    assert result["semantic"]["comparison_count"] == 2


def test_multidut_rejects_schema_names_ports_and_path_escape(project):
    proj, root, _ = project
    duts = _muxes(project)
    vector = [{"drive": {"a": 0, "b": 0, "sel": 0}, "expect": {"u_bh.y": 0, "u_st.y": 0}}]
    from copy import deepcopy
    cases = []
    for name in ("u-bad", "module", "__robei_internal"):
        bad = deepcopy(duts); bad[0]["instance"] = name; cases.append((bad, "bad_argument"))
    bad = deepcopy(duts); bad[1]["instance"] = "u_bh"; cases.append((bad, "bad_argument"))
    bad = deepcopy(duts); del bad[0]["connections"]["a"]; cases.append((bad, "bad_argument"))
    bad = deepcopy(duts); bad[0]["connections"]["missing"] = "a"; cases.append((bad, "bad_argument"))
    bad = deepcopy(duts); bad[0]["connections"]["a"] = "a; $finish"; cases.append((bad, "bad_argument"))
    bad = deepcopy(duts); bad[0]["model_path"] = "D:/Temp/outside_missing.model"; cases.append((bad, "path_escape"))
    for bad, code in cases:
        result = server_app.create_testbench(proj, "tb_reject", duts=bad, vectors=vector)
        assert result["code"] == code, result
    for kwargs in ({}, {"model_path": duts[0]["model_path"], "duts": duts}):
        assert server_app.create_testbench(proj, "tb_reject", vectors=vector, **kwargs)["code"] == "bad_argument"
    for target in ("u_missing.y", "u_bh.missing", "u_bh.a"):
        bad_vector = [{"drive": vector[0]["drive"], "expect": {target: 0, "u_st.y": 0}}]
        assert server_app.create_testbench(proj, "tb_reject", duts=duts, vectors=bad_vector)["code"] == "bad_argument"
    assert not (root/"test"/"tb_reject.test").exists()


def test_multidut_width_driver_and_comparison_rules(project):
    proj, root, model = project
    duts = _muxes(project)
    from copy import deepcopy
    bad = deepcopy(duts); bad[1]["connections"]["y"] = "y_bh"
    assert server_app.create_testbench(proj, "tb_reject", duts=bad)["code"] == "bad_argument"
    wide = model("wide", [{"name": "a", "direction": "input", "width": 2}, {"name": "y", "direction": "output", "width": 2}], "assign y=a;")
    extra = {"instance": "u_wide", "model_path": wide, "connections": {"a": "a", "y": "y_wide"}}
    assert server_app.create_testbench(proj, "tb_reject", duts=[*duts, extra])["code"] == "bad_argument"
    extra["connections"]["a"] = "a_wide"
    for pair in [{"left": "u_bh.y", "right": "u_wide.y"}, {"left": "u_bh.y", "right": "u_st.bad"}]:
        result = server_app.create_testbench(proj, "tb_reject", duts=[*duts, extra], comparisons=[pair])
        assert result["code"] == "bad_argument"
    assert not (root/"test"/"tb_reject.test").exists()


def test_multidut_resource_limits_reject_before_writes(project, monkeypatch):
    proj, root, _ = project
    duts = _muxes(project)
    before = hashlib.sha256(Path(proj).read_bytes()).hexdigest()
    result = server_app.create_testbench(proj, "tb_limit", duts=duts * (tb_dut.MAX_DUTS+1))
    assert result["code"] == "limit_exceeded"
    with monkeypatch.context() as m:
        m.setattr(tb_dut, "MAX_CONNECTIONS", 3)
        assert server_app.create_testbench(proj, "tb_limit", duts=duts)["code"] == "limit_exceeded"
    with monkeypatch.context() as m:
        m.setattr(tools, "SETTINGS", replace(SETTINGS, max_source_bytes=64))
        result = server_app.create_testbench(proj, "tb_limit", duts=duts, vectors=[
            {"drive": {"a": 0, "b": 0, "sel": 0}, "expect": {"u_bh.y": 0, "u_st.y": 0}}])
        assert result["code"] == "limit_exceeded"
    assert not (root/"test"/"tb_limit.test").exists()
    assert hashlib.sha256(Path(proj).read_bytes()).hexdigest() == before


def test_comparisons_are_four_state_and_detect_independent_mismatch(project):
    proj, _, model = project
    ports = [{"name": "a", "direction": "input"}, {"name": "y", "direction": "output"}]
    path = model("identity", ports, "assign y=a;")
    inverse = model("inverse", ports, "assign y=~a;")
    duts = [{"instance": inst, "model_path": path, "connections": {"a": "a", "y": "y_"+inst}}
            for inst in ("u1", "u2")]
    comparisons = [{"left": "u1.y", "right": "u2.y"}]
    _passed(tools.create_testbench(proj, "tb_xz", duts=duts,
        vectors=[{"drive": {"a": bit}, "expect": {"u1.y": bit, "u2.y": bit}} for bit in ("x", "z")],
        comparisons=comparisons, run=True))
    duts[1]["model_path"] = inverse
    result = tools.create_testbench(proj, "tb_compare_fail", duts=duts,
        vectors=[{"drive": {"a": 0}, "expect": {"u1.y": 0, "u2.y": 1}}],
        comparisons=comparisons, run=True)
    assert result["run"]["code"] == "selftest_failed"  # both literals match; only the comparison fails
    signature = inspect.signature(server_app.create_testbench)
    assert signature.parameters["model_path"].default is None
    assert {"duts", "comparisons"} <= set(signature.parameters)
