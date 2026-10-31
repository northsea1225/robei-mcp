from __future__ import annotations

import hashlib
import shutil
import uuid
from pathlib import Path

import pytest

from robei_mcp import errors, p1, tools
from robei_mcp.config import SETTINGS


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_project(with_parent: bool = True) -> tuple[str, Path, Path | None]:
    name = f"pytest18_m3_{uuid.uuid4().hex[:8]}"
    proj = tools.create_robei_project(name, models=["child", "top"])["proj"]
    tools.create_robei_model(
        proj,
        "child",
        ports=[
            {"name": "a", "direction": "input", "width": 1},
            {"name": "y", "direction": "output", "width": 1},
        ],
        code="",
        overwrite=True,
    )
    tools.create_robei_model(
        proj,
        "top",
        ports=[
            {"name": "a", "direction": "input", "width": 1},
            {"name": "y", "direction": "output", "width": 1},
        ],
        code="",
        overwrite=True,
    )
    top_model = Path(proj).parent / "model" / "top.model"
    if with_parent:
        tools.edit_robei_canvas(
            str(top_model),
            [
                {"op": "add_instance", "name": "u1", "model": "child"},
                {"op": "connect", "from": "a", "to": "u1.a", "net": "in_n"},
                {"op": "connect", "from": "u1.y", "to": "y", "net": "out_n"},
            ],
        )
    return proj, Path(proj).parent / "model" / "child.model", top_model if with_parent else None


@pytest.fixture()
def project():
    proj, child, top = make_project()
    try:
        yield proj, child, top
    finally:
        shutil.rmtree(Path(proj).parent, ignore_errors=True)


def test_behavioral_suggestions_have_ports_for_exp1_exp3_exp6() -> None:
    expected = {
        "exp1_and/exp1_and.proj": "and2",
        "exp3_decoder/exp3_decoder.proj": "decode3_8",
        "exp6_counter/exp6_counter.proj": "dec_counter",
    }
    for proj_rel, name in expected.items():
        source = SETTINGS.workspace / proj_rel.split("/")[0] / "verilog" / f"{name}.v"
        result = p1.import_verilog_canvas(
            str(SETTINGS.workspace / proj_rel),
            str(source.relative_to(SETTINGS.workspace)),
            mode="behavioral",
        )
        assert result["classification"] == "behavioral"
        ports = result["create_robei_model"]["ports"]
        assert ports and all(set(("name", "direction", "width")) <= set(port) for port in ports)


def test_run_all_tests_returns_elapsed_ms(project, monkeypatch) -> None:
    proj, _, _ = project
    tools.create_robei_test(proj, "tb_one", code="initial begin #1; $finish; end", overwrite=True)
    monkeypatch.setattr(tools, "simulate_project", lambda **kwargs: {"ok": True, "simulation": {"returncode": 0, "duration_ms": 1}, "vcd_paths": []})
    result = tools.run_all_tests(proj)
    assert isinstance(result["elapsed_ms"], int)
    assert result["elapsed_ms"] >= 0


def test_move_instance_dry_run_and_write_preserve_other_fields(project) -> None:
    _, _, top = project
    before = sha(top)
    dry = tools.edit_robei_canvas(str(top), [{"op": "move_instance", "name": "u1", "x": 500, "y": 240}], dry_run=True)
    assert dry["diff"]["instances_moved"][0]["after"] == {"x": 500, "y": 240}
    assert sha(top) == before
    applied = tools.edit_robei_canvas(str(top), [{"op": "move_instance", "name": "u1", "x": 500, "y": 240}])
    assert applied["instances"][0]["parameters"] == ""
    parsed = tools.read_robei_model(str(top))
    moved = next(item for item in parsed["instances"] if item["instance"] == "u1")
    assert int(moved["x"]) == 500 and int(moved["y"]) == 240


def test_rename_port_updates_wire_connect_and_netlist(project) -> None:
    proj, _, _ = make_project(with_parent=False)
    child = Path(proj).parent / "model" / "child.model"
    try:
        # Child has no behavior Code, so a canvas rename is allowed.
        result = tools.edit_robei_canvas(str(child), [{"op": "rename_port", "name": "a", "new_name": "data_in"}], dry_run=True)
        assert result["ports"][0]["name"] == "data_in"
        assert result["netlist"] is not None
    finally:
        shutil.rmtree(Path(proj).parent, ignore_errors=True)


def test_rename_port_rejects_duplicate_and_unknown(project) -> None:
    _, child, _ = project
    with pytest.raises(errors.BadRequest):
        tools.edit_robei_canvas(str(child), [{"op": "rename_port", "name": "a", "new_name": "y"}], dry_run=True)
    with pytest.raises(errors.BadRequest):
        tools.edit_robei_canvas(str(child), [{"op": "rename_port", "name": "missing", "new_name": "x"}], dry_run=True)


def test_rename_port_parent_reference_rejected_with_references(project) -> None:
    _, child, _ = project
    with pytest.raises(errors.BadRequest) as excinfo:
        tools.edit_robei_canvas(str(child), [{"op": "rename_port", "name": "a", "new_name": "data_in"}], dry_run=True)
    assert errors.code_for(excinfo.value) == "model_in_use"
    assert any(item["kind"] == "canvas_port" for item in excinfo.value.references)


