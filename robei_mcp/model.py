from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

MAX_TREE_DEPTH = 16
MAX_TREE_NODES = 5000

_PARAM_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_$]*)\s*=\s*(.+?)\s*$", re.DOTALL)


def _attr_ci(element: ET.Element, name: str) -> str | None:
    target = name.lower()
    for key, value in element.attrib.items():
        if key.lower() == target:
            return value
    return None


def generic_node(element: ET.Element, depth: int = 0, budget: list[int] | None = None) -> dict[str, Any]:
    """Return an unmodified, structure-only view of an XML node.

    Unknown Robei nodes are preserved verbatim (tag / attributes / text /
    children) instead of being interpreted. Structure is never guessed.
    """
    if budget is None:
        budget = [MAX_TREE_NODES]
    budget[0] -= 1
    node: dict[str, Any] = {
        "tag": element.tag,
        "attributes": dict(element.attrib),
        "text": (element.text or "").strip(),
        "children": [],
    }
    if depth >= MAX_TREE_DEPTH or budget[0] <= 0:
        node["truncated"] = True
        return node
    for child in element:
        node["children"].append(generic_node(child, depth + 1, budget))
        if budget[0] <= 0:
            node["truncated"] = True
            break
    return node


def parse_endpoint(text: str | None) -> dict[str, Any] | None:
    """Split a Robei wire endpoint: ``top#u1>a`` -> scope/instance/port.

    Canvas models reference a port as ``<owner>#<instance>><port>`` for an
    instance and ``<owner>><port>`` for a top-level port.
    """
    raw = (text or "").strip()
    if not raw:
        return None
    owner, _, port = raw.partition(">")
    owner = owner.strip()
    port = port.strip() or None
    scope, _, instance = owner.partition("#")
    return {
        "raw": raw,
        "scope": scope.strip() or None,
        "instance": instance.strip() or None,
        "port": port,
    }


def _parse_port(element: ET.Element) -> dict[str, Any]:
    return {
        "name": _attr_ci(element, "Name") or "",
        "inout": _attr_ci(element, "Inout") or "",
        "datatype": _attr_ci(element, "Datatype") or "",
        "datasize": _attr_ci(element, "Datasize") or "",
        "connect": _attr_ci(element, "Connect") or "",
        "side": _attr_ci(element, "Side") or "",
        "parent": _attr_ci(element, "Parent") or "",
        "description": _attr_ci(element, "Description") or "",
    }


def _parse_instance(element: ET.Element) -> dict[str, Any]:
    """A nested ``<Module Type="model">`` is one placed instance on the canvas."""
    ports = [_parse_port(child) for child in element if child.tag.lower() == "port"]
    return {
        "instance": _attr_ci(element, "Name") or "",
        "model_file": _attr_ci(element, "File") or "",
        "class": _attr_ci(element, "Class") or "",
        "x": _attr_ci(element, "X"),
        "y": _attr_ci(element, "Y"),
        "width": _attr_ci(element, "Width"),
        "height": _attr_ci(element, "Height"),
        "code": _attr_ci(element, "Code") or "",
        "parameters": _attr_ci(element, "Parameters") or "",
        "ports": ports,
    }


def _parse_wire(element: ET.Element) -> dict[str, Any]:
    frm = parse_endpoint(_attr_ci(element, "From"))
    to = parse_endpoint(_attr_ci(element, "To"))
    return {
        "net": _attr_ci(element, "Name") or "",
        "from": frm,
        "to": to,
        "datatype": _attr_ci(element, "Datatype") or "",
        "datasize": _attr_ci(element, "Datasize") or "",
        "parent": _attr_ci(element, "Parent") or "",
    }


def parse_robei_model(path: Path) -> dict[str, Any]:
    tree = ET.parse(path)
    root = tree.getroot()
    ports: list[dict[str, Any]] = []
    instances: list[dict[str, Any]] = []
    wires: list[dict[str, Any]] = []
    other_children: list[dict[str, Any]] = []
    for child in root:
        tag = child.tag.lower()
        if tag == "port":
            ports.append(_parse_port(child))
            continue
        if tag == "module":
            instances.append(_parse_instance(child))
            continue
        if tag == "wire":
            wires.append(_parse_wire(child))
            continue
        other_children.append(generic_node(child))

    parameters = _attr_ci(root, "Parameters") or ""
    code = _attr_ci(root, "Code") or ""
    return {
        "ok": True,
        "path": str(path),
        "root_tag": root.tag,
        "name": _attr_ci(root, "Name") or root.tag,
        "type": _attr_ci(root, "Type") or "",
        "timescale": _attr_ci(root, "Timescale") or "",
        "vendor": _attr_ci(root, "Vendor") or "",
        "device": _attr_ci(root, "Device") or "",
        "family": _attr_ci(root, "Family") or "",
        "class": _attr_ci(root, "Class") or "",
        "category": _attr_ci(root, "Category") or "",
        "comment": _attr_ci(root, "Comment") or "",
        "parameters": parameters,
        "parameter_lines": parameters.splitlines(),
        "parameter_list": parse_parameter_text(parameters),
        "ports": ports,
        "instances": instances,
        "wires": wires,
        "is_canvas": bool(instances or wires),
        "netlist": build_netlist(ports, instances, wires),
        "code": code,
        "code_lines": code.splitlines(),
        "other_children": other_children,
        "size_bytes": path.stat().st_size,
    }


