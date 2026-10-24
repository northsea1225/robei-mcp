"""1.4: canvas bit-selects (Connect="a[0]") and per-instance parameter overrides.

The encodings under test are the ones Robei 5.0 itself saves (samples in
``D:\\Temp\\robei_samples\\gui_probe``): a select lives on the instance port and
Robei deletes any ``<Wire>`` that mentions one, while overrides live in the
nested ``<Module Parameters="LEN = 8;">`` and become ``#(.LEN(8))``.
"""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest

from robei_mcp import p1, tools
from robei_mcp.config import SETTINGS
from robei_mcp.model import model_to_verilog, parse_robei_model


@pytest.fixture()
def proj() -> Path:
    name = f"t14_{uuid.uuid4().hex[:8]}"
    project = tools.create_robei_project(name, models=[])
    path = Path(project["proj"])
    try:
        yield path
    finally:
        shutil.rmtree(path.parent, ignore_errors=True)


def _child(name: str, parameters: str, ports: list[tuple[str, str, str]]) -> str:
    lines = [
        "<!DOCTYPE Robei>",
        f'<Module Category="Current" Class="module" Code="" Comment="" Height="600" '
        f'Name="{name}" Parameters="{parameters}" Parent="0" Type="model" Width="900" X="0" Y="0">',
    ]
    for index, (port, direction, width) in enumerate(ports):
        lines.append(
            f' <Port Color="#faebd7" Connect="" Datasize="{width}" Datatype="wire" '
            f'Description="" Height="30" Inout="{direction}" Name="{port}" Parent="{name}" '
            f'Side="left" Width="30" X="-0.02" Y="{0.2 + index * 0.2:.3f}"/>'
        )
    lines.append("</Module>")
    return "\n".join(lines) + "\n"


def _add_child(proj: Path, name: str, parameters: str, ports: list[tuple[str, str, str]]) -> None:
    model_dir = proj.parent / "model"
    (model_dir / f"{name}.model").write_text(_child(name, parameters, ports), encoding="utf-8")
    tools._register_model_in_project(proj, name)


def _canvas(proj: Path, **kwargs):
    args = dict(
        proj_path=str(proj),
        name="top",
        ports=[
            {"name": "a", "direction": "input", "width": 4},
            {"name": "y", "direction": "output", "width": 1},
        ],
        instances=[{"name": "u1", "model": "sub"}],
        wires=[{"from": "u1.y", "to": "y"}],
        overwrite=True,
    )
    args.update(kwargs)
    return tools.create_robei_canvas_model(**args)


# --------------------------------------------------------------------- F1
def test_bit_select_is_written_as_instance_port_connect(proj: Path) -> None:
    _add_child(proj, "sub", "LEN = 4;", [("x", "input", "1"), ("y", "output", "1")])
    result = _canvas(proj, wires=[{"from": "a[0]", "to": "u1.x"}, {"from": "u1.y", "to": "y"}])
    assert result["warnings"] == []

    text = (proj.parent / "model" / "top.model").read_text(encoding="utf-8")
    assert 'Connect="a[0]"' in text
    # A <Wire> that mentions a select is deleted by Robei on save, so none may exist.
    assert "a[0]" not in "".join(
        line for line in text.splitlines() if "<Wire" in line
    )

    model = parse_robei_model(proj.parent / "model" / "top.model")
    ports = {port["name"]: port for port in model["instances"][0]["ports"]}
    assert ports["x"]["connect"] == "a[0]"
    assert model["instances"][0]["ports"][0]["connect"] == "a[0]"

    verilog = model_to_verilog(model)
    assert ".x(a[0])" in verilog
    # The select is an expression, never a net that needs declaring.
    assert "wire a[0]" not in verilog


def test_slice_must_match_the_instance_port_width(proj: Path) -> None:
    _add_child(
        proj,
        "bus4",
        "",
        [("x", "input", "4"), ("y", "output", "1")],
    )
    result = _canvas(
        proj,
        ports=[{"name": "a", "direction": "input", "width": 8},
               {"name": "y", "direction": "output", "width": 1}],
        instances=[{"name": "u1", "model": "bus4"}],
        wires=[{"from": "a[7:4]", "to": "u1.x"}, {"from": "u1.y", "to": "y"}],
    )
    assert result["ok"] is True
    text = (proj.parent / "model" / "top.model").read_text(encoding="utf-8")
    assert 'Connect="a[7:4]"' in text

    with pytest.raises(tools.WidthMismatch) as excinfo:
        _canvas(
            proj,
            ports=[{"name": "a", "direction": "input", "width": 8},
                   {"name": "y", "direction": "output", "width": 1}],
            instances=[{"name": "u1", "model": "bus4"}],
            wires=[{"from": "a[3:1]", "to": "u1.x"}, {"from": "u1.y", "to": "y"}],
        )
    assert "must be exactly as wide" in str(excinfo.value)


