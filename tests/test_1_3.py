"""1.3: nested project names, skeleton switch, project-local artifacts, tight read_run_log."""
from __future__ import annotations

import base64
import shutil
import uuid
from pathlib import Path

import pytest

from robei_mcp import tools
from robei_mcp.config import SETTINGS
from robei_mcp.errors import LogPathEscape, code_for

IVERILOG_MISSING = not Path(SETTINGS.iverilog).is_file()
needs_iverilog = pytest.mark.skipif(
    IVERILOG_MISSING, reason=f"iverilog not found at {SETTINGS.iverilog}"
)

VCD = (
    "$timescale 1ns $end\n$scope module qa $end\n"
    "$var wire 1 ! a $end\n$upscope $end\n$enddefinitions $end\n"
    "#0\n1!\n#10\n0!\n#20\n1!\n"
)


@pytest.fixture()
def parent_name() -> str:
    name = f"pytest13_{uuid.uuid4().hex[:6]}"
    yield name
    shutil.rmtree(SETTINGS.workspace / name, ignore_errors=True)


def _project(parent: str, leaf: str, **kwargs) -> dict:
    return tools.create_robei_project(f"{parent}/{leaf}", **kwargs)


# ---------------------------------------------------------------------------
# P0-2 nested project names
# ---------------------------------------------------------------------------
def test_nested_name_creates_the_sub_directory(parent_name: str) -> None:
    created = _project(parent_name, "exp1", models=["inv"])
    root = Path(created["root"])
    assert root == (SETTINGS.workspace / parent_name / "exp1").resolve()
    assert created["project"] == "exp1"
    assert created["project_path"] == f"{parent_name}/exp1"
    proj = Path(created["proj"])
    assert proj.name == "exp1.proj" and proj.is_file()
    text = proj.read_text(encoding="utf-8")
    assert text.startswith('<exp1 name="exp1" parent="0" type="proj">')
    assert f'parent="{parent_name}"' not in text
    assert "<exp1.proj" not in text
    for sub in ("model", "test", "const", "verilog"):
        assert (root / sub).is_dir()
    assert (root / "model" / "inv.model").is_file()


def test_nested_project_reads_back_through_both_resolvers(parent_name: str) -> None:
    created = _project(parent_name, "reader", models=["inv"])
    info = tools.read_robei_project(created["proj"])
    assert info["root_tag"] == "reader"
    assert info["project_name"] == "reader"
    assert info["folders"]["model"]["file_count"] == 1
    listed = {p["relative"] for p in tools.list_projects(limit=500)["projects"]}
    assert f"{parent_name}/reader/reader.proj" in listed


@pytest.mark.parametrize(
    "bad",
    ["../x", "/abs", "a//b", "a/../b", "a\\b", "1bad", "", "a/-b", "a/b/", "a/."],
)
def test_invalid_nested_names_are_rejected(bad: str) -> None:
    with pytest.raises(ValueError):
        tools.create_robei_project(bad)
    with pytest.raises(ValueError):
        tools.create_robei_project(bad, skeleton=False)


# ---------------------------------------------------------------------------
# P1-7 skeleton switch
# ---------------------------------------------------------------------------
def test_skeleton_default_still_creates_the_blank_model(parent_name: str) -> None:
    created = _project(parent_name, "skel")
    assert created["skeleton"] is True
    assert (Path(created["root"]) / "model" / "skel.model").is_file()


def test_skeleton_false_creates_no_model_and_no_empty_line(parent_name: str) -> None:
    created = _project(parent_name, "bare", skeleton=False)
    assert created["skeleton"] is False
    assert created["models"] == []
    root = Path(created["root"])
    assert not (root / "model" / "bare.model").exists()
    text = Path(created["proj"]).read_text(encoding="utf-8")
    assert '<model name="model" parent="bare" type="folder"/>' in text
    assert "\n\n" not in text
    assert tools.read_robei_project(created["proj"])["models"] == []


