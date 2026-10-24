"""Coverage for tools that had no pytest reference before 1.1.0 (M3).

Everything here is engine-level: Icarus/matplotlib where the tool needs them,
no Robei GUI and no desktop. GUI wrappers are exercised with a mocked
``gui.call`` so the op/argument contract is locked without a window.
"""
from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

import pytest

from robei_mcp import doctor, errors, gui, p1, server_app, tools
from robei_mcp.config import SETTINGS

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


@pytest.fixture()
def work() -> Path:
    root = SETTINGS.workspace / ".tmp" / f"cov11-{uuid.uuid4().hex[:8]}"
    root.mkdir(parents=True, exist_ok=False)
    yield root
    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture()
def project() -> Path:
    from robei_mcp import tools as tools_module

    name = f"cov11_{uuid.uuid4().hex[:8]}"
    proj = Path(tools_module.create_robei_project(name)["proj"])
    yield proj
    shutil.rmtree(SETTINGS.workspace / name, ignore_errors=True)


def _vcd(work: Path) -> Path:
    path = work / "qa.vcd"
    path.write_text(
        "$timescale 1ns $end\n$scope module qa $end\n"
        "$var wire 1 ! clk $end\n$var wire 4 v bus [3:0] $end\n"
        "$upscope $end\n$enddefinitions $end\n"
        "#0\n0!\nb0000 v\n#5\n1!\n#10\n0!\nb1011 v\n#15\n1!\n#20\n0!\nb0010 v\n#25\n",
        encoding="utf-8",
    )
    return path


def _rel(path: Path) -> str:
    return str(path.relative_to(SETTINGS.workspace)).replace("\\", "/")


# ---------------------------------------------------------------------------
# robei_doctor
# ---------------------------------------------------------------------------


def test_doctor_reports_the_toolchain() -> None:
    res = doctor.run(deep=False)
    assert res["ok"] is True
    names = {check["name"] for check in res["checks"]}
    assert {"python", "mcp_sdk"} <= names
    assert res["failures"] == []
    assert isinstance(res["paths"], dict) and res["paths"]
    for check in res["checks"]:
        assert {"name", "ok"} <= set(check)


@pytest.mark.skipif(not Path(SETTINGS.iverilog).exists(), reason="iverilog not installed")
def test_doctor_deep_run_reports_version() -> None:
    res = doctor.run(deep=True)
    assert res["ok"] is True
    checks = {check["name"]: check for check in res["checks"]}
    assert checks["iverilog"]["ok"] is True
    assert "deep_simulate" in checks and checks["deep_simulate"]["ok"] is True


# ---------------------------------------------------------------------------
# check_connectivity
# ---------------------------------------------------------------------------


def test_check_connectivity_flags_dangling_and_unknown_module(work: Path) -> None:
    (work / "bad.v").write_text(
        "module bad(input a, input b, output y);\n"
        "  and2 u1(.a(a), .b(b), .y(y));\n"
        "  and2 u2(.a(a), .b(b), .y());\n"
        "endmodule\n",
        encoding="utf-8",
    )
    res = p1.check_connectivity(files=[_rel(work / "bad.v")], top="bad")
    assert res["ok"] is False
    rules = {issue["rule"] for issue in res["issues"]}
    assert "undriven_net" in rules
    assert "unknown_module" in rules
    assert res["issue_count"] == len(res["issues"]) >= 3
    assert res["top"] == "bad"


def test_check_connectivity_accepts_a_complete_design(work: Path) -> None:
    (work / "and2.v").write_text(
        "module and2(input a, input b, output y);\n  assign y = a & b;\nendmodule\n",
        encoding="utf-8",
    )
    (work / "top.v").write_text(
        "module top(input a, input b, output y);\n"
        "  and2 u1(.a(a), .b(b), .y(y));\n"
        "endmodule\n",
        encoding="utf-8",
    )
    res = p1.check_connectivity(files=[_rel(work / "top.v"), _rel(work / "and2.v")], top="top")
    assert res["ok"] is True, res["issues"]
    assert res["issue_count"] == 0


# ---------------------------------------------------------------------------
# expect_waveform / waveform_stats / render_waveform
# ---------------------------------------------------------------------------


