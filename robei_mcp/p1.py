"""P1 features: Verilog -> canvas, connectivity checks, truth tables, waveform queries.

Kept out of ``tools.py`` so the subprocess/path plumbing there stays readable;
these functions reuse its helpers and never bypass the workspace policy.
"""
from __future__ import annotations

import statistics
from pathlib import Path
from typing import Any

from . import tools
from .config import SETTINGS
from .erc import check as erc_check
from .model import model_to_verilog, parse_robei_model
from .security import (
    is_within,
    resolve_project_path,
    resolve_source_files,
    resolve_vcd_path,
    resolve_workspace_path,
)
from .vcd import match_signals, parse_vcd, value_at
from .verilog import width_info
from .verilog_ast import is_plain_net, net_width, parse_module, parse_modules, port_map, width_of

REL = tools._rel


# ---------------------------------------------------------------------------
# Verilog -> canvas model
# ---------------------------------------------------------------------------
def _wire_list(module: dict[str, Any]) -> tuple[list[dict[str, str]], list[str]]:
    """Turn instance port connections into Robei ``<Wire>`` entries."""
    top_ports = {port["name"] for port in module["ports"]}
    endpoints: dict[str, list[str]] = {}
    skipped: list[str] = []
    for instance in module["instances"]:
        for connection in instance["connections"]:
            expr = connection["expr"].strip()
            ref = f"{instance['instance']}.{connection['port']}"
            if not is_plain_net(expr):
                skipped.append(f"{ref} <- {expr}")
                continue
            bucket = endpoints.setdefault(expr, [])
            if expr in top_ports and expr not in bucket:
                bucket.insert(0, expr)
            if ref not in bucket:
                bucket.append(ref)
    wires: list[dict[str, str]] = []
    for net, refs in endpoints.items():
        if len(refs) < 2:
            skipped.append(f"net {net} has only one endpoint ({refs[0] if refs else '-'})")
            continue
        base, others = refs[0], refs[1:]
        width = net_width(module, net) or 1
        for other in others:
            wires.append({"net": net, "from": base, "to": other, "datasize": str(width)})
    return wires, skipped


