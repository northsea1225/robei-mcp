"""1.5: root parameters, unified width types, and specific error hints."""
from __future__ import annotations

import asyncio
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest

from robei_mcp import errors, tools
from robei_mcp.config import SETTINGS
from robei_mcp.model import model_to_verilog, parse_robei_model


@pytest.fixture()
def proj() -> Path:
    name = f"t15_{uuid.uuid4().hex[:8]}"
    project = tools.create_robei_project(name, models=[])
    path = Path(project["proj"])
    try:
        yield path
    finally:
        shutil.rmtree(path.parent, ignore_errors=True)


def _model(proj: Path, name: str) -> Path:
    return proj.parent / "model" / f"{name}.model"


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


# ---------------------------------------------------------------- G1 root parameters
def test_create_model_writes_root_parameters(proj: Path) -> None:
    result = tools.create_robei_model(
        proj_path=str(proj),
        name="bus8",
        ports=[{"name": "a", "direction": "input", "width": "LEN"},
               {"name": "y", "direction": "output", "width": 1}],
        parameters={"LEN": 4},
        code="assign y = a[0];",
    )
    assert result["ok"] is True
    assert result["parameters"] == [{"name": "LEN", "default": "4", "comment": ""}]
    assert result["parameters_text"] == "LEN = 4;"

    text = _model(proj, "bus8").read_text(encoding="utf-8")
    assert 'Parameters="LEN = 4;"' in text
    assert 'Datasize="LEN-1:0"' in text
    # 4 is a number now, "LEN-1:0" cannot be
    assert [port["datasize"] for port in result["ports"]] == ["LEN-1:0", 1]
    assert result["warnings"] == []


def test_root_parameters_are_sorted_and_escaped(proj: Path) -> None:
    result = tools.create_robei_model(
        proj_path=str(proj),
        name="pair15",
        ports=[{"name": "y", "direction": "output", "width": 1}],
        parameters={"B": 2, "A": 1},
    )
    text = _model(proj, "pair15").read_text(encoding="utf-8")
    assert 'Parameters="A = 1;&#xa;B = 2;"' in text
    assert 'Parameters="A = 1;\nB = 2;"' not in text
    assert result["parameters_text"] == "A = 1;\nB = 2;"


def test_port_width_uses_robei_range_form(proj: Path) -> None:
    for name, width, expected in [
        ("w_int", 8, "8"),
        ("w_str", "8", "8"),
        ("w_param", "LEN", "LEN-1:0"),
        ("w_range", "LEN-1:0", "LEN-1:0"),
    ]:
        tools.create_robei_model(
            proj_path=str(proj),
            name=name,
            ports=[{"name": "a", "direction": "input", "width": width}],
            parameters={"LEN": 4},
        )
        assert f'Datasize="{expected}"' in _model(proj, name).read_text(encoding="utf-8")


def test_port_width_must_reference_a_declared_parameter(proj: Path) -> None:
    with pytest.raises(errors.BadRequest) as excinfo:
        tools.create_robei_model(
            proj_path=str(proj),
            name="undeclared",
            ports=[{"name": "a", "direction": "input", "width": "LEN"}],
        )
    message = str(excinfo.value)
    assert "does not declare" in message
    assert "parameters=" in excinfo.value.hint


@pytest.mark.parametrize(
    "parameters",
    [
        {"1BAD": 1},
        {"LEN": "4;8"},
        {"LEN": "4\n8"},
        {"LEN": ""},
        "#(.LEN(4))",
        ".LEN(4)",
        "parameter LEN = 4;",
        "LEN = 4;\nLEN = 8;",
    ],
)
def test_root_parameters_reject_bad_input(proj: Path, parameters) -> None:
    with pytest.raises(errors.BadRequest) as excinfo:
        tools.create_robei_model(
            proj_path=str(proj), name="badparam", parameters=parameters
        )
    assert excinfo.value.hint


def test_new_model_parameters_can_be_overridden(proj: Path) -> None:
    tools.create_robei_model(
        proj_path=str(proj),
        name="sub",
        ports=[{"name": "x", "direction": "input", "width": 1},
               {"name": "y", "direction": "output", "width": 1}],
        parameters={"LEN": 4},
        code="assign y = x;",
    )
    _canvas(proj, instances=[{"name": "u1", "model": "sub", "parameters": {"LEN": 8}}])
    verilog = model_to_verilog(parse_robei_model(_model(proj, "top")))
    assert "sub #(.LEN(8)) u1(" in verilog

    with pytest.raises(errors.BadRequest) as excinfo:
        _canvas(proj, instances=[{"name": "u1", "model": "sub", "parameters": {"NOPE": 1}}])
    assert "not declared" in str(excinfo.value)
    assert "LEN" in excinfo.value.hint


