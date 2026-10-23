from __future__ import annotations

import bisect
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
from . import runstate, xmlsafe
from .security import (
    is_within,
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


def _run_impl(
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
    token = runstate.current()
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            env=env,
        )
    except FileNotFoundError as exc:
        return {
            "ok": False,
            "returncode": None,
            "duration_ms": round((time.monotonic() - started) * 1000),
            "command": cmd,
            "stdout": "",
            "stderr": f"executable not found: {exc}",
        }
    if token is not None:
        token.register(proc)
    try:
        deadline = started + timeout_s
        while True:
            try:
                stdout_b, stderr_b = proc.communicate(timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                # Client cancelled the MCP request: kill and report, do not wait out the timeout.
                if token is not None and token.aborted():
                    proc.kill()
                    out, err = proc.communicate()
                    return {
                        "ok": False,
                        "returncode": None,
                        "duration_ms": round((time.monotonic() - started) * 1000),
                        "command": cmd,
                        "stdout": _clip(_decode(out)),
                        "stderr": _clip(_decode(err)) + "\nCANCELLED by client; child process killed",
                        "cancelled": True,
                    }
                if time.monotonic() >= deadline:
                    proc.kill()
                    out, err = proc.communicate()
                    return {
                        "ok": False,
                        "returncode": None,
                        "duration_ms": round((time.monotonic() - started) * 1000),
                        "command": cmd,
                        "stdout": _clip(_decode(out)),
                        "stderr": _clip(_decode(err)) + f"\nTIMEOUT after {timeout_s}s",
                        "timeout": True,
                    }
        cancelled = token is not None and token.aborted()
        return {
            "ok": proc.returncode == 0 and not cancelled,
            "returncode": proc.returncode,
            "duration_ms": round((time.monotonic() - started) * 1000),
            "command": cmd,
            "stdout": _clip(_decode(stdout_b)),
            "stderr": _clip(_decode(stderr_b))
            + ("\nCANCELLED by client; child process killed" if cancelled else ""),
            **({"cancelled": True} if cancelled else {}),
        }
    finally:
        if token is not None:
            token.unregister(proc)


def _run(
    args: list[str | Path],
    cwd: Path,
    timeout_s: int,
    env_extra: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run a tool and record it in workspace/logs so failures stay explainable."""
    result = _run_impl(args, cwd, timeout_s, env_extra)
    try:
        from . import oplog

        command = result.get("command") or [""]
        oplog.log_event(
            "exec",
            tool=Path(str(command[0])).name,
            rc=result.get("returncode"),
            ok=result.get("ok"),
            ms=result.get("duration_ms"),
            timeout=bool(result.get("timeout")),
            cwd=str(cwd),
            tail=(result.get("stderr") or "")[-200:],
        )
    except Exception:
        pass
    return result


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
        "robei_home": str(SETTINGS.robei_home),
        "model_roots": [str(p) for p in SETTINGS.model_roots],
        "tools": tools,
        "limits": {
            "default_timeout_s": SETTINGS.default_timeout_s,
            "max_timeout_s": SETTINGS.max_timeout_s,
            "max_source_bytes": SETTINGS.max_source_bytes,
            "max_vcd_bytes": SETTINGS.max_vcd_bytes,
        },
        "config": {
            "file": str(SETTINGS.config_file) if SETTINGS.config_file else None,
            "exists": bool(SETTINGS.config_file and SETTINGS.config_file.is_file()),
            "error": SETTINGS.config_error,
            "sources": SETTINGS.sources,
            "discovered": sorted(k for k, v in SETTINGS.sources.items() if v == "discovered"),
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


def _notify(progress, done: float, total: float, message: str) -> None:
    """Report MCP progress from a worker thread; never let it break the tool."""
    if progress is None:
        return
    try:
        progress(float(done), float(total), message)
    except Exception:
        pass


def _failure_code(result: dict[str, Any], fallback: str) -> str | None:
    """Map a child-process result to a stable code the MCP caller can branch on."""
    if result.get("ok"):
        return None
    if result.get("cancelled"):
        return "cancelled"
    if result.get("timeout"):
        return "timeout"
    return fallback


def lint_verilog(
    files: list[str],
    top: str | None = None,
    timeout_s: int = 30,
    progress=None,
) -> dict[str, Any]:
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
    _notify(progress, 0, 4, "iverilog -Wall")
    iverilog_result = _run(iverilog_cmd, cwd=run_dir, timeout_s=timeout_s)

    read_commands: list[str] = []
    for source in sources:
        if source.suffix.lower() in {".sv", ".svh"}:
            read_commands.append(f"read_verilog -sv {_quote_yosys(source)}")
        else:
            read_commands.append(f"read_verilog {_quote_yosys(source)}")
    read_commands.append(f"synth -top {top}" if top else "synth")
    yosys_script = "; ".join(read_commands)
    _notify(progress, 1, 4, "yosys synth")
    yosys_result = _run([SETTINGS.yosys, "-p", yosys_script], cwd=run_dir, timeout_s=timeout_s)

    _notify(progress, 2, 4, "verilator --lint-only")
    verilator_result = _verilator_lint(sources, top, timeout_s, run_dir)
    _notify(progress, 3, 4, "yosys unused-wire diff")
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
    progress=None,
) -> dict[str, Any]:
    sources = resolve_source_files(files)
    top = _valid_top(top)
    if not top:
        raise ValueError("simulate requires top")
    scan = scan_source_paths(sources)
    if not scan["ok"]:
        return {
            "ok": False,
            "code": "path_rejected",
            "stage": "security_scan",
            "reason": "source references paths outside the workspace",
            "security": scan,
            "compile": None,
            "simulation": None,
            "vcd_paths": [],
        }
    run_dir = _new_run_dir("sim")
    sim_vvp = run_dir / "sim.vvp"

    _notify(progress, 0, 2, "iverilog: compiling")
    compile_result = _run(
        [SETTINGS.iverilog, "-g2012", "-s", top, "-o", sim_vvp, *sources],
        cwd=run_dir,
        timeout_s=timeout_s,
    )
    if not compile_result["ok"]:
        return {
            "ok": False,
            "code": _failure_code(compile_result, "compile_failed"),
            "stage": "compile",
            "run_dir": _rel(run_dir),
            "security": scan,
            "compile": compile_result,
            "simulation": None,
            "vcd_paths": [],
        }

    _notify(progress, 1, 2, "vvp: running testbench")
    sim_result = _run(
        [SETTINGS.vvp, sim_vvp],
        cwd=run_dir,
        timeout_s=timeout_s,
    )
    _notify(progress, 2, 2, "vvp: finished")
    vcd_files = sorted(run_dir.rglob("*.vcd"))
    vcd_paths = [str(p) for p in vcd_files]
    vcd_rel_paths = [_rel(p) for p in vcd_files]
    return {
        "ok": sim_result["ok"],
        "code": _failure_code(sim_result, "run_failed"),
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


def simulate_project(
    proj_path: str,
    test_name: str,
    timeout_s: int = 30,
    render: bool = False,
    progress=None,
) -> dict[str, Any]:
    """Run a Robei ``test/*.test`` model with the bundled iverilog — no IDE needed.

    Robei only compiles ``build/<test>.v`` inside its own process; this rebuilds
    the same source (test Code + every model exported to Verilog) and simulates
    it headlessly, so experiments still run when the desktop is locked or the
    UIA bridge is dead.
    """
    proj = resolve_project_path(proj_path)
    if not is_within(proj, SETTINGS.workspace):
        raise ValueError("refusing to simulate a project outside the workspace")
    project_dir = proj.parent
    stem = test_name[:-5] if test_name.lower().endswith(".test") else test_name
    if not MODEL_NAME_RE.match(stem):
        raise ValueError(f"invalid test name: {test_name!r}")
    test_path = project_dir / "test" / f"{stem}.test"
    if not test_path.is_file():
        available = sorted(p.stem for p in (project_dir / "test").glob("*.test"))
        raise FileNotFoundError(f"test model not found: {test_path} (available: {available})")

    _notify(progress, 0, 3, "parsing test model")
    test_model = parse_robei_model(test_path)
    out_dir = SETTINGS.workspace / "build" / "headless" / f"{project_dir.name}-{stem}"
    out_dir.mkdir(parents=True, exist_ok=True)
    tb_src = out_dir / f"{stem}.v"
    timescale = test_model.get("timescale") or "1ns/1ns"
    body = test_model["code"]
    tb_src.write_text(
        "`timescale " + timescale + "\n"
        + "// generated from " + test_path.name + " by simulate_project\n"
        + "module " + stem + "();\n"
        + body + "\n"
        + "  initial begin\n"
        + '    $dumpfile("' + stem + '.vcd");\n'
        + "    $dumpvars;\n"
        + "  end\n"
        + "endmodule\n",
        encoding="utf-8",
        newline="\n",
    )

    deps: list[Path] = []
    deps_dir = out_dir / "deps"
    deps_dir.mkdir(exist_ok=True)
    _notify(progress, 1, 3, "exporting project models")
    for model_file in sorted((project_dir / "model").glob("*.model")):
        model = parse_robei_model(model_file)
        target = deps_dir / f"{model['name']}.v"
        target.write_text(model_to_verilog(model), encoding="utf-8", newline="\n")
        deps.append(target)

    _notify(progress, 2, 3, "compiling + running")
    result = simulate(files=[str(tb_src), *[str(d) for d in deps]], top=stem, timeout_s=timeout_s)
    result["headless"] = {
        "project": str(proj),
        "test": stem,
        "tb_source": str(tb_src),
        "tb_source_rel": _rel(tb_src),
        "dependencies": [_rel(d) for d in deps],
        "timescale": timescale,
    }
    vcds = result.get("vcd_paths") or []
    if render and vcds:
        try:
            listing = read_waveform(vcds[0])
            names = [s["name"] for s in listing.get("signals", [])][:16]
            png = render_waveform(vcds[0], names, title=f"{stem} (headless iverilog)")
            result["plot"] = {
                "ok": png.get("ok"),
                "path": png.get("path"),
                "relative_path": png.get("relative_path"),
            }
        except Exception as exc:
            result["plot"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return result


def _norm_value(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text.startswith(("b", "r", "s")) and len(text) > 1:
        text = text[1:]
    if text and all(ch in "01" for ch in text):
        text = text.lstrip("0") or "0"
    return text


def _sorted_changes(data, signal) -> tuple[list[tuple[int, str]], list[int]]:
    changes = data.changes.get(signal.identifier, [])
    return changes, [t for t, _ in changes]


def _value_at_changes(changes: list[tuple[int, str]], times: list[int], when: int) -> str | None:
    index = bisect.bisect_right(times, when) - 1
    if index < 0:
        return None
    return changes[index][1]


def compare_waveforms(
    golden_vcd: str,
    dut_vcd: str,
    signals: list[str] | None = None,
    max_mismatches: int = 20,
) -> dict[str, Any]:
    """Diff two VCDs signal by signal, using change times from both sides.

    Values are compared at the union of both timelines, so a signal that simply
    never toggles in one run is reported at the moment the other one does.
    """
    golden_path = resolve_vcd_path(golden_vcd)
    dut_path = resolve_vcd_path(dut_vcd)
    golden = parse_vcd(golden_path)
    dut = parse_vcd(dut_path)

    if signals:
        wanted = [s.full_name for s in match_signals(golden, signals)]
    else:
        wanted = [name for name in golden.signals if name in dut.signals]
    missing_golden = [n for n in wanted if n not in golden.signals]
    missing_dut = [n for n in wanted if n not in dut.signals]
    comparable = [n for n in wanted if n in golden.signals and n in dut.signals]

    results: list[dict[str, Any]] = []
    total = 0
    for name in comparable:
        g_sig = golden.signals[name]
        d_sig = dut.signals[name]
        g_changes, g_times = _sorted_changes(golden, g_sig)
        d_changes, d_times = _sorted_changes(dut, d_sig)
        timeline = sorted({t for t, _ in g_changes} | {t for t, _ in d_changes})
        mismatches: list[dict[str, Any]] = []
        for when in timeline:
            gv = _value_at_changes(g_changes, g_times, when)
            dv = _value_at_changes(d_changes, d_times, when)
            if _norm_value(gv) != _norm_value(dv):
                if len(mismatches) < max_mismatches:
                    mismatches.append({"time": when, "golden": gv, "dut": dv})
                total += 1
        results.append(
            {
                "signal": name,
                "width": g_sig.width,
                "width_matches": g_sig.width == d_sig.width,
                "golden_changes": len(g_changes),
                "dut_changes": len(d_changes),
                "mismatch_count": sum(
                    1
                    for when in timeline
                    if _norm_value(_value_at_changes(g_changes, g_times, when))
                    != _norm_value(_value_at_changes(d_changes, d_times, when))
                ),
                "first_mismatch": mismatches[0] if mismatches else None,
                "mismatches": mismatches,
            }
        )

    differing = [row for row in results if row["mismatch_count"]]
    return {
        "ok": not differing and not missing_dut,
        "golden": str(golden_path),
        "dut": str(dut_path),
        "timescale": {"golden": golden.timescale, "dut": dut.timescale},
        "max_time": {"golden": golden.max_time, "dut": dut.max_time},
        "signal_count": len(comparable),
        "mismatch_total": total,
        "differing_signals": [row["signal"] for row in differing],
        "missing_in_dut": missing_dut,
        "missing_in_golden": missing_golden,
        "results": results,
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


def list_projects(limit: int = 200) -> dict[str, Any]:
    """Enumerate every Robei project inside the workspace (read-only).

    Answers the first question an agent usually has: which project am I working
    on, what models/tests does it have, and is there a build output already.
    """
    root = SETTINGS.workspace
    projects: list[dict[str, Any]] = []
    problems: list[dict[str, str]] = []
    for proj in sorted(root.rglob("*.proj")):
        if not is_within(proj, root):
            continue
        entry: dict[str, Any] = {
            "name": proj.stem,
            "proj": str(proj),
            "relative": _rel(proj),
            "dir": str(proj.parent),
        }
        try:
            tree = xmlsafe.load(proj)
        except Exception as exc:
            problems.append({"proj": str(proj), "error": f"{type(exc).__name__}: {exc}"})
            projects.append({**entry, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
            continue
        entry["ok"] = True
        entry["project_name"] = _attr_ci(tree, "Name") or tree.tag
        declared: dict[str, int] = {}
        for child in tree:
            tag = child.tag.lower()
            if tag in ("model", "test", "const", "verilog"):
                declared[tag] = len(list(child))
        entry["declared_entries"] = declared
        model_dir = proj.parent / "model"
        test_dir = proj.parent / "test"
        verilog_dir = proj.parent / "verilog"
        build_dir = proj.parent / "build"
        entry["models"] = sorted(p.stem for p in model_dir.glob("*.model")) if model_dir.is_dir() else []
        entry["tests"] = sorted(p.stem for p in test_dir.glob("*.test")) if test_dir.is_dir() else []
        entry["verilog"] = sorted(p.name for p in verilog_dir.glob("*.v")) if verilog_dir.is_dir() else []
        const_dir = proj.parent / "const"
        entry["constraints"] = sorted(p.name for p in const_dir.glob("*.const")) if const_dir.is_dir() else []
        entry["build"] = {
            "exists": build_dir.is_dir(),
            "vcd": sorted(p.name for p in build_dir.glob("*.vcd")) if build_dir.is_dir() else [],
            "mtime": max((p.stat().st_mtime for p in build_dir.glob("*")), default=None)
            if build_dir.is_dir()
            else None,
        }
        projects.append(entry)
        if len(projects) >= limit:
            break
    return {
        "ok": True,
        "workspace": str(root),
        "count": len(projects),
        "projects": projects,
        "problems": problems,
        "truncated": len(projects) >= limit,
    }


def read_robei_project(proj_path: str) -> dict[str, Any]:
    path = resolve_project_path(proj_path)
    sha_before = _sha256(path)
    root = xmlsafe.load(path)
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

    tests: list[dict[str, Any]] = []
    test_errors: list[dict[str, str]] = []
    for entry in (folders.get("test") or {}).get("files", []):
        if entry["extension"] != ".test":
            continue
        test_path = Path(entry.get("path") or (project_dir / entry["name"]))
        try:
            tests.append(parse_robei_model(test_path))
        except Exception as exc:
            test_errors.append({"file": str(test_path), "error": f"{type(exc).__name__}: {exc}"})

    constraints: list[dict[str, Any]] = []
    constraint_errors: list[dict[str, str]] = []
    for entry in (folders.get("const") or {}).get("files", []):
        if entry["extension"] != ".const":
            continue
        const_path = Path(entry.get("path") or (project_dir / entry["name"]))
        try:
            constraints.append(parse_robei_model(const_path))
        except Exception as exc:
            constraint_errors.append({"file": str(const_path), "error": f"{type(exc).__name__}: {exc}"})

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
        "tests": tests,
        "test_errors": test_errors,
        "constraints": constraints,
        "constraint_errors": constraint_errors,
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
    full_combo: bool = False,
    max_combo_bits: int = 8,
) -> dict[str, Any]:
    source = resolve_source_files([verilog_path])[0]
    top = _valid_top(top)
    if not top:
        raise ValueError("generate_testbench requires top")

    text = source.read_text(encoding="utf-8", errors="replace")
    ports = parse_module_ports(text, top)
    clock, reset, active_low = classify_control(ports)
    tb_name = f"{top}_tb"
    content = build_testbench(
        top,
        ports,
        tb_name=tb_name,
        clock_period_ns=clock_period_ns,
        full_combo=full_combo,
        max_combo_bits=max_combo_bits,
    )

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
        "full_combo": full_combo,
        "clock_period_ns": clock_period_ns,
        "simulate_hint": {
            "files": [_rel(source), _rel(target)],
            "top": tb_name,
        },
    }


# ---------------------------------------------------------------------------
# .model editing: ports
# ---------------------------------------------------------------------------

PORT_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
PORT_DIRECTIONS = {"input", "output", "inout"}
PORT_DATATYPES = {"wire", "reg"}
PORT_SIDES = {"left", "right", "top", "bottom"}


def _port_geometry(side: str, index: int) -> tuple[str, str]:
    if side == "left" or side == "right":
        x = "-0.01875" if side == "left" else "0.98125"
        y = 0.163889 + 0.145 * index
        return x, f"{y:.6f}"
    y = "-0.0277778" if side == "top" else "1.02778"
    x = 0.21875 + 0.15625 * index
    return f"{x:.6f}", y


def _port_tag(
    module: str,
    name: str,
    direction: str,
    width: int,
    datatype: str,
    side: str,
    index: int,
) -> str:
    color = "#faebd7" if direction == "input" else ("#f0ffff" if direction == "output" else "#e0ffff")
    x, y = _port_geometry(side, index)
    return (
        f'<Port Color="{color}" Connect="" Datasize="{width}" Datatype="{datatype}" '
        f'Description="" Height="30" Inout="{direction}" Name="{name}" Parent="{module}" '
        f'Side="{side}" Width="30" X="{x}" Y="{y}"/>'
    )


def set_port(
    model_path: str,
    name: str,
    direction: str = "input",
    width: int = 1,
    datatype: str = "wire",
    side: str | None = None,
) -> dict[str, Any]:
    """Add or update one <Port> in a .model file (backup + XML validation, no GUI).

    Robei's canvas click-to-add-port only creates default names and is fragile to
    drive; editing the port node directly is deterministic and Robei reloads it.
    """
    path = resolve_model_path(model_path)
    if not is_within(path, SETTINGS.workspace):
        raise ValueError("refusing to modify a .model outside the workspace")
    if not PORT_NAME_RE.match(name):
        raise ValueError(f"invalid port name: {name!r}")
    direction = direction.strip().lower()
    datatype = datatype.strip().lower()
    if direction not in PORT_DIRECTIONS:
        raise ValueError(f"direction must be one of {sorted(PORT_DIRECTIONS)}")
    if datatype not in PORT_DATATYPES:
        raise ValueError(f"datatype must be one of {sorted(PORT_DATATYPES)}")
    width = int(width)
    if not 1 <= width <= 1024:
        raise ValueError("width must be between 1 and 1024")
    if side is None:
        side = "left" if direction == "input" else ("right" if direction == "output" else "left")
    side = side.strip().lower()
    if side not in PORT_SIDES:
        raise ValueError(f"side must be one of {sorted(PORT_SIDES)}")

    with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        text = handle.read()
    module_match = re.search(r"<Module\b[^>]*>", text)
    if module_match is None:
        raise ValueError(f"no <Module> element in {path}")
    module_tag = module_match.group(0)
    name_match = re.search(r'\bName="([^"]+)"', module_tag)
    if name_match is None:
        raise ValueError("module has no Name attribute")
    module = name_match.group(1)

    existing_tags = re.findall(r"<Port\b[^>]*/>", text)
    index = len(existing_tags)
    replacement = _port_tag(module, name, direction, width, datatype, side, index)
    pattern = re.compile(r'<Port\b[^>]*\bName="%s"[^>]*/>' % re.escape(name))
    replaced = False
    if pattern.search(text):
        for position, tag in enumerate(existing_tags):
            if re.search(r'\bName="%s"' % re.escape(name), tag):
                index = position
                break
        replacement = _port_tag(module, name, direction, width, datatype, side, index)
        new_text = pattern.sub(replacement, text, count=1)
        replaced = True
    else:
        insert_at = module_match.end()
        new_text = text[:insert_at] + "\n " + replacement + text[insert_at:]

    ET.fromstring(new_text)  # raises on malformed XML

    sha_before = _sha256(path)
    backup = path.with_name(path.name + ".bak-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    backup.write_text(text, encoding="utf-8", newline="")
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(new_text)

    parsed = parse_robei_model(path)
    return {
        "ok": True,
        "model": module,
        "path": str(path),
        "relative_path": _rel(path),
        "port": {"name": name, "direction": direction, "width": width, "datatype": datatype, "side": side},
        "replaced": replaced,
        "backup": str(backup),
        "sha256_before": sha_before,
        "sha256_after": _sha256(path),
        "ports": parsed.get("ports"),
    }


# ---------------------------------------------------------------------------
# project / model scaffolding (workspace only)
# ---------------------------------------------------------------------------

MODEL_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _attr(value: str) -> str:
    from xml.sax.saxutils import escape

    return escape(value, {'"': "&quot;"}).replace("\n", "&#xa;")


def _model_xml(name: str, ports: list[dict] | None, code: str, comment: str) -> str:
    head = (
        f'<Module Category="Current" Class="module" Code="{_attr(code)}" Color="#d3d3d3" '
        f'Comment="{_attr(comment)}" Height="1080" Name="{name}" Parameters="" Parent="0" '
        f'Type="model" Width="1600" X="0" Y="0">'
    )
    lines = ["<!DOCTYPE Robei>", head]
    for index, port in enumerate(ports or []):
        direction = str(port.get("direction", "input")).lower()
        side = str(port.get("side") or ("left" if direction == "input" else "right")).lower()
        lines.append(
            " "
            + _port_tag(
                name,
                str(port["name"]),
                direction,
                int(port.get("width", 1)),
                str(port.get("datatype", "wire")).lower(),
                side,
                index,
            )
        )
    lines.append("</Module>")
    return "\n".join(lines) + "\n"


def _register_entry_in_project(proj_path: Path, folder: str, name: str, extension: str, node_type: str) -> str:
    """Append one file entry to a folder block of a .proj, text-preserving.

    Returns ``"created"`` when the entry was appended, ``"exists"`` when the
    manifest already declared it, so callers can stay idempotent.
    """
    with proj_path.open("r", encoding="utf-8", newline="") as handle:
        text = handle.read()
    file_name = f"{name}.{extension}"
    entry = f'<{file_name} name="{file_name}" parent="{folder}" type="{node_type}"/>'
    if f'<{file_name} ' in text:
        return "exists"
    folder_tag = re.search(rf"<{folder}\b[^>]*>", text)
    if folder_tag is None:
        raise ValueError(f"project has no <{folder}> folder")
    tag = folder_tag.group(0)
    if tag.endswith("/>"):
        block = tag[:-2] + ">\n  " + entry + f"\n </{folder}>"
        new_text = text[: folder_tag.start()] + block + text[folder_tag.end():]
    else:
        close = text.find(f"</{folder}>", folder_tag.end())
        if close == -1:
            raise ValueError(f"unterminated <{folder}> folder")
        new_text = text[:close] + "  " + entry + "\n " + text[close:]
    ET.fromstring(new_text)
    with proj_path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(new_text)
    return "created"


def _register_model_in_project(proj_path: Path, model_name: str) -> str:
    return _register_entry_in_project(proj_path, "model", model_name, "model", "model")


def _backup_file(target: Path) -> Path | None:
    if not target.exists():
        return None
    backup = target.with_name(target.name + ".bak-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    backup.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
    return backup


def sync_robei_verilog(proj_path: str, overwrite: bool = True) -> dict[str, Any]:
    """Regenerate <proj>/verilog/<model>.v for every model in the project.

    Robei compiles a simulation with ``iverilog -o ... filelist.vf -y <proj>/verilog``,
    i.e. instantiated modules are resolved from this directory by module name.
    Without the exported file a hierarchical test model fails with
    ``Unable to open input file`` at run time.
    """
    proj = resolve_project_path(proj_path)
    if not is_within(proj, SETTINGS.workspace):
        raise ValueError("refusing to modify a project outside the workspace")
    model_dir = proj.parent / "model"
    if not model_dir.is_dir():
        raise FileNotFoundError(f"project has no model folder: {model_dir}")
    verilog_dir = proj.parent / "verilog"
    verilog_dir.mkdir(parents=True, exist_ok=True)

    written: list[dict[str, Any]] = []
    skipped: list[str] = []
    errors: list[dict[str, str]] = []
    for model_file in sorted(model_dir.glob("*.model")):
        try:
            model = parse_robei_model(model_file)
            text = model_to_verilog(model)
        except Exception as exc:
            errors.append({"file": str(model_file), "error": f"{type(exc).__name__}: {exc}"})
            continue
        target = verilog_dir / f"{model['name']}.v"
        if target.exists() and not overwrite:
            skipped.append(str(target))
            continue
        target.write_text(text, encoding="utf-8", newline="\n")
        written.append(
            {
                "path": str(target),
                "relative_path": _rel(target),
                "module": model["name"],
                "size_bytes": target.stat().st_size,
                "sha256": _sha256(target),
            }
        )
    return {
        "ok": not errors,
        "verilog_dir": str(verilog_dir),
        "written": written,
        "skipped": skipped,
        "errors": errors,
    }


def _test_xml(name: str, code: str, timescale: str = "1ns/1ns") -> str:
    head = (
        f'<Module Category="Current" Class="module" Code="{_attr(code)}" Color="#d3d3d3" '
        f'Comment="" Height="1080" Name="{name}" Parent="0" Timescale="{_attr(timescale)}" '
        f'Type="test" Width="1600" X="0" Y="0"/>'
    )
    return "<!DOCTYPE Robei>\n" + head + "\n"


CANVAS_PORT_X = {"left": "-0.0222222", "right": "0.977778"}
INSTANCE_PORT_X = {"left": "-0.125", "right": "0.875"}


def _canvas_endpoint(top: str, ref: str) -> str:
    """``u1.y`` -> ``top#u1>y``；``y`` -> ``top>y``."""
    ref = (ref or "").strip()
    if not ref:
        raise ValueError("wire endpoint must not be empty")
    if "." in ref:
        instance, _, port = ref.partition(".")
        if not port:
            raise ValueError(f"wire endpoint {ref!r} is missing a port")
        return f"{top}#{instance.strip()}>{port.strip()}"
    return f"{top}>{ref}"


def _canvas_port_xml(
    parent: str,
    port: dict[str, Any],
    index: int,
    total: int,
    instance: bool,
) -> str:
    side = str(port.get("side") or ("left" if port.get("direction") == "input" else "right")).lower()
    x = (INSTANCE_PORT_X if instance else CANVAS_PORT_X).get(side, CANVAS_PORT_X["left"])
    step = 0.25 if instance else 0.2
    y = 0.1875 + index * step
    direction = port.get("direction") or port.get("inout") or "input"
    datatype = port.get("datatype") or "wire"
    width = port.get("width") or port.get("datasize") or 1
    return (
        f'  <Port Connect="{_attr(port.get("connect", ""))}" Name="{_attr(port["name"])}" '
        f'Inout="{direction}" Function="" Width="20" Side="{side}" X="{x}" Y="{y:.6f}" '
        f'Datatype="{datatype}" Parent="{_attr(parent)}" Height="20" Color="#faebd7" Datasize="{width}"/>'
    )


def create_robei_canvas_model(
    proj_path: str,
    name: str,
    ports: list[dict[str, Any]],
    instances: list[dict[str, Any]],
    wires: list[dict[str, str]],
    comment: str = "",
    overwrite: bool = False,
) -> dict[str, Any]:
    """Write a canvas-level model: placed instances (nested ``<Module>``) plus ``<Wire>`` nets.

    This is the format Robei itself produces when a circuit is drawn on the graph
    page. Robei turns it into Verilog on Compile (``//---Module instantiation---``),
    so a hierarchical design can be generated without touching the canvas.
    """
    proj = resolve_project_path(proj_path)
    if not is_within(proj, SETTINGS.workspace):
        raise ValueError("refusing to modify a project outside the workspace")
    if not MODEL_NAME_RE.match(name):
        raise ValueError(f"invalid model name: {name!r}")
    if not instances:
        raise ValueError("create_robei_canvas_model needs at least one instance")
    model_dir = proj.parent / "model"
    if not model_dir.is_dir():
        raise FileNotFoundError(f"project has no model folder: {model_dir}")

    cleaned_ports: list[dict[str, Any]] = []
    for port in ports:
        direction = str(port.get("direction", "input")).lower()
        if direction not in PORT_DIRECTIONS:
            raise ValueError(f"invalid direction: {direction}")
        if not PORT_NAME_RE.match(str(port["name"])):
            raise ValueError(f"invalid port name: {port['name']!r}")
        cleaned_ports.append(
            {
                "name": str(port["name"]),
                "direction": direction,
                "width": int(port.get("width", 1)),
                "datatype": str(port.get("datatype", "wire")).lower(),
                "side": str(port.get("side") or ("left" if direction == "input" else "right")).lower(),
            }
        )
    top_ports = {p["name"] for p in cleaned_ports}

    placed: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(instances):
        inst_name = str(item.get("name") or f"u{index + 1}")
        if not MODEL_NAME_RE.match(inst_name):
            raise ValueError(f"invalid instance name: {inst_name!r}")
        if inst_name in seen:
            raise ValueError(f"duplicate instance name: {inst_name!r}")
        seen.add(inst_name)
        model_stem = str(item.get("model") or "").strip()
        if model_stem.lower().endswith(".model"):
            model_stem = model_stem[:-6]
        if not MODEL_NAME_RE.match(model_stem):
            raise ValueError(f"invalid model reference for {inst_name}: {model_stem!r}")
        source = model_dir / f"{model_stem}.model"
        if not source.is_file():
            available = sorted(p.stem for p in model_dir.glob("*.model"))
            raise FileNotFoundError(f"model {model_stem!r} not found (available: {available})")
        target_model = parse_robei_model(source)
        placed.append(
            {
                "instance": inst_name,
                "model": model_stem,
                # Robei writes the referenced model name as the instance Class
                "class": model_stem,
                "x": int(item.get("x", 200 + index * 320)),
                "y": int(item.get("y", 120)),
                "ports": [
                    {
                        "name": p["name"],
                        "direction": p["inout"] or "input",
                        "datatype": p["datatype"] or "wire",
                        "width": p["datasize"] or 1,
                    }
                    for p in target_model["ports"]
                ],
            }
        )

    by_instance = {item["instance"]: item for item in placed}
    cleaned_wires: list[dict[str, str]] = []
    for index, wire in enumerate(wires):
        frm = str(wire.get("from") or "").strip()
        to = str(wire.get("to") or "").strip()
        for ref in (frm, to):
            if "." in ref:
                inst, _, port = ref.partition(".")
                if inst not in by_instance:
                    raise ValueError(f"wire references unknown instance {inst!r}")
                if port not in {p["name"] for p in by_instance[inst]["ports"]}:
                    raise ValueError(f"instance {inst!r} has no port {port!r}")
            elif ref not in top_ports:
                raise ValueError(f"wire references unknown top-level port {ref!r}")
        cleaned_wires.append(
            {
                "net": str(wire.get("net") or f"{name}_n{index}"),
                "from": _canvas_endpoint(name, frm),
                "to": _canvas_endpoint(name, to),
                "datasize": str(wire.get("datasize") or 1),
            }
        )

    lines = ["<!DOCTYPE Robei>"]
    lines.append(
        f'<Module Name="{_attr(name)}" Type="module" Width="900" Class="module" '
        f'Comment="{_attr(comment)}" X="0" Parameters="" Y="0" Parent="0" Height="600" '
        f'Color="#d3d3d3" Include="" File="Current/{_attr(name)}.model" Code="">'
    )
    for item in placed:
        lines.append(
            f' <Module Name="{_attr(item["instance"])}" Type="model" Width="160" '
            f'Class="{_attr(item["class"])}" Comment="" X="{item["x"]}" Parameters="" Y="{item["y"]}" '
            f'Parent="{_attr(name)}" Height="160" Color="#d3d3d3" Include="" '
            f'File="Current/{_attr(item["model"])}.model" Code="">'
        )
        for index, port in enumerate(item["ports"]):
            lines.append(_canvas_port_xml(item["instance"], port, index, len(item["ports"]), True))
        lines.append(" </Module>")
    for index, port in enumerate(cleaned_ports):
        lines.append(_canvas_port_xml(name, port, index, len(cleaned_ports), False))
    for wire in cleaned_wires:
        lines.append(
            f' <Wire Name="{_attr(wire["net"])}" To="{wire["to"]}" From="{wire["from"]}" '
            f'Datatype="wire" Parent="{_attr(name)}" Datasize="{wire["datasize"]}"/>'
        )
    lines.append("</Module>")
    xml_text = "\n".join(lines) + "\n"
    ET.fromstring(xml_text)

    target = model_dir / f"{name}.model"
    if target.exists() and not overwrite:
        raise FileExistsError(f"model already exists: {target}")
    backup = _backup_file(target)
    target.write_text(xml_text, encoding="utf-8", newline="\n")
    registration = _register_model_in_project(proj, name)
    parsed = parse_robei_model(target)
    return {
        "ok": True,
        "model": name,
        "path": str(target),
        "relative_path": _rel(target),
        "project": str(proj),
        "registered_in_project": registration in ("created", "exists"),
        "registration": registration,
        "backup": str(backup) if backup else None,
        "sha256": _sha256(target),
        "ports": parsed["ports"],
        "instances": parsed["instances"],
        "wires": parsed["wires"],
        "netlist": parsed["netlist"],
    }


def create_robei_test(
    proj_path: str,
    name: str,
    code: str,
    timescale: str = "1ns/1ns",
    overwrite: bool = False,
    sync_deps: bool = True,
) -> dict[str, Any]:
    """Create a ``test/<name>.test`` (Type="test") model and register it in the .proj.

    ``code`` is the testbench body only: Robei wraps it in ``module <name>();`` and
    appends the ``$dumpfile``/``$dumpvars`` block itself when it generates
    ``build/<name>.v``. With ``sync_deps`` every project model is re-exported to
    ``verilog/<model>.v`` so the instantaneous submodules resolve through ``-y``.
    """
    proj = resolve_project_path(proj_path)
    if not is_within(proj, SETTINGS.workspace):
        raise ValueError("refusing to modify a project outside the workspace")
    if not MODEL_NAME_RE.match(name):
        raise ValueError(f"invalid test name: {name!r}")
    if not timescale or any(ch in timescale for ch in '"<>&'):
        raise ValueError("invalid timescale")
    test_dir = proj.parent / "test"
    test_dir.mkdir(parents=True, exist_ok=True)
    target = test_dir / f"{name}.test"
    if target.exists() and not overwrite:
        raise FileExistsError(f"test already exists: {target}")

    xml_text = _test_xml(name, code, timescale)
    ET.fromstring(xml_text)
    backup = _backup_file(target)
    target.write_text(xml_text, encoding="utf-8", newline="\n")
    registration = _register_entry_in_project(proj, "test", name, "test", "test")
    deps = sync_robei_verilog(str(proj)) if sync_deps else None
    return {
        "ok": True,
        "test": name,
        "path": str(target),
        "relative_path": _rel(target),
        "project": str(proj),
        "registered_in_project": registration in ("created", "exists"),
        "registration": registration,
        "backup": str(backup) if backup else None,
        "sha256": _sha256(target),
        "code": code,
        "verilog_dependencies": deps,
    }


def create_robei_project(name: str, models: list[str] | None = None) -> dict[str, Any]:
    """Create a Robei project skeleton (folders + .proj + blank models) in the workspace."""
    if not MODEL_NAME_RE.match(name):
        raise ValueError(f"invalid project name: {name!r}")
    models = [name] if models is None else list(models)
    for model in models:
        if not MODEL_NAME_RE.match(model):
            raise ValueError(f"invalid model name: {model!r}")
    root = SETTINGS.workspace / name
    if root.exists():
        raise FileExistsError(f"project path already exists: {root}")
    for sub in ("model", "test", "const", "verilog"):
        (root / sub).mkdir(parents=True, exist_ok=True)

    entries = "\n".join(
        f'  <{m}.model name="{m}.model" parent="model" type="model"/>' for m in models
    )
    proj_text = (
        f'<{name} name="{name}" parent="0" type="proj">\n'
        f' <model name="model" parent="{name}" type="folder">\n{entries}\n </model>\n'
        f' <test name="test" parent="{name}" type="folder"/>\n'
        f' <const name="const" parent="{name}" type="folder"/>\n'
        f' <verilog name="verilog" parent="{name}" type="folder"/>\n'
        f"</{name}>\n"
    )
    proj_path = root / f"{name}.proj"
    proj_path.write_text(proj_text, encoding="utf-8", newline="\n")
    ET.fromstring(proj_text)

    created = []
    for model in models:
        model_path = root / "model" / f"{model}.model"
        model_path.write_text(_model_xml(model, [], "", ""), encoding="utf-8", newline="\n")
        created.append(str(model_path))
    return {
        "ok": True,
        "project": name,
        "root": str(root),
        "proj": str(proj_path),
        "models": created,
        "relative_root": _rel(root),
    }


def create_robei_model(
    proj_path: str,
    name: str,
    ports: list[dict[str, Any]] | None = None,
    code: str = "",
    comment: str = "",
    overwrite: bool = False,
) -> dict[str, Any]:
    """Create a .model with ports + Code inside an existing project and register it."""
    proj = resolve_project_path(proj_path)
    if not is_within(proj, SETTINGS.workspace):
        raise ValueError("refusing to modify a project outside the workspace")
    if not MODEL_NAME_RE.match(name):
        raise ValueError(f"invalid model name: {name!r}")
    model_dir = proj.parent / "model"
    if not model_dir.is_dir():
        raise FileNotFoundError(f"project has no model folder: {model_dir}")
    target = model_dir / f"{name}.model"
    if target.exists() and not overwrite:
        raise FileExistsError(f"model already exists: {target}")

    cleaned: list[dict[str, Any]] = []
    for port in ports or []:
        entry = {
            "name": str(port["name"]),
            "direction": str(port.get("direction", "input")).lower(),
            "width": int(port.get("width", 1)),
            "datatype": str(port.get("datatype", "wire")).lower(),
            "side": (str(port["side"]).lower() if port.get("side") else None),
        }
        if not PORT_NAME_RE.match(entry["name"]):
            raise ValueError(f"invalid port name: {entry['name']!r}")
        if entry["direction"] not in PORT_DIRECTIONS:
            raise ValueError(f"invalid direction: {entry['direction']}")
        if entry["datatype"] not in PORT_DATATYPES:
            raise ValueError(f"invalid datatype: {entry['datatype']}")
        if not 1 <= entry["width"] <= 1024:
            raise ValueError("port width must be 1..1024")
        cleaned.append(entry)

    xml_text = _model_xml(name, cleaned, code, comment)
    ET.fromstring(xml_text)
    backup = None
    if target.exists():
        backup = target.with_name(target.name + ".bak-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
        backup.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
    target.write_text(xml_text, encoding="utf-8", newline="\n")
    registration = _register_model_in_project(proj, name)

    parsed = parse_robei_model(target)
    return {
        "ok": True,
        "model": name,
        "path": str(target),
        "relative_path": _rel(target),
        "project": str(proj),
        "registered_in_project": registration in ("created", "exists"),
        "registration": registration,
        "backup": str(backup) if backup else None,
        "sha256": _sha256(target),
        "ports": parsed.get("ports"),
        "code": parsed.get("code"),
    }
