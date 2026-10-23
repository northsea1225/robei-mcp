"""A deliberately small structural Verilog reader.

Robei teaching designs are tiny, so this only extracts what the canvas importer
and the connectivity checker need: ports, wires, instance statements and
``assign``s. Behavioural code (``always`` / ``initial``) is only summarised by a
flag — it is never interpreted, and nothing here claims to be a full parser.
"""
from __future__ import annotations

import re
from typing import Any

from .verilog import IDENT, _match_paren, _split_top_level, parse_module_ports, strip_comments

KEYWORDS = {
    "module", "endmodule", "input", "output", "inout", "wire", "reg", "logic", "tri",
    "assign", "always", "initial", "begin", "end", "if", "else", "case", "casex",
    "casez", "endcase", "for", "while", "repeat", "forever", "function", "endfunction",
    "task", "endtask", "generate", "endgenerate", "parameter", "localparam", "defparam",
    "posedge", "negedge", "or", "and", "not", "xor", "signed", "unsigned", "integer",
    "real", "time", "genvar", "specify", "endspecify", "default", "return",
}

_MODULE_RE = re.compile(rf"\bmodule\s+({IDENT})")
_WIRE_RE = re.compile(rf"(?m)^\s*(?:wire|logic|tri)\s*(?:\[([^\]]*)\])?\s*([^;]+);")
_ASSIGN_RE = re.compile(r"\bassign\s+([^;=]+?)\s*=\s*([^;]+);")
_INSTANCE_RE = re.compile(rf"(?m)^\s*({IDENT})\s+(?:#\s*\([^;]*?\)\s*)?({IDENT})\s*\(")


def width_of(range_text: str | None) -> int:
    """``[3:0]`` -> 4, ``[7:4]`` -> 4, ``""`` -> 1."""
    text = (range_text or "").strip()
    if not text:
        return 1
    match = re.fullmatch(r"\[\s*(\d+)\s*:\s*(\d+)\s*\]", text)
    if not match:
        return 1
    high, low = int(match.group(1)), int(match.group(2))
    return abs(high - low) + 1


def _module_spans(text: str) -> list[tuple[str, int, int]]:
    spans: list[tuple[str, int, int]] = []
    for match in _MODULE_RE.finditer(text):
        name = match.group(1)
        end = text.find("endmodule", match.end())
        spans.append((name, match.end(), len(text) if end == -1 else end))
    return spans


def _parse_connections(inner: str, known_ports: list[str] | None) -> tuple[list[dict[str, Any]], bool]:
    connections: list[dict[str, Any]] = []
    positional = False
    for index, item in enumerate(_split_top_level(inner)):
        item = item.strip()
        if not item:
            continue
        match = re.fullmatch(rf"\.({IDENT})\s*\(\s*(.*?)\s*\)", item, re.DOTALL)
        if match:
            connections.append({"port": match.group(1), "expr": match.group(2).strip()})
            continue
        positional = True
        port = known_ports[index] if known_ports and index < len(known_ports) else f"p{index}"
        connections.append({"port": port, "expr": item})
    return connections, positional


def parse_modules(source: str) -> list[dict[str, Any]]:
    text = strip_comments(source)
    modules: list[dict[str, Any]] = []
    for name, start, end in _module_spans(text):
        body = text[start:end]
        try:
            ports = parse_module_ports(source, name)
        except ValueError:
            ports = []
        wires: list[dict[str, Any]] = []
        for match in _WIRE_RE.finditer(body):
            width, names = match.group(1), match.group(2)
            for item in names.split(","):
                item = item.strip()
                if re.fullmatch(IDENT, item):
                    wires.append({"name": item, "width": width_of(width)})
        assigns = [
            {"lhs": match.group(1).strip(), "rhs": match.group(2).strip()}
            for match in _ASSIGN_RE.finditer(body)
        ]
        instances: list[dict[str, Any]] = []
        for match in _INSTANCE_RE.finditer(body):
            module, instance = match.group(1), match.group(2)
            if module in KEYWORDS or instance in KEYWORDS or module == name:
                continue
            open_index = match.end() - 1
            close_index = _match_paren(body, open_index)
            if close_index == -1:
                continue
            inner = body[open_index + 1 : close_index]
            connections, positional = _parse_connections(inner, None)
            instances.append(
                {
                    "module": module,
                    "instance": instance,
                    "connections": connections,
                    "positional": positional,
                }
            )
        modules.append(
            {
                "name": name,
                "ports": ports,
                "wires": wires,
                "assigns": assigns,
                "instances": instances,
                "has_behaviour": bool(re.search(r"\b(always|initial)\b", body)),
            }
        )
    return modules


def parse_module(source: str, name: str | None = None) -> dict[str, Any]:
    modules = parse_modules(source)
    if not modules:
        raise ValueError("no module found in source")
    if name is None:
        return modules[0]
    for module in modules:
        if module["name"] == name:
            return module
    raise ValueError(f"module {name!r} not found (have: {[m['name'] for m in modules]})")


def port_map(module: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {port["name"]: port for port in module["ports"]}


def net_width(module: dict[str, Any], net: str) -> int | None:
    for port in module["ports"]:
        if port["name"] == net:
            return width_of(port["width"])
    for wire in module["wires"]:
        if wire["name"] == net:
            return wire["width"]
    return None


def is_plain_net(expr: str) -> bool:
    return re.fullmatch(IDENT, expr.strip()) is not None
