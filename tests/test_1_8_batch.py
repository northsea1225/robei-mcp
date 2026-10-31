from __future__ import annotations

import shutil
import threading
import time
import uuid
from pathlib import Path

import pytest

from robei_mcp import tools
from robei_mcp.config import SETTINGS


@pytest.fixture()
def project() -> str:
    name = f"pytest18_batch_{uuid.uuid4().hex[:8]}"
    proj = tools.create_robei_project(name, models=["inv"])["proj"]
    tools.create_robei_model(
        proj,
        "inv",
        ports=[
            {"name": "a", "direction": "input", "width": 1},
            {"name": "y", "direction": "output", "width": 1},
        ],
        code="assign y = ~a;",
        overwrite=True,
    )
    try:
        yield proj
    finally:
        shutil.rmtree(SETTINGS.workspace / name, ignore_errors=True)


def _test(project: str, name: str) -> None:
    tools.create_robei_test(
        project,
        name,
        code="reg a; wire y; inv u(.a(a), .y(y)); initial begin a=0; #1; $finish; end",
        overwrite=True,
    )


def _ok_result(**kwargs):
    return {
        "ok": True,
        "simulation": {"returncode": 0, "duration_ms": 7},
        "vcd_paths": ["workspace/build/sim-x/tb.vcd"],
        "display_output": "",
        **kwargs,
    }


def test_run_all_tests_extracts_nested_returncode_and_duration(project: str, monkeypatch) -> None:
    _test(project, "tb_one")
    monkeypatch.setattr(tools, "simulate_project", lambda **kwargs: _ok_result())
    result = tools.run_all_tests(project)
    row = result["tests"][0]
    assert result["total"] == result["passed"] == 1
    assert row["returncode"] == 0
    assert row["duration_ms"] == 7
    assert row["timed_out"] is False
    assert row["vcd_paths"]
    assert row["error_tail"] == []


def test_run_all_tests_selects_named_tests_and_accepts_suffix(project: str, monkeypatch) -> None:
    _test(project, "tb_one")
    _test(project, "tb_two")
    monkeypatch.setattr(tools, "simulate_project", lambda **kwargs: _ok_result())
    result = tools.run_all_tests(project, tests=["tb_two.test"])
    assert [row["test"] for row in result["tests"]] == ["tb_two"]
    with pytest.raises(ValueError):
        tools.run_all_tests(project, tests=["../escape"])


def test_run_all_tests_rejects_parallelism_above_two(project: str) -> None:
    with pytest.raises(ValueError, match="between 1 and 2"):
        tools.run_all_tests(project, max_parallel=3)


def test_run_all_tests_parallel_mode_is_bounded(project: str, monkeypatch) -> None:
    for name in ("tb_one", "tb_two", "tb_three"):
        _test(project, name)
    lock = threading.Lock()
    active = 0
    peak = 0

    def fake(**kwargs):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.04)
        with lock:
            active -= 1
        return _ok_result()

    monkeypatch.setattr(tools, "simulate_project", fake)
    result = tools.run_all_tests(project, max_parallel=2)
    assert result["passed"] == 3
    assert peak <= 2
    assert peak >= 2


def test_run_all_tests_fail_fast_marks_remaining_aborted(project: str, monkeypatch) -> None:
    for name in ("tb_a", "tb_b", "tb_c"):
        _test(project, name)
    calls: list[str] = []

    def fake(**kwargs):
        calls.append(kwargs["test_name"])
        if kwargs["test_name"] == "tb_a":
            return {"ok": False, "code": "run_failed", "simulation": {"returncode": 1, "duration_ms": 2}, "stderr": "bad"}
        return _ok_result()

    monkeypatch.setattr(tools, "simulate_project", fake)
    result = tools.run_all_tests(project, fail_fast=True)
    assert calls == ["tb_a"]
    assert result["failed"] == 1
    assert result["aborted"] == 2
    assert all(row.get("aborted") for row in result["tests"] if row["test"] != "tb_a")


def test_run_all_tests_total_timeout_aborts_pending(project: str, monkeypatch) -> None:
    _test(project, "tb_slow")

    def slow(**kwargs):
        time.sleep(1.2)
        return _ok_result()

    monkeypatch.setattr(tools, "simulate_project", slow)
    result = tools.run_all_tests(project, total_timeout_s=1)
    assert result["total"] == 1
    assert result["aborted"] == 1
    assert result["tests"][0]["aborted"] is True


def test_run_all_tests_timeout_does_not_stop_following_tests(project: str, monkeypatch) -> None:
    _test(project, "tb_timeout")
    _test(project, "tb_ok")

    def fake(**kwargs):
        if kwargs["test_name"] == "tb_timeout":
            return {
                "ok": False,
                "code": "timeout",
                "simulation": {"returncode": None, "duration_ms": 10, "timeout": True},
                "stderr": "TIMEOUT after 1s",
                "vcd_paths": [],
            }
        return _ok_result()

    monkeypatch.setattr(tools, "simulate_project", fake)
    result = tools.run_all_tests(project)
    by_name = {row["test"]: row for row in result["tests"]}
    assert result["failed"] == 1
    assert by_name["tb_timeout"]["timed_out"] is True
    assert by_name["tb_ok"]["ok"] is True


def test_run_all_tests_null_fields_get_warnings(project: str, monkeypatch) -> None:
    _test(project, "tb_missing")
    monkeypatch.setattr(tools, "simulate_project", lambda **kwargs: {"ok": False})
    row = tools.run_all_tests(project)["tests"][0]
    assert row["returncode"] is None
    assert row["duration_ms"] is None
    assert row["vcd_paths"] is None
    assert row["error_tail"] is None
    assert {item["field"] for item in row["warnings"]} >= {
        "returncode", "duration_ms", "vcd_paths", "error_tail"
    }


def test_run_all_tests_empty_selection_is_not_ok(project: str) -> None:
    result = tools.run_all_tests(project, tests=[])
    assert result["ok"] is False
    assert result["total"] == result["passed"] == result["failed"] == result["aborted"] == 0
    assert result["tests"] == []
    assert result["warnings"] == []
