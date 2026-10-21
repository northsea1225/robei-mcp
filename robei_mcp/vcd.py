from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class VcdParseError(ValueError):
    pass


@dataclass
class VcdSignal:
    identifier: str
    name: str
    width: int
    var_type: str
    scope: tuple[str, ...] = ()
    bit_range: str = ""

    @property
    def full_name(self) -> str:
        return ".".join((*self.scope, self.name))


@dataclass
class VcdData:
    timescale: str
    signals: dict[str, VcdSignal] = field(default_factory=dict)
    changes: dict[str, list[tuple[int, str]]] = field(default_factory=dict)
    max_time: int | None = None


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gbk", "cp936"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _take_until_end(tokens: list[str], index: int) -> tuple[list[str], int]:
    parts: list[str] = []
    index += 1
    while index < len(tokens) and tokens[index] != "$end":
        parts.append(tokens[index])
        index += 1
    if index >= len(tokens):
        raise VcdParseError("unterminated VCD directive")
    return parts, index + 1


def parse_vcd(path: Path) -> VcdData:
    text = _decode(path.read_bytes())
    tokens = text.split()
    data = VcdData(timescale="unknown")
    scope: list[str] = []
    current_time: int | None = None
    index = 0

    while index < len(tokens):
        token = tokens[index]
        if token == "$end":
            index += 1
            continue

        if token.startswith("$"):
            directive = token.lower()
            if directive == "$scope":
                parts, index = _take_until_end(tokens, index)
                if len(parts) >= 2:
                    scope.append(parts[1])
                continue
            if directive == "$upscope":
                _, index = _take_until_end(tokens, index)
                if scope:
                    scope.pop()
                continue
            if directive == "$var":
                parts, index = _take_until_end(tokens, index)
                if len(parts) < 4:
                    continue
                try:
                    width = int(parts[1])
                except ValueError:
                    width = 1
                identifier = parts[2]
                name_parts = parts[3:]
                bit_range = ""
                if len(name_parts) >= 2 and name_parts[-1].startswith("["):
                    bit_range = name_parts[-1]
                    name_parts = name_parts[:-1]
                name = " ".join(name_parts).strip()
                signal = VcdSignal(
                    identifier=identifier,
                    name=name,
                    width=width,
                    var_type=parts[0],
                    scope=tuple(scope),
                    bit_range=bit_range,
                )
                # Different scopes may legally share one VCD identifier when
                # nets are aliased. Key by full name, not by identifier.
                data.signals[signal.full_name] = signal
                continue
            if directive == "$timescale":
                parts, index = _take_until_end(tokens, index)
                data.timescale = " ".join(parts)
                continue
            if directive in {"$comment", "$date", "$version", "$attrbegin"}:
                _, index = _take_until_end(tokens, index)
                continue
            if directive in {
                "$enddefinitions",
                "$dumpvars",
                "$dumpall",
                "$dumpon",
                "$dumpoff",
            }:
                index += 1
                continue
            # Unknown directive: skip its payload rather than interpreting text
            # as signal values.
            _, index = _take_until_end(tokens, index)
            continue

        if token.startswith("#"):
            try:
                current_time = int(token[1:])
            except ValueError as exc:
                raise VcdParseError(f"invalid VCD time token: {token}") from exc
            if data.max_time is None or current_time > data.max_time:
                data.max_time = current_time
            index += 1
            continue

        if current_time is None:
            index += 1
            continue

        first = token[0].lower()
        if first in {"0", "1", "x", "z"}:
            if len(token) > 1:
                value, identifier = token[0], token[1:]
                index += 1
            else:
                if index + 1 >= len(tokens):
                    raise VcdParseError("scalar value without identifier")
                value, identifier = token, tokens[index + 1]
                index += 2
            data.changes.setdefault(identifier, []).append((current_time, value))
            continue

        if first in {"b", "r", "s"}:
            if index + 1 >= len(tokens):
                raise VcdParseError("vector/real value without identifier")
            value = token[1:]
            identifier = tokens[index + 1]
            data.changes.setdefault(identifier, []).append((current_time, value))
            index += 2
            continue

        index += 1

    return data


def _signal_descriptor(signal: VcdSignal) -> dict[str, Any]:
    return {
        "name": signal.full_name,
        "simple_name": signal.name,
        "scope": ".".join(signal.scope),
        "width": signal.width,
        "type": signal.var_type,
        "range": signal.bit_range,
        "id": signal.identifier,
    }


def match_signals(data: VcdData, requested: list[str]) -> list[VcdSignal]:
    matched: list[VcdSignal] = []
    for query in requested:
        exact = [s for s in data.signals.values() if s.full_name == query]
        if exact:
            matched.extend(exact)
            continue
        candidates = [
            s
            for s in data.signals.values()
            if s.name == query or s.full_name.endswith("." + query)
        ]
        if len(candidates) == 1:
            matched.append(candidates[0])
            continue
        if not candidates:
            raise KeyError(f"signal not found: {query}")
        names = ", ".join(s.full_name for s in candidates[:10])
        raise KeyError(f"ambiguous signal {query}; candidates: {names}")
    return matched


def _format_value(signal: VcdSignal, raw: str) -> tuple[str, int | None]:
    if signal.width <= 1 and len(raw) == 1:
        return raw, (int(raw, 2) if raw in {"0", "1"} else None)
    bits = raw
    if bits and bits[0].lower() in {"b", "r", "s"}:
        bits = bits[1:]
    if all(ch in "01" for ch in bits):
        bits = bits.zfill(signal.width)
        return bits, int(bits, 2) if bits else 0
    return bits, None


def value_at(data: VcdData, signal: VcdSignal, time: int | None) -> dict[str, Any]:
    history = data.changes.get(signal.identifier, [])
    if not history:
        return {
            "name": signal.full_name,
            "found": False,
            "value": None,
            "value_int": None,
            "effective_time": None,
        }
    if time is None:
        change_time, raw = history[-1]
    else:
        times = [item[0] for item in history]
        pos = bisect_right(times, time) - 1
        if pos < 0:
            return {
                "name": signal.full_name,
                "found": False,
                "value": None,
                "value_int": None,
                "effective_time": None,
                "before_first_change": True,
            }
        change_time, raw = history[pos]
    value, value_int = _format_value(signal, raw)
    return {
        "name": signal.full_name,
        "found": True,
        "value": value,
        "value_int": value_int,
        "effective_time": change_time,
    }


def describe_vcd(data: VcdData) -> dict[str, Any]:
    return {
        "timescale": data.timescale,
        "max_time": data.max_time,
        "signal_count": len(data.signals),
        "signals": [_signal_descriptor(s) for s in data.signals.values()],
    }


def query_vcd(
    data: VcdData,
    signals: list[str] | None = None,
    time: int | None = None,
) -> dict[str, Any]:
    result = {
        "timescale": data.timescale,
        "max_time": data.max_time,
        "signal_count": len(data.signals),
        "query_time": time,
    }
    if not signals:
        result.update(describe_vcd(data))
        return result

    selected = match_signals(data, signals)
    result["values"] = [value_at(data, signal, time) for signal in selected]
    return result
