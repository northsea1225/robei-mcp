"""Pin constraints: write Robei .const canvases and the Xilinx .xdc Robei derives from them.

Schema comes from a published Robei project (github.com/wwkkww1983/uart,
prac_04_led/light_constrain.const) plus its generated light_constrain.xdc:

    <Module Type="constrain" Name="light_constrain" File="Current/light_constrain.const">
      <Module Type="model" Class="light" File="Current/light.model" Name="light1"> <Port .../> </Module>
      <Port Name="G15" Inout="input" Datasize="1" .../>        <!-- one port per FPGA pin -->
      <Wire Name="0" From="light_constrain>G15" To="light_constrain#light1>swt" Datasize="1"/>
    </Module>

The wire ``Name`` is the bit index inside the (possibly wider) instance port; the
pin port itself is always one bit. The .xdc is a straight projection of that map.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .config import SETTINGS
from .model import _attr_ci, parse_endpoint, parse_robei_model
from .security import is_within, resolve_project_path

PIN_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,15}$")
MODEL_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _attr(value: Any) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("\n", "&#xa;")
    )


def _port_xml(name: str, direction: str, width: int, x: float, y: float, parent: str,
              datatype: str = "wire", function: str = "") -> str:
    color = "#0000ff" if direction == "input" else "#b8860b"
    side = "left" if direction == "input" else "right"
    return (
        f'  <Port Height="20" Side="{side}" Inout="{direction}" Width="20" X="{x:.6f}" Function="{_attr(function)}" '
        f'Parent="{_attr(parent)}" Name="{_attr(name)}" Color="{color}" Y="{y:.6f}" '
        f'Datatype="{datatype}" Datasize="{width}"/>'
    )


def create_const(
    proj_path: str,
    name: str,
    model: str,
    pins: dict[str, list[str]],
    overwrite: bool = False,
    kind: str = "const",
    comment: str = "",
    vendor: str = "AMD",
    device: str = "",
    family: str = "",
    iostandard: str = "LVCMOS33",
) -> dict[str, Any]:
    """Write a pin-constraint canvas: ``pins = {"swt": ["G15","P15","W13","T16"], "led": [...]}``."""
    proj = resolve_project_path(proj_path)
    if not is_within(proj, SETTINGS.workspace):
        raise ValueError("refusing to modify a project outside the workspace")
    if not MODEL_NAME_RE.match(name):
        raise ValueError(f"invalid constraint name: {name!r}")
    if kind not in {"constrain", "const"}:
        raise ValueError("kind must be 'constrain' or 'const'")

    model_stem = model[:-6] if model.lower().endswith(".model") else model
    model_file = proj.parent / "model" / f"{model_stem}.model"
    if not model_file.is_file():
        available = sorted(p.stem for p in (proj.parent / "model").glob("*.model"))
        raise FileNotFoundError(f"model {model_stem!r} not found (available: {available})")
    parsed_model = parse_robei_model(model_file)
    ports = parsed_model["ports"]
    port_names = [port["name"] for port in ports]
    unknown = sorted(set(pins) - set(port_names))
    if unknown:
        raise ValueError(f"unknown port(s) {unknown}; model has {port_names}")
    missing = [port["name"] for port in ports if not pins.get(port["name"])]
    if missing:
        raise ValueError(f"every port needs pins; missing {missing}")

    for port in ports:
        want = int(port["datasize"] or 1)
        got = len(pins[port["name"]])
        if got != want:
            raise ValueError(f"port {port['name']} is {want} bit(s) but {got} pin(s) were given")
        for pin in pins[port["name"]]:
            if not PIN_RE.match(str(pin)):
                raise ValueError(f"invalid pin name: {pin!r}")

    used: dict[str, str] = {}
    duplicates: list[str] = []
    for port in ports:
        for index, pin in enumerate(pins[port["name"]]):
            key = str(pin).upper()
            label = f"{port['name']}[{index}]"
            if key in used:
                duplicates.append(f"{pin} is assigned to both {used[key]} and {label}")
            else:
                used[key] = label
    if duplicates:
        raise ValueError("duplicate FPGA pin assignment: " + "; ".join(duplicates))

    instance = f"{name}_inst"
    lines = ["<!DOCTYPE Robei>"]
    board = f' Vendor="{_attr(vendor)}" Device="{_attr(device)}" Family="{_attr(family)}"' if kind == "const" else ""
    lines.append(
        f'<Module Height="547" Type="{kind}"{board} Width="1185" Class="module" Comment="{_attr(comment)}" '
        f'Code="&#xa;&#xa;&#xa;" X="95.8814" Parent="0" Name="{_attr(name)}" Include="" '
        f'Color="#d3d3d3" Y="69.1928" File="Current/{_attr(name)}.const" Parameters="">'
    )
    lines.append(
        f' <Module Height="304" Type="model" Width="374" Class="{_attr(model_stem)}" Comment="" '
        f'Code="{_attr(parsed_model.get("code") or "")}" X="356.657" Parent="{_attr(name)}" '
        f'Name="{_attr(instance)}" Include="" Color="#d3d3d3" Y="107.967" '
        f'File="Current/{_attr(model_stem)}.model" Parameters="">'
    )
    for index, port in enumerate(ports):
        y = 0.15 + index * 0.18
        x = -0.017 if port["inout"] == "input" else 0.983
        lines.append(
            _port_xml(port["name"], port["inout"] or "input", int(port["datasize"] or 1), x, y, instance,
                      port["datatype"] or "wire")
        )
    lines.append(" </Module>")

    slot = 0
    for port in ports:
        for pin in pins[port["name"]]:
            y = 0.148 + slot * 0.166
            slot += 1
            x = -0.017 if port["inout"] == "input" else 0.983
            lines.append(_port_xml(str(pin), port["inout"] or "input", 1, x, y, name, function=iostandard))

    for port in ports:
        width = int(port["datasize"] or 1)
        direction = port["inout"] or "input"
        for index, pin in enumerate(pins[port["name"]]):
            if direction == "input":
                frm, to = f"{name}>{pin}", f"{name}#{instance}>{port['name']}"
            else:
                frm, to = f"{name}#{instance}>{port['name']}", f"{name}>{pin}"
            lines.append(
                f' <Wire Parent="{_attr(name)}" To="{_attr(to)}" Name="{index}" From="{_attr(frm)}" '
                f'Datatype="wire" Datasize="{width}"/>'
            )
    lines.append("</Module>")
    text = "\n".join(lines) + "\n"

    from xml.etree import ElementTree as ET

    ET.fromstring(text)

    target_dir = proj.parent / "const"
    target_dir.mkdir(parents=True, exist_ok=True)
    # Build > Synthesis writes build/<name>.json; without the folder yosys fails at the
    # very last step ("Can't open output file .../build/<name>.json").
    (proj.parent / "build").mkdir(exist_ok=True)
    target = target_dir / f"{name}.const"
    if target.exists() and not overwrite:
        raise FileExistsError(f"constraint already exists: {target}")
    backup = None
    if target.exists():
        from . import tools as _tools

        backup = _tools._backup_file(target)
    target.write_text(text, encoding="utf-8", newline="\n")

    from . import tools

    registration = tools._register_entry_in_project(proj, "const", name, "const", "const")
    return {
        "ok": True,
        "name": name,
        "kind": kind,
        "path": str(target),
        "relative_path": tools._rel(target),
        "model": model_stem,
        "pins": pins,
        "board": {"vendor": vendor, "device": device, "family": family, "iostandard": iostandard},
        "registered_in_project": registration in ("created", "exists"),
        "registration": registration,
        "backup": str(backup) if backup else None,
        "sha256": tools._sha256(target),
    }


def pin_map(const_path: Path) -> dict[str, Any]:
    """Parse a .const into {port: {bit: pin}} plus the instance model name."""
    parsed = parse_robei_model(const_path)
    instances = parsed.get("instances") or []
    instance = instances[0] if instances else None
    mapping: dict[str, dict[int, str]] = {}
    for wire in parsed.get("wires") or []:
        frm = wire.get("from") or {}
        to = wire.get("to") or {}
        if frm.get("instance") is None:
            pin, port = frm.get("port"), to.get("port")
        elif to.get("instance") is None:
            pin, port = to.get("port"), frm.get("port")
        else:
            continue
        if not pin or not port:
            continue
        try:
            bit = int(str(wire.get("net") or "0"))
        except ValueError:
            bit = 0
        mapping.setdefault(str(port), {})[bit] = str(pin)
    return {
        "name": parsed.get("name"),
        "kind": parsed.get("type"),
        "instance": instance["instance"] if instance else None,
        "model": (instance["class"] if instance else None),
        "pins": mapping,
        "port_count": len(mapping),
        "bit_count": sum(len(bits) for bits in mapping.values()),
    }


def export_xdc(
    proj_path: str,
    const_name: str | None = None,
    out_name: str | None = None,
    iostandard: str = "LVCMOS33",
    also_build: bool = False,
    quote_bus: bool = True,
) -> dict[str, Any]:
    """Project a .const into ``<proj>/constrain/<name>.xdc`` (same text Robei generates).

    ``also_build=True`` additionally writes ``<proj>/build/<name>.xdc`` - the copy
    Robei's Build > Route parses, and Robei's own version leaves IOSTANDARD empty.

    ``quote_bus=True`` (default) emits standard Tcl, ``[get_ports {swt[0]}]``.
    Robei's legacy writer emits ``[get_ports swt[0]]``, which a Tcl interpreter
    reads as a nested ``[0]`` command; pass ``quote_bus=False`` only when an old
    Robei build needs that exact spelling.
    """
    proj = resolve_project_path(proj_path)
    if not is_within(proj, SETTINGS.workspace):
        raise ValueError("refusing to write a project outside the workspace")
    if const_name is None:
        candidates = sorted((proj.parent / "const").glob("*.const"))
        if not candidates:
            raise FileNotFoundError("project has no const/*.const file")
        const_path = candidates[0]
    else:
        stem = const_name[:-6] if const_name.lower().endswith(".const") else const_name
        const_path = proj.parent / "const" / f"{stem}.const"
    if not const_path.is_file():
        raise FileNotFoundError(f"constraint not found: {const_path}")

    info = pin_map(const_path)
    if not info["pins"]:
        raise ValueError("constraint has no pin-to-port wires")
    stem = out_name[:-4] if out_name and out_name.lower().endswith(".xdc") else (out_name or const_path.stem)
    out_dir = proj.parent / "constrain"
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{stem}.xdc"

    lines = ["#This file is generated by Robei!", "#Pin Assignment for Xilinx FPGA with Software Vivado."]
    for port in info["pins"]:  # declaration order, exactly like Robei emits it
        bits = info["pins"][port]
        for bit in sorted(bits):
            suffix = "" if len(bits) == 1 else f"[{bit}]"
            target_port = f"{port}{suffix}"
            if quote_bus and suffix:
                target_port = "{" + target_port + "}"
            lines.append(f"set_property PACKAGE_PIN {bits[bit]} [get_ports {target_port}]")
            lines.append(f"set_property IOSTANDARD {iostandard} [get_ports {target_port}]")
    text = "\n".join(lines) + "\n"
    target.write_text(text, encoding="utf-8", newline="\n")
    build_copy = None
    if also_build:
        build_dir = proj.parent / "build"
        build_dir.mkdir(exist_ok=True)
        build_copy = build_dir / f"{stem}.xdc"
        build_copy.write_text(text, encoding="utf-8", newline="\n")

    return {
        "ok": True,
        "path": str(target),
        "relative_path": target.relative_to(SETTINGS.workspace).as_posix(),
        "build_copy": str(build_copy) if build_copy else None,
        "const": str(const_path),
        "constraints": info,
        "line_count": len(lines),
        "tcl_quoting": "standard" if quote_bus else "robei",
        "sha256": __import__("hashlib").sha256(target.read_bytes()).hexdigest(),
    }


def read_const(path: str) -> dict[str, Any]:
    """Read a .const file: kind, instance model, port->bit->pin map, ports and wires."""
    from .security import resolve_workspace_path

    const_path = resolve_workspace_path(path)
    if const_path.suffix.lower() != ".const":
        raise ValueError(f"not a .const file: {const_path}")
    parsed = parse_robei_model(const_path)
    info = pin_map(const_path)
    return {
        "ok": True,
        "path": str(const_path),
        "sha256": __import__("hashlib").sha256(const_path.read_bytes()).hexdigest(),
        "kind": info["kind"],
        "instance": info["instance"],
        "model": info["model"],
        "pins": {name: {str(bit): pin for bit, pin in sorted(bits.items())} for name, bits in info["pins"].items()},
        "bit_count": info["bit_count"],
        "ports": parsed["ports"],
        "wires": parsed["wires"],
    }
