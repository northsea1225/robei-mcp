"""Regression tests for 1.1.0 (M1: component library)."""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest

from robei_mcp import tools
from robei_mcp.config import SETTINGS
from robei_mcp.model import model_to_verilog, parse_robei_model


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


def test_import_accepts_proj_path_alias(fake_library: Path) -> None:
    """1.2.2: ``proj_path`` is accepted next to ``project``; a missing one says so."""
    name = f"libalias_{uuid.uuid4().hex[:6]}"
    project = _project(name)
    try:
        rel = project.relative_to(SETTINGS.workspace).as_posix()
        out = tools.import_library_model("plain", proj_path=rel, dry_run=True)
        assert out["dry_run"] is True and out["registration"] == "pending"
        out = tools.import_library_model("plain", proj_path=str(project), dry_run=True)
        assert out["dry_run"] is True
        with pytest.raises(ValueError) as caught:
            tools.import_library_model("plain")
        message = str(caught.value)
        assert '"project": "work/a.proj"' in message
        assert "proj_path" in message
        with pytest.raises(ValueError) as caught:
            tools.import_library_model("plain", project=rel, proj_path="other/x.proj")
        assert "disagree" in str(caught.value)
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


# ---------------------------------------------------------------------------
# M2 — canvas incremental editing
# ---------------------------------------------------------------------------


@pytest.fixture()
def project() -> Path:
    name = f"p11_{uuid.uuid4().hex[:8]}"
    proj = Path(tools.create_robei_project(name)["proj"])
    yield proj
    shutil.rmtree(SETTINGS.workspace / name, ignore_errors=True)


def _model(project: Path, name: str) -> Path:
    return project.parent / "model" / f"{name}.model"


def _four_bit_project(project: Path) -> None:
    tools.create_robei_model(
        proj_path=str(project),
        name="buf4",
        ports=[
            {"name": "a", "direction": "input", "width": 4},
            {"name": "y", "direction": "output", "width": 4},
        ],
        code="assign y = a;",
    )
    tools.create_robei_model(
        proj_path=str(project),
        name="chain",
        ports=[
            {"name": "a", "direction": "input", "width": 4},
            {"name": "y", "direction": "output", "width": 4},
        ],
    )


def _chain_ops() -> list[dict]:
    return [
        {"op": "add_instance", "name": "u1", "model": "buf4"},
        {"op": "add_instance", "name": "u2", "model": "buf4"},
        {"op": "connect", "from": "a", "to": "u1.a", "net": "a_n"},
        {"op": "connect", "from": "u1.y", "to": "u2.a", "net": "mid", "datasize": 4},
        {"op": "connect", "from": "u2.y", "to": "y", "net": "y_n"},
    ]


def test_edit_canvas_builds_the_four_bit_chain(project: Path) -> None:
    _four_bit_project(project)
    out = tools.edit_robei_canvas(f"{project.stem}/model/chain.model", _chain_ops())
    assert out["ok"] is True and out["dry_run"] is False
    assert out["diff"]["instances_added"] == ["u1", "u2"]
    assert [w["net"] for w in out["wires"]] == ["a_n", "mid", "y_n"]
    # 1.5 returns numeric widths as ints (a parameterised one stays a string).
    assert [w["datasize"] for w in out["wires"]] == [4, 4, 4]
    assert [i["instance"] for i in out["instances"]] == ["u1", "u2"]
    preview = out["verilog_preview"]
    assert "wire [3:0] mid;" in preview
    assert "buf4 u1(" in preview and "buf4 u2(" in preview
    assert out["backup"] is not None
    on_disk = parse_robei_model(_model(project, "chain"))
    assert [i["instance"] for i in on_disk["instances"]] == ["u1", "u2"]
    assert len(on_disk["wires"]) == 3


@pytest.mark.skipif(not Path(SETTINGS.iverilog).exists(), reason="iverilog not installed")
def test_edit_canvas_chain_simulates_y_equals_a(project: Path) -> None:
    _four_bit_project(project)
    out = tools.edit_robei_canvas(f"{project.stem}/model/chain.model", _chain_ops())
    build = SETTINGS.workspace / ".tmp" / f"sim11-{uuid.uuid4().hex[:8]}"
    build.mkdir(parents=True, exist_ok=True)
    try:
        (build / "chain.v").write_text(out["verilog_preview"], encoding="utf-8")
        (build / "buf4.v").write_text(
            model_to_verilog(parse_robei_model(_model(project, "buf4"))), encoding="utf-8"
        )
        (build / "tb.v").write_text(
            "`timescale 1ns/1ns\nmodule tb;\n  reg [3:0] a;\n  wire [3:0] y;\n"
            "  chain dut(.a(a), .y(y));\n  initial begin\n"
            "    a = 4'b1011; #5;\n"
            '    $display("A=%b Y=%b %s", a, y, (y === a) ? "MATCH" : "MISMATCH");\n'
            "    a = 4'b0110; #5;\n"
            '    $display("A=%b Y=%b %s", a, y, (y === a) ? "MATCH" : "MISMATCH");\n'
            "    $finish;\n  end\nendmodule\n",
            encoding="utf-8",
        )
        rel = f".tmp/{build.name}"
        sim = tools.simulate(
            files=[f"{rel}/chain.v", f"{rel}/buf4.v", f"{rel}/tb.v"], top="tb"
        )
        assert sim["ok"] is True, sim.get("compile")
        output = sim["display_output"]
        assert output.count("MATCH") == 2
        assert "MISMATCH" not in output
        assert "A=1011 Y=1011" in output
        assert "A=0110 Y=0110" in output
    finally:
        shutil.rmtree(build, ignore_errors=True)