def test_expect_waveform_supports_at_and_window_checks(work: Path) -> None:
    path = _vcd(work)
    res = p1.expect_waveform(
        _rel(path),
        [
            {"signal": "clk", "at": 10, "value": "0"},
            {"signal": "bus", "at": 10, "value": "1011"},
            {"signal": "clk", "from": 5, "to": 9, "value": "1"},
        ],
    )
    assert res["ok"] is True
    assert res["passed"] == 3 and res["failed"] == 0
    assert res["results"][1]["actual"] == "1011"


def test_expect_waveform_reports_mismatch_and_unknown_signal(work: Path) -> None:
    path = _vcd(work)
    res = p1.expect_waveform(
        _rel(path),
        [
            {"signal": "clk", "at": 10, "value": "1"},
            {"signal": "nope", "at": 1, "value": "0"},
        ],
    )
    assert res["ok"] is False
    assert res["failed"] == 2
    assert res["results"][0]["expected"] == "1" and res["results"][0]["actual"] == "0"
    assert "not found" in res["results"][1]["error"]


def test_waveform_stats_counts_toggles_and_levels(work: Path) -> None:
    res = p1.waveform_stats(_rel(_vcd(work)))
    assert res["ok"] is True
    assert res["signal_count"] == 2
    assert res["duration"] == 25
    clock = next(row for row in res["signals"] if row["signal"].endswith("clk"))
    assert clock["changes"] == 5
    assert clock["saw_zero"] is True and clock["saw_one"] is True
    assert res["coverage"]["one_bit_signals"] == 1


@pytest.mark.skipif(
    not __import__("importlib").util.find_spec("matplotlib"), reason="matplotlib not installed"
)
def test_render_waveform_writes_a_real_png(work: Path) -> None:
    out_name = f"cov_render_{uuid.uuid4().hex[:8]}.png"
    out = tools.render_waveform(
        _rel(_vcd(work)), ["clk", "bus"], out_name=out_name, title="coverage"
    )
    target = Path(out["path"])
    try:
        assert out["ok"] is True
        assert target.is_file() and target.stat().st_size > 1000
        assert target.read_bytes()[:8] == PNG_MAGIC
        assert [name.split(".")[-1] for name in out["signals"]] == ["clk", "bus"]
        with pytest.raises(ValueError, match=r"\.png"):
            tools.render_waveform(_rel(_vcd(work)), ["clk"], out_name="nope.txt")
        with pytest.raises(FileExistsError):
            tools.render_waveform(_rel(_vcd(work)), ["clk"], out_name=out_name)
    finally:
        target.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# create_robei_test / import_verilog_canvas
# ---------------------------------------------------------------------------


def test_create_robei_test_writes_registers_and_syncs(project: Path) -> None:
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
    out = tools.create_robei_test(
        proj_path=str(project),
        name="tb_cov",
        code="initial begin #5 $finish; end",
        timescale="1ns/1ns",
    )
    assert out["ok"] is True and out["test"] == "tb_cov"
    test_file = project.parent / "test" / "tb_cov.test"
    assert test_file.is_file()
    assert "tb_cov.test" in project.read_text(encoding="utf-8")
    assert out["verilog_dependencies"]["written"], out["verilog_dependencies"]
    with pytest.raises(FileExistsError):
        tools.create_robei_test(proj_path=str(project), name="tb_cov", code="", sync_deps=False)


def test_import_verilog_canvas_builds_a_canvas_model(project: Path, work: Path) -> None:
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
    source = work / "import_me.v"
    source.write_text(
        "module import_me(input a, input b, output y);\n"
        "  and2 u1(.a(a), .b(b), .y(y));\n"
        "endmodule\n",
        encoding="utf-8",
    )
    out = p1.import_verilog_canvas(proj_path=str(project), verilog_path=_rel(source))
    assert out["ok"] is True
    target = project.parent / "model" / "import_me.model"
    assert target.is_file()
    assert "import_me.model" in project.read_text(encoding="utf-8")
    from robei_mcp.model import model_to_verilog, parse_robei_model

    parsed = parse_robei_model(target)
    assert [instance["instance"] for instance in parsed["instances"]] == ["u1"]
    assert "and2 u1(" in model_to_verilog(parsed)


def test_import_verilog_canvas_rejects_missing_child(project: Path, work: Path) -> None:
    source = work / "orphan.v"
    source.write_text(
        "module orphan(input a, output y);\n  ghost u1(.a(a), .y(y));\nendmodule\n",
        encoding="utf-8",
    )
    with pytest.raises(FileNotFoundError, match="ghost"):
        p1.import_verilog_canvas(proj_path=str(project), verilog_path=_rel(source))


