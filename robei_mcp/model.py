from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from . import xmlsafe
from pathlib import Path
from typing import Any

MAX_TREE_DEPTH = 16
MAX_TREE_NODES = 5000

_PARAM_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_$]*)\s*=\s*(.+?)\s*$", re.DOTALL)

# A connection that is just a name; anything else (``a[0]``, ``{a,b}``, a
# constant) is an expression and must never become a wire declaration.
_PLAIN_NET_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


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
        # Pin-constraint canvases use these; empty on ordinary ports.
        "function": _attr_ci(element, "Function") or "",
        "drive": _attr_ci(element, "Drive") or "",
        "pull": _attr_ci(element, "Pull") or "",
        "voltage": _attr_ci(element, "Voltage") or "",
    }


def _parse_instance(element: ET.Element) -> dict[str, Any]:
    """A nested ``<Module Type="model">`` is one placed instance on the canvas."""
    ports = [_parse_port(child) for child in element if child.tag.lower() == "port"]
    return {
        "instance": _attr_ci(element, "Name") or "",
        "model_file": _attr_ci(element, "File") or "",
        "class": _attr_ci(element, "Class") or "",
        # Only the legacy <Instance Module="..."> schema carries this; Robei's
        # nested <Module> keeps the reference in Class/File instead.
        "module": _attr_ci(element, "Module") or "",
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
    return parse_robei_model_root(xmlsafe.load(path), path)


def parse_robei_model_text(text: str, path: Path | str = "<memory>") -> dict[str, Any]:
    """Parse model XML that is already in memory (editor round-trips, previews)."""
    xmlsafe.check_text(text, str(path))
    return parse_robei_model_root(ET.fromstring(text), Path(str(path)))


def parse_robei_model_root(root: ET.Element, path: Path) -> dict[str, Any]:
    """Build the model dict from an already-parsed root element."""
    ports: list[dict[str, Any]] = []
    instances: list[dict[str, Any]] = []
    wires: list[dict[str, Any]] = []
    other_children: list[dict[str, Any]] = []
    for child in root:
        tag = child.tag.lower()
        if tag == "port":
            ports.append(_parse_port(child))
            continue
        # Robei's canvas uses a nested <Module Type="model"> per placed instance;
        # the older/synthetic schema writes <Instance Name="u1" Module="child"/>.
        if tag in {"module", "instance"}:
            instances.append(_parse_instance(child))
            continue
        if tag == "wire":
            wires.append(_parse_wire(child))
            continue
        other_children.append(generic_node(child))

    parameters = _attr_ci(root, "Parameters") or ""
    code = _attr_ci(root, "Code") or ""
    parameter_list, parameter_bad = parse_parameter_text_checked(parameters)
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
        "parameter_list": parameter_list,
        "parameter_bad": parameter_bad,
        "ports": ports,
        "instances": instances,
        "wires": wires,
        "is_canvas": bool(instances or wires),
        "netlist": build_netlist(ports, instances, wires),
        "code": code,
        "code_lines": code.splitlines(),
        "other_children": other_children,
        "size_bytes": _size_bytes(path, root),
    }


def _size_bytes(path: Path, root: ET.Element) -> int:
    try:
        return path.stat().st_size
    except OSError:  # in-memory documents have no file behind them
        return len(ET.tostring(root, encoding="unicode").encode("utf-8"))


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
    parsed, _bad = parse_parameter_text_checked(text)
    return parsed