def test_export_keeps_the_parameterised_port(proj: Path) -> None:
    tools.create_robei_model(
        proj_path=str(proj),
        name="bus4",
        ports=[{"name": "a", "direction": "input", "width": "LEN"},
               {"name": "y", "direction": "output", "width": 1}],
        parameters={"LEN": 4},
        code="assign y = a[0];",
    )
    verilog = model_to_verilog(parse_robei_model(_model(proj, "bus4")))
    assert "parameter LEN = 4" in verilog
    assert "input wire [LEN-1:0] a" in verilog


# ---------------------------------------------------------------- G2 width types
def test_wire_datasize_accepts_int_and_numeric_string(proj: Path) -> None:
    tools.create_robei_model(
        proj_path=str(proj),
        name="sub",
        ports=[{"name": "x", "direction": "input", "width": 1},
               {"name": "y", "direction": "output", "width": 8}],
    )
    as_int = _canvas(
        proj, name="top_int",
        wires=[{"from": "u1.y", "to": "y", "datasize": 8}],
    )
    as_str = _canvas(
        proj, name="top_str",
        wires=[{"from": "u1.y", "to": "y", "datasize": "8"}],
    )
    assert as_int["wires"][0]["datasize"] == 8
    assert as_str["wires"][0]["datasize"] == 8
    for name in ("top_int", "top_str"):
        assert 'Datasize="8"' in _model(proj, name).read_text(encoding="utf-8")


def test_port_width_accepts_int_and_numeric_string(proj: Path) -> None:
    tools.create_robei_model(
        proj_path=str(proj), name="sub", ports=[{"name": "y", "direction": "output", "width": 1}]
    )
    ports_int = [{"name": "a", "direction": "input", "width": 4},
                 {"name": "y", "direction": "output", "width": 1}]
    ports_str = [{"name": "a", "direction": "input", "width": "4"},
                 {"name": "y", "direction": "output", "width": "1"}]
    first = _canvas(proj, name="top_a", ports=ports_int)
    second = _canvas(proj, name="top_b", ports=ports_str)

    def shape(result):
        return [(port["name"], port["datasize"]) for port in result["ports"]]

    assert shape(first) == shape(second) == [("a", 4), ("y", 1)]
    assert [port["width"] for port in first["ports"]] == [4, 1]


def test_set_port_accepts_int_and_numeric_string(proj: Path) -> None:
    tools.create_robei_model(
        proj_path=str(proj), name="p1", ports=[{"name": "a", "direction": "input", "width": 1}]
    )
    first = tools.set_port(str(_model(proj, "p1")), "a", "input", 8)
    second = tools.set_port(str(_model(proj, "p1")), "a", "input", "8")
    assert first["port"] == second["port"]
    assert first["port"]["width"] == 8 and first["port"]["datasize"] == 8
    assert 'Datasize="8"' in _model(proj, "p1").read_text(encoding="utf-8")


@pytest.mark.parametrize("width", [0, -1, "", "8;9", True, 2.5])
def test_width_rejects_non_widths(proj: Path, width) -> None:
    with pytest.raises(errors.BadRequest) as excinfo:
        tools.create_robei_model(
            proj_path=str(proj),
            name="badwidth",
            ports=[{"name": "a", "direction": "input", "width": width}],
        )
    assert excinfo.value.hint


def test_add_port_accepts_a_parameterised_width(proj: Path) -> None:
    tools.create_robei_model(
        proj_path=str(proj), name="p2", ports=[{"name": "y", "direction": "output", "width": 1}],
        parameters={"LEN": 4},
    )
    result = tools.edit_robei_canvas(
        str(_model(proj, "p2")),
        [{"op": "add_port", "name": "bus", "direction": "input", "width": "LEN"}],
    )
    assert 'Datasize="LEN-1:0"' in _model(proj, "p2").read_text(encoding="utf-8")
    assert result["ports"][-1]["datasize"] == "LEN-1:0"
    assert result["ports"][-1]["width"] is None


# ---------------------------------------------------------------- G3 hints
def _payload(exc: BaseException) -> dict:
    return errors.payload(exc)


