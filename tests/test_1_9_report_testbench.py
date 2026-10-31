from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest

from robei_mcp import tools
from robei_mcp.config import SETTINGS


@pytest.fixture()
def project() -> str:
    name = f"pytest19_report_{uuid.uuid4().hex[:8]}"
    proj = tools.create_robei_project(name, models=["inv"])["proj"]
    tools.create_robei_model(
        proj,
        "inv",
        ports=[{"name": "a", "direction": "input"}, {"name": "y", "direction": "output"}],
        code="assign y = ~a;",
        overwrite=True,
    )
    tools.create_robei_test(
        proj,
        "tb_inv",
        "reg a; wire y; inv dut(.a(a),.y(y)); initial begin a=0; #1; $finish; end",
        overwrite=True,
    )
    try:
        yield proj
    finally:
        shutil.rmtree(SETTINGS.workspace / name, ignore_errors=True)


def _fake_sim(*args, **kwargs):
    return {
        "ok": True,
        "simulation": {"returncode": 0, "duration_ms": 5},
        "vcd_paths": [],
        "display_output": "SELFTEST PASS: tb_inv 1/1 vectors",
        "headless": {"tb_source_rel": "build/tb_inv.v"},
    }


def test_build_report_includes_semantic_tb_facts(project: str, monkeypatch):
    monkeypatch.setattr(tools, "simulate_project", _fake_sim)
    report = tools.build_report(
        project,
        tests=["tb_inv"],
        out="semantic.md",
        include=["simulation"],
        testbench_results=[{
            "test": "tb_inv",
            "ok": True,
            "semantic": {"mode": "combinational", "vector_count": 4, "assertion_count": 4},
            "run": {"ok": True, "tests": [{"display_output": "SELFTEST PASS", "run_dir": "build/sim-x"}]},
        }],
    )
    text = Path(report["report_path"]).read_text(encoding="utf-8")
    assert "Semantic testbench facts" in text
    assert "4" in text
    assert report["testbench"][0]["selftest"] == "PASS"


def test_build_report_missing_tb_fields_is_not_fatal(project: str, monkeypatch):
    monkeypatch.setattr(tools, "simulate_project", _fake_sim)
    report = tools.build_report(
        project,
        tests=["tb_inv"],
        out="missing-semantic.md",
        include=["simulation"],
        testbench_results=[{"test": "tb_inv", "semantic": {}, "run": {}}],
    )
    text = Path(report["report_path"]).read_text(encoding="utf-8")
    assert report["ok"] is True
    assert "未提供" in text
    assert "[evidence:" in text


def test_build_report_ignores_malformed_or_unmatched_tb_entries(project: str, monkeypatch):
    monkeypatch.setattr(tools, "simulate_project", _fake_sim)
    report = tools.build_report(
        project,
        tests=["tb_inv"],
        out="malformed-semantic.md",
        include=["simulation"],
        testbench_results=[None, {"name": "other", "semantic": {"vector_count": 9}}, "bad"],
    )
    assert report["ok"] is True
    assert report["testbench"] == []


def test_build_report_records_selftest_fail_without_abort(project: str, monkeypatch):
    monkeypatch.setattr(tools, "simulate_project", _fake_sim)
    report = tools.build_report(
        project,
        tests=["tb_inv"],
        out="failed-semantic.md",
        include=["simulation"],
        testbench_results=[{
            "test": "tb_inv",
            "ok": False,
            "semantic": {"mode": "timed", "vector_count": 0, "assertion_count": 1},
            "run": {"ok": False, "tests": [{"display_output": "SELFTEST FAIL: 1 mismatches", "run_dir": "build/sim-bad"}]},
        }],
    )
    assert report["ok"] is True
    assert report["testbench"][0]["selftest"] == "FAIL"