def parse_parameter_text_checked(text: str) -> tuple[list[dict[str, str]], list[str]]:
    """Like :func:`parse_parameter_text`, but report chunks that did not parse."""
    result: list[dict[str, str]] = []
    bad: list[str] = []
    # Comments must go first: Robei writes ``NAME = value; //comment``, so
    # splitting on ``;`` before stripping the comment swallowed every parameter
    # after the first one (the comment of line 1 ran into line 2's text).
    carried: list[str] = []
    buffer: list[str] = []
    closed_here: list[int] = []
    quote = ""
    escaped = False

    def close_statement() -> None:
        raw = "".join(buffer).strip()
        buffer.clear()
        if not raw:
            return
        match = _PARAM_RE.match(raw)
        value = match.group(2).strip() if match else ""
        if not match or not value:
            # ``W = ;`` matches the regex only because the gap can be a space;
            # an empty default is still not a usable override.
            carried.clear()
            bad.append(raw)
            return
        comment = " ".join(carried).strip()
        carried.clear()
        result.append({"name": match.group(1), "default": value, "comment": comment})
        closed_here.append(len(result) - 1)

    for line in text.splitlines():
        code, line_comment = _strip_line_comment(line)
        closed_here.clear()
        for char in code:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif quote:
                if char == quote:
                    quote = ""
            elif char in {"'", '"'}:
                quote = char
            elif char == ";":
                close_statement()
                continue
            buffer.append(char)
        if line_comment:
            if closed_here:
                # The comment trails the parameters of this line; the last one
                # owns it, which is what a one-parameter-per-line file implies.
                target = result[closed_here[-1]]
                target["comment"] = f"{target['comment']} {line_comment}".strip()
            else:
                carried.append(line_comment)
    close_statement()
    return result, bad


def _strip_line_comment(line: str) -> tuple[str, str]:
    """Split one ``Parameters`` line into code and its trailing ``//`` comment.

    A ``//`` inside a quoted string is part of the value, not a comment, so the
    scan tracks quotes and backslash escapes.
    """
    quote = ""
    escaped = False
    index = 0
    while index < len(line):
        char = line[index]
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif quote:
            if char == quote:
                quote = ""
        elif char in {"'", '"'}:
            quote = char
        elif char == "/" and line.startswith("//", index):
            return line[:index], line[index + 2 :].strip()
        index += 1
    return line, ""


def _range_suffix(datasize: str) -> str:
    """Render a Robei Datasize attribute as a Verilog range suffix.

    Robei stores the *width* (``4``), but also accepts a literal range
    (``3:0``).  Constant widths are folded to ``[3:0]`` so the exported module
    stays readable and every downstream parser (ours included) sees a plain
    literal range instead of ``[4-1:0]``.
    """
    width = (datasize or "").strip()
    if not width:
        return ""
    if ":" in width:
        return f" [{width}]"
    from .verilog import const_eval

    value = const_eval(width)
    if value is not None:
        if value <= 1:
            return ""
        return f" [{value - 1}:0]"
    return f" [{width}-1:0]"


def model_to_verilog(
    model: dict[str, Any],
    parameter_overrides: dict[str, str | int] | None = None,
) -> str:
    """Build a complete module from a parsed .model (Code + Parameters + Ports)."""
    text, _warnings = model_to_verilog_with_warnings(model, parameter_overrides)
    return text


def _wider(candidate: int | None, current: int | None) -> bool:
    """True when a wire declaration is strictly wider than the one already kept.

    ``robei_width`` returns ``None`` for an expression it cannot resolve yet
    (``LEN-1:0``); such a declaration never wins, and any resolvable width beats
    it, so ``Datasize="4"`` is no longer read as one bit.
    """
    if candidate is None:
        return False
    if current is None:
        return True
    return candidate > current


