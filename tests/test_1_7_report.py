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


def test_report_missing_values_are_unprovided(monkeypatch, project: Path) -> None:
    import re
    from robei_mcp import fpga

    monkeypatch.setattr(tools, "simulate_project", lambda *a, **k: {
        "ok": True, "vcd_paths": [], "display_output": "SELFTEST PASS", "headless": {},
        "simulation": {"returncode": None, "duration_ms": None},
    })
    monkeypatch.setattr(fpga, "synthesize_project", lambda *a, **k: {
        "ok": True, "utilization": {"LUT": 0}, "utilization_capacity": {"LUT": None},
        "utilization_percent": {"LUT": None, "FF": 0.0}, "summary_path": None,
    })
    result = tools.build_report(str(project), include=["simulation", "display", "synthesis"])
    text = Path(result["report_path"]).read_text(encoding="utf-8")
    assert not re.search(r"\b(?:None|null)\b", text)
    assert '"LUT": "未提供"' in text
    assert '"LUT": 0' in text and '"FF": 0.0' in text
    assert result["tests"][0]["returncode"] is None  # JSON API keeps nullable data.


def test_report_missing_synthesis_is_unprovided(monkeypatch, project: Path) -> None:
    import re
    from robei_mcp import fpga

    monkeypatch.setattr(tools, "simulate_project", lambda *a, **k: {
        "ok": True, "vcd_paths": [], "simulation": {"returncode": 0, "duration_ms": 1},
    })

    def unavailable(*args, **kwargs):
        raise FileNotFoundError("constraint was not provided")

    monkeypatch.setattr(fpga, "synthesize_project", unavailable)
    result = tools.build_report(str(project), include=["simulation", "synthesis"])
    text = Path(result["report_path"]).read_text(encoding="utf-8")
    section = text.split("## Synthesis facts", 1)[1].split("\n## ", 1)[0]
    assert "未提供" in section
    assert not re.search(r"\b(?:None|null)\b", text)
    assert any(item["section"] == "synthesis" for item in result["missing"])


def test_report_preserves_real_simulation_zero_values(monkeypatch, project: Path) -> None:
    monkeypatch.setattr(tools, "simulate_project", lambda *a, **k: {
        "ok": True, "vcd_paths": [], "simulation": {"returncode": 0, "duration_ms": 0},
    })
    result = tools.build_report(str(project), include=["simulation"])
    row = result["tests"][0]
    text = Path(result["report_path"]).read_text(encoding="utf-8")
    assert row["returncode"] == row["duration_ms"] == 0
    assert f"`0` ([evidence:{row['returncode_evidence']}])" in text
    assert f"`0` ([evidence:{row['duration_ms_evidence']}])" in text


def test_report_reused_run_marks_timing_unprovided(monkeypatch, project: Path) -> None:
    build = project.parent / "build" / "sim-existing"
    build.mkdir(parents=True)
    (build / "tb_missing.vcd").write_text("$timescale 1ps $end\n$enddefinitions $end\n#0\n", encoding="utf-8")

    def no_simulation(*args, **kwargs):
        pytest.fail("existing VCD must not trigger simulation")

    monkeypatch.setattr(tools, "simulate_project", no_simulation)
    result = tools.build_report(str(project), include=["simulation"], reuse_existing_runs=True)
    text = Path(result["report_path"]).read_text(encoding="utf-8")
    assert result["tests"][0]["reused"] is True
    assert "`None`" not in text
    assert text.count("`未提供`") >= 2