@pytest.mark.parametrize("select", ["a[4]", "a[9:8]"])
def test_bit_select_out_of_range_is_rejected(proj: Path, select: str) -> None:
    _add_child(proj, "sub", "LEN = 4;", [("x", "input", "1"), ("y", "output", "1")])
    with pytest.raises(ValueError) as excinfo:
        _canvas(proj, wires=[{"from": select, "to": "u1.x"}, {"from": "u1.y", "to": "y"}])
    assert "out of range" in str(excinfo.value)


def test_wire_endpoint_with_a_select_is_refused() -> None:
    with pytest.raises(ValueError) as excinfo:
        tools._canvas_endpoint("top", "a[0]")
    message = str(excinfo.value)
    assert "silently deletes" in message
    assert 'Connect="a[0]"' in message


def test_both_ends_sliced_is_refused(proj: Path) -> None:
    _add_child(proj, "sub", "LEN = 4;", [("x", "input", "1"), ("y", "output", "1")])
    with pytest.raises(ValueError) as excinfo:
        _canvas(proj, wires=[{"from": "a[0]", "to": "u1.x[0]"}])
    assert "only the parent-side port" in str(excinfo.value)


def test_select_on_an_instance_port_is_refused(proj: Path) -> None:
    _add_child(proj, "sub", "LEN = 4;", [("x", "input", "1"), ("y", "output", "1")])
    with pytest.raises(ValueError) as excinfo:
        _canvas(proj, wires=[{"from": "u1.y[0]", "to": "y"}])
    assert "only supported on a top-level port" in str(excinfo.value)


def test_parameterised_width_warns_but_is_written(proj: Path) -> None:
    model_dir = proj.parent / "model"
    _add_child(proj, "sub", "LEN = 4;", [("x", "input", "1"), ("y", "output", "1")])
    (model_dir / "wide.model").write_text(
        _child("wide", "N = 8;", [("a", "input", "N-1:0"), ("y", "output", "1")]),
        encoding="utf-8",
    )
    tools._register_model_in_project(proj, "wide")
    (model_dir / "top_param.model").write_text(
        "<!DOCTYPE Robei>\n"
        '<Module Category="Current" Class="module" Code="" Comment="" Height="600" '
        'Name="top_param" Parameters="" Parent="0" Type="model" Width="900" X="0" Y="0">\n'
        ' <Module Category="Current" Class="sub" Code="" Comment="" Height="160" '
        'Name="u1" Parameters="" Parent="top_param" Type="model" Width="160" X="300" Y="120">\n'
        '  <Port Color="#faebd7" Connect="" Datasize="1" Datatype="wire" Description="" '
        'Height="30" Inout="input" Name="x" Parent="u1" Side="left" Width="30" X="-0.1" Y="0.2"/>\n'
        " </Module>\n"
        ' <Port Color="#faebd7" Connect="" Datasize="N-1:0" Datatype="wire" Description="" '
        'Height="30" Inout="input" Name="a" Parent="top_param" Side="left" Width="30" X="-0.02" Y="0.2"/>\n'
        "</Module>\n",
        encoding="utf-8",
    )
    target = model_dir / "top_param.model"
    tools._register_model_in_project(proj, "top_param")
    result = tools.edit_robei_canvas(
        str(target), [{"op": "connect", "from": "a[0]", "to": "u1.x"}]
    )
    assert result["ok"] is True
    assert result["warnings"] and "not a constant" in result["warnings"][0]
    assert 'Connect="a[0]"' in target.read_text(encoding="utf-8")


def test_import_verilog_canvas_keeps_bit_selects(proj: Path) -> None:
    _add_child(proj, "sub", "LEN = 4;", [("x", "input", "1"), ("y", "output", "1")])
    source = proj.parent / "top_src.v"
    source.write_text(
        "module top_src(input wire [3:0] a, output wire y);\n"
        "  sub u1(.x(a[0]), .y(y));\n"
        "endmodule\n",
        encoding="utf-8",
    )
    imported = p1.import_verilog_canvas(str(proj), str(source))
    assert imported["not_imported"]["skipped_connections"] == []
    model = parse_robei_model(Path(imported["path"]))
    ports = {port["name"]: port for port in model["instances"][0]["ports"]}
    assert ports["x"]["connect"] == "a[0]"
    assert "wire a[0]" not in model_to_verilog(model)


