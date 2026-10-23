"""Teaching-grade connectivity checks (ERC) for structural Verilog.

Rules: multiple drivers, undriven nets, width mismatches, unknown modules and
unconnected instance ports. Combinational loops are *not* analysed here — that
needs elaboration semantics; the Yosys ``check`` stage of ``lint_verilog`` is the
cross-check for that class of bug.
"""
from __future__ import annotations

from typing import Any

from .verilog_ast import is_plain_net, net_width, port_map, width_of


def _add(issues: list[dict[str, Any]], rule: str, module: str, message: str, **extra: Any) -> None:
    issues.append({"rule": rule, "module": module, "message": message, **extra})


def check_module(module: dict[str, Any], known: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    name = module["name"]
    ports = port_map(module)
    drivers: dict[str, list[dict[str, Any]]] = {}
    consumers: dict[str, list[dict[str, Any]]] = {}

    def drive(net: str, who: dict[str, Any]) -> None:
        drivers.setdefault(net, []).append(who)

    def consume(net: str, who: dict[str, Any]) -> None:
        consumers.setdefault(net, []).append(who)

    for port in module["ports"]:
        if port["direction"] == "input":
            drive(port["name"], {"kind": "port", "name": port["name"]})
        elif port["direction"] == "output":
            consume(port["name"], {"kind": "port", "name": port["name"]})

    for assign in module["assigns"]:
        if is_plain_net(assign["lhs"]):
            drive(assign["lhs"].strip(), {"kind": "assign", "name": assign["lhs"].strip()})
        for token in _rhs_nets(assign["rhs"]):
            consume(token, {"kind": "assign", "name": token})

    for instance in module["instances"]:
        target = known.get(instance["module"])
        target_ports = port_map(target) if target else {}
        if target and instance.get("positional"):
            order = [port["name"] for port in target["ports"]]
            for index, connection in enumerate(instance["connections"]):
                if index < len(order):
                    connection["port"] = order[index]
            instance["positional"] = False
        seen = set()
        for connection in instance["connections"]:
            port_name, expr = connection["port"], connection["expr"].strip()
            seen.add(port_name)
            port = target_ports.get(port_name)
            if target and port is None:
                _add(issues, "unknown_port", name,
                     f"{instance['instance']}.{port_name}: module {instance['module']} has no such port",
                     instance=instance["instance"], port=port_name)
            if not is_plain_net(expr):
                continue  # constants / concatenations: out of scope for this pass
            if port and port["direction"] == "input":
                consume(expr, {"kind": "instance", "name": instance["instance"]})
            elif port and port["direction"] == "output":
                drive(expr, {"kind": "instance", "name": instance["instance"]})
            if port:
                have = net_width(module, expr)
                want = width_of(port["width"])
                if have is not None and have != want:
                    _add(issues, "width_mismatch", name,
                         f"net {expr} is {have} bit(s) but {instance['instance']}.{port_name} needs {want}",
                         instance=instance["instance"], port=port_name, net=expr,
                         net_width=have, port_width=want)
        if target:
            missing = [p["name"] for p in target["ports"] if p["name"] not in seen]
            for port_name in missing:
                _add(issues, "unconnected_port", name,
                     f"{instance['instance']}.{port_name} is not connected",
                     instance=instance["instance"], port=port_name)

    for net, who in drivers.items():
        if len(who) > 1:
            _add(issues, "multi_driver", name,
                 f"net {net} has {len(who)} drivers: " + ", ".join(f"{w['kind']}:{w['name']}" for w in who),
                 net=net, drivers=who)
    for net, who in consumers.items():
        if net not in drivers:
            _add(issues, "undriven_net", name,
                 f"net {net} is read but never driven: " + ", ".join(f"{w['kind']}:{w['name']}" for w in who),
                 net=net, consumers=who)

    for instance in module["instances"]:
        if instance["module"] not in known:
            _add(issues, "unknown_module", name,
                 f"instance {instance['instance']} references module {instance['module']}, which is not in the checked sources",
                 instance=instance["instance"], missing=instance["module"])
    return issues


def _rhs_nets(expr: str) -> list[str]:
    import re

    return [tok for tok in re.findall(r"[A-Za-z_][A-Za-z0-9_$]*", expr) if tok not in {"b", "h", "d", "o"}]


def check(modules: list[dict[str, Any]]) -> dict[str, Any]:
    known = {module["name"]: module for module in modules}
    issues: list[dict[str, Any]] = []
    for module in modules:
        issues.extend(check_module(module, known))
    counts: dict[str, int] = {}
    for issue in issues:
        counts[issue["rule"]] = counts.get(issue["rule"], 0) + 1
    return {
        "ok": not issues,
        "modules": [module["name"] for module in modules],
        "issue_count": len(issues),
        "counts": counts,
        "issues": issues,
    }
