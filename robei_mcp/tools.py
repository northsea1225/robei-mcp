from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
from typing import Any

from .config import SETTINGS, file_version
from .model import _attr_ci, generic_node, model_to_verilog, parse_robei_model
from .security import (
    resolve_model_path,
    resolve_project_path,
    resolve_source_files,
    resolve_vcd_path,
    resolve_workspace_path,
    scan_source_paths,
)
from .vcd import find_events as vcd_find_events
from .vcd import parse_vcd, query_vcd, render_waveform_png
from .verilog import generate_testbench as build_testbench
from .verilog import parse_module_ports, classify_control

TOP_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
FILE_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def _clip(text: str, limit: int | None = None) -> str:
    limit = limit or SETTINGS.max_output_chars
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated, {len(text) - limit} chars omitted]"


def _decode(data: bytes | None) -> str:
    if not data:
        return ""
    for encoding in ("utf-8-sig", "utf-8", "gbk", "cp936"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(
    args: list[str | Path],
    cwd: Path,
    timeout_s: int,
    env_extra: dict[str, str] | None = None,
) -> dict[str, Any]:
    timeout_s = max(1, min(int(timeout_s), SETTINGS.max_timeout_s))
    env = os.environ.copy()
    # Keep tool temporary files under the selected working directory as well.
    tmp_dir = cwd / ".tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    env["TMP"] = str(tmp_dir)
    env["TEMP"] = str(tmp_dir)
    # Icarus on this installation looks for its support files below E:\Robei.
    env.setdefault("IVERILOG_ROOT", str(SETTINGS.robei_home))
    if env_extra:
        env.update(env_extra)
    cmd = [str(arg) for arg in args]
    started = time.monotonic()
    try:
        completed = subprocess.run(
            cmd,
            cwd=str(cwd),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_s,
            shell=False,
            env=env,
        )
        stdout = _decode(completed.stdout)
        stderr = _decode(completed.stderr)
        return {
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "duration_ms": round((time.monotonic() - started) * 1000),
            "command": cmd,
            "stdout": _clip(stdout),
            "stderr": _clip(stderr),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "returncode": None,
            "duration_ms": round((time.monotonic() - started) * 1000),
            "command": cmd,
            "stdout": _clip(_decode(exc.stdout)),
            "stderr": _clip(_decode(exc.stderr)) + f"\nTIMEOUT after {timeout_s}s",
            "timeout": True,
        }
    except FileNotFoundError as exc:
        return {
            "ok": False,
            "returncode": None,
            "duration_ms": round((time.monotonic() - started) * 1000),
            "command": cmd,
            "stdout": "",
            "stderr": f"executable not found: {exc}",
        }


def _require_file(path: Path, label: str) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"{label} not found: {path}")