def test_disconnect_clears_a_select_connect(proj: Path) -> None:
    _add_child(proj, "sub", "LEN = 4;", [("x", "input", "1"), ("y", "output", "1")])
    _canvas(proj, wires=[{"from": "a[0]", "to": "u1.x"}, {"from": "u1.y", "to": "y"}])
    target = proj.parent / "model" / "top.model"
    result = tools.edit_robei_canvas(str(target), [{"op": "disconnect", "from": "u1.x"}])
    assert result["ops_applied"][0]["connects_cleared"] == ["u1.x"]
    assert "a[0]" not in target.read_text(encoding="utf-8")


# --------------------------------------------------------------------- F2
def test_parameter_dict_is_sorted_and_escaped(proj: Path) -> None:
    _add_child(
        proj, "pair", "A = 0; // first&#xa;B = 0; // second&#xa;",
        [("x", "input", "1"), ("y", "output", "1")],
    )
    result = _canvas(
        proj,
        instances=[{"name": "u1", "model": "pair", "parameters": {"B": 2, "A": 1}}],
        wires=[{"from": "a[0]", "to": "u1.x"}, {"from": "u1.y", "to": "y"}],
    )
    assert result["warnings"] == []
    text = (proj.parent / "model" / "top.model").read_text(encoding="utf-8")
    assert 'Parameters="A = 1;&#xa;B = 2;"' in text
    # A literal newline inside an attribute would be folded to a space by any
    # XML parser, which is exactly why Robei writes &#xa;.
    assert 'Parameters="A = 1;\nB = 2;"' not in text

    model = parse_robei_model(proj.parent / "model" / "top.model")
    assert model["instances"][0]["parameters"] == "A = 1;\nB = 2;"
    verilog = model_to_verilog(model)
    assert "pair #(.A(1), .B(2)) u1(" in verilog


def test_single_parameter_override_is_emitted(proj: Path) -> None:
    _add_child(proj, "sub", "LEN = 4; // width", [("x", "input", "1"), ("y", "output", "1")])
    _canvas(
        proj,
        instances=[{"name": "u1", "model": "sub", "parameters": {"LEN": 8}}],
        wires=[{"from": "a[0]", "to": "u1.x"}, {"from": "u1.y", "to": "y"}],
    )
    model = parse_robei_model(proj.parent / "model" / "top.model")
    assert model["instances"][0]["parameters"] == "LEN = 8;"
    assert "sub #(.LEN(8)) u1(" in model_to_verilog(model)


def test_parameter_equal_to_the_default_is_not_emitted(proj: Path) -> None:
    _add_child(proj, "sub", "LEN = 4;", [("x", "input", "1"), ("y", "output", "1")])
    _canvas(
        proj,
        instances=[{"name": "u1", "model": "sub", "parameters": {"LEN": 4}}],
        wires=[{"from": "a[0]", "to": "u1.x"}, {"from": "u1.y", "to": "y"}],
    )
    verilog = model_to_verilog(parse_robei_model(proj.parent / "model" / "top.model"))
    assert "#(" not in verilog


def test_undeclared_parameter_is_rejected(proj: Path) -> None:
    _add_child(proj, "sub", "LEN = 4;", [("x", "input", "1"), ("y", "output", "1")])
    with pytest.raises(ValueError) as excinfo:
        _canvas(proj, instances=[{"name": "u1", "model": "sub", "parameters": {"NOPE": 1}}])
    message = str(excinfo.value)
    assert "not declared" in message and "['LEN']" in message


def test_child_without_parameters_is_rejected(proj: Path) -> None:
    _add_child(proj, "sub", "", [("x", "input", "1"), ("y", "output", "1")])
    with pytest.raises(ValueError) as excinfo:
        _canvas(proj, instances=[{"name": "u1", "model": "sub", "parameters": {"LEN": 8}}])
    assert "declares no parameters" in str(excinfo.value)


@pytest.mark.parametrize(
    "parameters",
    [
        {"1BAD": 1},
        {"LEN": "1;2"},
        {"LEN": "1\n2"},
        {"LEN": ""},
    ],
)
def test_invalid_parameter_input_is_rejected(proj: Path, parameters: dict) -> None:
    _add_child(proj, "sub", "LEN = 4;", [("x", "input", "1"), ("y", "output", "1")])
    with pytest.raises(ValueError):
        _canvas(proj, instances=[{"name": "u1", "model": "sub", "parameters": parameters}])


