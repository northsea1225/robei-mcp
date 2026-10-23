from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class VcdParseError(ValueError):
    pass


_TIMESCALE_RE = re.compile(r"^\s*(\d+)\s*(fs|ps|ns|us|ms|s)\s*$", re.IGNORECASE)
_UNIT_FS = {"fs": 1, "ps": 1_000, "ns": 1_000_000, "us": 1_000_000_000,
            "ms": 1_000_000_000_000, "s": 1_000_000_000_000_000}


def timescale_fs(text: str | None) -> int | None:
    """Return one time tick in femtoseconds, or ``None`` when unparseable."""
    match = _TIMESCALE_RE.match(text or "")
    if not match:
        return None
    return int(match.group(1)) * _UNIT_FS[match.group(2).lower()]


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
    saw_enddefinitions = False
    saw_var = False

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
                saw_var = True
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
            if directive == "$enddefinitions":
                saw_enddefinitions = True
                index += 1
                continue
            if directive in {
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

    if not saw_enddefinitions or not saw_var:
        raise VcdParseError(
            f"not a VCD file: {path.name} has no $var declarations and $enddefinitions"
        )
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


# ---------------------------------------------------------------------------
# Stage 4: event search and PNG rendering
# ---------------------------------------------------------------------------

import math
import re as _re

_COND_RE = _re.compile(r"^\s*(==|!=|>=|<=|>|<)\s*(\S+)\s*$")
_LITERAL_RE = _re.compile(r"^(\d*)'([bBoOdDhH])([0-9a-fA-FxXzZ_?]+)$")


class ConditionError(ValueError):
    pass


def parse_condition(condition: str) -> tuple[str, dict[str, Any]]:
    match = _COND_RE.match(condition or "")
    if not match:
        raise ConditionError(
            "condition must look like '== 4'b1111', '>= 5' or '!= 0' (no eval is used)"
        )
    op, token = match.group(1), match.group(2)
    token = token.replace("_", "")
    literal: dict[str, Any] = {"raw": token, "int": None, "bits": None, "width": None}

    sized = _LITERAL_RE.match(token)
    if sized:
        width_text, base, digits = sized.groups()
        literal["width"] = int(width_text) if width_text else None
        base = base.lower()
        if base == "b":
            literal["bits"] = digits.lower()
            if all(ch in "01" for ch in digits):
                literal["int"] = int(digits, 2)
        elif base == "h":
            if all(ch in "0123456789abcdef" for ch in digits.lower()):
                literal["int"] = int(digits, 16)
                literal["bits"] = format(literal["int"], "b").zfill(literal["width"] or 0)
        elif base == "o":
            if all(ch in "01234567" for ch in digits):
                literal["int"] = int(digits, 8)
        elif base == "d":
            if digits.isdigit():
                literal["int"] = int(digits, 10)
    elif token.isdigit():
        literal["int"] = int(token, 10)
    else:
        raise ConditionError(f"unsupported literal: {token}")
    return op, literal


def _compare(op: str, value: str, value_int: int | None, literal: dict[str, Any]) -> bool:
    if value_int is not None and literal["int"] is not None:
        left, right = value_int, literal["int"]
        return {
            "==": left == right,
            "!=": left != right,
            ">": left > right,
            ">=": left >= right,
            "<": left < right,
            "<=": left <= right,
        }[op]
    if op in {"==", "!="} and literal["bits"] is not None:
        equal = value.lower() == literal["bits"].lower()
        return equal if op == "==" else not equal
    return False


def find_events(
    data: VcdData,
    signal_query: str,
    condition: str,
    limit: int = 10,
) -> dict[str, Any]:
    """Find transitions into a condition, e.g. count == 4'b1111."""
    limit = max(1, min(int(limit), 1000))
    signal = match_signals(data, [signal_query])[0]
    op, literal = parse_condition(condition)
    history = data.changes.get(signal.identifier, [])

    events: list[dict[str, Any]] = []
    previous = False
    matched_total = 0
    for time, raw in history:
        value, value_int = _format_value(signal, raw)
        current = _compare(op, value, value_int, literal)
        if current and not previous:
            matched_total += 1
            if len(events) < limit:
                events.append({"time": time, "value": value, "value_int": value_int})
        previous = current

    return {
        "signal": signal.full_name,
        "condition": condition,
        "timescale": data.timescale,
        "event_count": matched_total,
        "events": events,
        "truncated": matched_total > len(events),
        "max_time": data.max_time,
    }


def render_waveform_png(
    data: VcdData,
    signal_names: list[str],
    out_path: Path,
    title: str | None = None,
) -> dict[str, Any]:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:  # pragma: no cover - depends on local venv
        raise RuntimeError(
            "matplotlib is not installed in this environment; run "
            "'python -m pip install matplotlib' inside the venv"
        ) from exc

    if not signal_names:
        raise ValueError("signals must not be empty")
    if len(signal_names) > 16:
        raise ValueError("at most 16 signals can be rendered at once")
    signals = match_signals(data, signal_names)

    fig, axes = plt.subplots(len(signals), 1, figsize=(10, 1.6 * len(signals) + 0.6), sharex=True)
    if len(signals) == 1:
        axes = [axes]

    for axis, signal in zip(axes, signals):
        times: list[int] = []
        values: list[float] = []
        for time, raw in data.changes.get(signal.identifier, []):
            _value, value_int = _format_value(signal, raw)
            times.append(time)
            values.append(float(value_int) if value_int is not None else math.nan)
        if times:
            # A step plot needs an endpoint: without it the final value is not
            # drawn past the last change (a reset that stays high looked missing).
            if data.max_time is not None and data.max_time > times[-1]:
                times.append(data.max_time)
                values.append(values[-1])
            axis.step(times, values, where="post", linewidth=1.2)
            axis.plot(times, values, "o", markersize=2)
        axis.set_ylabel(signal.full_name if len(signal.full_name) < 28 else signal.name, fontsize=8)
        axis.grid(True, alpha=0.3)
        axis.tick_params(labelsize=7)

    axes[-1].set_xlabel(f"time [{data.timescale}]", fontsize=8)
    fig.suptitle(title or "Robei MCP waveform", fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=120, format="png")
    plt.close(fig)

    return {
        "path": str(out_path),
        "signals": [s.full_name for s in signals],
        "size_bytes": out_path.stat().st_size,
        "max_time": data.max_time,
        "timescale": data.timescale,
    }
