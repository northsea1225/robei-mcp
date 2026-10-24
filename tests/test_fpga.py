"""FPGA backend (1.2.0): device table, yosys synthesis, Roplace -> .bit.

Every test that needs an external piece skips with the reason spelled out:
the Robei chip database, yosys, or the three Robei FPGA binaries.
"""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest

from robei_mcp import constraints, fpga, tools
from robei_mcp.config import SETTINGS
from robei_mcp import errors
from robei_mcp.errors import DeviceUnsupported, code_for

DB_ROOT = fpga.db_root()
DB_MISSING = not (DB_ROOT / "chipdb").is_dir()
YOSYS_MISSING = not Path(SETTINGS.yosys).is_file()
FPGA_BINARIES = fpga.tool_paths()
BINARIES_MISSING = [name for name, path in FPGA_BINARIES.items() if not Path(path).is_file()]

needs_db = pytest.mark.skipif(
    DB_MISSING, reason=f"Robei chip database not installed under {DB_ROOT}"
)
needs_yosys = pytest.mark.skipif(
    YOSYS_MISSING, reason=f"yosys not found at {SETTINGS.yosys}"
)
needs_fpga_binaries = pytest.mark.skipif(
    bool(BINARIES_MISSING),
    reason=f"Robei FPGA binaries missing next to Robei: {BINARIES_MISSING} ({fpga._bin_dir()})",
)

LIGHT_PINS = {
    "swt": ["A1", "A14", "A15", "A16"],
    "led": ["A20", "A19", "A13", "A18"],
}


def _light_project(pins: dict[str, list[str]] | None = None) -> str:
    name = f"pytest_fpga_{uuid.uuid4().hex[:8]}"
    created = tools.create_robei_project(name)
    proj = created["proj"]
    tools.create_robei_model(
        proj,
        "light",
        ports=[
            {"name": "swt", "direction": "input", "width": 4},
            {"name": "led", "direction": "output", "width": 4},
        ],
        code="assign led = swt + 4'd1;",
    )
    constraints.create_const(
        proj,
        name="light_constrain",
        model="light",
        pins=pins or LIGHT_PINS,
        device="xc7a50tfgg484",
        family="artix7",
    )
    return proj


@pytest.fixture()
def light_project():
    proj = _light_project()
    yield proj
    shutil.rmtree(Path(proj).parent, ignore_errors=True)


@pytest.fixture()
def bad_pin_project():
    """Same design, but one ball xc7a50tfgg484 does not have.

    ``ZZ9`` is not a valid fgg484 ball; ``Y18`` *is* one (a 1.2.2 probe showed the
    route stage accepts it), so it cannot be used to force a route failure.
    """
    proj = _light_project(pins={"swt": ["ZZ9", "A14", "A15", "A16"], "led": list(LIGHT_PINS["led"])})
    yield proj
    shutil.rmtree(Path(proj).parent, ignore_errors=True)


@needs_db
def test_device_table_pairs_chipdb_with_part_yaml() -> None:
    rows = fpga.device_table()
    assert rows, "the installed database must list at least one part"
    for row in rows:
        assert row["part"] and row["family"]
        if row["buildable"]:
            assert Path(row["chipdb"]).is_file()
            assert Path(row["part_yaml"]).is_file()
        else:
            assert row["reason"], f"{row['part']} is not buildable but carries no reason"


@needs_db
def test_list_fpga_devices_reports_db_and_tools() -> None:
    payload = fpga.list_fpga_devices()
    assert payload["db_root_exists"] is True
    assert payload["devices"]
    assert set(payload["tools"]) == {"roplace", "fasm2frames", "xc7frames2bit"}
    for entry in payload["tools"].values():
        assert set(entry) == {"path", "exists"}


@needs_db
def test_missing_chipdb_device_is_not_buildable() -> None:
    rows = {row["part"]: row for row in fpga.device_table()}
    if "xc7a100tcsg324-1" not in rows:
        pytest.skip("xc7a100tcsg324-1 part.yaml is not installed on this machine")
    row = rows["xc7a100tcsg324-1"]
    assert row["buildable"] is False
    assert "chipdb" in (row["reason"] or "")
    assert row["part_yaml"]