def test_explicit_empty_models_matches_skeleton_false(parent_name: str) -> None:
    created = _project(parent_name, "empty", models=[])
    assert created["models"] == []
    assert not (Path(created["root"]) / "model" / "empty.model").exists()


# ---------------------------------------------------------------------------
# P1-5 artifacts land in the project directory
# ---------------------------------------------------------------------------
@needs_iverilog
def test_simulation_output_lands_in_the_project_build(parent_name: str) -> None:
    created = _project(parent_name, "simrun", models=[])
    tools.create_robei_test(
        created["proj"],
        "tb_sim",
        code="reg a;\ninitial begin a = 0; #5 a = 1; #5 $finish; end",
    )
    result = tools.simulate_project(created["proj"], "tb_sim", timeout_s=30)
    assert result["ok"] is True
    tb = Path(result["headless"]["tb_source"])
    build = (Path(created["root"]) / "build").resolve()
    assert tb.parent.parent == build
    assert tb.parent.name.startswith("headless-simrun-tb_sim-")


def test_render_waveform_defaults_to_the_project_build(parent_name: str) -> None:
    created = _project(parent_name, "wave", models=[])
    root = Path(created["root"])
    (root / "build").mkdir()
    vcd = root / "build" / "qa.vcd"
    vcd.write_text(VCD, encoding="utf-8", newline="\n")
    rel = vcd.relative_to(SETTINGS.workspace).as_posix()
    out = tools.render_waveform(rel, ["qa.a"], title="t")
    assert out["ok"] is True
    assert Path(out["path"]).parent == (root / "build" / "waveforms").resolve()
    assert out["project"] == f"{parent_name}/wave"
    assert out["out_dir"] == f"{parent_name}/wave/build/waveforms"


def test_render_waveform_out_dir_override_and_escape(parent_name: str) -> None:
    created = _project(parent_name, "wave2", models=[])
    root = Path(created["root"])
    (root / "build").mkdir()
    vcd = root / "build" / "qa.vcd"
    vcd.write_text(VCD, encoding="utf-8", newline="\n")
    rel = vcd.relative_to(SETTINGS.workspace).as_posix()
    out = tools.render_waveform(rel, ["qa.a"], out_dir=f"{parent_name}/wave2/art")
    assert Path(out["path"]).parent == (root / "art").resolve()
    with pytest.raises(Exception) as caught:
        tools.render_waveform(rel, ["qa.a"], out_dir="../escape")
    assert code_for(caught.value) in ("path_escape", "invalid_argument")


def test_run_dir_rejects_a_base_outside_the_workspace(parent_name: str) -> None:
    with pytest.raises(ValueError):
        tools._new_run_dir("lint", base=Path("C:/Windows/Temp"))


# ---------------------------------------------------------------------------
# P1-8 read_run_log: three roots only
# ---------------------------------------------------------------------------
def test_read_run_log_accepts_the_three_roots(parent_name: str) -> None:
    created = _project(parent_name, "logs", models=[])
    workspace = SETTINGS.workspace
    stamp = f"pytest13-{uuid.uuid4().hex[:8]}.log"
    logs = workspace / "logs" / stamp
    logs.parent.mkdir(parents=True, exist_ok=True)
    logs.write_text("one\ntwo\n", encoding="utf-8")
    run_dir = workspace / "build" / "headless-demo-tb-20261025-010203-abcdef12"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "route.log").write_text("x\n", encoding="utf-8")
    proj_log = Path(created["root"]) / "build" / "route.log"
    proj_log.parent.mkdir(parents=True, exist_ok=True)
    proj_log.write_text("y\n", encoding="utf-8")
    try:
        for path in (logs, run_dir / "route.log", proj_log):
            rel = path.relative_to(workspace).as_posix()
            result = tools.read_run_log(rel, tail=5)
            assert result["ok"] is True and result["lines"]
    finally:
        logs.unlink(missing_ok=True)
        shutil.rmtree(run_dir, ignore_errors=True)