def test_connect_width_mismatch_uses_its_own_code(project: Path) -> None:
    from robei_mcp.errors import code_for

    _four_bit_project(project)
    tools.create_robei_model(
        proj_path=str(project),
        name="inv1",
        ports=[
            {"name": "a", "direction": "input", "width": 1},
            {"name": "y", "direction": "output", "width": 1},
        ],
        code="assign y = ~a;",
    )
    with pytest.raises(tools.WidthMismatch) as caught:
        tools.edit_robei_canvas(
            f"{project.stem}/model/chain.model",
            [
                {"op": "add_instance", "name": "u9", "model": "inv1"},
                {"op": "connect", "from": "a", "to": "u9.y"},
            ],
        )
    assert code_for(caught.value) == "width_mismatch"
    assert "4 bit" in str(caught.value) and "1 bit" in str(caught.value)


def test_ops_are_atomic_when_a_later_op_fails(project: Path) -> None:
    _four_bit_project(project)
    target = _model(project, "chain")
    tools.edit_robei_canvas(f"{project.stem}/model/chain.model", _chain_ops())
    before = target.read_bytes()
    with pytest.raises(ValueError):
        tools.edit_robei_canvas(
            f"{project.stem}/model/chain.model",
            [
                {"op": "add_instance", "name": "u7", "model": "buf4"},
                {"op": "connect", "from": "a", "to": "u7.nope"},
            ],
        )
    assert target.read_bytes() == before


def test_unknown_port_instance_and_op_are_rejected(project: Path) -> None:
    _four_bit_project(project)
    tools.edit_robei_canvas(f"{project.stem}/model/chain.model", _chain_ops())
    path = f"{project.stem}/model/chain.model"
    with pytest.raises(ValueError, match="has no port"):
        tools.edit_robei_canvas(path, [{"op": "connect", "from": "a", "to": "u1.zzz"}])
    with pytest.raises(ValueError, match="unknown top-level port"):
        tools.edit_robei_canvas(path, [{"op": "connect", "from": "nope", "to": "u1.a"}])
    with pytest.raises(ValueError, match="duplicate instance"):
        tools.edit_robei_canvas(path, [{"op": "add_instance", "name": "u1", "model": "buf4"}])
    with pytest.raises(ValueError, match="unknown op"):
        tools.edit_robei_canvas(path, [{"op": "explode"}])
    with pytest.raises(ValueError, match="ops must be a non-empty list"):
        tools.edit_robei_canvas(path, [])


def test_disconnect_rename_and_remove_instance(project: Path) -> None:
    _four_bit_project(project)
    path = f"{project.stem}/model/chain.model"
    tools.edit_robei_canvas(path, _chain_ops())

    renamed = tools.edit_robei_canvas(path, [{"op": "rename_wire", "net": "mid", "new_name": "bus_mid"}])
    assert [w["net"] for w in renamed["wires"]] == ["a_n", "bus_mid", "y_n"]
    with pytest.raises(ValueError, match="duplicate net name"):
        tools.edit_robei_canvas(path, [{"op": "rename_wire", "net": "bus_mid", "new_name": "a_n"}])

    dropped = tools.edit_robei_canvas(path, [{"op": "disconnect", "net": "bus_mid"}])
    assert [w["net"] for w in dropped["wires"]] == ["a_n", "y_n"]
    assert dropped["diff"]["wires_removed"][0]["net"] == "bus_mid"
    with pytest.raises(ValueError, match="no wire matched"):
        tools.edit_robei_canvas(path, [{"op": "disconnect", "net": "bus_mid"}])

    removed = tools.edit_robei_canvas(path, [{"op": "remove_instance", "name": "u2"}])
    assert [i["instance"] for i in removed["instances"]] == ["u1"]
    assert [w["net"] for w in removed["wires"]] == ["a_n"]  # y_n touched u2
    assert removed["diff"]["wires_removed"][0]["net"] == "y_n"