@needs_db
def test_device_lookup_exposes_the_missing_chipdb() -> None:
    row = fpga._device("xc7a100tcsg324-1")
    assert row["buildable"] is False
    assert row["chipdb"] is None
    assert code_for(DeviceUnsupported("x")) == "device_unsupported"


def test_unknown_part_is_rejected() -> None:
    with pytest.raises(DeviceUnsupported) as excinfo:
        fpga._device("not-a-part-9")
    assert code_for(excinfo.value) == "device_unsupported"


@needs_yosys
def test_synthesize_reports_utilization_and_writes_json(light_project: str) -> None:
    result = fpga.synthesize_project(proj_path=light_project, part="xc7a50tfgg484-1")
    assert result["ok"] is True
    assert result["top"] == "light"
    assert Path(result["json_path"]).is_file()
    assert result["utilization"]["LUT"] >= 1
    assert result["utilization"]["CARRY"] >= 1  # 4-bit add: LUTs feed the carry chain
    assert result["utilization"]["IO"] == 8
    assert "synth_xilinx -flatten -arch xc7 -abc9 -nodsp" in result["yosys_script"]
    assert result["json_sha256"]
    assert Path(result["summary_path"]).is_file()


@needs_yosys
def test_synthesize_accepts_explicit_top(light_project: str) -> None:
    result = fpga.synthesize_project(proj_path=light_project, top="light", timeout_s=180)
    assert result["top"] == "light"
    assert result["ok"] is True


# ---------------------------------------------------------------------------
# M2: route + package
# ---------------------------------------------------------------------------
def _drop_pin(const_path: Path, pin: str) -> None:
    """Remove one package pin (Port + Wire) so the const no longer covers a port bit."""
    lines = const_path.read_text(encoding="utf-8").splitlines()
    kept = []
    for line in lines:
        if f'Name="{pin}"' in line and "<Port" in line:
            continue
        # Robei escapes '>' as &gt; inside Wire endpoints, so match the pin loosely.
        if pin in line and "<Wire" in line:
            continue
        kept.append(line)
    const_path.write_text("\n".join(kept) + "\n", encoding="utf-8", newline="\n")


@needs_yosys
@needs_db
@needs_fpga_binaries
def test_build_bitstream_produces_bit_with_all_stages(light_project: str) -> None:
    result = fpga.build_bitstream(proj_path=light_project, part="xc7a50tfgg484-1")
    assert result["ok"] is True
    assert [stage["stage"] for stage in result["stages"]] == [
        "synth",
        "route",
        "package.frames",
        "package.bit",
    ]
    bit = result["artifacts"]["bit"]
    assert bit and Path(bit["path"]).is_file()
    assert bit["size_bytes"] == Path(bit["path"]).stat().st_size
    assert len(bit["sha256"]) == 64
    assert Path(result["artifacts"]["fasm"]["path"]).is_file()
    assert Path(result["artifacts"]["frame"]["path"]).is_file()
    assert result["timing_note"]
    # 1.2.1: the default request must fit ROBEI_MAX_TIMEOUT_S, so nothing is clamped.
    assert result["timeout"]["requested_s"] == 120
    assert result["timeout"]["clamped"] is False
    assert not [w for w in result["warnings"] if "clamp" in w.lower()]


@needs_yosys
@needs_db
@needs_fpga_binaries
def test_build_bitstream_steps_subset_stops_after_synth(light_project: str) -> None:
    result = fpga.build_bitstream(proj_path=light_project, part="xc7a50tfgg484-1", steps=["synth"])
    assert [stage["stage"] for stage in result["stages"]] == ["synth"]
    assert result["ok"] is False
    assert result["artifacts"]["bit"] is None


@needs_db
def test_build_bitstream_rejects_part_without_chipdb(light_project: str) -> None:
    with pytest.raises(DeviceUnsupported) as excinfo:
        fpga.build_bitstream(proj_path=light_project, part="xc7a100tcsg324-1")
    assert code_for(excinfo.value) == "device_unsupported"
    assert "chipdb" in str(excinfo.value)


@needs_db
def test_build_bitstream_reports_missing_pins(light_project: str) -> None:
    from robei_mcp.errors import UnconstrainedIO

    const = Path(light_project).parent / "const" / "light_constrain.const"
    _drop_pin(const, "A18")
    with pytest.raises(UnconstrainedIO) as excinfo:
        fpga.build_bitstream(proj_path=light_project, part="xc7a50tfgg484-1")
    assert code_for(excinfo.value) == "unconstrained_io"
    assert excinfo.value.missing == [
        {"port": "led", "direction": "output", "missing_bits": [3]}
    ]