def test_out_of_range_bitsel_hint(proj: Path) -> None:
    tools.create_robei_model(
        proj_path=str(proj), name="sub", ports=[{"name": "x", "direction": "input", "width": 1}]
    )
    with pytest.raises(errors.BadRequest) as excinfo:
        _canvas(proj, wires=[{"from": "a[9]", "to": "u1.x"}])
    data = _payload(excinfo.value)
    assert data["code"] == "invalid_argument"
    assert "out of range" in data["error"]
    assert "a is 4 bit(s) wide" in data["hint"]
    assert "a[3]" in data["hint"]


def test_unknown_port_hint_lists_candidates(proj: Path) -> None:
    tools.create_robei_model(
        proj_path=str(proj), name="sub", ports=[{"name": "x", "direction": "input", "width": 1}]
    )
    with pytest.raises(errors.BadRequest) as excinfo:
        _canvas(proj, wires=[{"from": "aa", "to": "u1.x"}])
    data = _payload(excinfo.value)
    assert "unknown top-level port 'aa'" in data["error"]
    assert "a" in data["hint"] and "y" in data["hint"]
    assert '{"from": "a[0]", "to": "u1.x"}' in data["hint"]


def test_unknown_instance_hint_lists_candidates(proj: Path) -> None:
    tools.create_robei_model(
        proj_path=str(proj), name="sub", ports=[{"name": "x", "direction": "input", "width": 1}]
    )
    with pytest.raises(errors.BadRequest) as excinfo:
        _canvas(proj, wires=[{"from": "a[0]", "to": "u2.x"}])
    data = _payload(excinfo.value)
    assert "u2" in data["error"]
    assert "u1" in data["hint"]
    assert "close" in data["hint"] or "u1" in data["hint"]


def test_undeclared_parameter_hint_names_the_declared_ones(proj: Path) -> None:
    tools.create_robei_model(
        proj_path=str(proj), name="sub",
        ports=[{"name": "x", "direction": "input", "width": 1}], parameters={"LEN": 4},
    )
    with pytest.raises(errors.BadRequest) as excinfo:
        _canvas(proj, instances=[{"name": "u1", "model": "sub", "parameters": {"LN": 8}}])
    data = _payload(excinfo.value)
    assert "LN" in data["error"]
    assert "LEN" in data["hint"]
    assert "closest" in data["hint"]


def test_width_mismatch_hint_names_the_width(proj: Path) -> None:
    tools.create_robei_model(
        proj_path=str(proj), name="bus4",
        ports=[{"name": "x", "direction": "input", "width": 4}],
    )
    with pytest.raises(errors.WidthMismatch) as excinfo:
        _canvas(
            proj,
            instances=[{"name": "u1", "model": "bus4"}],
            wires=[{"from": "a[3:1]", "to": "u1.x"}],
        )
    data = _payload(excinfo.value)
    assert data["code"] == "width_mismatch"
    assert "4 bit(s)" in data["hint"]
    assert "a[3:0]" in data["hint"]


def test_generic_hint_is_kept_for_plain_value_errors() -> None:
    data = _payload(ValueError("something odd"))
    assert data["code"] == "invalid_argument"
    assert data["hint"] == errors.HINTS["invalid_argument"]


def test_bad_request_can_override_the_code() -> None:
    data = _payload(errors.BadRequest("nope", "do it this way", code="bad_argument"))
    assert data["code"] == "bad_argument"
    assert data["hint"] == "do it this way"


