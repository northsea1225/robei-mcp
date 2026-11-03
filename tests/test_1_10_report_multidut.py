from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil
import uuid

import pytest

from robei_mcp import errors, p1, tools
from robei_mcp.config import SETTINGS


@pytest.fixture()
def project():
    name = "pytest110_report_" + uuid.uuid4().hex[:8]
    proj = Path(tools.create_robei_project("_qa_runs/" + name, models=[], skeleton=False)["proj"])
    for model in ("inv", "inv_copy"):
        tools.create_robei_model(str(proj), model,
            ports=[{"name": "a", "direction": "input"}, {"name": "y", "direction": "output"}],
            code="assign y = ~a;")
    tools.create_robei_test(str(proj), "tb_inv", "initial begin $finish; end")
    try:
        yield proj
    finally:
        shutil.rmtree(proj.parent, ignore_errors=True)


def _semantic():
    return {"test": "tb_inv", "ok": True,
            "semantic": {"mode": "combinational", "vector_count": 4, "assertion_count": 4},
            "run": {"ok": True, "tests": [{"display_output": "SELFTEST PASS", "run_dir": "build/sim-x"}]}}


def _multi():
    item = _semantic()
    item["semantic"].update(duts=[{"instance": "u_bh", "model": "inv"}, {"instance": "u_st", "model": "inv_copy"}],
                            comparison_count=0, expression_count=8)
    return item


def _mock_sim(monkeypatch):
    monkeypatch.setattr(tools, "simulate_project", lambda *a, **k: {
        "ok": True, "simulation": {"returncode": 0, "duration_ms": 5}, "vcd_paths": [],
        "display_output": "SELFTEST PASS: tb_inv 1/1 vectors", "headless": {"tb_source_rel": "build/tb_inv.v"},
    })


def _rendered(result, proj):
    def normalise(text):
        return (text.replace(result["evidence_relative_path"], "<EVIDENCE>")
                .replace(str(proj), "<PROJ>").replace(result["project_relative"], "<PROJ>")
                .replace(proj.parent.name, "<PROJECT>"))
    return {"markdown": normalise(Path(result["report_path"]).read_text(encoding="utf-8")),
            "evidence_jsonl": normalise(Path(result["evidence_path"]).read_text(encoding="utf-8"))}


def _valid_evidence(result):
    text = Path(result["report_path"]).read_text(encoding="utf-8")
    ledger = [json.loads(line) for line in Path(result["evidence_path"]).read_text(encoding="utf-8").splitlines()]
    refs = re.findall(r"\[evidence:([^\]]+)\]", text)
    assert refs and all(ref.isdigit() and 1 <= int(ref) <= len(ledger) for ref in refs)
    assert not re.search(r"\b(None|null)\b", text)
    return text, ledger


def _fake_truth(**kwargs):
    return {"top": kwargs["top"], "row_count": 2, "expected_rows": 2, "testbench": "build/truth_fixture.v",
            "rows": [{"a": "0", "y": "1"}, {"a": "1", "y": "0"}]}


def test_legacy_single_report_and_evidence_are_byte_compatible(project, monkeypatch):
    _mock_sim(monkeypatch)
    result = tools.build_report(str(project), tests=["tb_inv"], out="legacy.md", include=["simulation"],
                                testbench_results=[_semantic()])
    baseline = json.loads((Path(__file__).parent / "fixtures" / "1_10_legacy_report.json").read_text(encoding="utf-8"))
    assert _rendered(result, project) == baseline


def test_modern_single_dut_keeps_legacy_layout_and_evidence(project, monkeypatch):
    _mock_sim(monkeypatch)
    item = _semantic()
    item["semantic"].update(duts=[{"instance": "dut", "model": "inv"}], comparison_count=0, expression_count=4)
    result = tools.build_report(str(project), tests=["tb_inv"], out="legacy.md", include=["simulation"], testbench_results=[item])
    baseline = json.loads((Path(__file__).parent / "fixtures" / "1_10_legacy_report.json").read_text(encoding="utf-8"))
    assert _rendered(result, project) == baseline


def test_multi_facts_include_counts_and_missing_fields_do_not_abort(project, monkeypatch):
    _mock_sim(monkeypatch)
    for index, fields in enumerate([{}, {"comparison_count": None, "expression_count": None},
                                     {"duts": None, "comparison_count": "bad", "expression_count": False}]):
        item = _multi(); item["semantic"].update(fields)
        result = tools.build_report(str(project), tests=["tb_inv"], out=f"multi{index}.md", include=["simulation"], testbench_results=[item])
        text, ledger = _valid_evidence(result)
        assert result["ok"] is True and "## Multi-DUT testbench facts" in text
        facts = result["testbench"][0]
        for key in ("duts", "comparison_count", "expression_count"):
            assert ledger[facts["_evidence"][key] - 1]["value"] == facts[key]
        if not fields:
            assert facts["comparison_count"] == 0 and facts["expression_count"] == 8
        else:
            assert "未提供" in text
            assert any(row["section"] == "testbench" for row in result["missing"])


