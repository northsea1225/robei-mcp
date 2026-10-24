"""Regression tests for 1.1.0 (M1: component library)."""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest

from robei_mcp import tools
from robei_mcp.config import SETTINGS
from robei_mcp.model import parse_robei_model


@pytest.fixture()
def work() -> Path:
    root = SETTINGS.workspace / ".tmp" / f"pytest11-{uuid.uuid4().hex[:8]}"
    root.mkdir(parents=True, exist_ok=False)
    yield root
    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture()
def fake_library(tmp_path, monkeypatch) -> Path:
    """Two library models, one with a module name that differs from its file name."""
    root = tmp_path / "toolbox"
    root.mkdir()
    (root / "plain.model").write_text(
        '<!DOCTYPE Robei>\n<Module Category="Current" Class="module" Code="assign y = a;" '
        'Comment="" Height="600" Name="plain" Parameters="LEN = 1; // width" Parent="0" '
        'Type="model" Width="900" X="0" Y="0">\n'
        ' <Port Datatype="wire" Datasize="LEN-1:0" Inout="input" Name="a" Parent="plain"/>\n'
        ' <Port Datatype="wire" Datasize="LEN-1:0" Inout="output" Name="y" Parent="plain"/>\n'
        "</Module>\n",
        encoding="utf-8",
    )
    (root / "odd_name.model").write_text(
        '<!DOCTYPE Robei>\n<Module Category="Current" Class="module" Code="assign y = 0;" '
        'Comment="" Height="600" Name="real_module" Parameters="" Parent="0" '
        'Type="model" Width="900" X="0" Y="0">\n'
        ' <Port Datatype="wire" Datasize="1" Inout="output" Name="y" Parent="real_module"/>\n'
        "</Module>\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(tools.LIBRARY_ENV, str(root))
    return root


def _project(name: str) -> Path:
    return Path(tools.create_robei_project(name)["proj"])


# ---------------------------------------------------------------------------
# list_library_models
# ---------------------------------------------------------------------------


def test_list_library_models_reports_ports_parameters_and_lint(fake_library: Path) -> None:
    res = tools.list_library_models(include_lint=False)
    assert res["ok"] is True
    assert res["roots"] == [str(fake_library)]
    names = {m["name"]: m for m in res["models"]}
    assert set(names) == {"plain", "odd_name"}
    plain = names["plain"]
    assert plain["module_name"] == "plain"
    assert plain["name_matches_module"] is True
    assert [p["name"] for p in plain["ports"]] == ["a", "y"]
    assert plain["parameters"] == [{"name": "LEN", "default": "1", "comment": "width"}]
    assert "lint" not in plain  # include_lint=False must not shell out


def test_list_library_models_flags_name_module_mismatch(fake_library: Path) -> None:
    res = tools.list_library_models(include_lint=False)
    odd = next(m for m in res["models"] if m["name"] == "odd_name")
    assert odd["module_name"] == "real_module"
    assert odd["name_matches_module"] is False


def test_list_library_models_filter(fake_library: Path) -> None:
    assert [m["name"] for m in tools.list_library_models(filter="plain", include_lint=False)["models"]] == ["plain"]
    assert [m["name"] for m in tools.list_library_models(filter="real_module", include_lint=False)["models"]] == ["odd_name"]
    assert tools.list_library_models(filter="nothing-here", include_lint=False)["models"] == []


@pytest.mark.skipif(not Path(SETTINGS.iverilog).exists(), reason="iverilog not installed")
def test_list_library_models_lint_is_a_real_compile(fake_library: Path) -> None:
    res = tools.list_library_models(filter="plain")
    lint = res["models"][0]["lint"]
    assert lint["ok"] is True, lint
    assert lint["returncode"] == 0
    assert lint["tool"] == "iverilog -g2012"


# ---------------------------------------------------------------------------
# import_library_model
# ---------------------------------------------------------------------------


def test_import_dry_run_writes_nothing(fake_library: Path) -> None:
    name = f"libdry_{uuid.uuid4().hex[:6]}"
    project = _project(name)
    try:
        out = tools.import_library_model("plain", project, dry_run=True)
        assert out["dry_run"] is True
        assert out["registration"] == "pending"
        assert not (project.parent / "model" / "plain.model").exists()
        assert "plain.model" not in project.read_text(encoding="utf-8")
    finally:
        shutil.rmtree(SETTINGS.workspace / name, ignore_errors=True)


def test_import_copies_file_registers_and_backs_up_project(fake_library: Path) -> None:
    name = f"libok_{uuid.uuid4().hex[:6]}"
    project = _project(name)
    try:
        out = tools.import_library_model("plain", project)
        target = project.parent / "model" / "plain.model"
        assert out["ok"] is True and out["dry_run"] is False
        assert target.is_file()
        assert out["registration"] == "created"
        assert out["proj_backup"] and Path(out["proj_backup"]).is_file()
        assert "plain.model" in project.read_text(encoding="utf-8")
        assert parse_robei_model(target)["name"] == "plain"
        assert out["lint"]["ok"] is True
    finally:
        shutil.rmtree(SETTINGS.workspace / name, ignore_errors=True)


def test_import_existing_model_returns_already_exists_code(fake_library: Path) -> None:
    from robei_mcp.errors import code_for

    name = f"libdup_{uuid.uuid4().hex[:6]}"
    project = _project(name)
    try:
        tools.import_library_model("plain", project)
        with pytest.raises(FileExistsError) as caught:
            tools.import_library_model("plain", project)
        assert code_for(caught.value) == "already_exists"
        assert "overwrite=true" in str(caught.value)
    finally:
        shutil.rmtree(SETTINGS.workspace / name, ignore_errors=True)


def test_import_overwrite_keeps_a_backup(fake_library: Path) -> None:
    name = f"libovr_{uuid.uuid4().hex[:6]}"
    project = _project(name)
    try:
        tools.import_library_model("plain", project)
        target = project.parent / "model" / "plain.model"
        target.write_text(target.read_text(encoding="utf-8").replace("plain", "handedit", 1), encoding="utf-8")
        out = tools.import_library_model("plain", project, overwrite=True)
        assert out["backup"] and Path(out["backup"]).is_file()
        assert parse_robei_model(target)["name"] == "plain"
    finally:
        shutil.rmtree(SETTINGS.workspace / name, ignore_errors=True)


def test_import_requires_as_name_when_module_name_differs(fake_library: Path) -> None:
    name = f"libconf_{uuid.uuid4().hex[:6]}"
    project = _project(name)
    try:
        with pytest.raises(ValueError, match="as_name"):
            tools.import_library_model("odd_name", project)
        assert not (project.parent / "model" / "odd_name.model").exists()
    finally:
        shutil.rmtree(SETTINGS.workspace / name, ignore_errors=True)


def test_import_with_as_name_renames_file_and_module(fake_library: Path) -> None:
    name = f"libren_{uuid.uuid4().hex[:6]}"
    project = _project(name)
    try:
        out = tools.import_library_model("real_module", project, as_name="chosen")
        target = project.parent / "model" / "chosen.model"
        assert out["renamed_module"] == {"from": "real_module", "to": "chosen"}
        assert parse_robei_model(target)["name"] == "chosen"
        assert "chosen.model" in project.read_text(encoding="utf-8")
    finally:
        shutil.rmtree(SETTINGS.workspace / name, ignore_errors=True)


def test_import_unknown_library_model() -> None:
    name = f"libnone_{uuid.uuid4().hex[:6]}"
    project = _project(name)
    try:
        with pytest.raises(FileNotFoundError, match="list_library_models"):
            tools.import_library_model("definitely-not-a-model-xyz", project)
    finally:
        shutil.rmtree(SETTINGS.workspace / name, ignore_errors=True)


@pytest.mark.skipif(
    not any((root / "clock.model").is_file() for root in tools.library_roots()),
    reason="installed Robei ToolBox not available",
)
def test_real_toolbox_lint_passes() -> None:
    res = tools.list_library_models(filter="clock")
    assert res["model_count"] == 1
    entry = res["models"][0]
    assert entry["module_name"] == "clock"
    assert [p["name"] for p in entry["parameters"]] == ["START", "DUTY"]
    assert entry["lint"]["ok"] is True, entry["lint"]