@pytest.mark.parametrize(
    "raw",
    ["#(.LEN(8))", ".LEN(8)", "parameter LEN = 8;"],
)
def test_verilog_parameter_spellings_are_rejected(proj: Path, raw: str) -> None:
    _add_child(proj, "sub", "LEN = 4;", [("x", "input", "1"), ("y", "output", "1")])
    with pytest.raises(ValueError) as excinfo:
        _canvas(proj, instances=[{"name": "u1", "model": "sub", "parameters": raw}])
    message = str(excinfo.value)
    assert "is Verilog, not Robei's Parameters attribute" in message
    assert '{"LEN": 8}' in message


def test_duplicate_parameter_is_rejected(proj: Path) -> None:
    _add_child(
        proj, "pair", "A = 0;&#xa;B = 0;", [("x", "input", "1"), ("y", "output", "1")]
    )
    with pytest.raises(ValueError) as excinfo:
        _canvas(
            proj,
            instances=[{"name": "u1", "model": "pair", "parameters": "A = 1;\nA = 2;"}],
        )
    assert "more than once" in str(excinfo.value)


def test_robei_parameters_string_is_accepted_and_kept(proj: Path) -> None:
    _add_child(proj, "sub", "LEN = 4; // width", [("x", "input", "1"), ("y", "output", "1")])
    result = _canvas(
        proj,
        instances=[{"name": "u1", "model": "sub", "parameters": "LEN = 8; // eight\n"}],
    )
    assert result["warnings"] == []
    model = parse_robei_model(proj.parent / "model" / "top.model")
    assert model["instances"][0]["parameters"] == "LEN = 8; // eight"
    assert "#(.LEN(8))" in model_to_verilog(model)


def test_broken_parameters_string_is_written_with_a_warning(proj: Path) -> None:
    _add_child(proj, "sub", "LEN = 4;", [("x", "input", "1"), ("y", "output", "1")])
    result = _canvas(proj, instances=[{"name": "u1", "model": "sub", "parameters": "LEN = ;"}])
    assert result["warnings"] and "cannot parse Parameters" in result["warnings"][0]
    assert "#(" not in model_to_verilog(parse_robei_model(proj.parent / "model" / "top.model"))


def test_set_parameters_op_edits_and_previews(proj: Path) -> None:
    _add_child(
        proj, "pair", "A = 0;&#xa;B = 0;", [("x", "input", "1"), ("y", "output", "1")]
    )
    _canvas(
        proj,
        instances=[{"name": "u1", "model": "pair"}],
        wires=[{"from": "a[0]", "to": "u1.x"}, {"from": "u1.y", "to": "y"}],
    )
    target = proj.parent / "model" / "top.model"

    preview = tools.edit_robei_canvas(
        str(target),
        [{"op": "set_parameters", "name": "u1", "parameters": {"B": 2, "A": 1}}],
        dry_run=True,
    )
    assert preview["dry_run"] is True
    assert "pair #(.A(1), .B(2)) u1(" in preview["verilog_preview"]
    assert parse_robei_model(target)["instances"][0]["parameters"] == ""

    applied = tools.edit_robei_canvas(
        str(target),
        [{"op": "set_parameters", "name": "u1", "parameters": {"B": 2, "A": 1}}],
    )
    assert applied["ops_applied"][0]["updated"] is True
    assert applied["diff"]["parameters_changed"] == {
        "u1": {"before": "", "after": "A = 1;\nB = 2;"}
    }
    assert parse_robei_model(target)["instances"][0]["parameters"] == "A = 1;\nB = 2;"

    with pytest.raises(ValueError):
        tools.edit_robei_canvas(
            str(target), [{"op": "set_parameters", "name": "u9", "parameters": {"A": 1}}]
        )


def test_add_instance_takes_parameters(proj: Path) -> None:
    _add_child(proj, "sub", "LEN = 4;", [("x", "input", "1"), ("y", "output", "1")])
    _add_child(proj, "pair", "A = 0;&#xa;B = 0;", [("x", "input", "1"), ("y", "output", "1")])
    _canvas(proj)
    target = proj.parent / "model" / "top.model"
    result = tools.edit_robei_canvas(
        str(target),
        [{"op": "add_instance", "name": "u2", "model": "pair", "parameters": {"A": 3}}],
    )
    assert result["ops_applied"][0]["parameters"] == "A = 3;"
    assert "pair #(.A(3)) u2(" in result["verilog_preview"]