def test_dry_run_never_writes(project: Path) -> None:
    _four_bit_project(project)
    target = _model(project, "chain")
    before = target.read_bytes()
    out = tools.edit_robei_canvas(f"{project.stem}/model/chain.model", _chain_ops(), dry_run=True)
    assert out["dry_run"] is True
    assert out["diff"]["instances_added"] == ["u1", "u2"]
    assert "verilog_preview" in out
    assert target.read_bytes() == before


def test_edit_canvas_reads_and_keeps_the_legacy_instance_module_field(project: Path) -> None:
    _four_bit_project(project)
    legacy = _model(project, "legacy")
    legacy.write_text(
        '<!DOCTYPE Robei>\n<Module Category="Current" Class="module" Code="" Name="legacy" '
        'Parameters="" Parent="0" Type="model">\n'
        ' <Port Datatype="wire" Datasize="4" Inout="input" Name="a" Parent="legacy"/>\n'
        ' <Port Datatype="wire" Datasize="4" Inout="output" Name="y" Parent="legacy"/>\n'
        ' <Instance Name="u1" Module="buf4" X="100" Y="100"/>\n'
        "</Module>\n",
        encoding="utf-8",
    )
    parsed = parse_robei_model(legacy)
    assert parsed["instances"][0]["module"] == "buf4"
    assert "buf4 u1(" in model_to_verilog(parsed)
    out = tools.edit_robei_canvas(
        f"{project.stem}/model/legacy.model",
        [{"op": "connect", "from": "a", "to": "u1.a", "net": "a_n"}],
    )
    assert out["wires"][0]["from"] == "a"
    assert 'Module="buf4"' in legacy.read_text(encoding="utf-8")


def test_create_canvas_model_still_round_trips(project: Path) -> None:
    tools.create_robei_model(
        proj_path=str(project),
        name="and2",
        ports=[
            {"name": "a", "direction": "input", "width": 1},
            {"name": "b", "direction": "input", "width": 1},
            {"name": "y", "direction": "output", "width": 1},
        ],
        code="assign y = a & b;",
    )
    canvas = tools.create_robei_canvas_model(
        proj_path=str(project),
        name="top_c",
        ports=[
            {"name": "a", "direction": "input", "width": 1},
            {"name": "b", "direction": "input", "width": 1},
            {"name": "y", "direction": "output", "width": 1},
        ],
        instances=[{"name": "u1", "model": "and2"}],
        wires=[
            {"from": "a", "to": "u1.a", "net": "a_n"},
            {"from": "b", "to": "u1.b", "net": "b_n"},
            {"from": "u1.y", "to": "y", "net": "y_n"},
        ],
    )
    assert [w["net"] for w in canvas["wires"]] == ["a_n", "b_n", "y_n"]
    text = _model(project, "top_c").read_text(encoding="utf-8")
    assert 'Datasize="1"' in text and "<Wire " in text
    assert "and2 u1(" in model_to_verilog(parse_robei_model(_model(project, "top_c")))


# ---------------------------------------------------------------------------
# M2 — set_robei_code / set_robei_parameter
# ---------------------------------------------------------------------------


def test_set_code_rejects_structural_models(project: Path) -> None:
    from robei_mcp.errors import code_for

    _four_bit_project(project)
    tools.edit_robei_canvas(f"{project.stem}/model/chain.model", _chain_ops())
    with pytest.raises(tools.NotBehavioral) as caught:
        tools.set_robei_code(f"{project.stem}/model/chain.model", "assign y = a;")
    assert code_for(caught.value) == "not_behavioral"


def test_set_code_updates_a_behavioural_model(project: Path) -> None:
    _four_bit_project(project)
    out = tools.set_robei_code(f"{project.stem}/model/buf4.model", "assign y = a & a;")
    assert out["ok"] is True
    assert parse_robei_model(_model(project, "buf4"))["code"] == "assign y = a & a;"
    assert "a & a" in out["verilog_preview"]
    assert out["sha256_before"] != out["sha256"]
    dry = tools.set_robei_code(f"{project.stem}/model/buf4.model", "assign y = 4'b0;", dry_run=True)
    assert dry["dry_run"] is True and dry["code"] == "assign y = 4'b0;"
    assert parse_robei_model(_model(project, "buf4"))["code"] == "assign y = a & a;"