def import_verilog_canvas(
    proj_path: str,
    verilog_path: str,
    name: str | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Create a canvas-level .model from a structural Verilog module."""
    proj = resolve_project_path(proj_path)
    if not is_within(proj, SETTINGS.workspace):
        raise ValueError("refusing to write a project outside the workspace")
    source = resolve_workspace_path(verilog_path)
    text = source.read_text(encoding="utf-8", errors="replace")
    module = parse_module(text, name)
    model_dir = proj.parent / "model"
    available = sorted(path.stem for path in model_dir.glob("*.model"))
    used = {instance["module"] for instance in module["instances"]}
    missing = sorted(used - set(available))
    if missing:
        raise FileNotFoundError(
            f"instances reference modules without a .model file: {missing} (available: {available})"
        )
    if not module["instances"]:
        raise ValueError("module has no instance statements; nothing to draw on the canvas")

    for instance in module["instances"]:
        if not instance.get("positional"):
            continue
        target = parse_robei_model(model_dir / f"{instance['module']}.model")
        order = [port["name"] for port in target["ports"]]
        for index, connection in enumerate(instance["connections"]):
            if index < len(order):
                connection["port"] = order[index]
        instance["positional"] = False

    model_name = name or module["name"]
    ports = [
        {
            "name": port["name"],
            "direction": port["direction"],
            "width": width_of(port["width"]),
            "datatype": port["datatype"] or "wire",
        }
        for port in module["ports"]
    ]
    instances = [{"name": instance["instance"], "model": instance["module"]} for instance in module["instances"]]
    wires, skipped = _wire_list(module)
    if not wires:
        raise ValueError("no net connects two endpoints; the module has nothing to wire")

    result = tools.create_robei_canvas_model(
        proj_path=str(proj),
        name=model_name,
        ports=ports,
        instances=instances,
        wires=[{"net": w["net"], "from": w["from"], "to": w["to"], "datasize": w["datasize"]} for w in wires],
        comment=f"imported from {source.name}",
        overwrite=overwrite,
    )
    ignored_assigns = [f"assign {a['lhs']} = {a['rhs']};" for a in module["assigns"]]
    return {
        "ok": True,
        "model": model_name,
        "path": result["path"],
        "sha256": result["sha256"],
        "source": str(source),
        "source_relative": REL(source),
        "instances": result["instances"],
        "wires": result["wires"],
        "netlist": result["netlist"],
        "not_imported": {
            "assigns": ignored_assigns,
            "has_behaviour": module["has_behaviour"],
            "skipped_connections": skipped,
        },
        "note": "canvas models carry structure only; assign/always bodies must stay in a model's Code field",
    }


# ---------------------------------------------------------------------------
# Connectivity (ERC)
# ---------------------------------------------------------------------------
def check_connectivity(
    files: list[str] | None = None,
    top: str | None = None,
    proj_path: str | None = None,
) -> dict[str, Any]:
    """Static ERC over structural Verilog (and optionally a project's .model files)."""
    modules: list[dict[str, Any]] = []
    sources: list[str] = []
    if files:
        for path in resolve_source_files(files):
            modules.extend(parse_modules(path.read_text(encoding="utf-8", errors="replace")))
            sources.append(str(path))
    if proj_path:
        proj = resolve_project_path(proj_path)
        for model_file in sorted((proj.parent / "model").glob("*.model")):
            model = parse_robei_model(model_file)
            modules.extend(parse_modules(model_to_verilog(model)))
            sources.append(str(model_file))
    if not modules:
        raise ValueError("nothing to check: pass files and/or proj_path")
    if top and not any(module["name"] == top for module in modules):
        raise ValueError(f"module {top!r} not found in the checked sources")

    result = erc_check(modules)
    if top:
        scoped = [issue for issue in result["issues"] if issue["module"] == top]
        result["top"] = top
        result["top_issue_count"] = len(scoped)
    result["sources"] = sources
    result["note"] = "combinational loops are not analysed here; lint_verilog's yosys check is the cross-check"
    return result


# ---------------------------------------------------------------------------
# Truth table
# ---------------------------------------------------------------------------
def _bits(value: int, width: int) -> str:
    return format(value & ((1 << width) - 1), f"0{width}b")


def truth_table(
    verilog_path: str,
    top: str,
    max_inputs: int = 8,
    timeout_s: int = 30,
    deps: list[str] | None = None,
) -> dict[str, Any]:
    """Exhaustively simulate a combinational module and return its truth table."""
    source = resolve_workspace_path(verilog_path)
    module = parse_module(source.read_text(encoding="utf-8", errors="replace"), top)
    inputs = [port for port in module["ports"] if port["direction"] == "input"]
    outputs = [port for port in module["ports"] if port["direction"] == "output"]
    if not inputs or not outputs:
        raise ValueError("truth_table needs at least one input and one output port")
    if module["has_behaviour"] and "always" in source.read_text(encoding="utf-8", errors="replace"):
        pass  # sequential designs simply enumerate slower; not an error
    def _concrete_widths(ports: list[dict[str, Any]]) -> dict[str, int]:
        widths: dict[str, int] = {}
        unresolved: list[str] = []
        for port in ports:
            info = width_info(port["width"])
            if info["width"] is None:
                unresolved.append(f"{port['name']}[{info['expression']}]")
            else:
                widths[port["name"]] = int(info["width"])
        if unresolved:
            raise ValueError(
                "cannot enumerate a parameterised width: "
                + ", ".join(unresolved)
                + "; export the model with concrete parameter values first"
            )
        return widths

    widths = _concrete_widths(inputs)
    output_widths = _concrete_widths(outputs)
    total_bits = sum(widths.values())
    if total_bits > max_inputs:
        raise ValueError(
            f"too many input bits ({total_bits} over {len(inputs)} port(s) > max_inputs={max_inputs})"
        )
    lines = ["`timescale 1ns/1ps", f"// generated by robei-mcp truth_table for {top}", f"module {top}_tb;"]
    for port in inputs:
        lines.append(f"    reg [{widths[port['name']] - 1}:0] {port['name']};")
    for port in outputs:
        width = output_widths[port["name"]]
        lines.append(f"    wire [{width - 1}:0] {port['name']};")
    lines.append("    " + top + " dut (")
    lines.append(",\n".join(f"        .{port['name']}({port['name']})" for port in module["ports"]))
    lines.append("    );")
    lines.append("    initial begin")
    lines.append(f'        $dumpfile("{top}_tb.vcd");')
    lines.append(f"        $dumpvars(0, {top}_tb);")
    order = [port["name"] for port in inputs]
    total = 1 << total_bits
    # Pack the enumeration index across the *bits* of every input, last port
    # in the lowest bits.  Using the port count here was wrong as soon as the
    # ports had different widths.
    offsets: dict[str, int] = {}
    shift = 0
    for name in reversed(order):
        offsets[name] = shift
        shift += widths[name]
    for index in range(total):
        assignments = []
        for name in order:
            bit = (index >> offsets[name]) & ((1 << widths[name]) - 1)
            assignments.append(f"{name} = {widths[name]}'b{_bits(bit, widths[name])};")
        lines.append("        " + " ".join(assignments) + " #10;")
        fmt = " ".join(f"{name}=%b" for name in order + [p["name"] for p in outputs])
        args = ", ".join(order + [p["name"] for p in outputs])
        lines.append(f'        $display("TT {fmt}", {args});')
    lines.append("        $finish;")
    lines.append("    end")
    lines.append("endmodule")

    # Per-run directory so concurrent truth tables for the same top cannot
    # overwrite each other's generated testbench.
    out_dir = tools._new_run_dir(f"truth-{top}")
    tb_path = out_dir / f"{top}_tb.v"
    tb_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")

    sim_files = [str(source), str(tb_path)]
    if deps:
        sim_files.extend(str(path) for path in resolve_source_files(deps))
    result = tools.simulate(files=sim_files, top=f"{top}_tb", timeout_s=timeout_s)
    rows: list[dict[str, str]] = []
    for line in (result.get("display_output") or "").splitlines():
        line = line.strip()
        if not line.startswith("TT "):
            continue
        row = {}
        for token in line[3:].split():
            name, _, value = token.partition("=")
            row[name] = value
        if row:
            rows.append(row)
    return {
        "ok": bool(result.get("ok")) and len(rows) == total,
        "top": top,
        "inputs": [{"name": p["name"], "width": widths[p["name"]]} for p in inputs],
        "outputs": [{"name": p["name"], "width": output_widths[p["name"]]} for p in outputs],
        "expected_rows": total,
        "row_count": len(rows),
        "rows": rows,
        "testbench": str(tb_path),
        "vcd_paths": result.get("vcd_paths") or [],
        "simulate": {k: result.get(k) for k in ("ok", "stage", "code", "compile")},
    }


# ---------------------------------------------------------------------------
# Waveform assertions and statistics
# ---------------------------------------------------------------------------
def _norm(value: Any, width: int) -> str:
    text = str(value).strip().replace("_", "").lower()
    if "'" in text:
        _, _, digits = text.partition("'")
        digits = digits[1:] if digits and digits[0] in "bodh" else digits
        text = digits
    try:
        return format(int(text, 2) & ((1 << width) - 1), f"0{width}b")
    except ValueError:
        try:
            return format(int(text, 16 if any(c in "abcdef" for c in text) else 10) & ((1 << width) - 1), f"0{width}b")
        except ValueError:
            return text


def _value_at(data, signal, time: int) -> str:
    return value_at(data, signal, time)["value"]


def expect_waveform(vcd_path: str, checks: list[dict[str, Any]]) -> dict[str, Any]:
    """Assert signal values at a time, or held across an interval."""
    path = resolve_vcd_path(vcd_path)
    data = parse_vcd(path)
    results: list[dict[str, Any]] = []
    for check in checks:
        name = str(check.get("signal") or "").strip()
        try:
            selected = match_signals(data, [name]) if name else []
        except KeyError as exc:
            results.append({"ok": False, "signal": name, "error": str(exc).strip("'\"")})
            continue
        if not selected:
            results.append({"ok": False, "signal": name, "error": "unknown signal"})
            continue
        signal = selected[0]
        want = _norm(check.get("value", ""), signal.width)
        if "at" in check and check["at"] is not None:
            actual = _value_at(data, signal, int(check["at"]))
            results.append(
                {
                    "ok": actual == want,
                    "signal": signal.name,
                    "at": int(check["at"]),
                    "expected": want,
                    "actual": actual,
                }
            )
            continue
        if "from" in check and "to" in check:
            start, end = int(check["from"]), int(check["to"])
            actual_start = _value_at(data, signal, start)
            changes = data.changes.get(signal.identifier, [])
            moved = [t for t, _ in changes if start < t <= end]
            results.append(
                {
                    "ok": actual_start == want and not moved,
                    "signal": signal.name,
                    "from": start,
                    "to": end,
                    "expected": want,
                    "actual": actual_start,
                    "changes_in_window": moved[:10],
                }
            )
            continue
        results.append({"ok": False, "signal": name, "error": "check needs 'at' or 'from'+'to'"})
    passed = sum(1 for item in results if item.get("ok"))
    return {
        "ok": passed == len(results) and bool(results),
        "passed": passed,
        "failed": len(results) - passed,
        "results": results,
        "vcd_path": str(path),
    }


def _clock_candidates(data) -> list[dict[str, Any]]:
    clocks: list[dict[str, Any]] = []
    for name, signal in data.signals.items():
        changes = data.changes.get(signal.identifier, [])
        if len(changes) < 6:
            continue
        deltas = [b - a for (a, _), (b, _) in zip(changes, changes[1:]) if b > a]
        if len(deltas) < 4:
            continue
        if max(deltas) - min(deltas) <= 1:
            # edges alternate 0->1 / 1->0, so the full period is two edge gaps
            clocks.append({"signal": name, "period": 2 * int(statistics.median(deltas)), "edges": len(changes)})
    return clocks


def waveform_stats(vcd_path: str, max_signals: int = 64) -> dict[str, Any]:
    """Toggle counts, level coverage and a clock estimate from a VCD."""
    path = resolve_vcd_path(vcd_path)
    data = parse_vcd(path)
    rows: list[dict[str, Any]] = []
    one_bit = 0
    both_levels = 0
    for name, signal in list(data.signals.items())[:max_signals]:
        changes = data.changes.get(signal.identifier, [])
        values = []
        for _, raw in changes:
            text = (raw or "").lower()
            if text not in values:
                values.append(text)
        zeros = "0" in values
        ones = "1" in values
        if signal.width == 1:
            one_bit += 1
            if zeros and ones:
                both_levels += 1
        rows.append(
            {
                "signal": name,
                "width": signal.width,
                "type": signal.var_type,
                "changes": len(changes),
                "first_change": changes[0][0] if changes else None,
                "last_change": changes[-1][0] if changes else None,
                "distinct_values": len(values),
                "saw_zero": zeros,
                "saw_one": ones,
                "has_x": any("x" in value for value in values),
                "has_z": any("z" in value for value in values),
            }
        )
    clocks = sorted(_clock_candidates(data), key=lambda item: item["period"])
    clock = clocks[0] if clocks else None
    if clock:
        clock = {**clock, "frequency_mhz": round(1000.0 / clock["period"], 3) if clock["period"] else None}
    return {
        "ok": True,
        "vcd_path": str(path),
        "timescale": data.timescale,
        "duration": data.max_time,
        "signal_count": len(data.signals),
        "coverage": {
            "one_bit_signals": one_bit,
            "toggled_both_levels": both_levels,
            "ratio": round(both_levels / one_bit, 3) if one_bit else None,
        },
        "clock": clock,
        "signals": rows,
        "truncated": len(data.signals) > max_signals,
    }
