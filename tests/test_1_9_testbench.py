from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest

from robei_mcp import errors, tools
from robei_mcp.config import SETTINGS


@pytest.fixture()
def project() -> str:
    name = f"pytest19_tb_{uuid.uuid4().hex[:8]}"
    proj = tools.create_robei_project(name, models=["and2"])['proj']
    tools.create_robei_model(
        proj,
        "and2",
        ports=[
            {"name": "a", "direction": "input", "width": 1},
            {"name": "b", "direction": "input", "width": 1},
            {"name": "y", "direction": "output", "width": 1},
        ],
        code="assign y = a & b;",
        overwrite=True,
    )
    try:
        yield proj
    finally:
        shutil.rmtree(SETTINGS.workspace / name, ignore_errors=True)


def vectors():
    return [
        {"drive": {"a": 0, "b": 0}, "expect": {"y": 0}},
        {"drive": {"a": 0, "b": 1}, "expect": {"y": 0}},
        {"drive": {"a": 1, "b": 0}, "expect": {"y": 0}},
        {"drive": {"a": 1, "b": 1}, "expect": {"y": 1}},
    ]


def test_create_testbench_writes_test_and_registers(project: str):
    result = tools.create_testbench(project, "tb_and2_semantic", f"{Path(project).parent / 'model' / 'and2.model'}", vectors())
    assert result["ok"] is True
    assert result["registered_in_project"] is True
    assert result["semantic"]["vector_count"] == 4
    assert "module" not in result["code"].lower()
    assert "$dump" not in result["code"]
    assert "===" in result["code"]
    assert "$display" in result["code"]
    assert "$finish" in result["code"]
    assert Path(result["path"]).is_file()


def test_create_testbench_reuses_overwrite_rules(project: str):
    model = str(Path(project).parent / "model" / "and2.model")
    tools.create_testbench(project, "tb_same", model, vectors())
    with pytest.raises(FileExistsError):
        tools.create_testbench(project, "tb_same", model, vectors())
    result = tools.create_testbench(project, "tb_same", model, vectors(), overwrite=True)
    assert result["ok"] is True
    assert result["backup"]


def test_create_testbench_rejects_unknown_or_missing_ports(project: str):
    model = str(Path(project).parent / "model" / "and2.model")
    with pytest.raises(errors.BadRequest):
        tools.create_testbench(project, "tb_unknown", model, [{"drive": {"a": 0, "b": 0, "z": 0}, "expect": {"y": 0}}])
    with pytest.raises(errors.BadRequest):
        tools.create_testbench(project, "tb_missing", model, [{"drive": {"a": 0}, "expect": {"y": 0}}])


def test_create_testbench_rejects_bad_width_value(project: str):
    model = str(Path(project).parent / "model" / "and2.model")
    with pytest.raises(errors.BadRequest):
        tools.create_testbench(project, "tb_bad_value", model, [{"drive": {"a": 2, "b": 0}, "expect": {"y": 0}}])


def test_create_testbench_parameterised_width_is_rejected(project: str):
    name = f"pytest19_param_{uuid.uuid4().hex[:8]}"
    proj = tools.create_robei_project(name, models=["param"])["proj"]
    try:
        tools.create_robei_model(
            proj,
            "param",
            parameters={"LEN": 4},
            ports=[{"name": "a", "direction": "input", "width": "LEN"}, {"name": "y", "direction": "output", "width": 1}],
            code="assign y = a[0];",
            overwrite=True,
        )
        with pytest.raises(errors.BadRequest):
            tools.create_testbench(proj, "tb_param", str(Path(proj).parent / "model" / "param.model"), [{"drive": {"a": 0}, "expect": {"y": 0}}])
    finally:
        shutil.rmtree(SETTINGS.workspace / name, ignore_errors=True)


def test_create_testbench_input_bit_limit(project: str):
    name = f"pytest19_wide_{uuid.uuid4().hex[:8]}"
    proj = tools.create_robei_project(name, models=["wide"])["proj"]
    try:
        tools.create_robei_model(
            proj,
            "wide",
            ports=[{"name": "a", "direction": "input", "width": 13}, {"name": "y", "direction": "output", "width": 1}],
            code="assign y = a[0];",
            overwrite=True,
        )
        with pytest.raises(errors.BadRequest) as caught:
            tools.create_testbench(proj, "tb_wide", str(Path(proj).parent / "model" / "wide.model"), [{"drive": {"a": 0}, "expect": {"y": 0}}])
        assert errors.code_for(caught.value) == "limit_exceeded"
    finally:
        shutil.rmtree(SETTINGS.workspace / name, ignore_errors=True)


def test_create_testbench_vector_limit(project: str):
    model = str(Path(project).parent / "model" / "and2.model")
    with pytest.raises(errors.BadRequest) as caught:
        tools.create_testbench(project, "tb_many", model, [{}] * 4097)
    assert errors.code_for(caught.value) == "limit_exceeded"


def test_create_testbench_run_delegates_to_run_all_tests(project: str, monkeypatch):
    calls = []
    monkeypatch.setattr(tools, "run_all_tests", lambda *args, **kwargs: calls.append((args, kwargs)) or {"ok": True, "passed": 1})
    model = str(Path(project).parent / "model" / "and2.model")
    result = tools.create_testbench(project, "tb_run", model, vectors(), run=True)
    assert result["ok"] is True
    assert result["run"]["ok"] is True
    assert calls and calls[0][1]["tests"] == ["tb_run"]


def test_create_testbench_failed_run_is_not_ok(project: str, monkeypatch):
    monkeypatch.setattr(tools, "run_all_tests", lambda *args, **kwargs: {"ok": False, "failed": 1, "tests": [{"ok": False}]})
    model = str(Path(project).parent / "model" / "and2.model")
    result = tools.create_testbench(project, "tb_fail", model, vectors(), run=True)
    assert result["ok"] is False
    assert result["run"]["ok"] is False