@needs_db
def test_build_bitstream_reports_missing_binaries(light_project: str, monkeypatch) -> None:
    from robei_mcp.errors import FpgaStepFailed

    monkeypatch.setattr(
        fpga, "tool_paths", lambda: {name: Path(f"missing/{name}.exe") for name in ("roplace", "fasm2frames", "xc7frames2bit")}
    )
    with pytest.raises(FpgaStepFailed) as excinfo:
        fpga.build_bitstream(proj_path=light_project, part="xc7a50tfgg484-1")
    assert code_for(excinfo.value) == "fpga_step_failed"
    assert excinfo.value.stage == "synth"


def test_normalize_steps_rejects_unknown_stage() -> None:
    with pytest.raises(ValueError):
        fpga._normalize_steps(["synth", "gdsii"])


def test_timeout_budget_clamps_above_the_cap() -> None:
    from robei_mcp.config import SETTINGS

    budget = fpga._timeout_budget(900)
    assert budget["requested_s"] == 900
    assert budget["effective_s"] == SETTINGS.max_timeout_s
    assert budget["clamped"] is True
    inside = fpga._timeout_budget(30)
    assert inside["clamped"] is False


@needs_yosys
@needs_db
@needs_fpga_binaries
def test_bit_payload_sha256_survives_header_noise(light_project: str) -> None:
    """The header carries the .frame path and the time, the payload must not change."""
    first = fpga.build_bitstream(proj_path=light_project, part="xc7a50tfgg484-1")
    second = fpga.build_bitstream(proj_path=light_project, part="xc7a50tfgg484-1")
    assert first["bit_payload_sha256"] and len(first["bit_payload_sha256"]) == 64
    assert first["bit_payload_sha256"] == second["bit_payload_sha256"]
    assert first["artifacts"]["bit"]["payload_sha256"] == first["bit_payload_sha256"]
    assert first["bit_payload_offset"] >= 0
    assert first["artifacts"]["fasm"] and second["artifacts"]["fasm"]


@needs_yosys
@needs_db
@needs_fpga_binaries
def test_route_failure_reports_code_and_stage(bad_pin_project: str) -> None:
    """1.2.2 P0: a failing stage must be fpga_step_failed with argv/rc/tail/run_dir."""
    from robei_mcp.errors import FpgaStepFailed

    with pytest.raises(FpgaStepFailed) as excinfo:
        fpga.build_bitstream(proj_path=bad_pin_project, part="xc7a50tfgg484-1")
    exc = excinfo.value
    assert code_for(exc) == "fpga_step_failed"
    assert exc.stage == "route", exc.record.get("log_tail")
    payload = errors.payload(exc)
    assert payload["code"] == "fpga_step_failed"
    assert payload["stage"] == "route"
    assert payload["returncode"] not in (None, 0)
    assert payload["argv"] and any("Roplace" in part or "roplace" in part.lower() for part in payload["argv"])
    assert payload["log_tail"]
    assert payload["log_path"] and Path(payload["log_path"]).is_file()
    assert payload["run_dir"] and Path(payload["run_dir"]).is_dir()
    assert "read_run_log" in (payload["hint"] or "")


@needs_yosys
@needs_db
@needs_fpga_binaries
def test_route_regenerates_a_missing_xdc(light_project: str) -> None:
    build = Path(light_project).parent / "build"
    for stale in build.glob("light_constrain.xdc"):
        stale.unlink()
    result = fpga.build_bitstream(proj_path=light_project, part="xc7a50tfgg484-1", steps=["synth", "route"])
    assert result["xdc_generated"] is True
    assert Path(result["xdc"]).is_file()
    assert Path(result["artifacts"]["fasm"]["path"]).is_file()


@needs_db
def test_missing_const_is_invalid_argument(light_project: str) -> None:
    with pytest.raises(ValueError) as excinfo:
        fpga.build_bitstream(proj_path=light_project, part="xc7a50tfgg484-1", const_name="nope")
    assert code_for(excinfo.value) == "invalid_argument"
    assert "create_robei_const" in str(excinfo.value)