def _new_run_dir(kind: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = SETTINGS.workspace / "build" / f"{kind}-{stamp}-{uuid.uuid4().hex[:8]}"
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def _rel(path: Path) -> str:
    try:
        return path.relative_to(SETTINGS.workspace).as_posix()
    except ValueError:
        return path.as_posix()


def _tool_version(path: Path, flag: str = "-V", label: str = "") -> dict[str, Any]:
    info: dict[str, Any] = {
        "path": str(path),
        "exists": path.is_file(),
        "version": None,
    }
    if not path.is_file():
        return info
    env_extra = None
    if label == "verilator":
        env_extra = {"VERILATOR_ROOT": str(SETTINGS.verilator_root)}
    result = _run([path, flag], cwd=SETTINGS.workspace, timeout_s=10, env_extra=env_extra)
    text = (result.get("stdout") or "") + (result.get("stderr") or "")
    match = re.search(r"\bversion\s+([0-9][^\s]*)", text, re.IGNORECASE)
    if match:
        info["version"] = match.group(1)
    elif label == "verilator":
        match = re.search(r"Verilator\s+([0-9][^\s]*)", text)
        if match:
            info["version"] = match.group(1)
    info["returncode"] = result.get("returncode")
    nonempty = [line.strip() for line in text.splitlines() if line.strip()]
    # Keep only the version banner line; do not return bundled license text.
    info["output"] = _clip(nonempty[0] if nonempty else "", 600)
    if label == "yosys" and not info["version"]:
        info["version_note"] = (
            "this vendor build prints 'Synthesis complete' for -V; "
            "executable is present and synthesis checks still work"
        )
    return info


def runtime_status() -> dict[str, Any]:
    verilator = _tool_version(SETTINGS.verilator, "--version", "verilator")
    verilator["root"] = str(SETTINGS.verilator_root)
    verilator["root_exists"] = SETTINGS.verilator_root.is_dir()
    tools = {
        "robei": {
            "path": str(SETTINGS.robei_exe),
            "exists": SETTINGS.robei_exe.is_file(),
            "version": file_version(SETTINGS.robei_exe),
        },
        "iverilog": _tool_version(SETTINGS.iverilog, "-V", "iverilog"),
        "vvp": _tool_version(SETTINGS.vvp, "-V", "vvp"),
        "yosys": _tool_version(SETTINGS.yosys, "-V", "yosys"),
        "verilator": verilator,
    }
    try:
        mcp_version = package_version("mcp")
    except PackageNotFoundError:
        mcp_version = None
    required = ("robei", "iverilog", "vvp", "yosys")
    return {
        "ok": all(tools[name]["exists"] for name in required),
        "verilator_available": verilator["exists"] and verilator["root_exists"],
        "mcp_version": mcp_version,
        "python_executable": sys.executable,
        "python_version": sys.version.split()[0],
        "workspace": str(SETTINGS.workspace),
        "model_roots": [str(p) for p in SETTINGS.model_roots],
        "tools": tools,
        "limits": {
            "default_timeout_s": SETTINGS.default_timeout_s,
            "max_timeout_s": SETTINGS.max_timeout_s,
            "max_source_bytes": SETTINGS.max_source_bytes,
            "max_vcd_bytes": SETTINGS.max_vcd_bytes,
        },
    }


def _valid_top(top: str | None) -> str | None:
    if top is None:
        return None
    top = top.strip()
    if not TOP_RE.match(top):
        raise ValueError("top must be a simple Verilog identifier")
    return top


def _quote_yosys(path: Path) -> str:
    return '"' + path.as_posix().replace('"', '\\"') + '"'


def _yosys_summary(text: str) -> list[str]:
    keys = (
        "Top module:",
        "Number of cells:",
        "Number of wires:",
        "Number of port",
        "Found and reported",
        "Warning:",
        "ERROR:",
        "Synthesis complete",
        "Removed",
    )
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and any(key.lower() in stripped.lower() for key in keys):
            lines.append(stripped)
    return lines[:40]


_VERILATOR_WARN_RE = re.compile(r"%Warning-([A-Za-z0-9_]+):\s*(.+)")
_VERILATOR_ERROR_RE = re.compile(r"%Error(?:-([A-Za-z0-9_]+))?:\s*(.+)")


def _parse_verilator(text: str) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    warnings: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    for line in text.splitlines():
        line = line.strip()
        warn = _VERILATOR_WARN_RE.search(line)
        if warn:
            warnings.append({"code": f"Warning-{warn.group(1)}", "text": warn.group(2).strip()})
            continue
        err = _VERILATOR_ERROR_RE.search(line)
        if err:
            errors.append({"code": f"Error-{err.group(1)}" if err.group(1) else "Error", "text": err.group(2).strip()})
    return warnings, errors


def _verilator_lint(sources: list[Path], top: str | None, timeout_s: int, run_dir: Path) -> dict[str, Any]:
    info: dict[str, Any] = {
        "available": SETTINGS.verilator.is_file(),
        "skipped": not SETTINGS.verilator.is_file(),
        "path": str(SETTINGS.verilator),
    }
    if not SETTINGS.verilator.is_file():
        info["skip_reason"] = "verilator executable not found; install it or set VERILATOR_EXE"
        return info
    if not SETTINGS.verilator_root.is_dir():
        info["skip_reason"] = f"VERILATOR_ROOT not found: {SETTINGS.verilator_root}"
        info["skipped"] = True
        return info

    cmd: list[str | Path] = [
        SETTINGS.verilator,
        "--lint-only",
        "-Wall",
        "-Wno-fatal",
        "-Wno-DECLFILENAME",
    ]
    if top:
        cmd.extend(["--top-module", top])
    cmd.extend(sources)
    result = _run(cmd, cwd=run_dir, timeout_s=timeout_s, env_extra={"VERILATOR_ROOT": str(SETTINGS.verilator_root)})
    text = (result.get("stdout") or "") + (result.get("stderr") or "")
    warnings, errors = _parse_verilator(text)
    info.update(
        {
            "ok": bool(result["ok"] and not errors),
            "returncode": result["returncode"],
            "duration_ms": result["duration_ms"],
            "command": result["command"],
            "warning_count": len(warnings),
            "warnings": warnings[:50],
            "error_count": len(errors),
            "errors": errors[:50],
            "stdout": _clip(result.get("stdout") or "", 6000),
            "stderr": _clip(result.get("stderr") or "", 4000),
        }
    )
    return info


def _json_nets(module: dict[str, Any]) -> dict[str, Any]:
    """Yosys JSON changed the nets key across versions; accept both."""
    for key in ("netnames", "wires"):
        value = module.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _yosys_unused_check(
    sources: list[Path],
    top: str | None,
    timeout_s: int,
    run_dir: Path,
) -> dict[str, Any]:
    before = run_dir / "yosys_before.json"
    after = run_dir / "yosys_after.json"
    read_commands = []
    for source in sources:
        flag = "-sv " if source.suffix.lower() in {".sv", ".svh"} else ""
        read_commands.append(f"read_verilog {flag}{_quote_yosys(source)}")
    read_commands.append(f"hierarchy -check -top {top}" if top else "hierarchy -check -auto-top")
    script = "; ".join(
        read_commands
        + [
            "proc",
            "check",
            f"write_json {_quote_yosys(before)}",
            "opt_clean",
            f"write_json {_quote_yosys(after)}",
        ]
    )
    result = _run([SETTINGS.yosys, "-p", script], cwd=run_dir, timeout_s=timeout_s)
    text = (result.get("stdout") or "") + (result.get("stderr") or "")

    unused: list[dict[str, Any]] = []
    parse_error = None
    try:
        before_data = json.loads(before.read_text(encoding="utf-8"))
        after_data = json.loads(after.read_text(encoding="utf-8"))
        for module_name, module in (before_data.get("modules") or {}).items():
            nets_before = _json_nets(module)
            nets_after = _json_nets((after_data.get("modules") or {}).get(module_name) or {})
            ports = set((module.get("ports") or {}).keys())
            for name in sorted(set(nets_before) - set(nets_after)):
                clean = name.lstrip("\\")
                if clean in ports or clean.startswith("$"):
                    continue
                attributes = nets_before[name].get("attributes") or {}
                unused.append(
                    {
                        "module": module_name.lstrip("\\"),
                        "name": clean,
                        "src": attributes.get("src", ""),
                    }
                )
    except (OSError, json.JSONDecodeError) as exc:
        parse_error = f"{type(exc).__name__}: {exc}"

    return {
        "ok": bool(result["ok"]),
        "returncode": result["returncode"],
        "duration_ms": result["duration_ms"],
        "command": result["command"],
        "script": script,
        "unused_signals": unused,
        "unused_count": len(unused),
        "parse_error": parse_error,
        "summary": _yosys_summary(text),
        "stdout": _clip(result.get("stdout") or "", 6000),
        "stderr": _clip(result.get("stderr") or "", 4000),
    }


def lint_verilog(files: list[str], top: str | None = None, timeout_s: int = 30) -> dict[str, Any]:
    sources = resolve_source_files(files)
    top = _valid_top(top)
    run_dir = _new_run_dir("lint")
    iverilog_out = run_dir / "lint.out"

    iverilog_cmd: list[str | Path] = [
        SETTINGS.iverilog,
        "-Wall",
        "-g2012",
        "-o",
        iverilog_out,
    ]
    if top:
        iverilog_cmd.extend(["-s", top])
    iverilog_cmd.extend(sources)
    iverilog_result = _run(iverilog_cmd, cwd=run_dir, timeout_s=timeout_s)

    read_commands: list[str] = []
    for source in sources:
        if source.suffix.lower() in {".sv", ".svh"}:
            read_commands.append(f"read_verilog -sv {_quote_yosys(source)}")
        else:
            read_commands.append(f"read_verilog {_quote_yosys(source)}")
    read_commands.append(f"synth -top {top}" if top else "synth")
    yosys_script = "; ".join(read_commands)
    yosys_result = _run([SETTINGS.yosys, "-p", yosys_script], cwd=run_dir, timeout_s=timeout_s)

    verilator_result = _verilator_lint(sources, top, timeout_s, run_dir)
    yosys_check = _yosys_unused_check(sources, top, timeout_s, run_dir)

    iverilog_text = (iverilog_result.get("stdout") or "") + (iverilog_result.get("stderr") or "")
    warnings = [
        line.strip()
        for line in iverilog_text.splitlines()
        if "warning" in line.lower()
    ]
    all_unused = yosys_check["unused_signals"]
    return {
        "ok": bool(iverilog_result["ok"] and yosys_result["ok"] and verilator_result.get("ok", True)),
        "run_dir": _rel(run_dir),
        "iverilog": {
            "ok": iverilog_result["ok"],
            "returncode": iverilog_result["returncode"],
            "duration_ms": iverilog_result["duration_ms"],
            "command": iverilog_result["command"],
            "stdout": iverilog_result["stdout"],
            "stderr": iverilog_result["stderr"],
            "warning_count": len(warnings),
            "warnings": warnings[:50],
        },
        "yosys": {
            "ok": yosys_result["ok"],
            "returncode": yosys_result["returncode"],
            "duration_ms": yosys_result["duration_ms"],
            "command": yosys_result["command"],
            "script": yosys_script,
            "summary": _yosys_summary(yosys_result["stdout"] or ""),
            "stdout": _clip(yosys_result["stdout"] or "", 8000),
            "stderr": yosys_result["stderr"],
        },
        "verilator": verilator_result,
        "yosys_check": yosys_check,
        "unused_signals": all_unused,
    }


def simulate(
    files: list[str],
    top: str,
    timeout_s: int = 30,
) -> dict[str, Any]:
    sources = resolve_source_files(files)
    top = _valid_top(top)
    if not top:
        raise ValueError("simulate requires top")
    scan = scan_source_paths(sources)
    if not scan["ok"]:
        return {
            "ok": False,
            "stage": "security_scan",
            "reason": "source references paths outside the workspace",
            "security": scan,
            "compile": None,
            "simulation": None,
            "vcd_paths": [],
        }
    run_dir = _new_run_dir("sim")
    sim_vvp = run_dir / "sim.vvp"

    compile_result = _run(
        [SETTINGS.iverilog, "-g2012", "-s", top, "-o", sim_vvp, *sources],
        cwd=run_dir,
        timeout_s=timeout_s,
    )
    if not compile_result["ok"]:
        return {
            "ok": False,
            "stage": "compile",
            "run_dir": _rel(run_dir),
            "security": scan,
            "compile": compile_result,
            "simulation": None,
            "vcd_paths": [],
        }

    sim_result = _run(
        [SETTINGS.vvp, sim_vvp],
        cwd=run_dir,
        timeout_s=timeout_s,
    )
    vcd_files = sorted(run_dir.rglob("*.vcd"))
    vcd_paths = [str(p) for p in vcd_files]
    vcd_rel_paths = [_rel(p) for p in vcd_files]
    return {
        "ok": sim_result["ok"],
        "stage": "run",
        "run_dir": _rel(run_dir),
        "security": {k: scan[k] for k in ("ok", "rejected", "unresolved")},
        "compile": compile_result,
        "simulation": sim_result,
        "display_output": _clip(
            (sim_result.get("stdout") or "") + (sim_result.get("stderr") or "")
        ),
        "vcd_paths": vcd_paths,
        "vcd_rel_paths": vcd_rel_paths,
        "warning": None if vcd_files else "simulation completed but no .vcd file was produced",
    }


def read_waveform(
    vcd_path: str,
    signals: list[str] | None = None,
    time: int | None = None,
) -> dict[str, Any]:
    path = resolve_vcd_path(vcd_path)
    data = parse_vcd(path)
    query_time = None if time is None else int(time)
    result = query_vcd(data, signals=signals, time=query_time)
    result["ok"] = True
    result["path"] = str(path)
    result["relative_path"] = _rel(path)
    return result


def find_events(
    vcd_path: str,
    signal: str,
    condition: str,
    limit: int = 10,
) -> dict[str, Any]:
    path = resolve_vcd_path(vcd_path)
    data = parse_vcd(path)
    result = vcd_find_events(data, signal, condition, limit=limit)
    result["ok"] = True
    result["path"] = str(path)
    result["relative_path"] = _rel(path)
    return result


def render_waveform(
    vcd_path: str,
    signals: list[str],
    out_name: str | None = None,
    title: str | None = None,
) -> dict[str, Any]:
    path = resolve_vcd_path(vcd_path)
    data = parse_vcd(path)
    if out_name:
        if not FILE_RE.match(out_name) or not out_name.lower().endswith(".png"):
            raise ValueError("out_name must be a simple .png file name")
        out_path = SETTINGS.workspace / "build" / "waveforms" / out_name
    else:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        out_path = SETTINGS.workspace / "build" / "waveforms" / f"{path.stem}-{stamp}.png"
    out_path = out_path.resolve()
    if not str(out_path).startswith(str(SETTINGS.workspace.resolve()) + os.sep):
        raise ValueError(f"output path escapes workspace: {out_path}")
    if out_path.exists():
        raise FileExistsError(f"refusing to overwrite existing file: {out_path}")
    result = render_waveform_png(data, signals, out_path, title=title)
    result["ok"] = True
    result["relative_path"] = _rel(out_path)
    result["vcd"] = _rel(path)
    return result


def read_robei_model(path: str) -> dict[str, Any]:
    model_path = resolve_model_path(path)
    return parse_robei_model(model_path)


def _folder_listing(folder: Path, limit: int = 500) -> dict[str, Any]:
    info: dict[str, Any] = {"path": str(folder), "exists": folder.is_dir(), "file_count": 0, "files": []}
    if not folder.is_dir():
        return info
    entries = sorted(p for p in folder.iterdir() if p.is_file())
    info["file_count"] = len(entries)
    for path in entries[:limit]:
        info["files"].append(
            {
                "name": path.name,
                "path": str(path),
                "relative": _rel(path),
                "extension": path.suffix.lower(),
                "size_bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    if len(entries) > limit:
        info["truncated"] = True
    return info


def read_robei_project(proj_path: str) -> dict[str, Any]:
    path = resolve_project_path(proj_path)
    sha_before = _sha256(path)
    root = ET.parse(path).getroot()
    project_dir = path.parent
    folder_tags = ("model", "test", "const", "verilog")

    folders: dict[str, Any] = {}
    unknown_nodes: list[dict[str, Any]] = []
    for child in root:
        tag = child.tag.lower()
        if tag in folder_tags:
            name = _attr_ci(child, "Name") or tag
            folders[tag] = _folder_listing(project_dir / name)
        else:
            unknown_nodes.append(generic_node(child))

    models: list[dict[str, Any]] = []
    model_errors: list[dict[str, str]] = []
    for entry in (folders.get("model") or {}).get("files", []):
        if entry["extension"] != ".model":
            continue
        model_path = Path(entry.get("path") or (project_dir / entry["name"]))
        try:
            models.append(parse_robei_model(model_path))
        except Exception as exc:
            model_errors.append({"file": str(model_path), "error": f"{type(exc).__name__}: {exc}"})

    sha_after = _sha256(path)
    return {
        "ok": True,
        "proj_path": str(path),
        "project_dir": str(project_dir),
        "root_tag": root.tag,
        "project_name": _attr_ci(root, "Name") or root.tag,
        "folders": folders,
        "folder_names": [child.tag for child in root if child.tag.lower() in folder_tags],
        "unknown_nodes": unknown_nodes,
        "node_tree": generic_node(root),
        "models": models,
        "model_errors": model_errors,
        "sha256_before": sha_before,
        "sha256_after": sha_after,
        "unchanged": sha_before == sha_after,
    }


def _export_dependencies(out_path: Path, text: str, module_name: str) -> list[Path]:
    """Sibling exported modules that this model instantiates (for hierarchical lint)."""
    dependencies: list[Path] = []
    seen = {module_name}
    for candidate in sorted(out_path.parent.glob("*.v")):
        if candidate == out_path:
            continue
        try:
            source = candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        match = re.search(r"\bmodule\s+([A-Za-z_][A-Za-z0-9_$]*)", source)
        if not match:
            continue
        name = match.group(1)
        if name in seen:
            continue
        if re.search(rf"\b{re.escape(name)}\b", text):
            dependencies.append(candidate)
            seen.add(name)
    return dependencies


def export_robei_verilog(
    model_path: str,
    out_name: str | None = None,
    parameter_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    path = resolve_model_path(model_path)
    sha_before = _sha256(path)
    model = parse_robei_model(path)
    text = model_to_verilog(model, parameter_overrides=parameter_overrides)

    name = out_name or f"{model['name']}.v"
    if not FILE_RE.match(name) or not name.lower().endswith(".v"):
        raise ValueError("out_name must be a simple .v file name")

    export_dir = SETTINGS.workspace / "build" / "export"
    export_dir.mkdir(parents=True, exist_ok=True)
    out_path = export_dir / name
    if out_path.exists():
        raise FileExistsError(f"refusing to overwrite existing file: {out_path}")
    out_path.write_text(text, encoding="utf-8", newline="\n")

    lint_files = [_rel(out_path)] + [_rel(dep) for dep in _export_dependencies(out_path, text, model["name"])]
    lint = lint_verilog(files=lint_files, top=model["name"], timeout_s=30)
    return {
        "ok": bool(lint.get("ok")),
        "model_path": str(path),
        "model_sha256_before": sha_before,
        "model_sha256_after": _sha256(path),
        "verilog_path": str(out_path),
        "relative_path": _rel(out_path),
        "verilog_sha256": _sha256(out_path),
        "size_bytes": out_path.stat().st_size,
        "module": model["name"],
        "ports": [p["name"] for p in model["ports"]],
        "parameter_overrides": parameter_overrides or {},
        "lint": lint,
    }


def generate_testbench(
    verilog_path: str,
    top: str,
    out_path: str | None = None,
    clock_period_ns: int = 10,
) -> dict[str, Any]:
    source = resolve_source_files([verilog_path])[0]
    top = _valid_top(top)
    if not top:
        raise ValueError("generate_testbench requires top")

    text = source.read_text(encoding="utf-8", errors="replace")
    ports = parse_module_ports(text, top)
    clock, reset, active_low = classify_control(ports)
    tb_name = f"{top}_tb"
    content = build_testbench(top, ports, tb_name=tb_name, clock_period_ns=clock_period_ns)

    if out_path:
        target = Path(out_path)
        if not target.is_absolute():
            target = SETTINGS.workspace / target
        target = target.resolve()
        if not str(target).startswith(str(SETTINGS.workspace.resolve()) + os.sep):
            raise ValueError(f"output path escapes workspace: {target}")
    else:
        target = SETTINGS.workspace / "testbench" / f"{tb_name}.v"
    if target.suffix.lower() not in {".v", ".sv"}:
        raise ValueError("testbench output must use a .v or .sv extension")
    if target.exists():
        raise FileExistsError(f"refusing to overwrite existing file: {target}")

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8", newline="\n")
    return {
        "ok": True,
        "module": top,
        "testbench_module": tb_name,
        "source": str(source),
        "path": str(target),
        "relative_path": _rel(target),
        "size_bytes": target.stat().st_size,
        "sha256": _sha256(target),
        "ports": ports,
        "clock": clock,
        "reset": reset,
        "reset_active_low": active_low,
        "clock_period_ns": clock_period_ns,
        "simulate_hint": {
            "files": [_rel(source), _rel(target)],
            "top": tb_name,
        },
    }