# ---------------------------------------------------------------------------
# const / xdc tool wrappers
# ---------------------------------------------------------------------------


def test_const_and_xdc_tool_wrappers(project: Path) -> None:
    tools.create_robei_model(
        proj_path=str(project),
        name="pins_top",
        ports=[
            {"name": "a", "direction": "input", "width": 1},
            {"name": "y", "direction": "output", "width": 1},
        ],
        code="assign y = a;",
    )
    created = server_app.create_robei_const(
        proj_path=str(project),
        name="pins",
        model="pins_top",
        pins={"a": ["G15"], "y": ["M14"]},
    )
    assert created["ok"] is True
    assert created["pins"] == {"a": ["G15"], "y": ["M14"]}

    read = server_app.read_robei_const(path=created["relative_path"])
    assert read["ok"] is True
    assert read["pins"] == {"a": {"0": "G15"}, "y": {"0": "M14"}}
    assert read["model"] == "pins_top"

    xdc = server_app.export_robei_xdc(proj_path=str(project), const_name="pins")
    assert xdc["ok"] is True
    text = Path(xdc["path"]).read_text(encoding="utf-8")
    assert "set_property PACKAGE_PIN G15 [get_ports a]" in text
    assert "set_property IOSTANDARD LVCMOS33 [get_ports a]" in text
    assert "set_property PACKAGE_PIN M14 [get_ports y]" in text


def test_const_wrapper_surfaces_error_payloads(project: Path) -> None:
    bad = server_app.create_robei_const(
        proj_path=str(project), name="pins2", model="no_such_model", pins={"a": ["G15"]}
    )
    assert bad["ok"] is False
    assert bad["code"] in {"not_found", "invalid_argument"}
    assert bad["hint"]


# ---------------------------------------------------------------------------
# GUI wrappers with a mocked bridge
# ---------------------------------------------------------------------------


def test_gui_wrappers_send_the_expected_ops(monkeypatch: pytest.MonkeyPatch, project: Path) -> None:
    seen: list[tuple[str, dict]] = []

    def fake_call(op, args=None, timeout_s=None):
        seen.append((op, dict(args or {})))
        return {"ok": True, "op": op}

    monkeypatch.setattr(gui, "call", fake_call)
    monkeypatch.setattr(gui, "transport", lambda: {"mode": "test", "session": 1})

    assert gui.status(output_lines=5)["ok"] is True
    assert seen[-1] == ("status", {"output_lines": 5})
    assert gui.status()["bridge"]["mode"] == "test"

    assert gui.launch(wait_s=5)["ok"] is True
    assert seen[-1][0] == "launch" and seen[-1][1]["wait_s"] == 5
    assert seen[-1][1]["exe"].endswith(".exe")

    assert gui.open_project(str(project), timeout_s=30)["ok"] is True
    assert seen[-1] == ("open_project", {"path": str(project), "discard_unsaved": False})
    assert gui.open_project(str(project), discard_unsaved=True)["ok"] is True
    assert seen[-1][1]["discard_unsaved"] is True

    gui.open_model("top", timeout_s=20)
    assert seen[-1] == ("open_model", {"name": "top"})

    gui.compile_model()
    assert seen[-1][0] == "compile" and seen[-1][1]["timeout_s"] == 25

    gui.output(lines=12)
    assert seen[-1] == ("output", {"lines": 12})

    gui.save_project()
    assert seen[-1][0] == "save_project" and seen[-1][1] == {}

    gui.synthesis(timeout_s=60)
    assert seen[-1] == ("synthesis", {"timeout_s": 60})

    gui.close()
    assert seen[-1][0] == "close"

    gui.compile_all(models=["a", "b"])
    assert seen[-1][0] == "compile_all"
    assert seen[-1][1] == {"models": ["a", "b"], "compile_timeout": 45}

    gui.screenshot(label="cov")
    assert seen[-1] == ("screenshot", {"label": "cov"})

    gui.health()
    assert seen[-1] == ("health", {})


def test_gui_tools_are_hidden_when_the_bridge_is_unsupported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gui, "transport", lambda: {"mode": "unsupported", "session": 0})
    real_call = gui.call

    def guarded(op, args=None, timeout_s=None):
        return real_call(op, args, timeout_s)

    monkeypatch.setattr(gui, "call", guarded)
    payload = gui.call("status", {})
    assert payload["ok"] is False
    assert "require Windows" in payload["error"]


