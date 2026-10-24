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


def _light_project() -> str:
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
        pins=LIGHT_PINS,
        device="xc7a50tfgg484",
        family="artix7",
    )
    return proj


@pytest.fixture()
def light_project():
    proj = _light_project()
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
    assert set(payload["tools"]) == {"ropllace", "fasm2frames", "xc7frames2bit"}
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