def model_to_verilog_with_warnings(
    model: dict[str, Any],
    parameter_overrides: dict[str, str | int] | None = None,
) -> tuple[str, list[str]]:
    """Like :func:`model_to_verilog`, but also return export warnings.

    Warnings cover root and instance ``Parameters`` strings that cannot be
    parsed into ``NAME = value`` pairs: those are skipped instead of being
    emitted as a parameter / ``#(...)`` so a broken canvas never produces
    invalid Verilog silently.
    """
    parameter_overrides = {k.upper(): v for k, v in (parameter_overrides or {}).items()}
    module_name = model["name"]
    params = model.get("parameter_list") or []
    warnings: list[str] = []
    for chunk in model.get("parameter_bad") or []:
        warnings.append(
            f"root Parameters: cannot parse chunk {chunk!r}; no parameter declared for it"
        )

    lines: list[str] = [
        f"// Generated by robei-mcp from {Path(model['path']).name}; source is not modified.",
        f"// Robei root tag: {model['root_tag']}, category: {model.get('category') or '-'}",
    ]

    if params:
        lines.append(f"module {module_name} #(")
        rendered = []
        for index, param in enumerate(params):
            name = param["name"]
            value = parameter_overrides.get(name.upper(), param["default"])
            # The separating comma must come before ``// comment``: appending
            # it afterwards puts the comma inside the comment and iverilog then
            # sees an unterminated parameter list.
            comma = "," if index < len(params) - 1 else ""
            suffix = f"  // {param['comment']}" if param["comment"] else ""
            rendered.append(f"    parameter {name} = {value}{comma}{suffix}")
        lines.append("\n".join(rendered))
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
        from .verilog import robei_width

        port_names = {p.get("name") for p in (model.get("ports") or [])}
        connections: dict[tuple[str, str], str] = {}
        wires = model.get("wires") or []
        for wire in wires:
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
                if net and _PLAIN_NET_RE.match(net) and net not in port_names and not re.search(rf"\bwire\b[^;]*\b{re.escape(net)}\b", body)
            }
        )
        # A <Wire> carries its own Datasize/Datatype.  Losing it turned every
        # internal bus into a 1-bit wire, which iverilog then flagged as a
        # width mismatch (and the simulation produced 0 instead of the input).
        wire_meta: dict[str, dict[str, Any]] = {}
        for wire in wires:
            net = (wire.get("net") or "").strip()
            if not net:
                continue
            previous = wire_meta.get(net)
            if previous is None or _wider(
                robei_width(wire.get("datasize")), robei_width(previous.get("datasize"))
            ):
                wire_meta[net] = wire
        lines.append("")
        for net in internal:
            wire = wire_meta.get(net) or {}
            datatype = (wire.get("datatype") or "wire").strip() or "wire"
            if datatype not in {"wire", "reg", "logic"}:
                datatype = "wire"
            lines.append(f"    {datatype}{_range_suffix(wire.get('datasize') or '')} {net};")
        lines.append("")
        lines.append("    //---Module instantiation---")
        model_dir = Path(model["path"]).parent
        defaults_cache: dict[str, dict[str, str]] = {}

        def child_defaults(module_name: str) -> dict[str, str]:
            key = module_name.lower()
            if key in defaults_cache:
                return defaults_cache[key]
            values: dict[str, str] = {}
            child_path = model_dir / f"{module_name}.model"
            if child_path.is_file():
                try:
                    child = parse_robei_model(child_path)
                    values = {
                        item["name"].upper(): item["default"].strip()
                        for item in child.get("parameter_list") or []
                    }
                except Exception:
                    values = {}
            defaults_cache[key] = values
            return values

        for inst in instances:
            source = inst.get("model_file") or ""
            module = Path(source).stem if source else ""
            module = module or inst.get("class") or inst.get("module") or ""
            overrides: list[tuple[str, str]] = []
            raw_parameters = (inst.get("parameters") or "").strip()
            if raw_parameters:
                defaults = child_defaults(module)
                parsed, bad_chunks = parse_parameter_text_checked(raw_parameters)
                for chunk in bad_chunks:
                    warnings.append(
                        f"{inst['instance']}: cannot parse Parameters chunk {chunk!r}; "
                        "no override emitted"
                    )
                for param in parsed:
                    value = (param["default"] or "").strip()
                    default = defaults.get(param["name"].upper())
                    if defaults and default is None:
                        warnings.append(
                            f"{inst['instance']}: parameter {param['name']} is not declared "
                            f"by {module or 'the child model'}; override skipped"
                        )
                        continue
                    if default is not None and value == default:
                        continue
                    overrides.append((param["name"], value))
            rendered = []
            for port in inst.get("ports") or []:
                expr = connections.get((inst["instance"], port["name"]), "")
                rendered.append(f"        .{port['name']}({expr})")
            header = f"    {module}"
            if overrides:
                header += " #(" + ", ".join(f".{name}({value})" for name, value in overrides) + ")"
            lines.append(f"{header} {inst['instance']}(")
            lines.append(",\n".join(rendered))
            lines.append("    );")
    lines.append("")
    lines.append("endmodule")
    return "\n".join(lines) + "\n", warnings
