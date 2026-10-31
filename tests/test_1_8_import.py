from __future__ import annotations

import hashlib
import shutil
import uuid
from pathlib import Path

import pytest

from robei_mcp import errors, p1, tools
from robei_mcp.config import SETTINGS
from robei_mcp.verilog_ast import parse_module


@pytest.fixture()
def project() -> str:
    name = f"pytest18_import_{uuid.uuid4().hex[:8]}"
    proj = tools.create_robei_project(name, models=["child"])["proj"]
    tools.create_robei_model(
        proj,
        "child",
        ports=[
            {"name": "a", "direction": "input", "width": 1},
            {"name": "y", "direction": "output", "width": 1},
        ],
        code="assign y = a;",
        overwrite=True,
    )
    try:
        yield proj
    finally:
        shutil.rmtree(SETTINGS.workspace / name, ignore_errors=True)


def _source(project: str, name: str, body: str) -> str:
    path = Path(project).parent / "verilog" / f"{name}.v"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8", newline="\n")
    return path.relative_to(SETTINGS.workspace).as_posix()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_assign_only_is_behavioral_with_evidence(project: str) -> None:
    rel = _source(project, "and2", "module and2(input a, output y); assign y = a; endmodule\n")
    module, classification, evidence, blocks, _ = p1._classify_verilog_module(
        Path(SETTINGS.workspace / rel).read_text(encoding="utf-8")
    )
    assert module["name"] == "and2"
    assert classification == "behavioral"
    assert evidence == {"instances": 0, "wires": 0, "assigns": 1, "behavior_blocks": 0}
    assert blocks == []


def test_behavior_keywords_are_detected_without_legacy_flag(project: str) -> None:
    snippets = {
        "always": "always @* y = a;",
        "always_comb": "always_comb y = a;",
        "always_ff": "always_ff @(posedge a) y <= a;",
        "always_latch": "always_latch if (a) y = a;",
        "initial": "initial y = 1'b0;",
        "function": "function automatic f; f = 1'b0; endfunction",
        "task": "task t; y = a; endtask",
    }
    for token, statement in snippets.items():
        rel = _source(project, f"{token}_case", f"module m(input a, output y); {statement} endmodule\n")
        _, classification, evidence, blocks, _ = p1._classify_verilog_module(
            Path(SETTINGS.workspace / rel).read_text(encoding="utf-8")
        )
        assert classification == "behavioral", token
        assert evidence["behavior_blocks"] == 1, token
        assert blocks[0].lower() == token.lower(), token


def test_lab2_headless_classifications_match_m1() -> None:
    expected = {
        "exp1_and/verilog/and2.v": "behavioral",
        "exp2_mux/verilog/mux2_bh.v": "behavioral",
        "exp2_mux/verilog/mux2_st.v": "structural",
        "exp3_decoder/verilog/decode3_8.v": "behavioral",
        "exp4_adder/verilog/adder4_bh.v": "behavioral",
        "exp4_adder/verilog/adder4_st.v": "structural",
        "exp5_alu/verilog/alu8.v": "behavioral",
        "exp5_alu/verilog/alu8c.v": "behavioral",
        "exp5_alu/verilog/alu32_st.v": "structural",
        "exp6_counter/verilog/dec_counter.v": "behavioral",
    }
    for relative, want in expected.items():
        path = SETTINGS.workspace / relative
        assert path.is_file(), relative
        _, classification, _, _, _ = p1._classify_verilog_module(path.read_text(encoding="utf-8"))
        assert classification == want, relative


def test_behavioral_mode_returns_create_suggestion_without_writing(project: str) -> None:
    rel = _source(project, "beh", "module beh(input a, output y); assign y = a; endmodule\n")
    target = Path(project).parent / "model" / "beh.model"
    before = _sha(Path(project))
    result = p1.import_verilog_canvas(project, rel, mode="behavioral")
    assert result["ok"] is True
    assert result["classification"] == "behavioral"
    assert result["path"] is None
    assert result["create_robei_model"]["name"] == "beh"
    assert not target.exists()
    assert _sha(Path(project)) == before


def test_mixed_assign_is_rejected_with_stable_code(project: str) -> None:
    rel = _source(
        project,
        "mixed_assign",
        "module mixed_assign(input a, output y); child u(.a(a), .y(y)); assign y = a; endmodule\n",
    )
    with pytest.raises(errors.BadRequest) as excinfo:
        p1.import_verilog_canvas(project, rel)
    assert errors.code_for(excinfo.value) == "mixed_module"
    assert excinfo.value.hint
    assert not (Path(project).parent / "model" / "mixed_assign.model").exists()


def test_mixed_behavior_blocks_are_reported_before_canvas_write(project: str) -> None:
    rel = _source(
        project,
        "mixed_ff",
        "module mixed_ff(input a, output y); child u(.a(a), .y(y)); always_ff @(posedge a) y <= a; endmodule\n",
    )
    text = (SETTINGS.workspace / rel).read_text(encoding="utf-8")
    _, classification, evidence, blocks, _ = p1._classify_verilog_module(text)
    assert classification == "mixed"
    assert evidence["instances"] == 1
    assert evidence["behavior_blocks"] == 1
    assert blocks == ["always_ff"]
    with pytest.raises(errors.BadRequest):
        p1.import_verilog_canvas(project, rel)


def test_alu32_constant_connection_is_not_silent() -> None:
    path = SETTINGS.workspace / "exp5_alu" / "verilog" / "alu32_st.v"
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    module, classification, evidence, _, _ = p1._classify_verilog_module(text)
    wires, skipped = p1._wire_list(module)
    assert classification == "structural"
    assert evidence["instances"] == 4
    assert any("u0.ci <- 1'b0" == item for item in skipped)
    assert wires


def test_structural_mode_imports_canvas_and_invalid_modes_reject(project: str) -> None:
    rel = _source(
        project,
        "top_struct",
        "module top_struct(input a, output y); wire mid; child u1(.a(a), .y(mid)); child u2(.a(mid), .y(y)); endmodule\n",
    )
    result = p1.import_verilog_canvas(project, rel, mode="structural")
    assert result["ok"] is True
    assert result["classification"] == "structural"
    assert len(result["instances"]) == 2
    assert (Path(project).parent / "model" / "top_struct.model").is_file()
    with pytest.raises(ValueError, match="mode"):
        p1.import_verilog_canvas(project, rel, mode="bogus")


def test_structural_mode_rejects_behavioral_source(project: str) -> None:
    rel = _source(project, "only_beh", "module only_beh(input a, output y); assign y = a; endmodule\n")
    with pytest.raises(errors.BadRequest) as excinfo:
        p1.import_verilog_canvas(project, rel, mode="structural")
    assert errors.code_for(excinfo.value) == "not_behavioral"
