from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest

from robei_mcp import errors, tools
from robei_mcp.config import SETTINGS


@pytest.fixture()
def project() -> str:
    name = f"pytest19_timed_{uuid.uuid4().hex[:8]}"
    proj = tools.create_robei_project(name, models=["counter"])["proj"]
    tools.create_robei_model(
        proj,
        "counter",
        ports=[
            {"name": "clk", "direction": "input"},
            {"name": "rst", "direction": "input"},
            {"name": "en", "direction": "input"},
            {"name": "cnt", "direction": "output", "width": 4},
        ],
        code="reg [3:0] q; always @(posedge clk) if (rst) q <= 0; else if (en) q <= q + 1'b1; assign cnt = q;",
        overwrite=True,
    )
    try:
        yield proj
    finally:
        shutil.rmtree(SETTINGS.workspace / name, ignore_errors=True)


def _timed(project: str, **kwargs):
    model = str(Path(project).parent / "model" / "counter.model")
    base = {
        "proj_path": project,
        "name": "tb_timed",
        "model_path": model,
        "mode": "timed",
        "clock": {"signal": "clk", "period": 10, "edge": "posedge"},
        "reset": {"signal": "rst", "active": 1, "at": 0, "duration": 10},
        "stimuli": [{"at": 0, "drive": {"en": 1}}],
        "assertions": [{"after_edge": 2, "expect": {"cnt": 1}}],
    }
    base.update(kwargs)
    return tools.create_testbench(**base)


def test_timed_generation_has_clock_reset_edge_assertions(project: str):
    result = _timed(project)
    assert result["ok"] is True
    assert result["semantic"]["mode"] == "timed"
    assert "always #5 clk = ~clk;" in result["code"]
    assert "repeat (2)" in result["code"]
    assert "module" not in result["code"].lower()
    assert "$dump" not in result["code"]


def test_timed_accepts_absolute_assertion_and_same_time_stimulus(project: str):
    result = _timed(
        project,
        name="tb_absolute",
        stimuli=[{"at": 20, "drive": {"en": 0}}, {"at": 20, "drive": {"rst": 1}}],
        assertions=[{"at": 21, "expect": {"cnt": 0}}],
    )
    assert result["ok"] is True
    assert result["semantic"]["assertion_count"] == 1


def test_timed_requires_clock(project: str):
    with pytest.raises(errors.BadRequest):
        _timed(project, clock=None)


def test_timed_rejects_clock_drive(project: str):
    with pytest.raises(errors.BadRequest):
        _timed(project, stimuli=[{"at": 10, "drive": {"clk": 1}}])


def test_timed_requires_assertions(project: str):
    with pytest.raises(errors.BadRequest):
        _timed(project, assertions=[])


def test_timed_after_edge_must_be_positive(project: str):
    with pytest.raises(errors.BadRequest):
        _timed(project, assertions=[{"after_edge": 0, "expect": {"cnt": 0}}])


def test_timed_duration_limit_is_rejected(project: str):
    with pytest.raises(errors.BadRequest) as caught:
        _timed(project, stimuli=[{"at": 1_000_001, "drive": {"en": 1}}])
    assert errors.code_for(caught.value) == "limit_exceeded"


def test_timed_run_delegates_and_preserves_edge_metadata(project: str, monkeypatch):
    calls = []
    monkeypatch.setattr(tools, "run_all_tests", lambda *args, **kwargs: calls.append(kwargs) or {"ok": True, "tests": []})
    result = _timed(project, run=True)
    assert result["ok"] is True
    assert calls and calls[0]["tests"] == ["tb_timed"]
    assert result["semantic"]["clock"]["edge"] == "posedge"


def test_timed_selftest_failure_marks_result(project: str, monkeypatch):
    monkeypatch.setattr(
        tools,
        "run_all_tests",
        lambda *args, **kwargs: {
            "ok": True,
            "tests": [{"display_output": "SELFTEST FAIL: 1 mismatches"}],
        },
    )
    result = _timed(project, run=True)
    assert result["ok"] is False
    assert result["run"]["code"] == "selftest_failed"