# ---------------------------------------------------------------------------
# errors.py mapping table
# ---------------------------------------------------------------------------

MAPPING = [
    (errors.UnsafeXml("dtd"), "unsafe_xml"),
    (errors.PathEscape("outside"), "path_escape"),
    (errors.WidthMismatch("4 vs 1"), "width_mismatch"),
    (errors.NotBehavioral("structural"), "not_behavioral"),
    (errors.UnknownParameter("nope"), "unknown_parameter"),
    (FileNotFoundError("missing"), "not_found"),
    (FileExistsError("exists"), "already_exists"),
    (PermissionError("denied"), "permission_denied"),
    (TimeoutError("slow"), "timeout"),
    (ValueError("bad"), "invalid_argument"),
    (KeyError("bad"), "bad_argument"),
    (TypeError("bad"), "bad_argument"),
    (IndexError("bad"), "bad_argument"),
    (NotImplementedError("nope"), "unsupported"),
    (RuntimeError("boom"), "internal"),
]


@pytest.mark.parametrize(("exc", "code"), MAPPING)
def test_error_codes_and_hints(exc: BaseException, code: str) -> None:
    assert errors.code_for(exc) == code
    payload = errors.payload(exc)
    assert payload["ok"] is False
    assert payload["code"] == code
    assert payload["hint"] == errors.HINTS[code]
    assert payload["error"]


def test_every_producible_code_has_a_hint() -> None:
    producible = {code for _, code in MAPPING}
    assert producible <= set(errors.HINTS)
    # ``cancelled`` is reported by the cancellation path in tools._run, not by
    # code_for(); the hint still has to exist for that payload.
    assert "cancelled" in errors.HINTS
    assert errors.code_for(RuntimeError("x")) == "internal"


def test_error_payload_extra_fields_are_preserved() -> None:
    payload = errors.payload(errors.WidthMismatch("4 vs 1"), model="chain", endpoint="u1.y")
    assert payload["model"] == "chain"
    assert payload["endpoint"] == "u1.y"
    assert payload["code"] == "width_mismatch"

# ---------------------------------------------------------------------------
# owned_pid: only an instance this bridge launched may be closed (1.3 review)
# ---------------------------------------------------------------------------
ROBEI_IMAGE = r"D:\robei-lab\robei\bin\Robei.exe"


def _pid_record(tmp_path: Path, pid: int = 4242, exe: str = ROBEI_IMAGE) -> Path:
    record = tmp_path / "robei_pid.json"
    record.write_text(json.dumps({"pid": pid, "exe": exe}), encoding="utf-8")
    return record


def test_owned_pid_rejects_a_process_that_is_not_robei(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from robei_mcp.gui_agent import ui

    record = _pid_record(tmp_path)
    monkeypatch.setattr(ui, "PID_FILE", record)
    monkeypatch.setattr(ui, "_exe_path", lambda pid: r"C:\Windows\System32\notepad.exe")
    assert ui.owned_pid() is None
    assert not record.exists(), "the stale record must be dropped"
    result = ui.close()
    assert result["ok"] is True
    assert result["closed"] is False


def test_owned_pid_rejects_a_recycled_pid_from_another_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from robei_mcp.gui_agent import ui

    record = _pid_record(tmp_path)
    monkeypatch.setattr(ui, "PID_FILE", record)
    monkeypatch.setattr(ui, "_exe_path", lambda pid: r"C:\Temp\robei\bin\Robei.exe")
    assert ui.owned_pid() is None
    assert not record.exists()


def test_owned_pid_accepts_the_instance_it_launched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from robei_mcp.gui_agent import ui

    record = _pid_record(tmp_path)
    monkeypatch.setattr(ui, "PID_FILE", record)
    monkeypatch.setattr(ui, "_exe_path", lambda pid: ROBEI_IMAGE)
    assert ui.owned_pid() == 4242
    assert record.exists()


def test_owned_pid_rejects_a_dead_pid(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from robei_mcp.gui_agent import ui

    record = _pid_record(tmp_path, pid=999999)
    monkeypatch.setattr(ui, "PID_FILE", record)
    monkeypatch.setattr(ui, "_exe_path", lambda pid: "")
    assert ui.owned_pid() is None
    assert not record.exists()
