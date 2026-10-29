from __future__ import annotations

from pathlib import Path

import pytest

from robei_mcp import errors, fpga


def test_unknown_part_has_device_unsupported_and_available_parts() -> None:
    with pytest.raises(fpga.DeviceUnsupported) as exc:
        fpga._device("not-a-real-part")
    assert errors.code_for(exc.value) == "device_unsupported"
    assert exc.value.available


def test_capacity_missing_is_null_with_warning(tmp_path: Path) -> None:
    capacity, source, warnings = fpga._capacity_from_part_yaml(tmp_path / "missing.yaml")
    assert capacity is None
    assert source is None
    assert warnings
    percent = fpga._utilization_percent({"LUT": 10, "FF": 3}, capacity)
    assert percent == {"LUT": None, "FF": None}


def test_capacity_and_percent_fixture(tmp_path: Path) -> None:
    part = tmp_path / "part.yaml"
    part.write_text("resources:\n  LUT: 100\n  FF: 200\n  BRAM18: 10\n", encoding="utf-8")
    capacity, source, warnings = fpga._capacity_from_part_yaml(part)
    assert capacity == {"LUT": 100, "FF": 200, "BRAM18": 10}
    assert source["path"] == str(part)
    assert warnings == []
    used = {"LUT": 25, "FF": 50, "BRAM18": 1, "DSP": 0}
    assert fpga._utilization_percent(used, capacity) == {
        "LUT": 25.0,
        "FF": 25.0,
        "BRAM18": 10.0,
        "DSP": None,
    }
