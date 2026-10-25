from __future__ import annotations

import hashlib
import shutil
import uuid
from pathlib import Path

import pytest

from robei_mcp import errors, tools

@pytest.fixture()
def proj():
    result = tools.create_robei_project(f"h2_{uuid.uuid4().hex[:8]}", models=[])
    path = Path(result["proj"])
    try:
        yield path
    finally:
        shutil.rmtree(path.parent, ignore_errors=True)

def model(proj, name):
    return proj.parent / "model" / f"{name}.model"

def make_sub(proj, params=None, width="LEN"):
    return tools.create_robei_model(str(proj), "sub",
        ports=[{"name":"a","direction":"input","width":width}, {"name":"y","direction":"output","width":1}],
        parameters={"LEN":4} if params is None else params, code="assign y = a[0];")

def make_top(proj, override=None, slice_ref="a[3:0]"):
    return tools.create_robei_canvas_model(str(proj), "top",
        ports=[{"name":"a","direction":"input","width":4},{"name":"y","direction":"output","width":1}],
        instances=[{"name":"u1","model":"sub", **({"parameters":override} if override is not None else {})}],
        wires=[{"from":slice_ref,"to":"u1.a"},{"from":"u1.y","to":"y"}], overwrite=True)

def test_add_then_port_and_effective_width(proj):
    tools.create_robei_model(str(proj), "sub", ports=[{"name":"a","direction":"input","width":1}], parameters={})
    added = tools.set_robei_parameter(str(model(proj,"sub")), "LEN", 4, action="add")
    assert added["action"] == "add"
    port = tools.set_port(str(model(proj,"sub")), "a", "input", "LEN")
    assert port["port"]["datasize"] == "LEN-1:0"
    changed = tools.set_robei_parameter(str(model(proj,"sub")), "LEN", 8)
    assert changed["affected_ports"][0]["effective_width"] == 8
    assert changed["old_value"] == "4" and changed["new_value"] == "8"

def test_token_matching_and_name_rules(proj):
    make_sub(proj, {"LEN":4,"LENGTH":5}, "LEN")
    tools.set_port(str(model(proj,"sub")), "b", "input", "LENGTH")
    renamed = tools.set_robei_parameter(str(model(proj,"sub")), "LEN", action="rename", new_name="WIDTH", dry_run=True)
    rows = {row["port"]: row for row in renamed["width_info"]}
    assert rows["a"]["datasize"] == "WIDTH-1:0"
    assert rows["b"]["datasize"] == "LENGTH-1:0"
    with pytest.raises(errors.BadRequest):
        tools.set_robei_parameter(str(model(proj,"sub")), "LENGTH", action="delete")
    with pytest.raises(errors.BadRequest) as excinfo:
        tools.set_robei_parameter(str(model(proj,"sub")), "module", 4, action="add")
    assert "keyword" in excinfo.value.hint.lower()

def test_expression_dependencies_cycle_and_unknown(proj):
    tools.create_robei_model(str(proj), "sub", ports=[{"name":"a","direction":"input","width":1}], parameters={"BASE":4})
    with pytest.raises(errors.BadRequest):
        tools.set_robei_parameter(str(model(proj,"sub")), "LEN", "UNKNOWN+1", action="add")
    tools.set_robei_parameter(str(model(proj,"sub")), "LEN", "BASE+1", action="add")
    with pytest.raises(errors.BadRequest):
        tools.set_robei_parameter(str(model(proj,"sub")), "BASE", "LEN+1")

def test_direct_parent_warning_and_overridden_flag(proj):
    make_sub(proj)
    make_top(proj)
    result = tools.set_robei_parameter(str(model(proj,"sub")), "LEN", 8, dry_run=True)
    warning = next(item for item in result["warnings"] if item["code"] == "parent_width_conflict")
    assert warning["instance"] == "u1" and warning["overridden"] is False
    make_top(proj, override={"LEN":2})
    result = tools.set_robei_parameter(str(model(proj,"sub")), "LEN", 8, dry_run=True)
    warning = next(item for item in result["warnings"] if item["code"] == "parent_width_conflict")
    assert warning["overridden"] is True

def test_rename_parent_preview_and_commit(proj):
    make_sub(proj)
    make_top(proj, override={"LEN":4})
    parent = model(proj,"top")
    before = hashlib.sha256(parent.read_bytes()).hexdigest()
    preview = tools.set_robei_parameter(str(model(proj,"sub")), "LEN", action="rename", new_name="WIDTH", rename_in_parents=True, dry_run=True)
    assert preview["parent_updates"] and hashlib.sha256(parent.read_bytes()).hexdigest() == before
    result = tools.set_robei_parameter(str(model(proj,"sub")), "LEN", action="rename", new_name="WIDTH", rename_in_parents=True)
    assert result["parent_backups"]
    assert "WIDTH" in parent.read_text(encoding="utf-8")

def test_rename_parent_transaction_rolls_back(monkeypatch, proj):
    make_sub(proj)
    make_top(proj, override={"LEN": 4})
    target = model(proj, "sub")
    parent = model(proj, "top")
    before = {target: hashlib.sha256(target.read_bytes()).hexdigest(), parent: hashlib.sha256(parent.read_bytes()).hexdigest()}
    original = Path.write_text
    calls = {"n": 0}
    def fail_on_parent(self, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("injected parent write failure")
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Path, "write_text", fail_on_parent)
    with pytest.raises(OSError):
        tools.set_robei_parameter(str(target), "LEN", action="rename", new_name="WIDTH", rename_in_parents=True)
    assert {target: hashlib.sha256(target.read_bytes()).hexdigest(), parent: hashlib.sha256(parent.read_bytes()).hexdigest()} == before