# ---------------------------------------------------------------- width operators
@pytest.mark.skipif(not Path(SETTINGS.iverilog).exists(), reason="iverilog not installed")
def test_arithmetic_width_keeps_its_operators(proj: Path, tmp_path: Path) -> None:
    tools.create_robei_model(
        proj_path=str(proj),
        name="wide",
        ports=[{"name": "a", "direction": "input", "width": "2*LEN"},
               {"name": "y", "direction": "output", "width": 1}],
        parameters={"LEN": 4},
        code="assign y = a[0];",
    )
    # No auto parentheses: * binds tighter than -, exactly like Robei's own
    # "LENA+LENB-1:0" sample.
    assert 'Datasize="2*LEN-1:0"' in _model(proj, "wide").read_text(encoding="utf-8")
    verilog = model_to_verilog(parse_robei_model(_model(proj, "wide")))
    assert "input wire [2*LEN-1:0] a" in verilog

    source = proj.parent / "wide.v"
    source.write_text(verilog, encoding="utf-8")
    wrapper = proj.parent / "wrapper15.v"
    wrapper.write_text(
        "module wrapper15(input wire [7:0] w, output wire y);\n"
        "  wide #(.LEN(4)) u(.a(w), .y(y));\n"
        "endmodule\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [str(SETTINGS.iverilog), "-g2012", "-Wall", "-o", str(tmp_path / "wide.vvp"),
         str(wrapper), str(source)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    # 2*LEN with LEN=4 is 8 bits, so the 8-bit wire must not trigger a width warning.
    assert "expects" not in proc.stderr.lower()


@pytest.mark.parametrize("width", ["LEN<<1", "LEN%2", "LEN&3", "~LEN", "LEN>=4", "A?B:C"])
def test_unsupported_width_operators_are_rejected(proj: Path, width: str) -> None:
    with pytest.raises(errors.BadRequest) as excinfo:
        tools.create_robei_model(
            proj_path=str(proj),
            name=f"op_{uuid.uuid4().hex[:6]}",
            ports=[{"name": "a", "direction": "input", "width": width}],
            parameters={"LEN": 4, "A": 1, "B": 2, "C": 3},
        )
    assert "range" in excinfo.value.hint.lower()


@pytest.mark.parametrize("width", ["LEN-1:0", "LEN"])
def test_set_port_accepts_a_parameterised_width(proj: Path, width: str) -> None:
    tools.create_robei_model(
        proj_path=str(proj),
        name="p3",
        ports=[{"name": "y", "direction": "output", "width": 1}],
        parameters={"LEN": 4},
    )
    result = tools.set_port(str(_model(proj, "p3")), "a", "input", width)
    assert 'Datasize="LEN-1:0"' in _model(proj, "p3").read_text(encoding="utf-8")
    assert result["port"]["width"] is None
    assert result["port"]["datasize"] == "LEN-1:0"


def test_instance_port_width_is_null_for_a_parameterised_port(proj: Path) -> None:
    tools.create_robei_model(
        proj_path=str(proj),
        name="busx",
        ports=[{"name": "a", "direction": "input", "width": "LEN"},
               {"name": "y", "direction": "output", "width": 1}],
        parameters={"LEN": 4},
        code="assign y = a[0];",
    )
    result = _canvas(
        proj,
        instances=[{"name": "u1", "model": "busx"}],
        wires=[{"from": "a[3:0]", "to": "u1.a"}, {"from": "u1.y", "to": "y"}],
    )
    ports = {port["name"]: port for port in result["instances"][0]["ports"]}
    assert ports["a"]["width"] is None
    assert ports["a"]["datasize"] == "LEN-1:0"
    assert ports["y"]["width"] == 1 and ports["y"]["datasize"] == 1


def test_hints_table_is_unchanged() -> None:
    """1.5 only added BadRequest/WidthMismatch hints; the table itself must not drift."""
    assert errors.HINTS == {
        "unsafe_xml": "the XML declares a DTD or entities; Robei files never do",
        "path_escape": (
            "the path must stay inside the workspace: write it relative to workspace/ "
            "(e.g. proj/a.v), never with ../ or an absolute path outside"
        ),
        "width_mismatch": (
            "connect endpoints of equal width: widen the port/wire, or drive the narrow "
            "side through an explicit slice"
        ),
        "not_behavioral": (
            "set_robei_code only edits models without instances or wires; for a structural "
            "model use edit_robei_canvas or create_robei_model"
        ),
        "unknown_parameter": "read_robei_model lists the declared names in parameter_list[].name",
        "device_unsupported": (
            "this part has no chipdb (or no part.yaml); list_fpga_devices() shows which parts "
            "are buildable on the installed Robei database"
        ),
        "unconstrained_io": (
            "every port bit needs a package pin: create_robei_const / set_port the missing pins, "
            "then export_xdc again"
        ),
        "gui_foreign_instance": (
            "a Robei window started outside this bridge is in the way; close it yourself "
            "(the bridge only stops instances it launched), then retry"
        ),
        "gui_needs_active_desktop": (
            "the Windows session is locked or its desktop is disconnected: unlock it and "
            "retry. open_project/compile work without the desktop, the Files tree needs the mouse"
        ),
        "gui_unsaved_changes": (
            "Robei reports unsaved canvas edits; pass discard_unsaved=true to reopen and lose "
            "them, or save the project first"
        ),
        "fpga_step_failed": (
            "the failing stage is named in `stage`; `log_path` (or record.log_path) is a file "
            "under workspace/build that read_run_log can open, and log_tail already holds the last "
            "lines - fix that stage before retrying (synth -> route -> package)"
        ),
        "not_found": "the path must exist and, for models/projects, sit under an allowed root",
        "already_exists": "pass overwrite=true (the previous file is kept as .bak-<timestamp>)",
        "permission_denied": "the server may only write inside the configured workspace",
        "timeout": "raise timeout_s (bounded by max_timeout_s in robei.toml) or shorten the testbench",
        "invalid_argument": "check the argument names and types in the tool schema",
        "bad_argument": "check the argument names and types in the tool schema",
        "cancelled": "the client cancelled the request; child processes were killed",
        "unsupported": "this build does not implement the requested feature",
        "internal": (
            "unexpected failure; retry with the same arguments and, if it repeats, open the "
            "`log_path` file (or any file under `run_dir`) with read_run_log"
        ),
        "path_escape_log": (
            "read_run_log opens files inside a logs/ or build/ directory under the workspace; "
            "every fpga_step_failed payload carries the exact `log_path` to pass back"
        ),
    }


# ---------------------------------------------------------------- G4 self check
def test_self_check_reports_version_commit_and_freshness() -> None:
    check = tools.self_check()
    assert check["package_version"] == __import__("robei_mcp").__version__
    assert check["source_dir"].endswith("robei_mcp")
    assert check["process_started_at"] and check["newest_source_mtime"]
    assert check["process_age_s"] >= 0
    assert check["stale"] is False
    # The repo is a git checkout; a wheel install would report None here.
    assert check["git_commit"] is None or len(check["git_commit"]) >= 7


def test_self_check_git_commit_is_null_outside_a_repo(tmp_path: Path) -> None:
    assert tools._git_commit(tmp_path) is None


def test_stale_warning_appears_when_the_source_is_newer(monkeypatch) -> None:
    assert tools.stale_warning() is None
    monkeypatch.setattr(tools, "PROCESS_STARTED", 1.0)
    note = tools.stale_warning()
    assert note and "restart" in note and "reopen Codex" in note

    monkeypatch.setattr(tools, "PROCESS_STARTED", 1.0)
    merged = tools._with_stale_note({"ok": True, "warnings": ["existing"]})
    assert merged["warnings"][0] == note
    assert merged["warnings"][1] == "existing"

    fresh = tools._with_stale_note({"ok": True})
    assert "warnings" in fresh


def test_runtime_status_carries_the_self_check(monkeypatch) -> None:
    from robei_mcp import server_app

    fresh = server_app.runtime_status()
    assert fresh["package_version"] == fresh["server_version"]
    assert fresh["stale"] is False
    assert fresh["stale_warning"] is None
    assert "warnings" not in fresh  # nothing to warn about while up to date
    assert fresh["git_commit"] and fresh["source_dir"]

    monkeypatch.setattr(tools, "PROCESS_STARTED", 1.0)
    stale = server_app.runtime_status()
    assert stale["stale"] is True
    assert stale["stale_warning"] and "restart" in stale["stale_warning"]
    # every decorated tool attaches the same note
    assert stale["warnings"][0] == stale["stale_warning"]


def test_error_payloads_also_carry_the_stale_note(monkeypatch) -> None:
    from robei_mcp import server_app

    monkeypatch.setattr(tools, "PROCESS_STARTED", 1.0)
    payload = server_app.read_robei_model("does/not/exist.model")
    assert payload["ok"] is False
    assert payload["warnings"] and "restart" in payload["warnings"][0]


def test_gui_tools_carry_the_stale_note(monkeypatch) -> None:
    """Both GUI registration shapes (sync and async) get the warning."""
    from robei_mcp import server_app

    monkeypatch.setattr(tools, "PROCESS_STARTED", 1.0)
    monkeypatch.setattr(server_app.gui, "status", lambda **kwargs: {"ok": True})
    monkeypatch.setattr(server_app.gui, "compile_model", lambda **kwargs: {"ok": True})

    sync_payload = server_app._stale_wrapped(server_app.robei_gui_status)()
    assert sync_payload["ok"] is True
    assert sync_payload["warnings"] and "restart" in sync_payload["warnings"][0]

    async_payload = asyncio.run(server_app._stale_wrapped(server_app.robei_gui_compile)())
    assert async_payload["ok"] is True
    assert async_payload["warnings"] and "restart" in async_payload["warnings"][0]

    # and the wrapper must not invent a warning when the process is current
    monkeypatch.setattr(tools, "PROCESS_STARTED", time.time())
    fresh = server_app._stale_wrapped(server_app.robei_gui_status)()
    assert fresh == {"ok": True}
