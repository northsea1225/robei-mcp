"""Bounded Verilog-expression validation; never executes an expression in Python."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from . import xmlsafe
from .errors import BadRequest

MAX_LENGTH = 2048
MAX_NODES = 128
MAX_DEPTH = 32
MAX_WIDTH = 4096
_TOKEN = re.compile(r"\s+|[0-9]+|[A-Za-z_][A-Za-z0-9_$]*|<<|>>|==|!=|[+*\-&|^~<>?:()\[\]{},]")
_PRECEDENCE = {"|": 20, "^": 30, "&": 40, "==": 50, "!=": 50,
               "<": 60, ">": 60, "<<": 70, ">>": 70, "+": 80, "-": 80, "*": 90}


def invalid(field: str, offset: int, reason: str, hint: str | None = None) -> BadRequest:
    return BadRequest(
        f"{field} at offset {offset}: {reason}",
        hint or "use unsigned DUT inputs, non-negative integers and the documented expression operators",
        code="bad_argument", field=field, offset=offset, reason=reason,
    )


def reject_signed_ports(path: Path, field: str = "model_path") -> None:
    # Read raw attributes: the normal model reader deliberately drops unknown
    # metadata, so checking its port dictionaries alone would miss Signed=true.
    root = xmlsafe.load(path)
    for port in root:
        if port.tag.lower() != "port":
            continue
        attrs = {key.lower(): str(value).strip() for key, value in port.attrib.items()}
        signed = attrs.get("signed", "").lower() not in ("", "0", "false", "no")
        signed |= bool(re.search(r"\bsigned\b", attrs.get("datatype", ""), re.I))
        if signed:
            raise invalid(field, 0, f"signed port {attrs.get('name', '')!r} is not supported",
                          "use unsigned ports; signed semantics are not inferred")


@dataclass(frozen=True)
class Expression:
    text: str
    width: int
    depth: int = 1
    identifier: str | None = None


class _Parser:
    def __init__(self, source: str, inputs: dict[str, int], field: str):
        self.source, self.inputs, self.field = source, inputs, field
        self.tokens: list[tuple[str, int]] = []
        self.position = self.nodes = 0
        if len(source) > MAX_LENGTH:
            self.fail(MAX_LENGTH, f"expression length exceeds {MAX_LENGTH}")
        cursor = 0
        while cursor < len(source):
            match = _TOKEN.match(source, cursor)
            if match is None:
                self.fail(cursor, "unsupported token")
            token = match.group()
            if not token.isspace():
                self.tokens.append((token, cursor))
            cursor = match.end()
        self.tokens.append(("", len(source)))

    def fail(self, offset: int, reason: str):
        raise invalid(self.field, offset, reason)

    def peek(self) -> tuple[str, int]:
        return self.tokens[self.position]

    def take(self, expected: str | None = None) -> tuple[str, int]:
        token, offset = self.peek()
        if expected is not None and token != expected:
            self.fail(offset, f"expected {expected!r}")
        self.position += 1
        return token, offset

    def node(self, text: str, width: int, offset: int, children=(), identifier=None) -> Expression:
        self.nodes += 1
        depth = 1 + max((child.depth for child in children), default=0)
        if self.nodes > MAX_NODES:
            self.fail(offset, f"expression exceeds {MAX_NODES} AST nodes")
        if depth > MAX_DEPTH:
            self.fail(offset, f"expression exceeds AST depth {MAX_DEPTH}")
        if not 1 <= width <= MAX_WIDTH:
            self.fail(offset, f"expression width must be between 1 and {MAX_WIDTH}")
        return Expression(text, width, depth, identifier)

    def expression(self, minimum: int = 0, depth: int = 0) -> Expression:
        if depth >= MAX_DEPTH:
            self.fail(self.peek()[1], f"expression exceeds parse depth {MAX_DEPTH}")
        token, offset = self.take()
        if token == "~":
            value = self.expression(100, depth + 1)
            left = self.node(f"(~{value.text})", value.width, offset, (value,))
        elif token == "(":
            left = self.expression(0, depth + 1)
            self.take(")")
        elif token == "{":
            parts = [self.expression(0, depth + 1)]
            while self.peek()[0] == ",":
                self.take(",")
                parts.append(self.expression(0, depth + 1))
            if self.peek()[0] == "{":
                self.fail(self.peek()[1], "replication concatenation is not supported")
            self.take("}")
            left = self.node("{" + ", ".join(p.text for p in parts) + "}",
                             sum(p.width for p in parts), offset, parts)
        elif re.fullmatch(r"[0-9]+", token):
            value = int(token)
            # Unsigned, explicitly sized constants avoid host/Python integer
            # sizing and preserve normal unsigned Verilog context propagation.
            width = max(32, value.bit_length())
            left = self.node(f"{width}'d{value}", width, offset)
        elif token in self.inputs:
            left = self.node(token, self.inputs[token], offset, identifier=token)
        elif re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", token):
            self.fail(offset, f"undeclared or non-input identifier {token!r}")
        elif token == "-":
            self.fail(offset, "negative literals and unary minus are not supported")
        else:
            self.fail(offset, "expected an input, integer, parenthesis or concatenation")

        while True:
            operator, at = self.peek()
            if operator == "[" and minimum <= 110:
                if left.identifier is None:
                    self.fail(at, "selects must name a DUT input directly")
                self.take("[")
                high_token, high_offset = self.take()
                if not re.fullmatch(r"[0-9]+", high_token):
                    self.fail(high_offset, "select index must be a non-negative constant")
                high = low = int(high_token)
                partial = self.peek()[0] == ":"
                if partial:
                    self.take(":")
                    low_token, low_offset = self.take()
                    if not re.fullmatch(r"[0-9]+", low_token):
                        self.fail(low_offset, "part-select bounds must be non-negative constants")
                    low = int(low_token)
                self.take("]")
                if not 0 <= low <= high < left.width:
                    self.fail(high_offset, f"select is outside input width {left.width}")
                selector = f"32'd{high}:32'd{low}" if partial else f"32'd{high}"
                left = self.node(f"{left.text}[{selector}]", high - low + 1, at, (left,))
            elif operator == "?" and minimum <= 10:
                self.take("?")
                yes = self.expression(0, depth + 1)
                self.take(":")
                no = self.expression(10, depth + 1)
                left = self.node(f"({left.text} ? {yes.text} : {no.text})",
                                 max(yes.width, no.width), at, (left, yes, no))
            elif operator in _PRECEDENCE and _PRECEDENCE[operator] >= minimum:
                self.take()
                right = self.expression(_PRECEDENCE[operator] + 1, depth + 1)
                width = (1 if operator in {"==", "!=", "<", ">"} else
                         left.width if operator in {"<<", ">>"} else max(left.width, right.width))
                left = self.node(f"({left.text} {operator} {right.text})", width, at, (left, right))
            else:
                break
        return left


def compile_expression(source: str, inputs: dict[str, int], field: str) -> Expression:
    parser = _Parser(source, inputs, field)
    result = parser.expression()
    if parser.peek()[0]:
        parser.fail(parser.peek()[1], "unexpected token after expression")
    return result


def expected(value, width: int, inputs: dict[str, int], field: str) -> tuple[str, bool]:
    """Keep the 1.9 literal precedence; only the final branch parses an AST."""
    if isinstance(value, bool):
        value = int(value)
    if isinstance(value, int):
        if value < 0 or value.bit_length() > width:
            raise invalid(field, 0, f"literal does not fit in {width} bits")
        return f"{width}'b{value:0{width}b}", False
    if not isinstance(value, str):
        raise invalid(field, 0, "expected value must be an integer, bit string or expression")
    if len(value) > MAX_LENGTH:
        raise invalid(field, MAX_LENGTH, f"expected value length exceeds {MAX_LENGTH}")
    text = value.strip()
    if re.fullmatch(r"[01xXzZ]+", text):
        if len(text) > width:
            raise invalid(field, 0, f"bit literal does not fit in {width} bits")
        return f"{width}'b{text.lower().rjust(width, '0')}", False
    if re.fullmatch(r"[0-9]+", text):
        return expected(int(text), width, inputs, field)
    return compile_expression(value, inputs, field).text, True
