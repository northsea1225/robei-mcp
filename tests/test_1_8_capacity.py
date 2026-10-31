from __future__ import annotations

from pathlib import Path

import pytest

from robei_mcp import errors, fpga


BUILDABLE = {
    "xc7a50tfgg484-1",
    "xc7a100tfgg484-1",
    "xc7a200tfbg484-1",
    "xc7z020clg400-1",
    "xc7z020clg484-1",
}


def test_capacity_catalog_has_only_five_buildable_parts() -> None:
    rows = fpga._parse_manual_capacity()
    assert set(rows) == BUILDABLE
    assert all(rows[name]["confidence"] == "manual_datasheet" for name in rows)


def test_manual_source_has_provenance_and_sha() -> None:
    row = next(item for item in fpga.device_table() if item["buildable"])
    capacity, source, warnings = fpga._manual_capacity_for_part(row["part"])
    assert capacity is not None
    assert source["kind"] == "manual_datasheet"
    assert source["paths"] and source["sha256"]
    assert source["confidence"] == "manual_datasheet"
    assert not [item for item in warnings if "null" not in item]


def test_manual_capacity_keeps_bram_views_separate() -> None:
    capacity, _, _ = fpga._manual_capacity_for_part("xc7a50tfgg484-1")
    assert capacity["BRAM18"] == 150
    assert capacity["BRAM36"] == 75
    assert capacity["BRAM18"] != capacity["BRAM36"]


def test_io_percent_uses_package_user_capacity() -> None:
    capacity, _, _ = fpga._manual_capacity_for_part("xc7z020clg400-1")
    percent = fpga._utilization_percent({"IO": 10, "LUT": 100}, capacity)
    assert percent["IO"] == 8.0
    assert percent["LUT"] == round(100 * 100 / capacity["LUT"], 3)


def test_list_fpga_devices_exposes_capacity_source() -> None:
    result = fpga.list_fpga_devices()
    buildable = {row["part"]: row for row in result["devices"] if row["buildable"]}
    assert set(buildable) == BUILDABLE
    for row in buildable.values():
        assert row["capacity_source"]["kind"] == "manual_datasheet"
        assert row["capacity_source"]["sha256"]
        assert row["capacity"]["IO_PACKAGE_USER"] is not None


def test_missing_manual_part_returns_null_capacity_and_derived_or_none() -> None:
    capacity, source, warnings = fpga._capacity_for_device({"part": "unknown", "part_yaml": None, "family": None})
    assert capacity is None
    assert source is None
    assert warnings


def test_unknown_part_still_has_device_unsupported() -> None:
    with pytest.raises(fpga.DeviceUnsupported) as excinfo:
        fpga._device("not-a-real-part")
    assert errors.code_for(excinfo.value) == "device_unsupported"
    assert excinfo.value.available


def test_part_yaml_explicit_value_has_priority(tmp_path: Path, monkeypatch) -> None:
    part = tmp_path / "part.yaml"
    part.write_text("LUT: 10\nFF: 20\nIO: 30\n", encoding="utf-8")
    device = {"part": "fixture", "part_yaml": str(part), "family": "artix7"}
    capacity, source, _ = fpga._capacity_for_device(device)
    assert capacity == {"LUT": 10, "FF": 20, "IO": 30, "IO_PACKAGE_USER": 30}
    assert source["kind"] == "part_yaml"