def test_rename_port_update_parents_syncs_canvas_only(project) -> None:
    _, child, top = project
    result = tools.edit_robei_canvas(str(child), [{"op": "rename_port", "name": "a", "new_name": "data_in", "update_parents": True}], dry_run=False)
    assert result["parent_updates"]
    parsed = tools.read_robei_model(str(top))
    inst = next(item for item in parsed["instances"] if item["instance"] == "u1")
    assert any(port["name"] == "data_in" for port in inst["ports"])
    assert any(wire["to"].get("port") == "data_in" for wire in parsed["wires"])


def test_manage_model_rename_updates_project(project) -> None:
    proj, _, _ = project
    orphan = Path(proj).parent / "model" / "top.model"
    # top is referenced only by no parent in this fixture; remove parent first by using a fresh model.
    name = f"orphan_{uuid.uuid4().hex[:6]}"
    tools.create_robei_model(proj, name, overwrite=False)
    source = Path(proj).parent / "model" / f"{name}.model"
    result = tools.manage_robei_model(proj, str(source), "rename", new_name="renamed")
    assert result["registration"] == "renamed"
    assert not source.exists()
    assert (source.parent / "renamed.model").is_file()
    assert "renamed.model" in Path(proj).read_text(encoding="utf-8")


def test_manage_model_duplicate_and_delete(project) -> None:
    proj, _, _ = project
    source = Path(proj).parent / "model" / "child.model"
    duplicate = tools.manage_robei_model(proj, str(source), "duplicate", new_name="child_copy")
    assert duplicate["sha256"]
    copied = source.parent / "child_copy.model"
    assert copied.is_file()
    # Duplicate is allowed even while the source is referenced; delete the copy after checking registration.
    deleted = tools.manage_robei_model(proj, str(copied), "delete")
    assert deleted["sha256"] is None
    assert not copied.exists()


def test_manage_model_reference_rejection(project) -> None:
    proj, child, _ = project
    with pytest.raises(errors.BadRequest) as excinfo:
        tools.manage_robei_model(proj, str(child), "delete")
    assert errors.code_for(excinfo.value) == "model_in_use"
    assert excinfo.value.references


def test_manage_model_rollback_on_registration_failure(project, monkeypatch) -> None:
    proj, _, _ = project
    source = Path(proj).parent / "model" / "top.model"
    before_source = sha(source)
    before_proj = sha(Path(proj))
    original = tools._register_model_in_project
    monkeypatch.setattr(tools, "_register_model_in_project", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("register failed")))
    with pytest.raises(RuntimeError):
        tools.manage_robei_model(proj, str(source), "duplicate", new_name="top_copy")
    assert sha(source) == before_source
    assert sha(Path(proj)) == before_proj
    assert not source.parent.joinpath("top_copy.model").exists()
    monkeypatch.setattr(tools, "_register_model_in_project", original)


def test_renderer_preserves_device_metadata_and_unknown_child(project) -> None:
    _, _, top = project
    raw = top.read_text(encoding="utf-8")
    raw = raw.replace('Type="module"', 'Type="module" Vendor="AMD" Device="xc7" Family="artix7"', 1)
    close = raw.rfind("</Module>")
    raw = raw[:close] + ' <CustomNode Foo="bar">keep-me</CustomNode>\n' + raw[close:]
    top.write_text(raw, encoding="utf-8")
    result = tools.edit_robei_canvas(str(top), [{"op": "move_instance", "name": "u1", "x": 333, "y": 222}])
    rendered = top.read_text(encoding="utf-8")
    assert 'Vendor="AMD"' in rendered and 'Device="xc7"' in rendered and 'Family="artix7"' in rendered
    assert '<CustomNode Foo="bar">keep-me</CustomNode>' in rendered
    assert result["sha256"] == sha(top)


def test_manage_model_rejects_bad_operation(project) -> None:
    proj, child, _ = project
    with pytest.raises(errors.BadRequest) as excinfo:
        tools.manage_robei_model(proj, str(child), "create")
    assert errors.code_for(excinfo.value) == "bad_argument"


def test_rename_port_rejects_behavior_code_even_with_update_parents() -> None:
    proj, child, _ = make_project(with_parent=False)
    try:
        tools.set_robei_code(str(child), "assign y = a;")
        with pytest.raises(errors.BadRequest) as excinfo:
            tools.edit_robei_canvas(str(child), [{"op": "rename_port", "name": "a", "new_name": "data_in", "update_parents": True}], dry_run=True)
        assert errors.code_for(excinfo.value) == "model_in_use"
        assert any(item["kind"] == "behavior_code" for item in excinfo.value.references)
    finally:
        shutil.rmtree(Path(proj).parent, ignore_errors=True)


def test_manage_model_dry_run_does_not_write(project) -> None:
    proj, child, _ = project
    before = sha(Path(proj))
    result = tools.manage_robei_model(proj, str(child), "duplicate", new_name="child_preview", dry_run=True)
    assert result["dry_run"] is True
    assert not child.with_name("child_preview.model").exists()
    assert sha(Path(proj)) == before