def test_read_run_log_rejects_a_build_dir_without_a_project(parent_name: str) -> None:
    workspace = SETTINGS.workspace
    bogus = workspace / parent_name / "scratch" / "build" / "x.log"
    bogus.parent.mkdir(parents=True, exist_ok=True)
    bogus.write_text("z\n", encoding="utf-8")
    with pytest.raises(LogPathEscape) as caught:
        tools.read_run_log(bogus.relative_to(workspace).as_posix())
    assert code_for(caught.value) == "path_escape_log"
    assert "project" in str(caught.value) or "build" in str(caught.value)


def test_read_run_log_rejects_an_unstamped_workspace_build_subdir() -> None:
    workspace = SETTINGS.workspace
    path = workspace / "build" / "waveforms" / f"pytest13-{uuid.uuid4().hex[:6]}.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("z\n", encoding="utf-8")
    try:
        with pytest.raises(LogPathEscape):
            tools.read_run_log(path.relative_to(workspace).as_posix())
    finally:
        path.unlink(missing_ok=True)


def test_read_run_log_rejects_a_model_file_even_inside_a_project(parent_name: str) -> None:
    created = _project(parent_name, "strict", models=["inv"])
    model = Path(created["root"]) / "model" / "inv.model"
    with pytest.raises(LogPathEscape):
        tools.read_run_log(model.relative_to(SETTINGS.workspace).as_posix())


# ---------------------------------------------------------------------------
# P1-6 inline PNG (ImageContent)
# ---------------------------------------------------------------------------
def _waveform(created: dict) -> str:
    root = Path(created["root"])
    (root / "build").mkdir(exist_ok=True)
    vcd = root / "build" / "qa.vcd"
    vcd.write_text(VCD, encoding="utf-8", newline="\n")
    return vcd.relative_to(SETTINGS.workspace).as_posix()


def test_inline_is_off_by_default(parent_name: str) -> None:
    created = _project(parent_name, "noinline", models=[])
    out = tools.render_waveform(_waveform(created), ["qa.a"])
    assert "inline" not in out
    assert Path(out["path"]).is_file()


def test_inline_returns_the_png_as_base64(parent_name: str) -> None:
    created = _project(parent_name, "inline", models=[])
    out = tools.render_waveform(_waveform(created), ["qa.a"], inline=True)
    inline = out["inline"]
    assert inline["ok"] is True
    assert inline["mime_type"] == "image/png"
    assert inline["size_bytes"] == Path(out["path"]).stat().st_size
    assert base64.b64decode(inline["data_b64"])[:8] == b"\x89PNG\r\n\x1a\n"


def test_inline_reports_too_large_and_keeps_the_path(parent_name: str, monkeypatch) -> None:
    created = _project(parent_name, "biginline", models=[])
    monkeypatch.setattr(tools, "INLINE_IMAGE_LIMIT", 128)
    out = tools.render_waveform(_waveform(created), ["qa.a"], inline=True)
    assert out["ok"] is True
    assert Path(out["path"]).is_file()
    assert out["inline"]["ok"] is False
    assert out["inline"]["code"] == "image_too_large"
    assert out["inline"]["limit_bytes"] == 128
    assert out["inline"]["size_bytes"] > 128
    assert "data_b64" not in out["inline"]


@needs_iverilog
def test_simulate_project_can_inline_the_plot(parent_name: str) -> None:
    created = _project(parent_name, "siminline", models=[])
    tools.create_robei_test(
        created["proj"],
        "tb_plot",
        code="reg a;\ninitial begin a = 0; #5 a = 1; #5 $finish; end",
    )
    result = tools.simulate_project(
        created["proj"], "tb_plot", timeout_s=30, render=True, inline=True
    )
    assert result["plot"]["inline"]["ok"] is True
    assert Path(result["plot"]["path"]).is_file()
