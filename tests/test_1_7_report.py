from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from pathlib import Path

import pytest

from robei_mcp import errors, tools
from robei_mcp.config import SETTINGS


@pytest.fixture()
def project() -> Path:
    name = f"pytest17r_{uuid.uuid4().hex[:8]}"
    proj = Path(tools.create_robei_project(name)["proj"])
    tools.create_robei_test(proj_path=str(proj), name="tb_missing", code="initial begin $finish; end")
    yield proj
    shutil.rmtree(SETTINGS.workspace / name, ignore_errors=True)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rel(path: Path) -> str:
    return str(path.relative_to(SETTINGS.workspace)).replace("\\", "/")


def test_report_overwrite_sections_missing_and_truth_map(project: Path) -> None:
    report = tools.build_report(str(project), include=["truth_table"])
    path = Path(report["report_path"])
    text = path.read_text(encoding="utf-8")
    assert "Truth-table facts" in text
    assert "未提供" in text
    assert any(item["section"] == "truth_table" for item in report["missing"])
    assert "Simulation facts" not in text
    with pytest.raises(FileExistsError) as exc:
        tools.build_report(str(project), include=["truth_table"])
    assert errors.code_for(exc.value) == "already_exists"
    overwritten = tools.build_report(str(project), include=["truth_table"], overwrite=True)
    assert overwritten["ok"] is True
    # An explicit but unmatched mapping is a missing fact, not a report crash.
    mapped = tools.build_report(
        str(project), include=["truth_table"], truth_map={"tb_missing": {"top": "no_such_top"}}, overwrite=True
    )
    assert any(item["reason"] == "未提供" for item in mapped["missing"])


def test_report_path_escape_and_source_hashes(project: Path) -> None:
    source_files = [project, *project.parent.joinpath("model").glob("*"), *project.parent.joinpath("test").glob("*")]
    before = {path: _sha(path) for path in source_files if path.is_file()}
    with pytest.raises(Exception) as exc:
        tools.build_report(str(project), out="../escape.md", include=["simulation"])
    assert errors.code_for(exc.value) == "path_escape"
    with pytest.raises(Exception) as exc:
        tools.build_report(str(Path("D:/outside.proj")), include=["simulation"])
    assert errors.code_for(exc.value) == "path_escape"
    tools.build_report(str(project), include=["simulation"])
    after = {path: _sha(path) for path in source_files if path.is_file()}
    assert before == after


def test_report_reuses_existing_run_and_falls_back(monkeypatch, project: Path) -> None:
    build = project.parent / "build" / "sim-existing"
    build.mkdir(parents=True, exist_ok=True)
    (build / "tb_missing.vcd").write_text("$timescale 1ps $end\n", encoding="utf-8")
    calls = []
    monkeypatch.setattr(tools, "simulate_project", lambda *a, **k: calls.append((a, k)) or {"ok": True, "vcd_paths": []})
    reused = tools.build_report(str(project), include=["simulation"], reuse_existing_runs=True)
    assert reused["tests"][0]["reused"] is True
    assert calls == []
    shutil.rmtree(build)
    fallback = tools.build_report(str(project), include=["simulation"], reuse_existing_runs=True, overwrite=True)
    assert fallback["tests"][0]["reused"] is False
    assert len(calls) == 1


def test_report_marks_truncated_display_and_evidence_lines(monkeypatch, project: Path) -> None:
    clipped = "x" * SETTINGS.max_output_chars
    monkeypatch.setattr(
        tools,
        "simulate_project",
        lambda *a, **k: {
            "ok": True,
            "vcd_paths": [],
            "display_output": clipped,
            "headless": {"tb_source_rel": "pytest/tb.v"},
            "simulation": {"returncode": 0, "duration_ms": 1},
        },
    )
    report = tools.build_report(str(project), include=["simulation", "display"], overwrite=True)
    assert report["tests"][0]["display_truncated"] is True
    evidence_lines = Path(report["evidence_path"]).read_text(encoding="utf-8").splitlines()
    assert all(isinstance(json.loads(line), dict) for line in evidence_lines)
    markdown = Path(report["report_path"]).read_text(encoding="utf-8")
    referenced = []
    for token in markdown.split("[evidence:")[1:]:
        referenced.append(int(token.split("]", 1)[0]))
    assert referenced and all(1 <= number <= len(evidence_lines) for number in referenced)
