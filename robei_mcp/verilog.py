from __future__ import annotations

import ast
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
_BARE_PORT_RE = re.compile(rf"^\s*(?:\[([^\]]*)\])?\s*({IDENT})\s*$", re.IGNORECASE)
_BODY_DECL_RE = re.compile(
    r"(?:\A|(?<=;)|(?<=\n))[ \t]*(input|output|inout)\b[^;]*;",
    re.IGNORECASE,
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


def _port_paren(header: str) -> int:
    """Index of the port-list ``(``, skipping an optional ``#(parameter ...)``."""
    index = 0
    while index < len(header):
        char = header[index]
        if char.isspace():
            index += 1
            continue
        if char == "#":
            open_index = header.find("(", index)
            if open_index == -1:
                return -1
            close_index = _match_paren(header, open_index)
            if close_index == -1:
                return -1
            index = close_index + 1
            continue
        if char == "(":
            return index
        if char == ";":
            return -1
        index += 1
    return -1


def parse_module_ports(source: str, top: str) -> list[dict[str, Any]]:
    """Parse ANSI-style ports (falls back to non-ANSI body declarations).

    ANSI shorthand is legal Verilog (``input wire [3:0] a, b, c``): every bare
    name after the first declaration inherits its direction, type and range.
    Widths are always stored without the surrounding brackets (``3:0``).
    """
    text = strip_comments(source)
    header = module_header(text, top)
    if header is None:
        raise ValueError(f"module {top} not found")

    ports: list[dict[str, Any]] = []
    paren = _port_paren(header)
    if paren == -1:
        # ``module m;`` has no port list at all: a valid module with zero ports.
        return []
    if paren != -1:
        end = _match_paren(header, paren)
        inner = header[paren + 1 : end if end != -1 else len(header)]
        if not inner.strip():
            # ``module m();`` — same answer, and both parsers must agree.
            return []
        direction: str | None = None
        datatype = "wire"
        width = ""
        for item in _split_top_level(inner):
            match = _ANSI_PORT_RE.match(item)
            if match:
                raw_direction, raw_type, _signed, raw_width, name = match.groups()
                direction = raw_direction.lower()
                datatype = (raw_type or "wire").lower()
                width = (raw_width or "").strip()
                ports.append(
                    {
                        "name": name,
                        "direction": direction,
                        "datatype": datatype,
                        "width": width,
                    }
                )
                continue
            bare = _BARE_PORT_RE.match(item)
            if bare and direction:
                raw_width, name = bare.groups()
                if raw_width is not None:
                    width = raw_width.strip()
                ports.append(
                    {
                        "name": name,
                        "direction": direction,
                        "datatype": datatype,
                        "width": width,
                    }
                )

    if ports:
        return ports

    # Non-ANSI: declarations in the module body.
    match = re.search(rf"\bmodule\s+{re.escape(top)}\b", text)
    body_start = match.end() if match else 0
    body_end = text.find("endmodule", body_start)
    body = text[body_start : body_end if body_end != -1 else len(text)]
    # Declarations may follow the header on the same line
    # (``module m(a); input a; output y; ...``), so require start-of-module,
    # a newline or a preceding semicolon instead of only line starts.
    for decl in _BODY_DECL_RE.finditer(body):
        statement = decl.group(0).strip().rstrip(";")
        parsed = _DECL_RE.match(statement)
        if not parsed:
            continue
        direction, datatype, _signed, width, names = parsed.groups()
        for name in names.split(","):
            name = name.strip()
            if not name or re.fullmatch(IDENT, name) is None:
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


_CONST_EXPR_RE = re.compile(r"^[0-9+\-*/%() \t]+$")


def const_eval(text: str | None) -> int | None:
    """Evaluate a constant integer expression such as ``4-1`` or ``2*4``.

    Only decimal literals and ``+ - * / // %`` with parentheses are accepted.
    Anything else (identifiers, bit selects, function calls) returns ``None``
    so callers fall back to a conservative interpretation.  This deliberately
    avoids ``eval``.
    """
    source = (text or "").strip()
    if not source or not _CONST_EXPR_RE.match(source):
        return None
    try:
        tree = ast.parse(source, mode="eval")
    except (SyntaxError, ValueError, MemoryError):
        return None

    def visit(node: ast.AST) -> int:
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
            return node.value
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = visit(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BinOp):
            left = visit(node.left)
            right = visit(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return _verilog_div(left, right)
            if isinstance(node.op, ast.FloorDiv):
                return _verilog_div(left, right)
            if isinstance(node.op, ast.Mod):
                return left - _verilog_div(left, right) * right
        raise ValueError("unsupported expression")

    try:
        return visit(tree)
    except (ValueError, ZeroDivisionError, RecursionError):
        return None


def _verilog_div(left: int, right: int) -> int:
    """Integer division truncated toward zero, as Verilog (and C) do it."""
    if right == 0:
        raise ZeroDivisionError("division by zero")
    quotient = abs(left) // abs(right)
    return quotient if (left < 0) == (right < 0) else -quotient


def width_info(range_text: str | None) -> dict[str, Any]:
    """Describe a port range without guessing.

    Returns ``{"width": int | None, "range": str | None, "resolved": bool,
    "expression": str | None}``.  ``resolved`` is False for parameterised
    ranges such as ``LEN-1:0`` — callers that enumerate bits must stop instead
    of silently treating them as one bit wide.
    """
    if isinstance(range_text, int):
        value = max(1, range_text)
        return {"width": value, "range": f"{value - 1}:0", "resolved": True, "expression": str(value)}
    text = (str(range_text) if range_text is not None else "").strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1].strip()
    if not text:
        return {"width": 1, "range": None, "resolved": True, "expression": None}
    high_text, sep, low_text = text.partition(":")
    if not sep:
        return {"width": 1, "range": None, "resolved": True, "expression": None}
    high, low = const_eval(high_text), const_eval(low_text)
    if high is None or low is None:
        return {"width": None, "range": text, "resolved": False, "expression": text}
    return {"width": abs(high - low) + 1, "range": text, "resolved": True, "expression": text}


def width_of(range_text: str | None) -> int:
    """``[3:0]``/``3:0`` -> 4, ``[7:4]`` -> 4, ``[4-1:0]`` -> 4, ``""`` -> 1."""
    info = width_info(range_text)
    return int(info["width"]) if info["width"] is not None else 1


def classify_clocks(ports: list[dict[str, Any]]) -> list[str]:
    """Every input that looks like a clock, in declaration order."""
    clocks = [port["name"] for port in ports if port["direction"] == "input" and port["name"].lower() in CLOCK_NAMES]
    clocks += [
        port["name"]
        for port in ports
        if port["direction"] == "input"
        and port["name"].lower().endswith("clk")
        and port["name"] not in clocks
    ]
    return clocks


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
    width = (width or "").strip()
    if width.startswith("[") and width.endswith("]"):
        width = width[1:-1].strip()
    range_part = f" [{width}]" if width else ""
    suffix = f" = {init}" if init else ""
    return f"    {kind}{range_part} {name}{suffix};"


def generate_testbench(
    top: str,
    ports: list[dict[str, Any]],
    tb_name: str | None = None,
    clock_period_ns: int = 10,
    full_combo: bool = False,
    max_combo_bits: int = 8,
) -> str:
    tb_name = tb_name or f"{top}_tb"
    unresolved = [
        f"{port['name']}[{port['width']}]"
        for port in ports
        if width_info(port["width"])["width"] is None
    ]
    if unresolved:
        raise ValueError(
            "cannot size a testbench for a parameterised width: "
            + ", ".join(unresolved)
            + "; give the model concrete port widths or override the parameters first"
        )
    clocks = classify_clocks(ports)
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
    for index, name in enumerate(clocks):
        phase = "" if index == 0 else f"  // phase {index}"
        lines.append(f"    always #{half} {name} = ~{name};  // {clock_period_ns} ns period{phase}")
    if clocks:
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

    free = [
        port for port in ports
        if port["direction"] == "input" and port["name"] not in set(clocks) | {reset}
    ]
    if full_combo and free:
        bits = sum(width_of(port["width"]) for port in free)
        if bits > max_combo_bits:
            raise ValueError(f"full_combo needs {bits} input bits, above max_combo_bits={max_combo_bits}")
        outputs = [port for port in ports if port["direction"] != "input"]
        fmt = " ".join(f"{port['name']}=%b" for port in free + outputs)
        args = ", ".join(port["name"] for port in free + outputs)
        for index in range(1 << bits):
            assignments = []
            shift = bits
            for port in free:
                width = width_of(port["width"])
                shift -= width
                value = (index >> shift) & ((1 << width) - 1)
                assignments.append(f"{port['name']} = {width}'b{format(value, f'0{width}b')};")
            lines.append("        " + " ".join(assignments) + " #10;")
            lines.append(f'        $display("combo {fmt}", {args});')
    elif free:
        lines.append(f"        #20 {free[0]['name']} = 1'b1;  // TODO: drive real stimulus")
        for port in free[1:]:
            lines.append(f"        // TODO: drive {port['name']}")
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
