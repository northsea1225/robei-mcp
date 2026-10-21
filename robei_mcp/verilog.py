from __future__ import annotations

import re
from typing import Any

IDENT = r"[A-Za-z_][A-Za-z0-9_$]*"
_ANSI_PORT_RE = re.compile(
    rf"^\s*(input|output|inout)\b\s*(?:(wire|reg|logic|tri)\b)?\s*(?:(signed|unsigned)\b)?"
    rf"\s*(?:\[([^\]]*)\])?\s*({IDENT})\s*$",
    re.IGNORECASE,
)
_DECL_RE = re.compile(
    rf"^\s*(input|output|inout)\b\s*(?:(wire|reg|logic|tri)\b)?\s*(?:(signed|unsigned)\b)?"
    rf"\s*(?:\[([^\]]*)\])?\s*(.+?)\s*$",
    re.IGNORECASE | re.DOTALL,
)
_COMMENT_RE = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)

CLOCK_NAMES = {"clk", "clock", "clk_i", "i_clk", "clk_in", "sysclk", "sys_clk"}
RESET_NAMES = {
    "rst",
    "reset",
    "rst_n",
    "reset_n",
    "rstn",
    "nrst",
    "arst",
    "arst_n",
    "srst",
    "srst_n",
    "i_rst",
    "i_rst_n",
    "resetn",
}


def strip_comments(text: str) -> str:
    return _COMMENT_RE.sub(lambda m: "".join("\n" if ch == "\n" else " " for ch in m.group(0)), text)


def _match_paren(text: str, start: int) -> int:
    """Return index of the ')' that closes the '(' at start."""
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth == 0:
                return index
    return -1


def _split_top_level(text: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in text:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(ch)
    if "".join(current).strip():
        parts.append("".join(current))
    return parts


def module_header(text: str, top: str) -> str | None:
    match = re.search(rf"\bmodule\s+{re.escape(top)}\b", text)
    if not match:
        return None
    index = match.end()
    depth = 0
    while index < len(text):
        ch = text[index]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == ";" and depth <= 0:
            return text[match.end() : index]
        index += 1
    return None


def parse_module_ports(source: str, top: str) -> list[dict[str, Any]]:
    """Parse ANSI-style ports (falls back to non-ANSI body declarations)."""
    text = strip_comments(source)
    header = module_header(text, top)
    if header is None:
        raise ValueError(f"module {top} not found")

    ports: list[dict[str, Any]] = []
    paren = header.find("(")
    if paren != -1:
        end = _match_paren(header, paren)
        inner = header[paren + 1 : end if end != -1 else len(header)]
        for item in _split_top_level(inner):
            match = _ANSI_PORT_RE.match(item)
            if not match:
                continue
            direction, datatype, _signed, width, name = match.groups()
            ports.append(
                {
                    "name": name,
                    "direction": direction.lower(),
                    "datatype": (datatype or "wire").lower(),
                    "width": (width or "").strip(),
                }
            )

    if ports:
        return ports

    # Non-ANSI: declarations in the module body.
    match = re.search(rf"\bmodule\s+{re.escape(top)}\b", text)
    body_start = match.end() if match else 0
    body = text[body_start:]
    for decl in re.finditer(r"(?m)^\s*(input|output|inout)\b[^;]*;", body):
        statement = decl.group(0).rstrip(";")
        parsed = _DECL_RE.match(statement)
        if not parsed:
            continue
        direction, datatype, _signed, width, names = parsed.groups()
        for name in names.split(","):
            name = name.strip()
            if IDENT.fullmatch(name) is None and not name:
                continue
            ports.append(
                {
                    "name": name,
                    "direction": direction.lower(),
                    "datatype": (datatype or "wire").lower(),
                    "width": (width or "").strip(),
                }
            )
    if not ports:
        raise ValueError(f"no ports parsed for module {top}")
    return ports


def classify_control(ports: list[dict[str, Any]]) -> tuple[str | None, str | None, bool]:
    clock = None
    reset = None
    for port in ports:
        if port["direction"] != "input":
            continue
        name = port["name"].lower()
        if clock is None and name in CLOCK_NAMES:
            clock = port["name"]
        if reset is None and name in RESET_NAMES:
            reset = port["name"]
    if clock is None:
        for port in ports:
            if port["direction"] == "input" and port["name"].lower().endswith("clk"):
                clock = port["name"]
                break
    active_low = bool(reset) and (
        reset.lower().endswith("_n") or reset.lower() in {"nrst", "resetn"}
    )
    return clock, reset, active_low


def _decl(direction: str, width: str, name: str, init: str = "") -> str:
    kind = "reg" if direction == "input" else "wire"
    range_part = f" [{width}]" if width else ""
    suffix = f" = {init}" if init else ""
    return f"    {kind}{range_part} {name}{suffix};"


def generate_testbench(
    top: str,
    ports: list[dict[str, Any]],
    tb_name: str | None = None,
    clock_period_ns: int = 10,
) -> str:
    tb_name = tb_name or f"{top}_tb"
    clock, reset, active_low = classify_control(ports)

    lines: list[str] = [
        "`timescale 1ns/1ps",
        f"// Auto-generated skeleton by robei-mcp for module {top}.",
        "// TODO: add real stimulus and checks before relying on this testbench.",
        f"module {tb_name};",
        "",
    ]

    for port in ports:
        width = port["width"]
        if port["direction"] == "input":
            init = "1'b0"
            if port["name"] == clock:
                init = "1'b0"
            elif port["name"] == reset and not active_low:
                init = "1'b1"  # active-high reset asserted at time 0
            lines.append(_decl("input", width, port["name"], init))
        else:
            lines.append(_decl("output", width, port["name"]))
    lines.append("")

    half = max(1, clock_period_ns // 2)
    if clock:
        lines.append(f"    always #{half} {clock} = ~{clock};  // {clock_period_ns} ns period")
        lines.append("")

    lines.append("    initial begin")
    lines.append(f'        $dumpfile("{tb_name}.vcd");')
    lines.append(f"        $dumpvars(0, {tb_name});")
    lines.append(f'        $display("SIM start: {tb_name} at %0t", $time);')

    if reset:
        if active_low:
            lines.append(f"        #100 {reset} = 1'b1;  // release active-low reset")
        else:
            lines.append(f"        #100 {reset} = 1'b0;  // release active-high reset")

    stimulus = [
        port["name"]
        for port in ports
        if port["direction"] == "input" and port["name"] not in {clock, reset}
    ]
    if stimulus:
        lines.append(f"        #20 {stimulus[0]} = 1'b1;  // TODO: drive real stimulus")
        for name in stimulus[1:]:
            lines.append(f"        // TODO: drive {name}")
    lines.append("        #1000;")
    lines.append(f'        $display("SIM end: {tb_name} at %0t", $time);')
    lines.append("        $finish;")
    lines.append("    end")
    lines.append("")

    lines.append(f"    {top} dut (")
    connections = [f"        .{port['name']}({port['name']})" for port in ports]
    lines.append(",\n".join(connections))
    lines.append("    );")
    lines.append("")
    lines.append("    // TODO: assertions / scoreboard go here.")
    lines.append(f"endmodule")
    return "\n".join(lines) + "\n"