def test_non_constant_select_is_reported_clearly(proj: Path) -> None:
    _add_child(proj, "sub", "LEN = 4;", [("x", "input", "1"), ("y", "output", "1")])
    with pytest.raises(ValueError) as excinfo:
        _canvas(proj, wires=[{"from": "a[LEN-1]", "to": "u1.x"}])
    message = str(excinfo.value)
    assert "only constant selects" in message
    assert '{"from": "a[0]", "to": "u1.x"}' in message


def test_unknown_instance_in_a_wire_is_still_rejected(proj: Path) -> None:
    _add_child(proj, "sub", "LEN = 4;", [("x", "input", "1"), ("y", "output", "1")])
    with pytest.raises(ValueError) as excinfo:
        _canvas(proj, wires=[{"from": "a[0]", "to": "u9.x"}])
    assert "unknown instance" in str(excinfo.value)


def test_set_parameters_on_a_child_without_parameters(proj: Path) -> None:
    _add_child(proj, "sub", "", [("x", "input", "1"), ("y", "output", "1")])
    _canvas(proj, wires=[{"from": "a[0]", "to": "u1.x"}, {"from": "u1.y", "to": "y"}])
    target = proj.parent / "model" / "top.model"
    with pytest.raises(ValueError) as excinfo:
        tools.edit_robei_canvas(
            str(target), [{"op": "set_parameters", "name": "u1", "parameters": {"LEN": 8}}]
        )
    assert "declares no parameters" in str(excinfo.value)


# --------------------------------------------------------------------- F3
def test_driver_kill_targets_only_the_owned_pid(monkeypatch, tmp_path) -> None:
    from robei_mcp.gui_agent import driver, ui

    calls: list[list[str]] = []

    class _Done:
        returncode = 0
        stdout = "SUCCESS"
        stderr = ""

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return _Done()

    monkeypatch.setattr(driver, "OUT_DIR", tmp_path)
    monkeypatch.setattr(driver.subprocess, "run", fake_run)
    monkeypatch.setattr(ui, "owned_pid", lambda: 4321)

    assert driver.main(["driver", "kill", "Robei.exe"]) == 0
    assert calls == [["taskkill", "/F", "/PID", "4321"]]


def test_driver_kill_refuses_a_by_name_cleanup(monkeypatch, tmp_path) -> None:
    import json

    from robei_mcp.gui_agent import driver, ui

    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):  # pragma: no cover - must never be reached
        calls.append(list(argv))
        raise AssertionError("taskkill must not run without an owned pid")

    monkeypatch.setattr(driver, "OUT_DIR", tmp_path)
    monkeypatch.setattr(driver.subprocess, "run", fake_run)
    monkeypatch.setattr(ui, "owned_pid", lambda: None)

    assert driver.main(["driver", "kill", "Robei.exe"]) == 1
    assert calls == []
    payload = json.loads((tmp_path / "last.json").read_text(encoding="utf-8"))
    assert payload["ok"] is False
    assert "refusing to kill by image name" in payload["error"]


def test_driver_kill_refuses_a_foreign_pid(monkeypatch, tmp_path) -> None:
    from robei_mcp.gui_agent import driver, ui

    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):  # pragma: no cover - must never be reached
        calls.append(list(argv))
        raise AssertionError("taskkill must not run for a foreign pid")

    monkeypatch.setattr(driver, "OUT_DIR", tmp_path)
    monkeypatch.setattr(driver.subprocess, "run", fake_run)
    monkeypatch.setattr(ui, "owned_pid", lambda: 4321)

    assert driver.main(["driver", "kill", "999"]) == 1
    assert calls == []


def test_driver_never_kills_by_image_name() -> None:
    from robei_mcp.gui_agent import driver

    source = Path(driver.__file__).read_text(encoding="utf-8")
    assert '"/IM"' not in source


def test_same_image_compares_resolved_paths(monkeypatch) -> None:
    from robei_mcp.gui_agent import ui

    alias = {
        r"C:\junc\Robei.exe": r"C:\Robei\bin\Robei.exe",
        r"C:\Robei\bin\Robei.exe": r"C:\Robei\bin\Robei.exe",
        r"D:\portable\Robei.exe": r"D:\portable\Robei.exe",
    }
    monkeypatch.setattr(ui.os.path, "realpath", lambda value: alias.get(value, value))

    assert ui._same_image(r"C:\junc\Robei.exe", r"C:\robei\BIN\robei.exe")
    assert not ui._same_image(r"D:\portable\Robei.exe", r"C:\Robei\bin\Robei.exe")
    assert not ui._same_image("", r"C:\Robei\bin\Robei.exe")