def build_netlist(
    ports: list[dict[str, Any]],
    instances: list[dict[str, Any]],
    wires: list[dict[str, Any]],
) -> dict[str, Any]:
    """Turn ``<Port Connect>`` + ``<Wire From/To>`` into a readable net map."""
    nets: dict[str, list[dict[str, Any]]] = {}

    def add(net: str, endpoint: dict[str, Any] | None) -> None:
        if not net or not endpoint or not endpoint.get("port"):
            return
        nets.setdefault(net, []).append(
            {
                "instance": endpoint.get("instance"),
                "port": endpoint.get("port"),
                "side": "instance" if endpoint.get("instance") else "port",
            }
        )

    for wire in wires:
        add(wire["net"], wire["from"])
        add(wire["net"], wire["to"])
    for inst in instances:
        for port in inst["ports"]:
            if port.get("connect"):
                add(port["connect"], {"instance": inst["instance"], "port": port["name"]})

    touched = {(e["instance"], e["port"]) for endpoints in nets.values() for e in endpoints}
    dangling = [
        {"instance": inst["instance"], "port": port["name"]}
        for inst in instances
        for port in inst["ports"]
        if (inst["instance"], port["name"]) not in touched
    ]
    return {
        "nets": {name: endpoints for name, endpoints in nets.items()},
        "net_count": len(nets),
        "dangling_instance_ports": dangling,
    }


def parse_parameter_text(text: str) -> list[dict[str, str]]:
    """Split a Robei Parameters attribute into name / default / comment."""
    result: list[dict[str, str]] = []
    for chunk in text.split(";"):
        comment = ""
        if "//" in chunk:
            chunk, comment = chunk.split("//", 1)
        match = _PARAM_RE.match(chunk)
        if not match:
            continue
        result.append(
            {
                "name": match.group(1),
                "default": match.group(2).strip(),
                "comment": comment.strip(),
            }
        )
    return result


def _range_suffix(datasize: str) -> str:
    width = (datasize or "").strip()
    if width in {"", "1", "0"}:
        return ""
    if ":" in width:
        return f" [{width}]"
    return f" [{width}-1:0]"


def model_to_verilog(
    model: dict[str, Any],
    parameter_overrides: dict[str, str | int] | None = None,
) -> str:
    """Build a complete module from a parsed .model (Code + Parameters + Ports)."""
    parameter_overrides = {k.upper(): v for k, v in (parameter_overrides or {}).items()}
    module_name = model["name"]
    params = model.get("parameter_list") or []

    lines: list[str] = [
        f"// Generated by robei-mcp from {Path(model['path']).name}; source is not modified.",
        f"// Robei root tag: {model['root_tag']}, category: {model.get('category') or '-'}",
    ]

    if params:
        lines.append(f"module {module_name} #(")
        rendered = []
        for param in params:
            name = param["name"]
            value = parameter_overrides.get(name.upper(), param["default"])
            suffix = f"  // {param['comment']}" if param["comment"] else ""
            rendered.append(f"    parameter {name} = {value}{suffix}")
        lines.append(",\n".join(rendered))
        lines.append(") (")
    else:
        lines.append(f"module {module_name} (")

    port_lines = []
    for port in model.get("ports") or []:
        direction = (port.get("inout") or "input").strip()
        datatype = (port.get("datatype") or "wire").strip() or "wire"
        if direction not in {"input", "output", "inout"}:
            direction = "input"
        if datatype not in {"wire", "reg", "logic"}:
            datatype = "wire"
        range_part = _range_suffix(port.get("datasize") or "")
        port_lines.append(f"    {direction} {datatype}{range_part} {port.get('name')}")
    lines.append(",\n".join(port_lines))
    lines.append(");")

    body = (model.get("code") or "").strip()
    if body:
        lines.append("")
        for line in body.splitlines():
            lines.append(f"    {line.rstrip()}" if line.strip() else "")

    instances = model.get("instances") or []
    if instances:
        port_names = {p.get("name") for p in (model.get("ports") or [])}
        connections: dict[tuple[str, str], str] = {}
        for wire in model.get("wires") or []:
            endpoints = [wire.get("from") or {}, wire.get("to") or {}]
            # A wire that ends on a top-level port uses that port as the expression,
            # exactly like Robei's own generated instantiation list.
            top = next(
                (e.get("port") for e in endpoints if e.get("port") and not e.get("instance") and e.get("port") in port_names),
                None,
            )
            net = top or wire.get("net") or ""
            for endpoint in endpoints:
                if endpoint.get("instance") and endpoint.get("port"):
                    connections[(endpoint["instance"], endpoint["port"])] = net or f"{endpoint['instance']}_{endpoint['port']}"
        for inst in instances:
            for port in inst.get("ports") or []:
                if port.get("connect"):
                    connections.setdefault((inst["instance"], port["name"]), port["connect"])

        internal = sorted(
            {
                net
                for net in connections.values()
                if net and net not in port_names and not re.search(rf"\bwire\b[^;]*\b{re.escape(net)}\b", body)
            }
        )
        lines.append("")
        for net in internal:
            lines.append(f"    wire {net};")
        lines.append("")
        lines.append("    //---Module instantiation---")
        for inst in instances:
            source = inst.get("model_file") or ""
            module = Path(source).stem if source else ""
            module = module or inst.get("class") or ""
            rendered = []
            for port in inst.get("ports") or []:
                expr = connections.get((inst["instance"], port["name"]), "")
                rendered.append(f"        .{port['name']}({expr})")
            lines.append(f"    {module} {inst['instance']}(")
            lines.append(",\n".join(rendered))
            lines.append("    );")
    lines.append("")
    lines.append("endmodule")
    return "\n".join(lines) + "\n"