def test_truth_map_dispatches_by_instance_and_labels_evidence(project, monkeypatch):
    _mock_sim(monkeypatch)
    calls = []
    monkeypatch.setattr(p1, "truth_table", lambda **kwargs: calls.append(kwargs) or _fake_truth(**kwargs))
    result = tools.build_report(str(project), tests=["tb_inv"], out="instances.md", include=["truth_table"],
        testbench_results=[_multi()], truth_map={"tb_inv": {"instances": {
            "u_bh": "inv", "u_st": {"top": "inv_copy", "verilog_path": str(project.parent/"verilog"/"inv_copy.v"), "deps": []}}}})
    text, ledger = _valid_evidence(result)
    assert [call["top"] for call in calls] == ["inv", "inv_copy"]
    assert calls[1]["deps"] == []
    for name in ("u_bh", "u_st"):
        assert f"`tb_inv.{name}`" in text
        assert any(row["field"] == f"tb_inv.{name}.row_count" for row in ledger)


def test_missing_instance_mapping_only_degrades_that_instance(project, monkeypatch):
    _mock_sim(monkeypatch)
    monkeypatch.setattr(p1, "truth_table", _fake_truth)
    result = tools.build_report(str(project), tests=["tb_inv"], out="partial.md", include=["truth_table"],
        testbench_results=[_multi()], truth_map={"tb_inv": {"instances": {"u_bh": "inv"}}})
    text, _ = _valid_evidence(result)
    assert "`tb_inv.u_bh`: `row_count=2`" in text
    assert "`tb_inv.u_st`: 未提供" in text
    assert any(item.get("instance") == "u_st" for item in result["missing"])


def test_instance_failure_is_local_but_path_escape_is_not_hidden(project, monkeypatch):
    _mock_sim(monkeypatch)
    def broken(**kwargs):
        if kwargs["top"] == "bad_top":
            raise ValueError("module not found")
        return _fake_truth(**kwargs)
    monkeypatch.setattr(p1, "truth_table", broken)
    mapping = {"tb_inv": {"instances": {"u_bh": "inv", "u_st": {"top": "bad_top", "verilog_path": str(project.parent/"verilog"/"inv_copy.v")}}}}
    result = tools.build_report(str(project), tests=["tb_inv"], out="failed-instance.md", include=["truth_table"],
                                testbench_results=[_multi()], truth_map=mapping)
    text, _ = _valid_evidence(result)
    assert "tb_inv.u_bh" in text and "tb_inv.u_st" in text
    assert result["warnings"][0]["code"] == "truth_table_unavailable"
    def escape(**kwargs):
        raise errors.PathEscape("fixture escape")
    monkeypatch.setattr(p1, "truth_table", escape)
    with pytest.raises(errors.PathEscape):
        tools.build_report(str(project), tests=["tb_inv"], out="escape.md", include=["truth_table"], truth_map=mapping)
    assert not (project.parent/"report"/"escape.md").exists()


def test_old_default_string_and_dict_truth_maps_keep_identical_output(project, monkeypatch):
    _mock_sim(monkeypatch)
    monkeypatch.setattr(p1, "truth_table", _fake_truth)
    snapshots = []
    for index, mapping in enumerate([None, {"tb_inv": "inv"}, {"tb_inv": {"top": "inv"}}]):
        result = tools.build_report(str(project), tests=["tb_inv"], out=f"legacy-map{index}.md", include=["truth_table"],
                                    testbench_results=[_semantic()], truth_map=mapping)
        snapshots.append(_rendered(result, project))
    assert snapshots[0] == snapshots[1] == snapshots[2]


def test_real_two_dut_report_keeps_sources_and_evidence_valid(project):
    base = str(project.parent)
    duts = [{"instance": inst, "model_path": str(Path(base)/"model"/(model+".model")),
             "connections": {"a": "a", "y": "y_"+inst}} for inst,model in [("u_bh", "inv"), ("u_st", "inv_copy")]]
    result = tools.create_testbench(str(project), "tb_pair", duts=duts, run=True,
        vectors=[{"drive": {"a": bit}, "expect": {"u_bh.y": "~a", "u_st.y": "~a"}} for bit in (0, 1)],
        comparisons=[{"left": "u_bh.y", "right": "u_st.y"}])
    assert result["ok"] is True
    sources = [project, *project.parent.joinpath("model").glob("*.model"), *project.parent.joinpath("test").glob("*.test")]
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    report = tools.build_report(str(project), tests=["tb_pair"], out="real-multi.md",
        include=["simulation", "truth_table", "display", "assertions"], testbench_results=[result],
        truth_map={"tb_pair": {"instances": {"u_bh": "inv", "u_st": "inv_copy"}}})
    text, _ = _valid_evidence(report)
    assert report["tests"][0]["returncode"] == 0
    assert "tb_pair.u_bh" in text and "tb_pair.u_st" in text
    assert report["testbench"][0]["comparison_count"] == 2
    assert before == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
