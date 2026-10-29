from __future__ import annotations

import shutil
from dataclasses import replace
import uuid
from pathlib import Path

import pytest

from robei_mcp import errors, tools
from robei_mcp.config import SETTINGS


@pytest.fixture()
def source() -> Path:
    name = f"pytest17v_{uuid.uuid4().hex[:8]}"
    proj = Path(tools.create_robei_project(name)["proj"])
    path = proj.parent / "build" / "sim-fixture" / "tb.vcd"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "$timescale 1ps $end\n$scope module tb $end\n"
        "$var wire 1 ! clk $end\n$var wire 1 \" en $end\n"
        "$var wire 4 # cnt [3:0] $end\n$var wire 1 $ x $end\n"
        "$scope module dut $end\n$var wire 1 % clk $end\n$upscope $end\n"
        "$upscope $end\n$enddefinitions $end\n#0\n0!\n1\"\n"
        "b1001 #\nx$\n#100000\n1!\n#105000\n0!\nb0000 #\nz$\n",
        encoding="utf-8",
    )
    yield path
    shutil.rmtree(SETTINGS.workspace / name, ignore_errors=True)


def _rel(path: Path) -> str:
    return str(path.relative_to(SETTINGS.workspace)).replace("\\", "/")


def test_vcd_window_rejects_unknown_and_ambiguous_names(source: Path) -> None:
    rel = _rel(source)
    with pytest.raises(KeyError) as exc:
        tools.export_vcd_window(rel, ["does_not_exist"], 0, 105000)
    assert errors.code_for(exc.value) == "bad_argument"
    with pytest.raises(KeyError) as exc:
        tools.export_vcd_window(rel, ["clk"], 0, 105000)
    assert errors.code_for(exc.value) == "bad_argument"


def test_vcd_window_validates_time_and_empty_signals(source: Path) -> None:
    rel = _rel(source)
    for start, end in [(-1, 10), (10, 0)]:
        with pytest.raises(ValueError) as exc:
            tools.export_vcd_window(rel, ["tb.clk"], start, end)
        assert errors.code_for(exc.value) == "invalid_argument"
    with pytest.raises(ValueError) as exc:
        tools.export_vcd_window(rel, [], 0, 105000)
    assert errors.code_for(exc.value) == "invalid_argument"


def test_vcd_window_edges_integer_step_initial_and_xz(source: Path, monkeypatch) -> None:
    rel = _rel(source)
    edges = tools.export_vcd_window(rel, ["tb.clk", "tb.x"], 95000, 105000, format="md", step="edges")
    assert edges["timescale"] == "1ps"
    text = Path(edges["path"]).read_text(encoding="utf-8")
    assert "95000" in text and "105000" in text and "z" in text.lower()
    sampled = tools.export_vcd_window(rel, ["tb.clk"], 95000, 105000, step=5000, include_initial=False)
    assert sampled["window"]["step"] == 5000
    assert sampled["rows"] >= 3
    monkeypatch.setattr(tools, "SETTINGS", replace(tools.SETTINGS, max_vcd_bytes=10))
    with pytest.raises(ValueError):
        tools.export_vcd_window(rel, ["tb.clk"], 0, 105000)


def test_vcd_window_default_no_overwrite_and_output_escape(source: Path) -> None:
    rel = _rel(source)
    first = tools.export_vcd_window(rel, ["tb.clk"], 0, 105000, out="fixed.csv")
    with pytest.raises(FileExistsError):
        tools.export_vcd_window(rel, ["tb.clk"], 0, 105000, out="fixed.csv")
    with pytest.raises(Exception) as exc:
        tools.export_vcd_window(rel, ["tb.clk"], 0, 105000, out="../escape.csv")
    assert errors.code_for(exc.value) == "path_escape"
    assert Path(first["path"]).is_file()