def test_set_parameter_updates_value_and_width_info(project: Path) -> None:
    tools.create_robei_model(
        proj_path=str(project),
        name="pw",
        ports=[{"name": "d", "direction": "input", "width": 1}],
    )
    target = _model(project, "pw")
    text = target.read_text(encoding="utf-8")
    text = text.replace('Parameters=""', 'Parameters="LEN = 2; // width&#xa;MODE = 0; // kind"')
    text = text.replace('Datasize="1"', 'Datasize="LEN-1:0"')
    target.write_text(text, encoding="utf-8")

    out = tools.set_robei_parameter(f"{project.stem}/model/pw.model", "len", 8)
    assert out["parameter"] == "LEN" and out["old_value"] == "2" and out["new_value"] == "8"
    assert out["width_info"] == [
        {
            "port": "d",
            "datasize": "LEN-1:0",
            "expression": "8-1:0",
            "width": 8,
            "resolved": True,
            "range": "8-1:0",
        }
    ]
    reparsed = parse_robei_model(target)
    assert {p["name"]: p["default"] for p in reparsed["parameter_list"]} == {"LEN": "8", "MODE": "0"}
    assert reparsed["parameter_list"][0]["comment"] == "width"
    assert 'Datasize="LEN-1:0"' in target.read_text(encoding="utf-8")


def test_set_parameter_unknown_name_has_its_own_code(project: Path) -> None:
    from robei_mcp.errors import code_for

    tools.create_robei_model(
        proj_path=str(project),
        name="pw2",
        ports=[{"name": "d", "direction": "input", "width": 1}],
    )
    target = _model(project, "pw2")
    target.write_text(
        target.read_text(encoding="utf-8").replace('Parameters=""', 'Parameters="LEN = 2;"'),
        encoding="utf-8",
    )
    with pytest.raises(tools.UnknownParameter) as caught:
        tools.set_robei_parameter(f"{project.stem}/model/pw2.model", "NOPE", 3)
    assert code_for(caught.value) == "unknown_parameter"
    assert "LEN" in str(caught.value)


def test_set_parameter_dry_run_keeps_the_file(project: Path) -> None:
    tools.create_robei_model(
        proj_path=str(project),
        name="pw3",
        ports=[{"name": "d", "direction": "input", "width": 1}],
    )
    target = _model(project, "pw3")
    target.write_text(
        target.read_text(encoding="utf-8").replace('Parameters=""', 'Parameters="LEN = 2;"'),
        encoding="utf-8",
    )
    before = target.read_bytes()
    out = tools.set_robei_parameter(f"{project.stem}/model/pw3.model", "LEN", 4, dry_run=True)
    assert out["dry_run"] is True and out["new_value"] == "4"
    assert target.read_bytes() == before


def test_duplicate_wire_declaration_keeps_the_widest_datasize(project: Path) -> None:
    """Robei writes ``Datasize`` as a width, so ``4`` must beat ``1`` on one net.

    ``width_of`` used to read ``"4"`` as one bit, so the first declaration won by
    accident and the bus silently collapsed.
    """
    _four_bit_project(project)
    target = _model(project, "chain")
    header = (
        '<!DOCTYPE Robei>\n'
        '<Module Category="Current" Class="module" Code="" Name="chain" Parameters="" '
        'Parent="0" Type="model" Width="900">\n'
        ' <Port Inout="input" Datasize="4" Datatype="wire" Name="a" Parent="chain"/>\n'
        ' <Port Inout="output" Datasize="4" Datatype="wire" Name="y" Parent="chain"/>\n'
        ' <Module Class="buf4" File="Current/buf4.model" Name="u1" Parent="chain" Type="model">\n'
        '  <Port Inout="input" Datasize="4" Datatype="wire" Name="a" Parent="u1"/>\n'
        '  <Port Inout="output" Datasize="4" Datatype="wire" Name="y" Parent="u1"/>\n'
        " </Module>\n"
        ' <Module Class="buf4" File="Current/buf4.model" Name="u2" Parent="chain" Type="model">\n'
        '  <Port Inout="input" Datasize="4" Datatype="wire" Name="a" Parent="u2"/>\n'
        '  <Port Inout="output" Datasize="4" Datatype="wire" Name="y" Parent="u2"/>\n'
        " </Module>\n"
        ' <Wire Name="a_n" From="chain>a" To="chain#u1>a" Datasize="4"/>\n'
        ' <Wire Name="y_n" From="chain#u2>y" To="chain>y" Datasize="4"/>\n'
    )
    # the same net declared twice, narrow first: the wide one has to win
    narrow_first = header + (
        ' <Wire Name="mid" From="chain#u1>y" To="chain#u2>a" Datasize="1"/>\n'
        ' <Wire Name="mid" From="chain#u1>y" To="chain#u2>a" Datasize="4"/>\n'
        "</Module>\n"
    )
    target.write_text(narrow_first, encoding="utf-8")
    text = model_to_verilog(parse_robei_model(target))
    assert "wire [3:0] mid;" in text
    assert "wire mid;" not in text

    narrow_only = header + (
        ' <Wire Name="mid" From="chain#u1>y" To="chain#u2>a" Datasize="1"/>\n'
        "</Module>\n"
    )
    target.write_text(narrow_only, encoding="utf-8")
    assert "wire mid;" in model_to_verilog(parse_robei_model(target))
