from __future__ import annotations

import os
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
from typing import Any

from .config import SETTINGS, file_version
from .model import parse_robei_model
from .security import (
    resolve_model_path,
    resolve_source_files,
    resolve_vcd_path,
    resolve_workspace_path,
)
from .vcd import parse_vcd, query_vcd

TOP_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


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


def _run(args: list[str | Path], cwd: Path, timeout_s: int) -> dict[str, Any]:
    timeout_s = max(1, min(int(timeout_s), SETTINGS.max_timeout_s))
    env = os.environ.copy()
    # Keep tool temporary files under the selected working directory as well.
    tmp_dir = cwd / ".tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    env["TMP"] = str(tmp_dir)
    env["TEMP"] = str(tmp_dir)
    # Icarus on this installation looks for its support files below E:\Robei.
    env.setdefault("IVERILOG_ROOT", str(SETTINGS.robei_home))
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
    result = _run([path, flag], cwd=SETTINGS.workspace, timeout_s=10)
    text = (result.get("stdout") or "") + (result.get("stderr") or "")
    match = re.search(r"\bversion\s+([0-9][^\s]*)", text, re.IGNORECASE)
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
    tools = {
        "robei": {
            "path": str(SETTINGS.robei_exe),
            "exists": SETTINGS.robei_exe.is_file(),
            "version": file_version(SETTINGS.robei_exe),
        },
        "iverilog": _tool_version(SETTINGS.iverilog, "-V", "iverilog"),
        "vvp": _tool_version(SETTINGS.vvp, "-V", "vvp"),
        "yosys": _tool_version(SETTINGS.yosys, "-V", "yosys"),
    }
    try:
        mcp_version = package_version("mcp")
    except PackageNotFoundError:
        mcp_version = None
    return {
        "ok": all(item["exists"] for item in tools.values()),
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
    )
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and any(key.lower() in stripped.lower() for key in keys):
            lines.append(stripped)
    return lines[:40]


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

    iverilog_text = (iverilog_result.get("stdout") or "") + (iverilog_result.get("stderr") or "")
    warnings = [
        line.strip()
        for line in iverilog_text.splitlines()
        if "warning" in line.lower()
    ]
    return {
        "ok": bool(iverilog_result["ok"] and yosys_result["ok"]),
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


def read_robei_model(path: str) -> dict[str, Any]:
    model_path = resolve_model_path(path)
    return parse_robei_model(model_path)
