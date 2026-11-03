"""File-only semantic TB connections and output references (no simulation or writes)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Callable

from .config import SETTINGS
from .errors import BadRequest, PathEscape
from .model import parse_robei_model
from .security import is_within, resolve_model_path, resolve_workspace_path
from .tb_expr import MAX_WIDTH, reject_signed_ports
from .verilog import robei_width
from .verilog_ast import KEYWORDS

MAX_DUTS = 8
MAX_CONNECTIONS = 128
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")
_RESERVED = {"fail", "vector", "assertion_count"}


def parameter_overrides(value: Any, declared: list[dict], field: str = "parameters") -> dict[str, int]:
    """Only explicit scalar cases: never expand arrays into a Cartesian product."""
    if value is None:
        return {}
    if not isinstance(value, dict) or len(value) > 32:
        raise bad(field, "must be an object with at most 32 integer parameters")
    names = {p["name"] for p in declared}
    for name, number in value.items():
        if not isinstance(name, str) or name not in names:
            raise bad(field, f"unknown parameter {name!r}; declared: {sorted(names)}")
        if isinstance(number, bool) or not isinstance(number, int) or not -(2**31) <= number < 2**31:
            raise bad(field + "." + name, "must be a signed 32-bit integer; arrays/Cartesian products are not supported")
    return dict(value)


def bad(field: str, reason: str, hint: str = "check the declared DUT ports and connections"):
    return BadRequest(f"{field}: {reason}", hint, code="bad_argument", field=field,
                      offset=0, reason=reason)


def identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _IDENT.fullmatch(value) or value in KEYWORDS:
        raise bad(field, "not a legal non-keyword Verilog identifier")
    if value in _RESERVED or value.startswith("__robei_"):
        raise bad(field, "name is reserved by the semantic testbench")
    return value


@dataclass(frozen=True)
class Target:
    text: str
    width: int
    members: tuple[str, ...]
    slot: str


class Context:
    def __init__(self, proj: Path, model_path: str | None, duts: list[dict] | None,
                 comparisons: list[dict] | None, *,
                 width_resolver: Callable[[dict, dict], int | None] | None = None,
                 parameters: dict[str, int] | None = None):
        self.single = duts is None
        if (model_path is None) == (duts is None):
            raise bad("duts", "pass exactly one of model_path or duts")
        if duts is None:
            duts = [{"instance": "dut", "model_path": model_path}]
        if not isinstance(duts, list) or not duts:
            raise bad("duts", "must be a non-empty list")
        if len(duts) > MAX_DUTS:
            raise BadRequest(f"DUT count exceeds {MAX_DUTS}", "split the test", code="limit_exceeded")
        self.duts: list[dict[str, Any]] = []
        self.widths: dict[str, int] = {}
        self.outputs: dict[str, Target] = {}
        self._targets: dict[str, Target] = {}
        drivers: dict[str, str] = {}
        sinks: set[str] = set()
        instances: set[str] = set()
        connection_count = 0
        for index, spec in enumerate(duts):
            field = "model_path" if self.single else f"duts[{index}]"
            if not isinstance(spec, dict):
                raise bad(field, "must be an object")
            instance = identifier(spec.get("instance"), field + ".instance")
            if instance in instances:
                raise bad(field + ".instance", "duplicate instance name")
            instances.add(instance)
            value = spec.get("model_path")
            if not isinstance(value, str) or not value:
                raise bad(field, "model_path must be a non-empty string")
            candidate = resolve_workspace_path(value, must_exist=False)
            # Reject the scope before checking existence: an outside missing
            # file is still a path escape, not an invitation to read elsewhere.
            if not is_within(candidate, proj.parent / "model"):
                raise PathEscape("model_path must stay in the selected project's model/ directory")
            path = resolve_model_path(value)
            reject_signed_ports(path, field + ".model_path" if not self.single else field)
            model = parse_robei_model(path)
            declared = model.get("parameter_list") or []
            overrides = parameter_overrides(spec.get("parameters"), declared, field + ".parameters")
            overrides.update(parameter_overrides(parameters, declared))
            model = {**model, "parameter_list": [
                {**p, "default": str(overrides[p["name"]])} if p["name"] in overrides else p
                for p in declared
            ]}
            module = identifier(model["name"], field + ".module")
            ports = model.get("ports") or []
            if not ports:
                raise bad(field, "DUT has no ports")
            connection_count += len(ports)
            if connection_count > MAX_CONNECTIONS:
                raise BadRequest(f"connection count exceeds {MAX_CONNECTIONS}", "split the DUTs/test", code="limit_exceeded")
            names = [identifier(p["name"], field + ".ports") for p in ports]
            if len(names) != len(set(names)):
                raise bad(field + ".ports", "duplicate port names")
            connections = {name: name for name in names} if self.single else spec.get("connections")
            if not isinstance(connections, dict) or set(connections) != set(names):
                raise bad(field + ".connections", "every DUT port must be connected exactly once; unknown ports are rejected")
            for port in ports:
                name = port["name"]
                net = identifier(connections[name], field + f".connections.{name}")
                width = robei_width(port.get("datasize"))
                if width is None and width_resolver is not None:
                    width = width_resolver(model, port)
                if width is None:
                    raise BadRequest(f"cannot resolve parameterised width for port {name!r}",
                                     "use a model with concrete port widths")
                if width > MAX_WIDTH:
                    raise BadRequest(f"port width exceeds {MAX_WIDTH}", "reduce the width", code="limit_exceeded")
                direction = str(port.get("inout") or "input").lower()
                if direction not in {"input", "output"}:
                    raise bad(field + f".ports.{name}", "inout ports are not supported")
                if net in self.widths and self.widths[net] != width:
                    raise bad(field + f".connections.{name}", f"net {net!r} has inconsistent widths")
                self.widths[net] = width
                ref = name if self.single else f"{instance}.{name}"
                if direction == "output":
                    if net in drivers:
                        raise bad(field + f".connections.{name}", f"output net {net!r} has multiple drivers")
                    drivers[net] = ref
                    self.outputs[ref] = Target(net, width, (ref,), name if self.single else f"out{len(self.outputs)}")
                else:
                    sinks.add(net)
            self.duts.append({"instance": instance, "model": module, "model_path": str(path),
                              "connections": dict(connections),
                              **({"parameters": overrides} if overrides else {})})
        if instances.intersection(self.widths):
            raise bad("connections", "a TB net must not have the same name as a DUT instance")
        self.inputs = {net: self.widths[net] for net in self.widths if net in sinks and net not in drivers}
        self.output_nets = list(drivers)
        self.input_bits = sum(self.inputs.values())
        if not self.outputs:
            raise bad("duts", "DUTs must expose at least one output to check")
        if comparisons is not None and not isinstance(comparisons, list):
            raise bad("comparisons", "must be a list")
        if len(comparisons or []) > MAX_CONNECTIONS:
            raise BadRequest("too many comparisons", "split the test", code="limit_exceeded")
        self.comparisons: list[tuple[Target, Target]] = []
        for index, pair in enumerate(comparisons or []):
            field = f"comparisons[{index}]"
            if not isinstance(pair, dict):
                raise bad(field, "must have left and right output references")
            left = self.output(pair.get("left"), field + ".left")
            right = self.output(pair.get("right"), field + ".right")
            if left.width != right.width:
                raise bad(field, "compared outputs must have equal widths")
            self.comparisons.append((left, right))

    def output(self, ref: Any, field: str) -> Target:
        if not isinstance(ref, str) or ref not in self.outputs:
            raise bad(field, f"unknown output reference {ref!r}",
                      "use instance.port from a declared DUT (or the original single-DUT port name)")
        return self.outputs[ref]

    def target(self, key: str, field: str) -> Target:
        if key in self._targets:
            return self._targets[key]
        if key in self.outputs:
            target = self.outputs[key]
        elif isinstance(key, str) and key.startswith("{") and key.endswith("}"):
            # Output targets allow only flat, complete-port concatenations;
            # expected-value expressions use the separate Pratt parser.
            refs = [ref.strip() for ref in key[1:-1].split(",")]
            parts = [self.output(ref, field) for ref in refs]
            width = sum(part.width for part in parts)
            if width > MAX_WIDTH:
                raise bad(field, "concatenated output target is too wide")
            target = Target("{" + ", ".join(p.text for p in parts) + "}", width,
                            tuple(refs), f"__robei_concat{len(self._targets)}")
        else:
            return self.output(key, field)
        self._targets[key] = target
        return target

    def targets(self, expect: dict, field: str, require_all: bool = False) -> dict[str, Target]:
        result = {}
        seen: set[str] = set()
        for key in expect:
            if not isinstance(key, str):
                raise bad(field, "expected output keys must be strings")
            target = self.target(key, field + "." + key)
            for ref in target.members:
                if ref in seen:
                    raise bad(field, f"output {ref!r} is checked more than once")
                seen.add(ref)
            result[key] = target
        if require_all and seen != set(self.outputs):
            raise bad(field, "provide an expected value for every DUT output")
        if not result and not self.comparisons:
            raise bad(field, "at least one output expectation or comparison is required")
        return result

    def declarations(self) -> list[str]:
        return [f"{'reg' if net in self.inputs else 'wire'}" +
                (f" [{width-1}:0]" if width != 1 else "") + f" {net};"
                for net, width in self.widths.items()]

    def instances(self) -> list[str]:
        lines = []
        for dut in self.duts:
            parameters = dut.get("parameters") or {}
            override = " #(" + ", ".join(f".{key}({value})" for key, value in parameters.items()) + ")" if parameters else ""
            lines.extend([f"{dut['model']}{override} {dut['instance']}(",
                          ",\n".join(f"  .{port}({net})" for port, net in dut["connections"].items()), ");"])
        return lines

    def mismatches(self) -> list[str]:
        return [f"({left.text} !== {right.text})" for left, right in self.comparisons]
