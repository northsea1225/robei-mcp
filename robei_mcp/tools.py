from __future__ import annotations

import base64
import bisect
import copy
import csv
import difflib
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
from typing import Any

from .config import SETTINGS, file_version
from .model import (
    _attr_ci,
    generic_node,
    model_to_verilog,
    model_to_verilog_with_warnings,
    parse_parameter_text_checked,
    parse_robei_model,
    parse_robei_model_text,
)
from .errors import (
    BadRequest,
    LogPathEscape,
    NotBehavioral,
    PathEscape,
    UnknownParameter,
    WidthMismatch,
)
from . import lifecycle, runstate, xmlsafe
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
from .vcd import match_signals, parse_vcd, query_vcd, render_waveform_png, timescale_fs, value_at
from .verilog import generate_testbench as build_testbench
from .verilog import parse_module_ports, classify_control
from .verilog import robei_width, width_info
from .verilog_ast import KEYWORDS as VERILOG_KEYWORDS

# Wall clock at the moment this module was imported, i.e. a few milliseconds
# after the interpreter started.  File mtimes are compared against it, so
# editing a .py after the server loaded it flips ``stale``.  (Don't derive this
# from ``time.monotonic()``: that counts since boot, not since process start,
# and made every fresh process look two days old.)
PROCESS_STARTED = time.time()

TOP_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
FILE_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_QA_DIR_RE = re.compile(r"^(?:_qa_runs|qa_.*)$", re.IGNORECASE)
_RENDER_LOCK = threading.Lock()


def _source_dir() -> Path:
    return Path(__file__).resolve().parent


def _git_commit(directory: Path) -> str | None:
    """Short HEAD hash of the source checkout; ``None`` outside git, never raises."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(directory), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def _source_mtime(directory: Path) -> float | None:
    stamps = []
    for path in directory.glob("*.py"):
        try:
            stamps.append(path.stat().st_mtime)
        except OSError:
            continue
    return max(stamps) if stamps else None


def stale_warning() -> str | None:
    """A message when the running process predates the code on disk."""
    newest = _source_mtime(_source_dir())
    if newest is None or newest <= PROCESS_STARTED:
        return None
    from . import __version__ as server_version

    return (
        f"the MCP server loaded {server_version} at "
        f"{datetime.fromtimestamp(PROCESS_STARTED).isoformat(timespec='seconds')}, but "
        f"{_source_dir()}/*.py changed at "
        f"{datetime.fromtimestamp(newest).isoformat(timespec='seconds')}: restart the "
        "server (or reopen Codex) to run the new code"
    )


def _with_stale_note(payload: Any) -> Any:
    """Attach :func:`stale_warning` to a tool result (no-op when up to date)."""
    note = stale_warning()
    if not note or not isinstance(payload, dict):
        return payload
    result = dict(payload)
    warnings = result.get("warnings")
    result["warnings"] = [note, *warnings] if isinstance(warnings, list) else [note]
    return result


def self_check() -> dict[str, Any]:
    """Version, commit, source dir and staleness of the *running* process."""
    from . import __version__ as server_version

    source = _source_dir()
    newest = _source_mtime(source)

    def stamp(value: float | None) -> str | None:
        if value is None:
            return None
        return datetime.fromtimestamp(value).isoformat(timespec="seconds")

    return {
        "package_version": server_version,
        "git_commit": _git_commit(source),
        "source_dir": str(source),
        "process_started_at": stamp(PROCESS_STARTED),
        "process_age_s": round(time.time() - PROCESS_STARTED, 1),
        "newest_source_mtime": stamp(newest),
        "stale": bool(newest is not None and newest > PROCESS_STARTED),
    }


def _tool_path(path: Path | str) -> str:
    """Return a path the bundled Windows tools can actually open.

    Icarus/Yosys here open files through the ANSI CRT, so a non-ASCII path turns
    into ``??`` before the tool ever sees it.  Windows still offers an 8.3 alias
    for most volumes; use it when available and fail clearly when it is not.
    """
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = candidate.resolve()
    text = str(candidate)
    if os.name != "nt" or text.isascii():
        return text
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        size = kernel32.GetShortPathNameW(text, None, 0)
        if size:
            buffer = ctypes.create_unicode_buffer(size)
            if kernel32.GetShortPathNameW(text, buffer, size):
                short = buffer.value
                if short and short.isascii():
                    return short
    except Exception:
        pass
    raise ValueError(
        "unsupported_path: this Windows iverilog/yosys build cannot open non-ASCII "
        f"paths and no 8.3 alias is available: {text}; move the project to an ASCII path"
    )


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


def _new_run_dir(kind: str, base: Path | None = None) -> Path:
    """One directory per run: ``<base or workspace/build>/<kind>-<stamp>-<8 hex>``.

    ``base`` lets a project keep its own build products (``<project>/build``);
    it is still escape-checked against the workspace.
    """
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    root = (SETTINGS.workspace / "build") if base is None else Path(base)
    root = root.resolve()
    if not is_within(root, SETTINGS.workspace):
        raise ValueError(f"run directory must stay inside the workspace: {root}")
    run_dir = root / f"{kind}-{stamp}-{uuid.uuid4().hex[:8]}"
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


INLINE_IMAGE_LIMIT = 5 * 1024 * 1024


def inline_image_payload(path: Path | str, limit: int | None = None) -> dict[str, Any]:
    """Base64 payload for one PNG, or an ``image_too_large`` report.

    The caller keeps the file path either way; ``limit`` defaults to
    ``INLINE_IMAGE_LIMIT`` (5 MiB) and is read at call time so tests can shrink it.
    """
    target = Path(path)
    cap = INLINE_IMAGE_LIMIT if limit is None else int(limit)
    size = target.stat().st_size
    if size > cap:
        return {
            "ok": False,
            "code": "image_too_large",
            "path": str(target),
            "size_bytes": size,
            "limit_bytes": cap,
            "hint": f"inline images are capped at {cap} bytes; use the returned path instead",
        }
    return {
        "ok": True,
        "mime_type": "image/png",
        "path": str(target),
        "size_bytes": size,
        "data_b64": base64.b64encode(target.read_bytes()).decode("ascii"),
    }


RUN_DIR_RE = re.compile(
    r"^(?:fpga|lint|sim|library|truth-.+|headless-.+)-\d{8}-\d{6}-[0-9a-f]{8}$"
)


def _project_for(path: Path) -> Path | None:
    """Nearest ancestor inside the workspace that holds a ``*.proj`` file."""
    try:
        resolved = path.resolve()
    except OSError:
        return None
    for parent in resolved.parents:
        if not is_within(parent, SETTINGS.workspace):
            break
        if any(parent.glob("*.proj")):
            return parent
    return None


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
    from . import __version__ as server_version

    required = ("robei", "iverilog", "vvp", "yosys")
    check = self_check()
    return {
        "ok": all(tools[name]["exists"] for name in required),
        "verilator_available": verilator["exists"] and verilator["root_exists"],
        # ``package_version`` is the documented name; ``server_version`` stays for
        # clients written against 1.4.
        "package_version": server_version,
        "git_commit": check["git_commit"],
        "source_dir": check["source_dir"],
        "process_started_at": check["process_started_at"],
        "process_age_s": check["process_age_s"],
        "newest_source_mtime": check["newest_source_mtime"],
        "stale": check["stale"],
        "stale_warning": stale_warning(),
        "lifecycle": lifecycle.status(),
        "server_version": server_version,
        "mcp_version": mcp_version,
        "python_executable": sys.executable,
        "python_version": sys.version.split()[0],
        "workspace": str(SETTINGS.workspace),
        "robei_home": str(SETTINGS.robei_home),
        "model_roots": [str(p) for p in SETTINGS.model_roots],
        "tools": tools,
        "security": {"strict_path_scan": SETTINGS.strict_path_scan},
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
    return '"' + Path(_tool_path(path)).as_posix().replace('"', '\\"') + '"'


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


def _verilator_timing_support() -> tuple[bool, str | None]:
    """``--timing`` exists from Verilator 5.0; older builds reject the flag outright."""
    result = _run(
        [SETTINGS.verilator, "--version"],
        cwd=SETTINGS.workspace,
        timeout_s=10,
        env_extra={"VERILATOR_ROOT": str(SETTINGS.verilator_root)},
    )
    text = ((result.get("stdout") or "") + (result.get("stderr") or "")).strip()
    match = re.search(r"Verilator\s+(\d+)\.(\d+)", text)
    if not match:
        return False, None
    version = f"{match.group(1)}.{match.group(2)}"
    return (int(match.group(1)), int(match.group(2))) >= (5, 0), version


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

    timing_ok, timing_version = _verilator_timing_support()

    def _command(with_timing: bool) -> list[str | Path]:
        cmd: list[str | Path] = [
            SETTINGS.verilator,
            "--lint-only",
            "-Wall",
            "-Wno-fatal",
            "-Wno-DECLFILENAME",
        ]
        if with_timing:
            cmd.append("--timing")
        if top:
            cmd.extend(["--top-module", top])
        cmd.extend(_tool_path(source) for source in sources)
        return cmd

    cmd = _command(timing_ok)
    result = _run(cmd, cwd=run_dir, timeout_s=timeout_s, env_extra={"VERILATOR_ROOT": str(SETTINGS.verilator_root)})
    text = (result.get("stdout") or "") + (result.get("stderr") or "")
    timing_warning = None
    if timing_ok and not result.get("ok") and "--timing" in text and re.search(
        r"(unknown|unrecogni[sz]ed|invalid|bad).{0,20}timing|timing.{0,20}(unknown|not supported)",
        text,
        re.IGNORECASE,
    ):
        # Version said yes but this build refuses the flag: fall back and say so.
        cmd = _command(False)
        result = _run(cmd, cwd=run_dir, timeout_s=timeout_s, env_extra={"VERILATOR_ROOT": str(SETTINGS.verilator_root)})
        text = (result.get("stdout") or "") + (result.get("stderr") or "")
        timing_ok = False
        timing_warning = (
            f"verilator {timing_version or 'unknown'} rejected --timing; lint ran without it "
            "(delays and #-based clocks are then reported as errors)"
        )
    warnings, errors = _parse_verilator(text)
    info.update(
        {
            "ok": bool(result["ok"] and not errors),
            "timing": timing_ok,
            "timing_version": timing_version,
            "timing_warning": timing_warning,
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
        _tool_path(iverilog_out),
    ]
    if top:
        iverilog_cmd.extend(["-s", top])
    iverilog_cmd.extend(_tool_path(source) for source in sources)
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
    base: Path | None = None,
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
            "reason": "source references paths outside the workspace or paths that cannot be verified statically",
            # Mirror the decisive lists at the top level: clients that read
            # result["rejected"] must not get None just because the detail also
            # lives under "security".
            "strict": scan.get("strict"),
            "rejected": scan.get("rejected") or [],
            "unresolved": scan.get("unresolved") or [],
            "scanned_files": scan.get("scanned_files") or [],
            "security": scan,
            "compile": None,
            "simulation": None,
            "vcd_paths": [],
        }
    run_dir = _new_run_dir("sim", base=base)
    sim_vvp = run_dir / "sim.vvp"

    _notify(progress, 0, 2, "iverilog: compiling")
    compile_result = _run(
        [
            SETTINGS.iverilog,
            "-g2012",
            "-s",
            top,
            "-o",
            _tool_path(sim_vvp),
            *[_tool_path(source) for source in sources],
        ],
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
        [SETTINGS.vvp, _tool_path(sim_vvp)],
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
        "strict": scan.get("strict"),
        "rejected": scan.get("rejected") or [],
        "unresolved": scan.get("unresolved") or [],
        "security": {k: scan[k] for k in ("ok", "strict", "rejected", "unresolved")},
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
    inline: bool = False,
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
    # One directory per run: two concurrent simulations of the same test used to
    # rewrite the same tb_source/deps files while the other process was reading.
    out_dir = _new_run_dir(
        f"headless-{project_dir.name}-{stem}", base=project_dir / "build"
    )
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
        + "    $dumpvars(0, " + stem + ");\n"
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
    result = simulate(
        files=[str(tb_src), *[str(d) for d in deps]],
        top=stem,
        timeout_s=timeout_s,
        base=project_dir / "build",
    )
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
            png = render_waveform(
                vcds[0], names, title=f"{stem} (headless iverilog)", inline=inline
            )
            result["plot"] = {
                "ok": png.get("ok"),
                "path": png.get("path"),
                "relative_path": png.get("relative_path"),
            }
            if png.get("inline") is not None:
                result["plot"]["inline"] = png["inline"]
        except Exception as exc:
            result["plot"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return result


def run_all_tests(
    proj_path: str,
    timeout_s: int = 60,
    render: bool = False,
    progress=None,
) -> dict[str, Any]:
    """Run every ``test/*.test`` model in a project and aggregate the result.

    One failing test does not stop the batch: the payload keeps per-test rc,
    duration, VCD paths and the error tail so the caller can see what broke.
    """
    proj = resolve_project_path(proj_path)
    if not is_within(proj, SETTINGS.workspace):
        raise ValueError("refusing to simulate a project outside the workspace")
    test_dir = proj.parent / "test"
    tests = sorted(test_dir.glob("*.test")) if test_dir.is_dir() else []
    rows: list[dict[str, Any]] = []
    for index, test_path in enumerate(tests):
        _notify(progress, index, max(1, len(tests)), f"running {test_path.stem}")
        started = time.monotonic()
        try:
            result = simulate_project(
                proj_path=str(proj),
                test_name=test_path.stem,
                timeout_s=timeout_s,
                render=render,
            )
            row = {
                "test": test_path.stem,
                "ok": bool(result.get("ok")),
                "returncode": result.get("returncode"),
                "duration_ms": round((time.monotonic() - started) * 1000),
                "vcd_paths": result.get("vcd_paths") or [],
                "error_tail": [] if result.get("ok") else _result_tail(result),
            }
            if result.get("plot"):
                row["plot"] = result["plot"]
        except Exception as exc:  # one broken test must not hide the others
            row = {
                "test": test_path.stem,
                "ok": False,
                "returncode": None,
                "duration_ms": round((time.monotonic() - started) * 1000),
                "vcd_paths": [],
                "error_tail": [f"{type(exc).__name__}: {exc}"],
            }
        rows.append(row)
    _notify(progress, len(tests), max(1, len(tests)), "done")
    passed = sum(1 for row in rows if row["ok"])
    return {
        "ok": bool(rows) and passed == len(rows),
        "project": str(proj),
        "project_relative": _rel(proj),
        "total": len(rows),
        "passed": passed,
        "failed": len(rows) - passed,
        "tests": rows,
        "vcd_paths": [path for row in rows for path in row["vcd_paths"]],
    }



def _window_output_path(source: Path, out: str | None, suffix: str) -> tuple[Path, Path | None]:
    project = _project_for(source)
    base = project / "build" / "vcd_windows" if project is not None else SETTINGS.workspace / "build" / "vcd_windows"
    if out:
        requested = Path(out)
        if requested.is_absolute() or ".." in requested.parts:
            raise PathEscape(f"output path escapes workspace: {out}")
        if not FILE_RE.match(out) or not out.lower().endswith(suffix):
            raise ValueError(f"out must be a simple {suffix} file name")
        target = base / out
    else:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = base / f"{source.stem}-window-{stamp}-{uuid.uuid4().hex[:8]}{suffix}"
    target = target.resolve()
    if not is_within(target, SETTINGS.workspace):
        raise PathEscape(f"output path escapes workspace: {target}")
    return target, project


def export_vcd_window(
    vcd: str,
    signals: list[str] | None,
    t_start: int,
    t_end: int,
    format: str = "csv",
    step: str | int = "edges",
    out: str | None = None,
    overwrite: bool = False,
    include_initial: bool = True,
) -> dict[str, Any]:
    """Export a bounded VCD window without changing the source VCD."""
    path = resolve_vcd_path(vcd)
    data = parse_vcd(path)
    if format not in {"csv", "md"}:
        raise ValueError("format must be csv or md")
    if not isinstance(t_start, int) or not isinstance(t_end, int) or t_start < 0 or t_end < t_start:
        raise ValueError("t_start/t_end must be non-negative integers with t_start <= t_end")
    if data.max_time is not None and t_end > data.max_time:
        raise ValueError(f"t_end {t_end} exceeds VCD max_time {data.max_time}")
    if step != "edges":
        if not isinstance(step, int) or step <= 0:
            raise ValueError("step must be 'edges' or a positive integer VCD tick")
    selected = list(data.signals.values()) if signals is None else match_signals(data, signals)
    if not selected:
        raise ValueError("signals must not be empty")
    dedup: dict[str, Any] = {signal.full_name: signal for signal in selected}
    selected = list(dedup.values())
    times: set[int] = {t_start, t_end}
    if step == "edges":
        for signal in selected:
            times.update(
                t for t, _ in data.changes.get(signal.identifier, []) if t_start <= t <= t_end
            )
    else:
        times.update(range(t_start, t_end + 1, step))
    ordered_times = sorted(times)
    rows: list[dict[str, Any]] = []
    initial_missing: list[str] = []
    for tick in ordered_times:
        row: dict[str, Any] = {"time": tick}
        for signal in selected:
            value = value_at(data, signal, tick)
            if not value.get("found"):
                if tick == t_start and include_initial:
                    initial_missing.append(signal.full_name)
                row[signal.full_name] = None
            else:
                row[signal.full_name] = value.get("value")
        rows.append(row)
    suffix = ".csv" if format == "csv" else ".md"
    target, project = _window_output_path(path, out, suffix)
    if target.exists() and not overwrite:
        raise FileExistsError(f"refusing to overwrite existing file: {target}")
    if format == "csv":
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        names = [signal.full_name for signal in selected]
        writer.writerow(["time", *names])
        for row in rows:
            writer.writerow([row["time"], *["" if row[name] is None else row[name] for name in names]])
        text = stream.getvalue()
    else:
        names = [signal.full_name for signal in selected]
        lines = [
            f"# VCD window: `{_rel(path)}`",
            f"- timescale: `{data.timescale}`",
            f"- window: `{t_start}`..`{t_end}` ticks",
            f"- step: `{step}`",
            "",
            "| time | " + " | ".join(names) + " |",
            "| ---: | " + " | ".join("---" for _ in names) + " |",
        ]
        for row in rows:
            lines.append("| " + str(row["time"]) + " | " + " | ".join(
                "未定义" if row[name] is None else str(row[name]) for name in names
            ) + " |")
        text = "\n".join(lines) + "\n"
    encoded_size = len(text.encode("utf-8"))
    if encoded_size > SETTINGS.max_vcd_bytes:
        raise ValueError(f"export exceeds size limit: {encoded_size} > {SETTINGS.max_vcd_bytes}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\n")
    return {
        "ok": True,
        "source_vcd": str(path),
        "source_relative_path": _rel(path),
        "path": str(target),
        "relative_path": _rel(target),
        "format": format,
        "timescale": data.timescale,
        "timescale_fs": timescale_fs(data.timescale),
        "window": {"start": t_start, "end": t_end, "step": step, "include_initial": include_initial},
        "signals": [_signal_descriptor(signal) for signal in selected],
        "rows": len(rows),
        "change_count": max(0, len(rows) - 1),
        "initial_missing": sorted(set(initial_missing)),
        "size_bytes": encoded_size,
        "project": _rel(project) if project is not None else None,
    }


def _signal_descriptor(signal) -> dict[str, Any]:
    return {
        "name": signal.full_name,
        "simple_name": signal.name,
        "scope": ".".join(signal.scope),
        "width": signal.width,
        "type": signal.var_type,
        "range": signal.bit_range,
        "id": signal.identifier,
    }


def _find_reusable_vcd(project_dir: Path, stem: str) -> Path | None:
    candidates = sorted(
        (project_dir / "build").glob(f"sim-*/{stem}.vcd"),
        key=lambda item: item.stat().st_mtime_ns,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _report_source_line(path: Path | None, needle: str) -> int | None:
    if path is None or not path.is_file() or not needle:
        return None
    try:
        for lineno, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if needle in line:
                return lineno
    except OSError:
        return None
    return None


def _report_value(value: Any) -> str:
    """Render unavailable leaves without changing nullable API/evidence values."""
    def clean(item: Any) -> Any:
        if item is None:
            return "未提供"
        if isinstance(item, dict):
            return {key: clean(val) for key, val in item.items()}
        if isinstance(item, (list, tuple)):
            return [clean(val) for val in item]
        return item

    value = clean(value)
    return json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)


def build_report(
    proj_path: str,
    tests: list[str] | None = None,
    out: str | None = None,
    include: list[str] | None = None,
    truth_map: dict[str, Any] | None = None,
    overwrite: bool = False,
    timeout_s: int = 60,
    reuse_existing_runs: bool = False,
) -> dict[str, Any]:
    """Build a fact-only Markdown report and a line-addressable evidence ledger."""
    allowed = {"simulation", "truth_table", "display", "assertions", "waveform", "synthesis"}
    sections = set(include or allowed)
    unknown = sorted(sections - allowed)
    if unknown:
        raise ValueError(f"unknown include section(s): {unknown}; expected {sorted(allowed)}")
    proj = resolve_project_path(proj_path)
    if not is_within(proj, SETTINGS.workspace):
        raise PathEscape(f"refusing to report a project outside the workspace: {proj}")
    project_dir = proj.parent
    stems = [str(item).removesuffix(".test") for item in (tests or [])]
    if tests is None:
        stems = sorted(path.stem for path in (project_dir / "test").glob("*.test"))
    for stem in stems:
        if not MODEL_NAME_RE.match(stem):
            raise ValueError(f"invalid test name: {stem!r}")
        if not (project_dir / "test" / f"{stem}.test").is_file():
            raise FileNotFoundError(f"test model not found: {project_dir / 'test' / (stem + '.test')}")
    report_dir = project_dir / "report"
    report_name = out or f"{project_dir.name}.md"
    requested = Path(report_name)
    target = requested if requested.is_absolute() else report_dir / requested
    target = target.resolve()
    if target.suffix.lower() != ".md" or not is_within(target, report_dir.resolve()):
        raise PathEscape("out must be a Markdown file inside workspace/<project>/report")
    evidence_path = target.with_suffix(".evidence.jsonl")
    if (target.exists() or evidence_path.exists()) and not overwrite:
        raise FileExistsError(f"refusing to overwrite report or evidence: {target}")

    evidence: list[dict[str, Any]] = []
    def fact(section: str, field: str, value: Any, source: str | None = None, source_line: int | None = None) -> int:
        evidence.append({"section": section, "field": field, "value": value, "source": source, "source_line": source_line})
        return len(evidence)

    rows: list[dict[str, Any]] = []
    missing: list[dict[str, str]] = []
    for stem in stems:
        reused = False
        vcd = _find_reusable_vcd(project_dir, stem) if reuse_existing_runs else None
        if vcd:
            reused = True
            result = {"ok": True, "code": "reused_existing_run", "vcd_paths": [str(vcd)], "display_output": "", "headless": {"reused": True}}
        else:
            result = simulate_project(str(proj), stem, timeout_s=timeout_s, render=False)
        vcds = result.get("vcd_paths") or []
        display_output = result.get("display_output") or ""
        display_truncated = bool(result.get("display_truncated")) or len(display_output) >= SETTINGS.max_output_chars
        test_row = {
            "test": stem,
            "ok": bool(result.get("ok")),
            "returncode": (result.get("simulation") or {}).get("returncode") if isinstance(result.get("simulation"), dict) else None,
            "duration_ms": (result.get("simulation") or {}).get("duration_ms") if isinstance(result.get("simulation"), dict) else None,
            "vcd_paths": vcds,
            "reused": reused,
            "display_output": display_output,
            "display_truncated": display_truncated,
            "headless": result.get("headless") or {},
        }
        rows.append(test_row)
        unavailable = [field for field in ("returncode", "duration_ms") if test_row[field] is None]
        if unavailable:
            missing.append({"section": "simulation", "test": stem, "reason": "未提供（" + ", ".join(unavailable) + "）"})
        for field in ("returncode", "duration_ms"):
            fact_line = fact("simulation", f"{stem}.{field}", test_row[field], str((result.get("headless") or {}).get("tb_source_rel") or proj), None)
            test_row[f"{field}_evidence"] = fact_line
        vcd_fact = fact("simulation", f"{stem}.vcd_paths", vcds, str(proj), None)
        test_row["vcd_evidence"] = vcd_fact

    if "truth_table" in sections:
        truth_rows: list[dict[str, Any]] = []
        for stem in stems:
            mapping = (truth_map or {}).get(stem)
            if mapping is None:
                top = stem[3:] if stem.startswith("tb_") else None
                mapping = {"top": top} if top else None
            elif isinstance(mapping, str):
                mapping = {"top": mapping}
            if not mapping or not mapping.get("top"):
                missing.append({"section": "truth_table", "test": stem, "reason": "未提供"})
                continue
            top = str(mapping["top"])
            verilog = mapping.get("verilog_path")
            if verilog is None:
                candidates = [project_dir / "verilog" / f"{top}.v", project_dir / "build" / f"{top}.v"]
                verilog = next((str(item) for item in candidates if item.is_file()), None)
            if verilog is None:
                missing.append({"section": "truth_table", "test": stem, "reason": "未提供"})
                continue
            from . import p1
            table = p1.truth_table(verilog_path=str(verilog), top=top, deps=mapping.get("deps"))
            row_count_evidence = fact("truth_table", f"{stem}.row_count", table.get("row_count"), table.get("testbench"), None)
            expected_evidence = fact("truth_table", f"{stem}.expected_rows", table.get("expected_rows"), table.get("testbench"), None)
            row_evidence = [
                fact("truth_table", f"{stem}.row[{index}]", item, table.get("testbench"), None)
                for index, item in enumerate(table.get("rows", []))
            ]
            truth_rows.append({
                "test": stem,
                **table,
                "_row_count_evidence": row_count_evidence,
                "_expected_evidence": expected_evidence,
                "_row_evidence": row_evidence,
            })
        truth_result = truth_rows
    else:
        truth_result = []

    if "waveform" in sections:
        for row in rows:
            if not row["vcd_paths"]:
                missing.append({"section": "waveform", "test": row["test"], "reason": "未提供"})
                continue
            try:
                listing = read_waveform(row["vcd_paths"][0])
                names = [item["name"] for item in listing.get("signals", [])][:16]
                row["plot"] = render_waveform(row["vcd_paths"][0], names, title=f"{row['test']} (report)")
                fact("waveform", f"{row['test']}.signal_count", len(names), row["vcd_paths"][0], None)
            except Exception as exc:
                row["plot"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                missing.append({"section": "waveform", "test": row["test"], "reason": "未提供"})
    else:
        for row in rows:
            row.pop("plot", None)

    display_lines: list[dict[str, Any]] = []
    if "display" in sections or "assertions" in sections:
        for row in rows:
            if "display" in sections and not row["display_output"]:
                missing.append({"section": "display", "test": row["test"], "reason": "未提供"})
            tb_source = Path((row.get("headless") or {}).get("tb_source", "")) if (row.get("headless") or {}).get("tb_source") else None
            for line in row["display_output"].splitlines():
                item = {"test": row["test"], "line": line, "source": (row.get("headless") or {}).get("tb_source_rel"), "source_line": _report_source_line(tb_source, line)}
                display_lines.append(item)
                fact("display", f"{row['test']}.display", line, item["source"], item["source_line"])
            row["display_truncated_evidence"] = fact("display", f"{row['test']}.display_truncated", row["display_truncated"], (row.get("headless") or {}).get("tb_source_rel"), None)
    if "display" not in sections:
        display_lines = []
    if "assertions" in sections and not any(re.search(r"(?i)pass|fail|assert|mismatch|selftest", item["line"]) for item in display_lines):
        missing.append({"section": "assertions", "test": "*", "reason": "未提供"})

    synthesis = None
    if "synthesis" in sections:
        from . import fpga
        try:
            synthesis = fpga.synthesize_project(str(proj), timeout_s=timeout_s)
            synthesis_evidence = {}
            for key in ("utilization", "utilization_capacity", "utilization_percent"):
                synthesis_evidence[key] = fact("synthesis", key, synthesis.get(key), synthesis.get("summary_path"), None)
            synthesis["_evidence"] = synthesis_evidence
        except Exception as exc:
            synthesis = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            missing.append({"section": "synthesis", "test": "project", "reason": "未提供"})

    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in evidence), encoding="utf-8", newline="\n")
    lines = [f"# Robei MCP report: {project_dir.name}", "", f"- project: `{_rel(proj)}`", ""]
    if "simulation" in sections:
        lines += ["## Simulation facts", "", "| test | ok | rc | duration_ms | VCD | source |", "| --- | ---: | ---: | ---: | --- | --- |"]
        for row in rows:
            lines.append(f"| `{row['test']}` | `{row['ok']}` | `{_report_value(row['returncode'])}` ([evidence:{row['returncode_evidence']}]) | `{_report_value(row['duration_ms'])}` ([evidence:{row['duration_ms_evidence']}]) | `{', '.join(_rel(Path(p)) for p in row['vcd_paths']) or '未提供'}` ([evidence:{row['vcd_evidence']}]) | {'reused' if row['reused'] else 'simulate_project'} |")
        lines.append("")
    if "display" in sections:
        lines += ["## Raw $display facts", ""]
        for item in display_lines:
            lines.append(f"- `{item['test']}`: `{item['line']}` ([source:{_report_value(item['source'])}:{_report_value(item['source_line'])}])")
        for row in rows:
            lines.append(f"- `{row['test']}` display_truncated=`{row['display_truncated']}` ([evidence:{row.get('display_truncated_evidence')}])")
        if not display_lines:
            lines.append("未提供")
        lines.append("")
    if "assertions" in sections:
        lines += ["## Assertion facts", ""]
        assertion_lines = [item for item in display_lines if re.search(r"(?i)pass|fail|assert|mismatch|selftest", item["line"])]
        lines.extend(f"- `{item['test']}`: `{item['line']}` ([source:{_report_value(item['source'])}:{_report_value(item['source_line'])}])" for item in assertion_lines)
        if not assertion_lines:
            lines.append("未提供")
        lines.append("")
    if "truth_table" in sections:
        lines += ["## Truth-table facts", ""]
        for table in truth_result:
            lines.append(
                f"- `{table['test']}`: `row_count={_report_value(table.get('row_count'))}` "
                f"([evidence:{table.get('_row_count_evidence')}]), "
                f"`expected_rows={_report_value(table.get('expected_rows'))}` "
                f"([evidence:{table.get('_expected_evidence')}])"
            )
            for index, item in enumerate(table.get("rows", [])):
                evidence_id = (table.get("_row_evidence") or [None] * len(table.get("rows", [])))[index]
                lines.append(f"  - `{_report_value(item)}` ([evidence:{evidence_id}])")
        if not truth_result:
            lines.append("未提供")
        lines.append("")
    if "waveform" in sections:
        lines += ["## Waveform facts", ""]
        for row in rows:
            plot = row.get("plot") or {}
            lines.append(f"- `{row['test']}`: `{plot.get('relative_path') or '未提供'}`")
        lines.append("")
    if "synthesis" in sections:
        lines += ["## Synthesis facts", "", "| field | value | evidence |", "| --- | --- | --- |"]
        if synthesis and synthesis.get("ok"):
            for key in ("utilization", "utilization_capacity", "utilization_percent"):
                lines.append(f"| `{key}` | `{_report_value(synthesis.get(key))}` | [evidence:{(synthesis.get('_evidence') or {}).get(key)}] |")
        else:
            lines.append("| synthesis | 未提供 | - |")
        lines.append("")
    lines += ["## Missing", ""]
    lines.extend(f"- `{item['section']}` / `{item['test']}`: {item['reason']}" for item in missing) or lines.append("无")
    lines += ["", f"Evidence: `{_rel(evidence_path)}`"]
    report_text = "\n".join(lines) + "\n"
    target.write_text(report_text, encoding="utf-8", newline="\n")
    return {
        "ok": True,
        "project": str(proj),
        "project_relative": _rel(proj),
        "report_path": str(target),
        "report_relative_path": _rel(target),
        "evidence_path": str(evidence_path),
        "evidence_relative_path": _rel(evidence_path),
        "tests": rows,
        "sections": sorted(sections),
        "missing": missing,
        "size_bytes": target.stat().st_size,
        "sha256": _sha256(target),
    }


def _result_tail(result: dict[str, Any], lines: int = 8) -> list[str]:
    """Error text of a simulate() payload: top level plus the compile/run sections."""
    chunks = [result.get("stdout") or "", result.get("stderr") or ""]
    for key in ("compile", "simulation"):
        section = result.get(key)
        if isinstance(section, dict):
            chunks.extend([section.get("stderr") or "", section.get("stdout") or ""])
    return _tail_lines("\n".join(chunk for chunk in chunks if chunk), lines)


def _tail_lines(text: str, lines: int = 8) -> list[str]:
    kept = [line.rstrip() for line in (text or "").splitlines() if line.strip()]
    return kept[-lines:]


def _norm_value(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower().replace("_", "")
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
    allow_extra_signals: bool = False,
) -> dict[str, Any]:
    """Diff two VCDs signal by signal, using change times from both sides.

    Values are compared at the union of both timelines, so a signal that simply
    never toggles in one run is reported at the moment the other one does.
    Timescales are converted to a common unit instead of comparing raw ticks.
    Missing signals, width mismatches, an empty intersection and differing run
    lengths all make the result ``ok: false`` — a comparison that silently
    skips most of the data is not a pass.  Run lengths are compared after both
    sides are converted to femtoseconds, so ``#20`` at 1ns and ``#20000`` at
    1ps count as the same end time.

    ``allow_extra_signals=True`` compares only the shared signal set and reports
    the extra names instead of failing, for golden-vs-alternative comparisons
    where the two hierarchies legitimately differ.
    """
    golden_path = resolve_vcd_path(golden_vcd)
    dut_path = resolve_vcd_path(dut_vcd)
    golden = parse_vcd(golden_path)
    dut = parse_vcd(dut_path)
    max_mismatches = max(1, min(int(max_mismatches), 1000))

    unknown: list[str] = []
    ambiguous: list[str] = []
    if signals:
        requested: list[str] = []
        for query in signals:
            found = None
            for data in (golden, dut):
                try:
                    matches = match_signals(data, [query])
                except KeyError as exc:
                    message = str(exc)
                    if message.startswith("ambiguous") and message not in ambiguous:
                        ambiguous.append(message)
                    continue
                if matches:
                    found = matches[0].full_name
                    break
            if found is None:
                # Neither file has it: report it instead of raising KeyError.
                unknown.append(query)
            else:
                requested.append(found)
        missing_dut = [n for n in requested if n not in dut.signals]
        missing_golden = [n for n in requested if n not in golden.signals]
        comparable = [n for n in requested if n in golden.signals and n in dut.signals]
    else:
        missing_dut = [n for n in golden.signals if n not in dut.signals]
        missing_golden = [n for n in dut.signals if n not in golden.signals]
        comparable = [n for n in golden.signals if n in dut.signals]

    g_fs = timescale_fs(golden.timescale)
    d_fs = timescale_fs(dut.timescale)
    timescale_known = g_fs is not None and d_fs is not None

    results: list[dict[str, Any]] = []
    total = 0
    incompatible: list[str] = []
    for name in comparable:
        g_sig = golden.signals[name]
        d_sig = dut.signals[name]
        g_changes, g_times = _sorted_changes(golden, g_sig)
        d_changes, d_times = _sorted_changes(dut, d_sig)
        width_matches = g_sig.width == d_sig.width
        if not width_matches:
            incompatible.append(name)
            results.append(
                {
                    "signal": name,
                    "width": g_sig.width,
                    "dut_width": d_sig.width,
                    "width_matches": False,
                    "golden_changes": len(g_changes),
                    "dut_changes": len(d_changes),
                    "mismatch_count": 0,
                    "first_mismatch": None,
                    "mismatches": [],
                    "skipped_reason": "width mismatch",
                }
            )
            continue

        if timescale_known:
            timeline = sorted({t * g_fs for t in g_times} | {t * d_fs for t in d_times})
        else:
            timeline = sorted(set(g_times) | set(d_times))
        mismatches: list[dict[str, Any]] = []
        count = 0
        for when in timeline:
            if timescale_known:
                gv = _value_at_changes(g_changes, g_times, when // g_fs)
                dv = _value_at_changes(d_changes, d_times, when // d_fs)
            else:
                gv = _value_at_changes(g_changes, g_times, when)
                dv = _value_at_changes(d_changes, d_times, when)
            if _norm_value(gv) != _norm_value(dv):
                count += 1
                if len(mismatches) < max_mismatches:
                    entry: dict[str, Any] = {
                        "time": when // g_fs if timescale_known else when,
                        "golden": gv,
                        "dut": dv,
                    }
                    if timescale_known:
                        entry["time_fs"] = when
                        entry["golden_time"] = when // g_fs
                        entry["dut_time"] = when // d_fs
                    mismatches.append(entry)
        total += count
        results.append(
            {
                "signal": name,
                "width": g_sig.width,
                "dut_width": d_sig.width,
                "width_matches": True,
                "golden_changes": len(g_changes),
                "dut_changes": len(d_changes),
                "mismatch_count": count,
                "first_mismatch": mismatches[0] if mismatches else None,
                "mismatches": mismatches,
            }
        )

    differing = [row for row in results if row["mismatch_count"]]
    reasons: list[str] = []
    if not comparable:
        reasons.append("no comparable signals")
    if unknown:
        reasons.append("requested signal(s) not found: " + ", ".join(unknown[:10]))
    if ambiguous:
        reasons.append("; ".join(ambiguous[:5]))
    if missing_dut and not allow_extra_signals:
        reasons.append(f"{len(missing_dut)} golden signal(s) missing in dut")
    if missing_golden and not allow_extra_signals:
        reasons.append(f"{len(missing_golden)} dut signal(s) missing in golden")
    if incompatible:
        reasons.append(f"{len(incompatible)} signal(s) have different widths")
    if differing:
        reasons.append(f"{total} value mismatch(es)")
    if not timescale_known:
        reasons.append("timescale missing/unparseable; raw ticks could not be verified")
    golden_end_fs = (
        golden.max_time * g_fs if timescale_known and golden.max_time is not None else None
    )
    dut_end_fs = dut.max_time * d_fs if timescale_known and dut.max_time is not None else None
    if timescale_known and golden_end_fs is not None and dut_end_fs is not None:
        max_time_matches = golden_end_fs == dut_end_fs
    else:
        max_time_matches = golden.max_time == dut.max_time
    if not max_time_matches:
        if golden_end_fs is not None and dut_end_fs is not None:
            reasons.append(f"max_time differs ({golden_end_fs} fs vs {dut_end_fs} fs)")
        else:
            reasons.append("max_time differs")
    elif golden.max_time is None:
        reasons.append("no time markers in either VCD")
    return {
        "ok": not reasons,
        "reasons": reasons,
        "golden": str(golden_path),
        "dut": str(dut_path),
        "timescale": {"golden": golden.timescale, "dut": dut.timescale},
        "timescale_fs": {"golden": g_fs, "dut": d_fs},
        "timescale_comparable": timescale_known,
        "max_time": {"golden": golden.max_time, "dut": dut.max_time},
        "max_time_fs": {"golden": golden_end_fs, "dut": dut_end_fs},
        "max_time_matches": max_time_matches,
        "signal_count": len(comparable),
        "compared_signal_count": len(comparable) - len(incompatible),
        "mismatch_total": total,
        "differing_signals": [row["signal"] for row in differing],
        "incompatible_signals": incompatible,
        "missing_in_dut": missing_dut,
        "missing_in_golden": missing_golden,
        "unknown_signals": unknown,
        "ambiguous_signals": ambiguous,
        "allow_extra_signals": allow_extra_signals,
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
    out_dir: str | None = None,
    inline: bool = False,
) -> dict[str, Any]:
    """Render a waveform PNG.

    ``out_dir`` wins when given; otherwise a VCD that lives inside a project
    writes to ``<project>/build/waveforms`` and everything else falls back to
    ``workspace/build/waveforms`` (the pre-1.3 location).
    """
    path = resolve_vcd_path(vcd_path)
    data = parse_vcd(path)
    project = _project_for(path)
    if out_dir:
        base = resolve_workspace_path(out_dir, must_exist=False)
    elif project is not None:
        base = project / "build" / "waveforms"
    else:
        base = SETTINGS.workspace / "build" / "waveforms"
    if out_name:
        if not FILE_RE.match(out_name) or not out_name.lower().endswith(".png"):
            raise ValueError("out_name must be a simple .png file name")
        out_path = base / out_name
    else:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        out_path = base / f"{path.stem}-{stamp}-{uuid.uuid4().hex[:8]}.png"
    out_path = out_path.resolve()
    if not str(out_path).startswith(str(SETTINGS.workspace.resolve()) + os.sep):
        raise ValueError(f"output path escapes workspace: {out_path}")
    if out_path.exists():
        raise FileExistsError(f"refusing to overwrite existing file: {out_path}")
    # matplotlib keeps process-global state; concurrent renders in the MCP
    # worker pool must not interleave.
    with _RENDER_LOCK:
        result = render_waveform_png(data, signals, out_path, title=title)
    result["ok"] = True
    result["relative_path"] = _rel(out_path)
    result["vcd"] = _rel(path)
    result["out_dir"] = _rel(out_path.parent)
    result["project"] = _rel(project) if project is not None else None
    if inline:
        result["inline"] = inline_image_payload(out_path)
    return result


def _log_path_allowed(candidate: Path) -> bool:
    """Three roots only: ``logs/**``, ``build/<run dir>/**``, ``<project>/build/**``.

    A directory that merely happens to be called ``build`` is not enough - the
    project rule needs a ``*.proj`` sitting in the parent directory.
    """
    workspace = SETTINGS.workspace.resolve()
    try:
        parts = candidate.resolve().relative_to(workspace).parts
    except (ValueError, OSError):
        return False
    if not parts:
        return False
    if parts[0].lower() == "logs":
        return True
    if parts[0].lower() == "build":
        return len(parts) > 1 and bool(RUN_DIR_RE.match(parts[1]))
    for index in range(len(parts) - 1, 0, -1):
        if parts[index].lower() != "build":
            continue
        project_dir = workspace.joinpath(*parts[:index])
        return any(project_dir.glob("*.proj"))
    return False


def read_run_log(path: str, tail: int = 200, max_bytes: int = 500_000) -> dict[str, Any]:
    """Read a run log under ``workspace/logs`` or ``workspace/build``.

    Long files keep the tail (``truncated=True`` says the head was dropped); every
    ``fpga_step_failed`` / ``internal`` payload points at a file this tool can open.
    """
    candidate = resolve_workspace_path(path, must_exist=True)
    if not _log_path_allowed(candidate):
        raise LogPathEscape(
            f"{candidate} is not inside workspace/logs, workspace/build/<run dir> or "
            "a project's build/ directory"
        )
    if not candidate.is_file():
        raise FileNotFoundError(f"log not found: {candidate}")
    cap = max(4096, min(int(max_bytes), 5_000_000))
    size = candidate.stat().st_size
    truncated = size > cap
    with candidate.open("rb") as handle:
        if truncated:
            handle.seek(size - cap)
        data = handle.read(cap)
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    keep = max(0, int(tail))
    kept = lines[-keep:] if keep else []
    return {
        "ok": True,
        "path": str(candidate),
        "relative_path": _rel(candidate),
        "size_bytes": size,
        "max_bytes": cap,
        "truncated": truncated,
        "bytes_read": len(data),
        "line_count": len(lines),
        "returned_lines": len(kept),
        "note": (
            f"head dropped: only the last {len(data)} of {size} bytes are present"
            if truncated
            else None
        ),
        "lines": kept,
    }


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


def list_projects(limit: int = 200, include_qa: bool = False) -> dict[str, Any]:
    """Enumerate every Robei project inside the workspace (read-only).

    Answers the first question an agent usually has: which project am I working
    on, what models/tests does it have, and is there a build output already.
    QA scratch trees (``workspace/_qa_runs`` and ``workspace/qa_*``) are skipped
    unless ``include_qa=True``, so test residue does not drown the real projects.
    """
    root = SETTINGS.workspace
    projects: list[dict[str, Any]] = []
    problems: list[dict[str, str]] = []
    for proj in sorted(root.rglob("*.proj")):
        if not is_within(proj, root):
            continue
        if not include_qa and any(
            _QA_DIR_RE.match(part) for part in proj.relative_to(root).parts[:-1]
        ):
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
    text, export_warnings = model_to_verilog_with_warnings(
        model, parameter_overrides=parameter_overrides
    )

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
        "warnings": export_warnings,
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
    width: str | int,
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
    width: int | str = 1,
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
    datasize = _width_text(width, "width")
    width = robei_width(datasize)
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
    replacement = _port_tag(module, name, direction, datasize, datatype, side, index)
    pattern = re.compile(r'<Port\b[^>]*\bName="%s"[^>]*/>' % re.escape(name))
    replaced = False
    if pattern.search(text):
        for position, tag in enumerate(existing_tags):
            if re.search(r'\bName="%s"' % re.escape(name), tag):
                index = position
                break
        replacement = _port_tag(module, name, direction, datasize, datatype, side, index)
        new_text = pattern.sub(replacement, text, count=1)
        replaced = True
    else:
        insert_at = module_match.end()
        new_text = text[:insert_at] + "\n " + replacement + text[insert_at:]

    ET.fromstring(new_text)  # raises on malformed XML

    sha_before = _sha256(path)
    backup = _backup_file(path)
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(new_text)

    parsed = parse_robei_model(path)
    return {
        "ok": True,
        "model": module,
        "path": str(path),
        "relative_path": _rel(path),
        "port": {
            "name": name,
            "direction": direction,
            "width": width,
            "datasize": _width_value(datasize),
            "datatype": datatype,
            "side": side,
        },
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


def _model_xml(
    name: str,
    ports: list[dict] | None,
    code: str,
    comment: str,
    parameters: str = "",
) -> str:
    head = (
        f'<Module Category="Current" Class="module" Code="{_attr(code)}" Color="#d3d3d3" '
        f'Comment="{_attr(comment)}" Height="1080" Name="{name}" '
        f'Parameters="{_attr(parameters)}" Parent="0" '
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
                str(port.get("width", 1)),
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
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup = target.with_name(target.name + ".bak-" + stamp)
    counter = 1
    while backup.exists():
        backup = target.with_name(target.name + ".bak-" + stamp + f"-{counter}")
        counter += 1
    backup.write_bytes(target.read_bytes())
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
    warnings: list[str] = []
    for model_file in sorted(model_dir.glob("*.model")):
        try:
            model = parse_robei_model(model_file)
            text, model_warnings = model_to_verilog_with_warnings(model)
        except Exception as exc:
            errors.append({"file": str(model_file), "error": f"{type(exc).__name__}: {exc}"})
            continue
        target = verilog_dir / f"{model['name']}.v"
        if target.exists() and not overwrite:
            skipped.append(str(target))
            continue
        target.write_text(text, encoding="utf-8", newline="\n")
        warnings.extend(f"{model['name']}: {item}" for item in model_warnings)
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
        "warnings": warnings,
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


BIT_SELECT_RE = re.compile(
    r"^(?P<name>[A-Za-z_][A-Za-z0-9_$]*(?:\.[A-Za-z_][A-Za-z0-9_$]*)?)"
    r"\s*\[\s*(?P<sel>\d+(?:\s*:\s*\d+)?)\s*\]$"
)
WIRE_SELECT_HINT = (
    "a <Wire> endpoint cannot carry a bit-select: Robei silently deletes such a "
    "wire when it saves the canvas, leaving the port dangling. Put the select on "
    'the instance port instead, e.g. wires=[{"from": "a[0]", "to": "u1.x"}] '
    'writes Connect="a[0]" on u1.x.'
)
PARAM_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
PARAM_HINT = 'pass parameters as an object, e.g. {"LEN": 8}'
PARAM_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_$])([A-Za-z_][A-Za-z0-9_$]*)(?![A-Za-z0-9_$])")
PARAM_EXPR_RE = re.compile(r"^[A-Za-z0-9_$+\-*/%() ]+$")
PARAM_ACTIONS = {"set", "add", "rename", "delete"}
def _closest(name: Any, candidates: Any, limit: int = 3) -> str:
    """`` (closest: a, b)`` for an unknown name; empty when nothing is near."""
    pool = [str(item) for item in candidates if str(item)]
    found = difflib.get_close_matches(str(name), pool, n=limit, cutoff=0.4)
    return f" (closest: {', '.join(found)})" if found else ""


def _names(values: Any, limit: int = 12) -> str:
    pool = sorted({str(item) for item in values if str(item)})
    if not pool:
        return "(none)"
    shown = ", ".join(pool[:limit])
    return shown + (f", ... ({len(pool)} total)" if len(pool) > limit else "")


def _parameter_hint(name: str, candidates: Any) -> str:
    pool = [str(item) for item in candidates if str(item)]
    close = difflib.get_close_matches(name, pool, n=3, cutoff=0.35)
    suffix = f" closest: {', '.join(close)}." if close else ""
    return (
        "parameter names must be legal Verilog identifiers and must not be keywords; "
        f"available names: {_names(pool)}.{suffix}"
    )


def _validate_parameter_name(name: str, parameters: list[dict[str, str]], ports: list[dict[str, Any]], *, allow_existing: bool = False) -> str:
    wanted = str(name or "").strip()
    if not PARAM_NAME_RE.fullmatch(wanted) or wanted in VERILOG_KEYWORDS:
        raise BadRequest(f"invalid parameter name {wanted!r}", _parameter_hint(wanted, [p["name"] for p in parameters]))
    existing = {p["name"].upper() for p in parameters}
    port_names = {str(p.get("name", "")).upper() for p in ports}
    if wanted.upper() in port_names:
        raise BadRequest(
            f"parameter {wanted!r} conflicts with a port",
            f"choose a name different from port(s) {_names(port_names)}; {_parameter_hint(wanted, port_names)}",
        )
    if not allow_existing and wanted.upper() in existing:
        raise BadRequest(
            f"parameter {wanted!r} already exists",
            f"choose a new name; declared parameters: {_names(existing)}; {_parameter_hint(wanted, existing)}",
        )
    return wanted


def _replace_parameter_token(text: str, old: str, new: str) -> str:
    pattern = re.compile(rf"(?<![A-Za-z0-9_$]){re.escape(old)}(?![A-Za-z0-9_$])")
    return pattern.sub(new, text)


def _parameter_values(parameters: list[dict[str, str]]) -> dict[str, int | None]:
    values: dict[str, int | None] = {}
    expressions = {p["name"]: str(p.get("default", "")).strip() for p in parameters}
    states: dict[str, int] = {}

    def resolve(name: str) -> int | None:
        if name in values:
            return values[name]
        if states.get(name) == 1:
            raise BadRequest(
                f"parameter expression cycle includes {name}",
                "parameter defaults must not refer to one another cyclically; "
                f"check {', '.join(expressions)}",
            )
        states[name] = 1
        expr = expressions[name]
        if not expr or not PARAM_EXPR_RE.fullmatch(expr):
            values[name] = None
            states[name] = 2
            return None
        names = PARAM_TOKEN_RE.findall(expr)
        unknown = [item for item in names if item not in expressions]
        if unknown:
            raise BadRequest(
                f"parameter {name} refers to undeclared identifier(s) {unknown}",
                f"declare {unknown[0]!r} first; declared parameters: {_names(expressions)}",
            )
        rendered = expr
        for ref in sorted(set(names), key=len, reverse=True):
            value = resolve(ref)
            if value is None:
                values[name] = None
                states[name] = 2
                return None
            rendered = _replace_parameter_token(rendered, ref, str(value))
        from .verilog import const_eval
        values[name] = const_eval(rendered)
        states[name] = 2
        return values[name]

    for key in expressions:
        resolve(key)
    return values


def _effective_width_for_datasize(datasize: Any, values: dict[str, int | None]) -> int | None:
    text = str(datasize or "").strip()
    if not text:
        return 1
    rendered = text
    for name, value in sorted(values.items(), key=lambda item: len(item[0]), reverse=True):
        if value is None:
            return None
        rendered = _replace_parameter_token(rendered, name, str(value))
    return robei_width(rendered)


def _instance_model_matches(instance: dict[str, Any], target: str) -> bool:
    target = target.lower()
    candidates = [instance.get("model_file"), instance.get("class"), instance.get("module")]
    return any(Path(str(item)).stem.lower() == target for item in candidates if item)


def _parse_override_values(text: str, child: dict[str, Any]) -> dict[str, str]:
    if not text:
        return {}
    parsed, bad = parse_parameter_text_checked(text)
    if bad:
        raise ValueError(f"cannot parse Parameters {text!r}: {bad}")
    declared = {p["name"].upper(): p["name"] for p in child.get("parameter_list") or []}
    result: dict[str, str] = {}
    for item in parsed:
        if item["name"].upper() not in declared:
            raise ValueError(f"unknown override parameter {item['name']!r}")
        result[declared[item["name"].upper()]] = item["default"].strip()
    return result


def _direct_parent_scan(target_path: Path, target_model: dict[str, Any], old_params: list[dict[str, str]], new_params: list[dict[str, str]], changed: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[tuple[Path, dict[str, Any], dict[str, str]]], int]:
    model_dir = target_path.parent
    warnings: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    updates: list[tuple[Path, dict[str, Any], dict[str, str]]] = []
    scanned = 0
    old_defaults = {p["name"]: p["default"] for p in old_params}
    new_defaults = {p["name"]: p["default"] for p in new_params}
    for parent_path in sorted(model_dir.glob("*.model")):
        if parent_path.resolve() == target_path.resolve():
            continue
        try:
            parent = parse_robei_model(parent_path)
        except Exception as exc:
            skipped.append({"parent_model": parent_path.stem, "path": str(parent_path), "reason": f"{type(exc).__name__}: {exc}"})
            continue
        for instance in parent.get("instances") or []:
            if not _instance_model_matches(instance, target_model["name"]):
                continue
            scanned += 1
            try:
                overrides = _parse_override_values(instance.get("parameters") or "", target_model)
                overridden = changed in overrides
                old_effective = dict(old_defaults)
                new_effective = dict(new_defaults)
                old_effective.update(overrides)
                new_effective.update(overrides)
                old_values = _parameter_values([{"name": k, "default": v} for k, v in old_effective.items()])
                new_values = _parameter_values([{"name": k, "default": v} for k, v in new_effective.items()])
            except Exception as exc:
                skipped.append({"parent_model": parent["name"], "path": str(parent_path), "instance": instance.get("instance"), "reason": f"{type(exc).__name__}: {exc}"})
                continue
            if changed in overrides:
                updates.append((parent_path, instance, overrides))
            ports = {p["name"]: p for p in instance.get("ports") or []}
            for port in target_model.get("ports") or []:
                port_name = port["name"]
                new_width = _effective_width_for_datasize(port.get("datasize"), new_values)
                old_width = _effective_width_for_datasize(port.get("datasize"), old_values)
                connect = str((ports.get(port_name) or {}).get("connect") or "")
                match = re.search(r"\[(\d+)(?::(\d+))?\]$", connect)
                if match:
                    high, low = int(match.group(1)), int(match.group(2) or match.group(1))
                    slice_width = abs(high - low) + 1
                    if new_width != slice_width:
                        warnings.append({
                            "code": "parent_width_conflict", "parent_model": parent["name"],
                            "parent_path": str(parent_path), "instance": instance["instance"],
                            "child_model": target_model["name"], "port": port_name,
                            "kind": "connect_slice", "endpoint": connect,
                            "old_width": old_width, "new_width": new_width,
                            "overridden": overridden,
                            "suggestion": f"change {parent['name']}.{instance['instance']}.{port_name} Connect to an {new_width}-bit slice",
                        })
            for wire in parent.get("wires") or []:
                endpoints = [wire.get("from") or {}, wire.get("to") or {}]
                hit = any(ep.get("instance") == instance.get("instance") and ep.get("port") in ports for ep in endpoints if isinstance(ep, dict))
                if not hit:
                    continue
                wire_width = robei_width(wire.get("datasize"))
                port_name = next((ep.get("port") for ep in endpoints if isinstance(ep, dict) and ep.get("instance") == instance.get("instance")), None)
                target_port = next((p for p in target_model.get("ports") or [] if p["name"] == port_name), None)
                old_width = _effective_width_for_datasize(target_port.get("datasize") if target_port else None, old_values)
                new_width = _effective_width_for_datasize(target_port.get("datasize") if target_port else None, new_values)
                if wire_width is not None and new_width is not None and wire_width != new_width:
                    warnings.append({
                        "code": "parent_width_conflict", "parent_model": parent["name"],
                        "parent_path": str(parent_path), "instance": instance["instance"],
                        "child_model": target_model["name"], "port": port_name,
                        "kind": "wire_datasize", "endpoint": wire.get("net"),
                        "old_width": old_width, "new_width": new_width,
                        "overridden": overridden,
                        "suggestion": f"change wire {wire.get('net')} Datasize to {new_width}",
                    })
    return warnings, skipped, updates, scanned


def _rewrite_instance_parameters(text: str, instance: str, replacement: str) -> str:
    tag_re = re.compile(r"<Module\b[^>]*>", re.IGNORECASE)
    attr_re = re.compile(r'(\bParameters=")([^"]*)(")')
    def replace_tag(match):
        tag = match.group(0)
        name = re.search(r'\bName="([^"]+)"', tag, re.IGNORECASE)
        if not name or name.group(1) != instance:
            return tag
        return attr_re.sub(lambda m: m.group(1) + replacement.replace('"', "&quot;") + m.group(3), tag, count=1)
    return tag_re.sub(replace_tag, text)


UNRESOLVABLE_SELECT_HINT = (
    "cannot resolve the endpoint {ref!r}: only constant selects (a[0], a[7:0]) are "
    'static; write them as wires=[{{"from": "a[0]", "to": "u1.x"}}] so Robei stores '
    'them as Connect="a[0]" on the instance port'
)


def _split_bit_ref(ref: str) -> tuple[str, str]:
    """``a[0]`` -> ``("a", "0")``; a plain reference keeps an empty select."""
    text = (ref or "").strip()
    match = BIT_SELECT_RE.match(text)
    if match:
        return match.group("name"), re.sub(r"\s+", "", match.group("sel"))
    return text, ""


def _selector_width(selector: str) -> int:
    if not selector:
        return 0
    if ":" in selector:
        hi, lo = (int(part) for part in selector.split(":"))
        return abs(hi - lo) + 1
    return 1


def _plan_selector_connect(
    frm: str,
    to: str,
    resolve: Any,
) -> tuple[str, str, str, dict[str, Any], list[str]]:
    """Decide whether a wire is really a per-port ``Connect="a[0]"`` expression.

    Robei stores a bit-select on the *instance* port and deletes any ``<Wire>``
    that mentions one, so such a pair has to become a port attribute instead of
    a net.  Returns empty values when neither end carries a select.
    """
    frm_base, frm_select = _split_bit_ref(frm)
    to_base, to_select = _split_bit_ref(to)
    if not frm_select and not to_select:
        return "", "", "", {}, []
    if frm_select and to_select:
        raise BadRequest(
            f"both ends of {frm!r} -> {to!r} carry a select; only the parent-side port "
            "can be sliced, the other end must be an instance port",
            'keep the select on the parent side only: wires=[{"from": "a[0]", "to": "u1.x"}]',
        )
    parent_ref, selector = (frm_base, frm_select) if frm_select else (to_base, to_select)
    inst_ref = (to if frm_select else frm).strip()
    parent = resolve(parent_ref)
    if parent.get("instance"):
        raise BadRequest(
            f"{parent_ref}[{selector}] is an instance port; a select is only supported on a "
            "top-level port (Robei writes these as Connect on the instance port)",
            f"{parent_ref} is already inside an instance; select the top-level signal "
            f'instead, e.g. wires=[{{"from": "a[{selector}]", "to": "{parent_ref}"}}]',
        )
    if "." not in inst_ref:
        raise BadRequest(
            f"{parent_ref}[{selector}] is sliced but {inst_ref!r} is not an instance port; put "
            'the select on the instance side, e.g. {"from": "a[0]", "to": "u1.x"}',
            f"{inst_ref!r} must be uN.port (a port of a placed instance); example: "
            f'{{"from": "{parent_ref}[{selector}]", "to": "u1.x"}}',
        )
    target = resolve(inst_ref)
    warnings: list[str] = []
    width = parent.get("width")
    need = _selector_width(selector)
    if width is None:
        warnings.append(
            f"cannot verify {parent_ref}[{selector}]: its width {parent.get('datasize')!r} "
            "is not a constant"
        )
    else:
        if ":" in selector:
            hi, lo = (int(part) for part in selector.split(":"))
            if hi < lo:
                raise BadRequest(
                    f"{parent_ref}[{selector}]: the high index must not be below the low index",
                    f"write the slice high:low, i.e. {parent_ref}[{lo}:{hi}]",
                )
            high = max(hi, lo)
        else:
            high = int(selector)
        if high >= width:
            raise BadRequest(
                f"{parent_ref}[{selector}] is out of range: {parent_ref} is {width} bit(s) "
                f"wide (valid indices 0..{width - 1})",
                f"{parent_ref} is {width} bit(s) wide: address a bit as "
                f"{parent_ref}[0]..{parent_ref}[{width - 1}], or the whole bus as "
                f"{parent_ref}[{width - 1}:0]",
            )
        target_width = target.get("width")
        if target_width is not None and target_width != need:
            raise WidthMismatch(
                f"{parent_ref}[{selector}] is {need} bit(s) but {inst_ref} is "
                f"{target_width} bit(s); a slice must be exactly as wide as the instance port",
                hint=(
                    f"the slice must cover exactly {target_width} bit(s): "
                    f"{parent_ref}[{target_width - 1}:0] for the whole bus, or a "
                    f"{target_width}-bit window such as {parent_ref}[{target_width}:1]"
                ),
            )
    return parent_ref, selector, inst_ref, target, warnings


def _normalise_instance_parameters(
    label: str,
    value: Any,
    child: dict[str, Any] | None,
    *,
    root: bool = False,
) -> tuple[str, list[str]]:
    """Validate a ``Parameters`` attribute and normalise it for Robei.

    ``root=True`` is the model's own declaration: there is no child to compare
    against, so the "is it declared" check is skipped - everything here *is*
    the declaration.

    Robei keeps overrides on the nested ``<Module Parameters="LEN = 8;">`` and
    emits ``#(.LEN(8))`` from it.  The Verilog spellings (``#(.LEN(8))``, a
    leading ``.``, the ``parameter`` keyword) are not Robei's attribute syntax:
    Robei either drops them or writes broken Verilog, so they are rejected.
    """
    warnings: list[str] = []
    if value in (None, "", {}):
        return "", warnings
    declared = {
        item["name"].upper(): item["name"]
        for item in (child or {}).get("parameter_list") or []
    }
    if isinstance(value, dict):
        pairs: list[tuple[str, str]] = []
        for raw_name, raw_value in value.items():
            name = str(raw_name).strip()
            if not PARAM_NAME_RE.match(name):
                raise BadRequest(
                    f"{label}: invalid parameter name {name!r}",
                    "parameter names are Verilog identifiers "
                    "([A-Za-z_][A-Za-z0-9_$]*); example: " + PARAM_HINT,
                )
            text = str(raw_value).strip()
            if not text:
                raise BadRequest(
                    f"{label}: parameter {name} needs a value",
                    f'pass a non-empty value, e.g. {{"{name}": 8}}',
                )
            if any(char in text for char in ";\r\n"):
                raise BadRequest(
                    f"{label}: parameter {name} value {text!r} must not contain ';' or a newline",
                    f'one entry per parameter: {{"{name}": 8}}; the ";" and the separator '
                    "are added when the attribute is written",
                )
            pairs.append((name, text))
        pairs.sort(key=lambda pair: pair[0].lower())
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return "", warnings
        if "#(" in text or text.startswith(".") or "parameter" in text.lower():
            raise BadRequest(
                f"{label}: {text!r} is Verilog, not Robei's Parameters attribute; " + PARAM_HINT,
                'use the object form: parameters={"LEN": 8} (or a Robei "LEN = 8;" string); '
                'Robei drops "#(...)" and writes "parameter LEN = 8;" as broken Verilog',
            )
        parsed, bad = parse_parameter_text_checked(text)
        if bad or not parsed:
            # Keep Robei's own text so export - not the write - is what reports
            # it, exactly like the 1.0.4 behaviour for a hand-edited model.
            warnings.append(
                f"{label}: cannot parse Parameters {text!r}; written verbatim and the "
                "export will warn about it"
            )
            return text, warnings
        pairs = [(item["name"], item["default"].strip()) for item in parsed]
    else:
        raise BadRequest(
            f"{label}: parameters must be an object or a 'NAME = value;' string; " + PARAM_HINT,
            'send parameters={"LEN": 8}, not a list or a number: ' + PARAM_HINT,
        )

    upper = [name.upper() for name, _ in pairs]
    duplicates = sorted({name for name in upper if upper.count(name) > 1})
    if duplicates:
        raise BadRequest(
            f"{label}: parameter(s) {duplicates} are given more than once",
            "a dict cannot repeat a key, so this came from a string; keep one "
            f'"NAME = value;" per parameter, e.g. ' + PARAM_HINT,
        )
    if not declared and not root:
        raise BadRequest(
            f"{label}: the child model declares no parameters, so there is nothing to override",
            'drop "parameters" for this instance, or use a child that declares one '
            '(create_robei_model(parameters={"LEN": 4}) in 1.5, or import_library_model)',
        )
    unknown = [name for name, _ in pairs if not root and name.upper() not in declared]
    if unknown:
        raise BadRequest(
            f"{label}: parameter(s) {unknown} are not declared by the child model "
            f"(declared: {sorted(declared.values())})",
            f"the child declares {_names(declared.values())}"
            f"{_closest(unknown[0], declared.values())}; example: "
            f'{{"{next(iter(declared.values()))}": <value>}}',
        )
    if isinstance(value, str):
        return text, warnings
    body = "\n".join(f"{declared.get(name.upper(), name)} = {val};" for name, val in pairs)
    return body, warnings


def _canvas_endpoint(top: str, ref: str) -> str:
    """``u1.y`` -> ``top#u1>y``；``y`` -> ``top>y``."""
    ref = (ref or "").strip()
    if not ref:
        raise BadRequest(
            "wire endpoint must not be empty",
            'a wire endpoint is "y" for a top-level port or "u1.a" for an instance port; '
            'example: {"from": "u1.y", "to": "y"}',
        )
    if "[" in ref or "]" in ref:
        raise BadRequest(f"<Wire> endpoint {ref!r} carries a bit-select", WIRE_SELECT_HINT)
    if "." in ref:
        instance, _, port = ref.partition(".")
        if not port:
            raise BadRequest(
                f"wire endpoint {ref!r} is missing a port",
                f'name the port after the dot, e.g. "{instance.strip()}.y"',
            )
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
    # ``datasize`` keeps Robei's own spelling (a width, a range, or a parameter
    # expression such as ``LEN-1:0``); ``width`` is the resolved integer.
    width = port.get("datasize") or port.get("width") or 1
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
    warnings: list[str] = []

    cleaned_ports: list[dict[str, Any]] = []
    for port in ports:
        direction = str(port.get("direction", "input")).lower()
        if direction not in PORT_DIRECTIONS:
            raise ValueError(f"invalid direction: {direction}")
        if not PORT_NAME_RE.match(str(port["name"])):
            raise ValueError(f"invalid port name: {port['name']!r}")
        datasize = _width_text(port.get("width", 1), f"port {port['name']!r} width")
        cleaned_ports.append(
            {
                "name": str(port["name"]),
                "direction": direction,
                "width": robei_width(datasize),
                "datasize": datasize,
                "datatype": str(port.get("datatype", "wire")).lower(),
                "side": str(port.get("side") or ("left" if direction == "input" else "right")).lower(),
            }
        )
    top_ports = {p["name"]: p for p in cleaned_ports}

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
        params_text, param_warnings = _normalise_instance_parameters(
            inst_name, item.get("parameters"), target_model
        )
        warnings.extend(param_warnings)
        placed.append(
            {
                "instance": inst_name,
                "model": model_stem,
                # Robei writes the referenced model name as the instance Class
                "class": model_stem,
                "x": int(item.get("x", 200 + index * 320)),
                "y": int(item.get("y", 120)),
                # Robei keeps per-instance overrides in the nested Module's
                # Parameters attribute; a dict is normalised to "NAME = value;".
                "parameters": params_text,
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

    def resolve(ref: str) -> dict[str, Any]:
        if "[" in ref or "]" in ref:
            raise BadRequest(
                f"cannot resolve the endpoint {ref!r}: only constant selects "
                "(a[0], a[7:0]) are supported",
                UNRESOLVABLE_SELECT_HINT.format(ref=ref),
            )
        inst, separator, port = ref.partition(".")
        if separator:
            item = by_instance.get(inst.strip())
            if item is None:
                raise BadRequest(
                    f"wire references unknown instance {inst!r}",
                    f"`instances` must name placed instances; this model places "
                    f"{_names(by_instance)}{_closest(inst, by_instance)}; "
                    f'example: {{"name": "{inst.strip()}", "model": "<existing model>"}}',
                )
            found = next((p for p in item["ports"] if p["name"] == port.strip()), None)
            if found is None:
                known = [p["name"] for p in item["ports"]]
                hint = (
                    f"{inst} (model {item['model']}) has ports {_names(known)}"
                    f"{_closest(port, known)}; example: '{inst.strip()}.{known[0]}'"
                    if known
                    else f"{inst} has no ports"
                )
                raise BadRequest(f"instance {inst!r} has no port {port!r}", hint)
            return {
                "instance": inst.strip(),
                "port": port.strip(),
                "width": robei_width(found["width"]),
                "datasize": str(found["width"] or ""),
            }
        port_item = top_ports.get(ref)
        if port_item is None:
            raise BadRequest(
                f"wire references unknown top-level port {ref!r}",
                f"`ports` declares {_names(top_ports)}{_closest(ref, top_ports)}; a "
                'bit-select goes in the endpoint ({"from": "a[0]", "to": "u1.x"}), '
                "not as a separate port",
            )
        return {
            "instance": None,
            "port": ref,
            "width": port_item["width"],
            "datasize": str(port_item["width"]),
        }

    connects: dict[tuple[str, str], str] = {}
    wired_ports: set[tuple[str, str]] = set()
    cleaned_wires: list[dict[str, Any]] = []
    for index, wire in enumerate(wires):
        frm = str(wire.get("from") or "").strip()
        to = str(wire.get("to") or "").strip()
        parent_ref, selector, inst_ref, _target, select_warnings = _plan_selector_connect(
            frm, to, resolve
        )
        warnings.extend(select_warnings)
        if selector:
            instance, _, port = inst_ref.partition(".")
            key = (instance.strip(), port.strip())
            if key in connects or key in wired_ports:
                raise BadRequest(
                    f"instance port {instance}.{port} is connected twice; a bit-select and a "
                    "<Wire> cannot both drive the same instance port",
                    f"give {instance}.{port} exactly one connection: either a bit-select "
                    '({"from": "a[0]", "to": f"{instance}.{port}"}) or a plain wire',
                )
            connects[key] = f"{parent_ref}[{selector}]"
            continue
        for ref in (frm, to):
            resolve(ref)
            if "." in ref:
                instance, _, port = ref.partition(".")
                wired_ports.add((instance.strip(), port.strip()))
        cleaned_wires.append(
            {
                "net": str(wire.get("net") or f"{name}_n{index}"),
                "from": frm,
                "to": to,
                "datasize": _width_text(
                    wire.get("datasize") or 1, f"wires[{index}].datasize"
                ),
                "datatype": "wire",
            }
        )

    state: dict[str, Any] = {
        "name": name,
        "comment": comment,
        "parameters": "",
        "ports": [
            {
                "name": port["name"],
                "direction": port["direction"],
                "width": port["width"],
                "datasize": port["datasize"],
                "datatype": port["datatype"],
                "side": port["side"],
            }
            for port in cleaned_ports
        ],
        "instances": [
            {
                "instance": item["instance"],
                "model": item["model"],
                "class": item["class"],
                "module": "",
                "x": item["x"],
                "y": item["y"],
                "parameters": item["parameters"],
                "ports": [
                    {
                        "name": port["name"],
                        "direction": port["direction"],
                        "width": robei_width(port["width"]),
                        "datasize": str(port["width"] or 1),
                        "datatype": port["datatype"],
                        "side": "",
                        "connect": connects.get((item["instance"], port["name"]), ""),
                    }
                    for port in item["ports"]
                ],
            }
            for item in placed
        ],
        "wires": cleaned_wires,
    }
    xml_text = _render_canvas(state)
    ET.fromstring(xml_text)

    target = model_dir / f"{name}.model"
    if target.exists() and not overwrite:
        raise FileExistsError(f"model already exists: {target}")
    backup = _backup_file(target)
    target.write_text(xml_text, encoding="utf-8", newline="\n")
    registration = _register_model_in_project(proj, name)
    parsed = parse_robei_model(target)
    return _result_widths({
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
        "warnings": warnings,
    })


OP_KINDS = (
    "add_instance",
    "remove_instance",
    "add_port",
    "remove_port",
    "connect",
    "disconnect",
    "set_parameters",
    "rename_wire",
)


WIDTH_EXPR_RE = re.compile(r"^[A-Za-z0-9_$+\-*/() ]+$")
WIDTH_RANGE_RE = re.compile(r"^[A-Za-z0-9_$+\-*/() ]+:[A-Za-z0-9_$+\-*/() ]+$")


def _width_text(value: Any, field: str = "width") -> str:
    r"""Normalise a width argument to the text Robei keeps in ``Datasize``.

    Accepts ``8``, ``"8"``, a literal range (``"3:0"``) and a parameterised
    width (``"LEN"`` -> ``"LEN-1:0"``).  The last form is not a guess: the 24
    models Robei 5.0 installs under ``C:\ProgramData\Robei`` spell parameterised
    widths 35 times as ``Datasize="LEN-1:0"`` (plus ``DATA_WIDTH-1:0``,
    ``LENA+LENB-1:0``) and never as a bare ``LEN``.
    """
    if isinstance(value, bool):
        raise BadRequest(
            f"{field} must be a bit count, not a boolean",
            f"pass {field}=8 or {field}='LEN', not true/false",
        )
    if isinstance(value, int):
        if not 1 <= value <= 1024:
            raise BadRequest(
                f"{field} must be 1..1024 bit(s), got {value}",
                f"a port or wire is 1 to 1024 bits wide: {field}=8, or {field}='LEN'",
            )
        return str(value)
    if isinstance(value, float):
        if value.is_integer():
            return _width_text(int(value), field)
        raise BadRequest(
            f"{field} must be a whole number of bits, got {value}",
            f"pass {field}=8 or {field}='LEN'",
        )
    text = str(value if value is not None else "").strip()
    if not text:
        raise BadRequest(f"{field} must not be empty", f"pass {field}=8 or {field}='LEN'")
    if text.isdigit():
        return _width_text(int(text), field)
    if ":" in text:
        if not WIDTH_RANGE_RE.match(text):
            raise BadRequest(
                f"{field} {text!r} is not a range Robei can store",
                'a range is MSB:LSB built from identifiers, integers, + - * / and '
                'parentheses, e.g. "LEN-1:0"',
            )
        return text
    if not WIDTH_EXPR_RE.match(text):
        raise BadRequest(
            f"{field} {text!r} uses an operator this tool will not guess at",
            "only identifiers, integers, + - * / and parentheses are accepted - Robei "
            "has no saved sample for anything else; pass the range form directly "
            'instead, e.g. width="MSB:LSB" or width="15:0"',
        )
    # ``2*LEN`` -> ``2*LEN-1:0``: * and / bind tighter than -, so appending the
    # range is safe; no extra parentheses (Robei has no such sample).
    return f"{text}-1:0"


def _width_value(raw: Any) -> Any:
    """Numeric ``Datasize`` -> int; a parameterised expression stays a string."""
    if isinstance(raw, int):
        return raw
    text = str(raw if raw is not None else "").strip()
    if not text:
        return 1
    return int(text) if text.isdigit() else text


def _result_widths(result: dict[str, Any]) -> dict[str, Any]:
    """Return widths as ints where they are numbers (see tool-reference)."""
    def rows(values: Any) -> list[dict[str, Any]]:
        out = []
        for row in values or []:
            item = dict(row)
            if "datasize" in item:
                item["datasize"] = _width_value(item["datasize"])
            if "port" in row or "direction" in row or "inout" in row:
                # Ports also carry the resolved bit count; None means the width
                # is a parameter expression and cannot be resolved statically.
                item.setdefault("width", robei_width(item.get("datasize")))
            out.append(item)
        return out

    if "ports" in result:
        result["ports"] = rows(result["ports"])
    if "wires" in result:
        result["wires"] = rows(result["wires"])
    if "instances" in result:
        instances = []
        for instance in result["instances"] or []:
            item = dict(instance)
            if "ports" in item:
                item["ports"] = rows(item["ports"])
            instances.append(item)
        result["instances"] = instances
    return result


def _canvas_int(value: Any, default: int) -> int:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def _shorthand_endpoint(endpoint: dict[str, Any] | None) -> str:
    """``top#u1>a`` -> ``u1.a``; ``top>y`` -> ``y``."""
    if not endpoint:
        return ""
    port = endpoint.get("port") or ""
    if not port:
        return ""
    instance = endpoint.get("instance")
    return f"{instance}.{port}" if instance else port


def _canvas_state(model: dict[str, Any]) -> dict[str, Any]:
    """Normalize a parsed model into the shape the canvas renderer/editor uses."""
    return {
        "name": model["name"],
        "comment": model.get("comment") or "",
        "parameters": model.get("parameters") or "",
        "ports": [
            {
                "name": port["name"],
                "direction": port["inout"] or "input",
                "width": robei_width(port["datasize"]),
                "datasize": port["datasize"] or "",
                "datatype": port["datatype"] or "wire",
                "side": port["side"] or "",
                "connect": port.get("connect") or "",
            }
            for port in model["ports"]
        ],
        "instances": [
            {
                "instance": inst["instance"],
                "model": (
                    inst.get("module")
                    or inst.get("class")
                    or Path(inst.get("model_file") or "").stem
                ),
                "class": inst.get("class") or "",
                "module": inst.get("module") or "",
                "x": _canvas_int(inst.get("x"), 200),
                "y": _canvas_int(inst.get("y"), 120),
                "parameters": inst.get("parameters") or "",
                "ports": [
                    {
                        "name": port["name"],
                        "direction": port["inout"] or "input",
                        "width": robei_width(port["datasize"]),
                        "datasize": port["datasize"] or "",
                        "datatype": port["datatype"] or "wire",
                        "side": port["side"] or "",
                        "connect": port.get("connect") or "",
                    }
                    for port in inst["ports"]
                ],
            }
            for inst in model["instances"]
        ],
        "wires": [
            {
                "net": wire["net"],
                "from": _shorthand_endpoint(wire.get("from")),
                "to": _shorthand_endpoint(wire.get("to")),
                "datasize": wire.get("datasize") or "1",
                "datatype": wire.get("datatype") or "wire",
            }
            for wire in model["wires"]
        ],
    }


def _render_canvas(state: dict[str, Any]) -> str:
    """Serialize the editable state back into Robei's canvas XML."""
    name = state["name"]
    lines = ["<!DOCTYPE Robei>"]
    lines.append(
        f'<Module Name="{_attr(name)}" Type="module" Width="900" Class="module" '
        f'Comment="{_attr(state.get("comment") or "")}" X="0" '
        f'Parameters="{_attr(state.get("parameters") or "")}" Y="0" Parent="0" Height="600" '
        f'Color="#d3d3d3" Include="" File="Current/{_attr(name)}.model" Code="">'
    )
    for item in state["instances"]:
        module_attr = f'Module="{_attr(item["module"])}" ' if item.get("module") else ""
        lines.append(
            f' <Module Name="{_attr(item["instance"])}" Type="model" Width="160" '
            f'Class="{_attr(item["class"] or item["model"])}" {module_attr}Comment="" '
            f'X="{item["x"]}" Parameters="{_attr(item["parameters"])}" Y="{item["y"]}" '
            f'Parent="{_attr(name)}" Height="160" Color="#d3d3d3" Include="" '
            f'File="Current/{_attr(item["model"])}.model" Code="">'
        )
        for index, port in enumerate(item["ports"]):
            lines.append(_canvas_port_xml(item["instance"], port, index, len(item["ports"]), True))
        lines.append(" </Module>")
    for index, port in enumerate(state["ports"]):
        lines.append(_canvas_port_xml(name, port, index, len(state["ports"]), False))
    for wire in state["wires"]:
        lines.append(
            f' <Wire Name="{_attr(wire["net"])}" To="{_canvas_endpoint(name, wire["to"])}" '
            f'From="{_canvas_endpoint(name, wire["from"])}" Datatype="{_attr(wire["datatype"])}" '
            f'Parent="{_attr(name)}" Datasize="{_attr(str(wire["datasize"]))}"/>'
        )
    lines.append("</Module>")
    return "\n".join(lines) + "\n"


def _canvas_lookup(state: dict[str, Any]) -> tuple[dict[str, dict], dict[str, dict]]:
    instances = {item["instance"]: item for item in state["instances"]}
    ports = {port["name"]: port for port in state["ports"]}
    return instances, ports


def _endpoint_info(state: dict[str, Any], ref: str) -> dict[str, Any]:
    """Width + raw Datasize of ``u1.a`` / ``a``; unknown endpoints raise."""
    ref = (ref or "").strip()
    if not ref:
        raise ValueError("endpoint must not be empty")
    instances, ports = _canvas_lookup(state)
    if "[" in ref or "]" in ref:
        raise BadRequest(
            f"cannot resolve the endpoint {ref!r}: only constant selects "
            "(a[0], a[7:0]) are supported",
            UNRESOLVABLE_SELECT_HINT.format(ref=ref),
        )
    instance_name, separator, port_name = ref.partition(".")
    if separator:
        instance = instances.get(instance_name)
        if instance is None:
            raise BadRequest(
                f"unknown instance {instance_name!r} in endpoint {ref!r}",
                f"endpoints must start with a placed instance: {_names(instances)}"
                f"{_closest(instance_name, instances)}; example: \"u1.a\"",
            )
        port = next((item for item in instance["ports"] if item["name"] == port_name), None)
        if port is None:
            known = [item["name"] for item in instance["ports"]]
            hint = (
                f"{instance_name} has ports {_names(known)}{_closest(port_name, known)}; "
                f'example: "{instance_name}.{known[0]}"'
                if known
                else f"{instance_name} has no ports"
            )
            raise BadRequest(f"instance {instance_name!r} has no port {port_name!r}", hint)
    else:
        port = ports.get(ref)
        if port is None:
            raise BadRequest(
                f"unknown top-level port {ref!r}",
                f"this model declares {_names(ports)}{_closest(ref, ports)}; a top-level "
                f'endpoint is the bare port name, e.g. "y"',
            )
    return {
        "width": port["width"],
        "datasize": port.get("datasize") or "",
        "instance": instance_name if separator else None,
        "port": port_name if separator else ref,
    }


def _instance_ports_from_model(model: dict[str, Any]) -> list[dict[str, Any]]:
    """The port list a placed instance needs, taken from the referenced model."""
    return [
        {
            "name": port["name"],
            "direction": port["inout"] or "input",
            "width": robei_width(port["datasize"]),
            "datasize": port["datasize"] or "",
            "datatype": port["datatype"] or "wire",
            "side": "",
        }
        for port in model["ports"]
    ]


def _hydrate_instance_ports(state: dict[str, Any], model_dir: Path) -> None:
    """Legacy ``<Instance Module="x"/>`` carries no ports; read them from x.model.

    Without this a canvas that only references the child by name could not be
    wired (there would be nothing to validate the endpoint against).
    """
    for item in state["instances"]:
        if item["ports"] or not item["model"]:
            continue
        source = model_dir / f"{item['model']}.model"
        if source.is_file():
            item["ports"] = _instance_ports_from_model(parse_robei_model(source))


def _apply_canvas_op(state: dict[str, Any], op: Any, model_dir: Path) -> dict[str, Any]:
    """Mutate ``state`` for one op. Validation happens before anything is written."""
    if not isinstance(op, dict):
        raise ValueError("every op must be an object")
    kind = str(op.get("op") or "").strip()
    if kind not in OP_KINDS:
        raise ValueError(f"unknown op {kind!r}; expected one of {list(OP_KINDS)}")

    if kind == "add_port":
        name = str(op.get("name") or "").strip()
        direction = str(op.get("direction") or op.get("inout") or "input").strip().lower()
        if not PORT_NAME_RE.match(name):
            raise ValueError(f"invalid port name: {name!r}")
        if direction not in PORT_DIRECTIONS:
            raise ValueError(f"invalid direction {direction!r}; expected one of {list(PORT_DIRECTIONS)}")
        raw_width = op.get("width", op.get("datasize", 1))
        datasize = _width_text(raw_width, "add_port.width")
        width = robei_width(datasize)
        if any(port["name"] == name for port in state["ports"]):
            raise BadRequest(
                f"duplicate port name: {name!r}",
                f"this model already declares {_names(port['name'] for port in state['ports'])}; "
                'rename the new port or use {"op": "remove_port", "name": ...} first',
            )
        side = str(op.get("side") or ("left" if direction == "input" else "right")).strip().lower()
        if side not in {"left", "right", "top", "bottom"}:
            raise ValueError(f"invalid side {side!r}; expected left/right/top/bottom")
        state["ports"].append(
            {
                "name": name,
                "direction": direction,
                "width": width,
                "datasize": datasize,
                "datatype": str(op.get("datatype") or "wire").strip().lower(),
                "side": side,
            }
        )
        return {"op": kind, "port": name, "direction": direction, "width": width}

    if kind == "remove_port":
        name = str(op.get("name") or "").strip()
        if not any(port["name"] == name for port in state["ports"]):
            available = [port["name"] for port in state["ports"]]
            raise BadRequest(
                f"unknown port {name!r}; model has {available}",
                f"remove_port needs the exact top-level name: {_names(available)}"
                f"{_closest(name, available)}",
            )
        dropped = [
            wire for wire in state["wires"] if wire["from"] == name or wire["to"] == name
        ]
        state["ports"] = [port for port in state["ports"] if port["name"] != name]
        state["wires"] = [
            wire for wire in state["wires"]
            if not (wire["from"] == name or wire["to"] == name)
        ]
        return {"op": kind, "port": name, "wires_dropped": len(dropped)}

    if kind == "add_instance":
        instance = str(op.get("name") or "").strip()
        module = str(op.get("model") or op.get("module") or "").strip()
        if module.lower().endswith(".model"):
            module = module[:-6]
        if not MODEL_NAME_RE.match(instance):
            raise ValueError(f"invalid instance name: {instance!r}")
        if not MODEL_NAME_RE.match(module):
            raise ValueError(f"invalid model reference: {module!r}")
        if any(item["instance"] == instance for item in state["instances"]):
            raise BadRequest(
                f"duplicate instance name: {instance!r}",
                f"this model already places {_names(item['instance'] for item in state['instances'])}; "
                f'pick another name, e.g. "u{len(state["instances"]) + 1}"',
            )
        source = model_dir / f"{module}.model"
        if not source.is_file():
            available = sorted(path.stem for path in model_dir.glob("*.model"))
            raise FileNotFoundError(f"model {module!r} not found in the project (available: {available})")
        target = parse_robei_model(source)
        params_text, param_warnings = _normalise_instance_parameters(
            instance, op.get("parameters"), target
        )
        state["instances"].append(
            {
                "instance": instance,
                "model": module,
                "class": module,
                "module": "",
                "x": _canvas_int(op.get("x"), 200 + 320 * len(state["instances"])),
                "y": _canvas_int(op.get("y"), 120),
                "parameters": params_text,
                "ports": _instance_ports_from_model(target),
            }
        )
        return {
            "op": kind, "instance": instance, "model": module,
            "parameters": params_text, "warnings": param_warnings,
        }

    if kind == "remove_instance":
        instance = str(op.get("name") or "").strip()
        if not any(item["instance"] == instance for item in state["instances"]):
            placed = [item["instance"] for item in state["instances"]]
            raise BadRequest(
                f"unknown instance {instance!r}",
                f"remove_instance needs a placed instance: {_names(placed)}"
                f"{_closest(instance, placed)}",
            )
        prefix = f"{instance}."
        dropped = [
            wire for wire in state["wires"]
            if wire["from"].startswith(prefix) or wire["to"].startswith(prefix)
        ]
        state["instances"] = [item for item in state["instances"] if item["instance"] != instance]
        state["wires"] = [
            wire for wire in state["wires"]
            if not (wire["from"].startswith(prefix) or wire["to"].startswith(prefix))
        ]
        return {"op": kind, "instance": instance, "wires_dropped": len(dropped)}

    if kind == "connect":
        frm = str(op.get("from") or "").strip()
        to = str(op.get("to") or "").strip()
        if not frm or not to:
            raise ValueError("connect needs both from and to")
        parent_ref, selector, inst_ref, _target, select_warnings = _plan_selector_connect(
            frm, to, lambda ref: _endpoint_info(state, ref)
        )
        if selector:
            instance, _, port = inst_ref.partition(".")
            item = next(i for i in state["instances"] if i["instance"] == instance.strip())
            port_item = next(p for p in item["ports"] if p["name"] == port.strip())
            expression = f"{parent_ref}[{selector}]"
            previous = port_item.get("connect") or ""
            port_item["connect"] = expression
            return {
                "op": kind,
                "connect": expression,
                "instance": instance.strip(),
                "port": port.strip(),
                "width": _selector_width(selector),
                "updated": previous == expression,
                "warnings": select_warnings,
            }
        left = _endpoint_info(state, frm)
        right = _endpoint_info(state, to)
        if left["width"] is not None and right["width"] is not None:
            if left["width"] != right["width"]:
                raise WidthMismatch(
                    f"cannot connect {frm} ({left['width']} bit) to {to} ({right['width']} bit): "
                    "both ends of a wire must have the same width"
                )
        elif left["datasize"].strip().lower() != right["datasize"].strip().lower():
            raise WidthMismatch(
                f"cannot verify {frm} ({left['datasize'] or 1}) against {to} "
                f"({right['datasize'] or 1}): parameterised widths must match exactly"
            )
        width_from = left["width"] if left["width"] is not None else right["width"]
        net = str(op.get("net") or "").strip()
        if not net:
            net = re.sub(r"[^A-Za-z0-9_$]", "_", f"{state['name']}_{frm}_{to}")
        if not MODEL_NAME_RE.match(net):
            raise ValueError(f"invalid net name: {net!r}")
        datasize = _width_text(op.get("datasize") or width_from or 1, "connect.datasize")
        for wire in state["wires"]:
            if wire["from"] == frm and wire["to"] == to:
                wire.update({"net": net, "datasize": datasize})
                return {
                    "op": kind, "net": net, "from": frm, "to": to,
                    "width": width_from, "updated": True,
                }
        state["wires"].append(
            {"net": net, "from": frm, "to": to, "datasize": datasize, "datatype": "wire"}
        )
        return {
            "op": kind, "net": net, "from": frm, "to": to,
            "width": width_from, "updated": False,
        }

    if kind == "disconnect":
        net = str(op.get("net") or "").strip()
        frm = str(op.get("from") or "").strip()
        to = str(op.get("to") or "").strip()
        if not (net or frm or to):
            raise ValueError("disconnect needs net, from or to")

        def matches(wire: dict[str, Any]) -> bool:
            return (
                (not net or wire["net"] == net)
                and (not frm or wire["from"] == frm)
                and (not to or wire["to"] == to)
            )

        removed = [wire for wire in state["wires"] if matches(wire)]
        cleared: list[str] = []
        if not net:
            for item in state["instances"]:
                prefix = f"{item['instance']}."
                for port in item["ports"]:
                    expression = port.get("connect") or ""
                    if not expression:
                        continue
                    endpoint = f"{prefix}{port['name']}"
                    if (frm and frm in (endpoint, expression)) or (
                        to and to in (endpoint, expression)
                    ):
                        port["connect"] = ""
                        cleared.append(endpoint)
        if not removed and not cleared:
            nets = [wire["net"] for wire in state["wires"]]
            raise BadRequest(
                f"no wire matched disconnect(net={net!r}, from={frm!r}, to={to!r})",
                f"disconnect matches a net name or an endpoint; this model has "
                f"{_names(nets)}{_closest(net or frm or to, nets)}; example: "
                '{"op": "disconnect", "net": "<name from wires[].net>"}',
            )
        state["wires"] = [wire for wire in state["wires"] if not matches(wire)]
        return {
            "op": kind,
            "removed": len(removed),
            "nets": [wire["net"] for wire in removed],
            "connects_cleared": cleared,
        }

    if kind == "set_parameters":
        instance = str(op.get("name") or op.get("instance") or "").strip()
        item = next((i for i in state["instances"] if i["instance"] == instance), None)
        if item is None:
            available = [i["instance"] for i in state["instances"]]
            raise ValueError(f"unknown instance {instance!r}; model has {available}")
        source = model_dir / f"{item['model']}.model"
        child = parse_robei_model(source) if source.is_file() else None
        text, op_warnings = _normalise_instance_parameters(
            instance, op.get("parameters"), child
        )
        previous = item.get("parameters") or ""
        item["parameters"] = text
        return {
            "op": kind, "instance": instance, "parameters": text,
            "previous": previous, "updated": text != previous, "warnings": op_warnings,
        }

    net = str(op.get("net") or "").strip()
    frm = str(op.get("from") or "").strip()
    new_name = str(op.get("new_name") or op.get("rename_to") or "").strip()
    if not new_name:
        raise ValueError("rename_wire needs new_name")
    if not MODEL_NAME_RE.match(new_name):
        raise ValueError(f"invalid net name: {new_name!r}")
    if any(wire["net"] == new_name for wire in state["wires"]):
        raise BadRequest(
            f"duplicate net name: {new_name!r}",
            f"nets already in use: {_names(wire['net'] for wire in state['wires'])}; "
            f'pick a free name, e.g. "{new_name}_2"',
        )
    target = [
        wire for wire in state["wires"]
        if (net and wire["net"] == net) or (frm and (wire["from"] == frm or wire["to"] == frm))
    ]
    if not target:
        nets = [wire["net"] for wire in state["wires"]]
        raise BadRequest(
            f"no wire matched rename_wire(net={net!r}, from={frm!r})",
            f"rename_wire matches a net name or an endpoint; this model has "
            f"{_names(nets)}{_closest(net or frm, nets)}",
        )
    for wire in target:
        wire["net"] = new_name
    return {"op": kind, "from_name": net or frm, "new_name": new_name, "wires_renamed": len(target)}


def _canvas_diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    before_instances = {item["instance"] for item in before["instances"]}
    after_instances = {item["instance"] for item in after["instances"]}
    before_wires = {(w["net"], w["from"], w["to"], str(w["datasize"])) for w in before["wires"]}
    after_wires = {(w["net"], w["from"], w["to"], str(w["datasize"])) for w in after["wires"]}

    def connects(state: dict[str, Any]) -> dict[str, str]:
        return {
            f"{item['instance']}.{port['name']}": port.get("connect") or ""
            for item in state["instances"]
            for port in item["ports"]
            if port.get("connect")
        }

    def parameters(state: dict[str, Any]) -> dict[str, str]:
        return {item["instance"]: item.get("parameters") or "" for item in state["instances"]}

    before_connects, after_connects = connects(before), connects(after)
    before_params, after_params = parameters(before), parameters(after)

    def wire_row(key: tuple[str, str, str, str]) -> dict[str, str]:
        return {"net": key[0], "from": key[1], "to": key[2], "datasize": key[3]}

    return {
        "instances_added": sorted(after_instances - before_instances),
        "instances_removed": sorted(before_instances - after_instances),
        "wires_added": [wire_row(key) for key in sorted(after_wires - before_wires)],
        "wires_removed": [wire_row(key) for key in sorted(before_wires - after_wires)],
        "connects_added": {k: v for k, v in after_connects.items() if k not in before_connects},
        "connects_changed": {
            name: {"before": before_connects[name], "after": value}
            for name, value in after_connects.items()
            if name in before_connects and before_connects[name] != value
        },
        "connects_removed": sorted(set(before_connects) - set(after_connects)),
        "parameters_changed": {
            name: {"before": before_params.get(name, ""), "after": value}
            for name, value in after_params.items()
            if name in before_params and before_params[name] != value
        },
        "instances_total": len(after_instances),
        "wires_total": len(after_wires),
    }


def edit_robei_canvas(
    model_path: str,
    ops: list[dict[str, Any]],
    dry_run: bool = False,
) -> dict[str, Any]:
    """Apply canvas ops (add/remove instance, connect, disconnect, rename) atomically.

    Every op is validated before anything is written: an unknown port, a width
    mismatch or a duplicate instance name aborts the whole call, so a half
    applied edit can never reach the file.
    """
    path = resolve_model_path(model_path)
    if not dry_run and not is_within(path, SETTINGS.workspace):
        raise PermissionError(f"refusing to modify a model outside the workspace: {path}")
    model = parse_robei_model(path)
    if not isinstance(ops, list) or not ops:
        raise ValueError("ops must be a non-empty list of canvas operations")
    before = _canvas_state(model)
    _hydrate_instance_ports(before, path.parent)
    working = copy.deepcopy(before)
    applied = [_apply_canvas_op(working, op, path.parent) for op in ops]
    warnings = [item for entry in applied for item in entry.get("warnings") or []]

    text = _render_canvas(working)
    preview_model = parse_robei_model_text(text, path)
    result: dict[str, Any] = _result_widths({
        "ok": True,
        "dry_run": bool(dry_run),
        "model": model["name"],
        "path": str(path),
        "relative_path": _rel(path),
        "ops_applied": applied,
        "warnings": warnings,
        "diff": _canvas_diff(before, working),
        "ports": working["ports"],
        "instances": working["instances"],
        "wires": working["wires"],
        "netlist": preview_model["netlist"],
        "verilog_preview": model_to_verilog(preview_model),
    })
    if dry_run:
        return result

    backup = _backup_file(path)
    path.write_text(text, encoding="utf-8", newline="\n")
    result.update({"backup": str(backup) if backup else None, "sha256": _sha256(path)})
    return result


def set_robei_code(model_path: str, code: str, dry_run: bool = False) -> dict[str, Any]:
    """Rewrite the ``Code`` attribute of a *behavioural* model (no instances/wires)."""
    path = resolve_model_path(model_path)
    if not dry_run and not is_within(path, SETTINGS.workspace):
        raise PermissionError(f"refusing to modify a model outside the workspace: {path}")
    model = parse_robei_model(path)
    if model["instances"] or model["wires"]:
        raise NotBehavioral(
            f"{model['name']}: structural model ({len(model['instances'])} instance(s), "
            f"{len(model['wires'])} wire(s)) — set_robei_code only edits behavioural models; "
            "use edit_robei_canvas for a canvas"
        )
    text = path.read_text(encoding="utf-8")
    new_text = _rewrite_root_attr(text, "Code", code)
    updated = parse_robei_model_text(new_text, path)
    result: dict[str, Any] = {
        "ok": True,
        "dry_run": bool(dry_run),
        "model": updated["name"],
        "path": str(path),
        "relative_path": _rel(path),
        "code": updated["code"],
        "code_lines": updated["code_lines"],
        "sha256_before": _sha256(path),
        "verilog_preview": model_to_verilog(updated),
    }
    if dry_run:
        result["sha256"] = None
        return result
    backup = _backup_file(path)
    path.write_text(new_text, encoding="utf-8", newline="\n")
    result.update({"backup": str(backup) if backup else None, "sha256": _sha256(path)})
    return result


def _parameter_text(parameters: list[dict[str, str]]) -> str:
    """Render a parameter list back into Robei's ``NAME = value; //comment`` lines."""
    lines = []
    for param in parameters:
        line = f"{param['name']} = {param['default']};"
        if param.get("comment"):
            line += f" // {param['comment']}"
        lines.append(line)
    return "\n".join(lines) + ("\n" if lines else "")


def _param_width_report(model: dict[str, Any], name: str, value: str) -> list[dict[str, Any]]:
    """Re-resolve every port range that mentions ``name`` with the new value."""
    pattern = re.compile(rf"\b{re.escape(name)}\b", re.IGNORECASE)
    rows: list[dict[str, Any]] = []
    for port in model["ports"]:
        datasize = port.get("datasize") or ""
        if not datasize or not pattern.search(datasize):
            continue
        expression = pattern.sub(str(value), datasize)
        info = width_info(expression)
        rows.append(
            {
                "port": port["name"],
                "datasize": datasize,
                "expression": expression,
                "width": info["width"],
                "resolved": info["resolved"],
                "range": info["range"],
            }
        )
    return rows


def set_robei_parameter(
    model_path: str,
    name: str,
    value: str | int | None = None,
    dry_run: bool = False,
    action: str = "set",
    new_name: str | None = None,
    rename_in_parents: bool = False,
) -> dict[str, Any]:
    """Edit one root parameter; the default action preserves the 1.5 contract."""
    return _set_robei_parameter_h2(
        model_path, name, value, dry_run, action, new_name, rename_in_parents
    )


def _h2_core(model_path, name, value, dry_run, action, new_name, rename_in_parents):
    path = resolve_model_path(model_path)
    if not dry_run and not is_within(path, SETTINGS.workspace):
        raise PermissionError(f"refusing to modify a model outside the workspace: {path}")
    model = parse_robei_model(path)
    if model["parameter_bad"]:
        raise ValueError(f"{model['name']}: root Parameters contain unparsable chunks {model['parameter_bad']}")
    action = str(action or "set").lower()
    if action not in PARAM_ACTIONS:
        raise BadRequest(f"unknown parameter action {action!r}", 'action must be one of "set", "add", "rename", "delete"')
    if rename_in_parents and action != "rename":
        raise BadRequest("rename_in_parents is only valid for action=rename", 'use action="rename" with rename_in_parents=true')
    params = [dict(p) for p in model.get("parameter_list") or []]
    ports = model.get("ports") or []
    wanted = str(name or "").strip()
    match = next((p for p in params if p["name"].lower() == wanted.lower()), None)
    if action == "add":
        _validate_parameter_name(wanted, params, ports)
    elif match is None:
        raise UnknownParameter(f"{model['name']}: unknown parameter {wanted!r}; declared: {[p['name'] for p in params]}")
    else:
        _validate_parameter_name(match["name"], params, ports, allow_existing=True)
    if action in {"set", "add"}:
        if value is None:
            raise BadRequest(f"parameter {wanted!r} needs a value", 'pass value=8 or a declared-parameter expression such as "BASE+1"')
        new_value = str(value).strip()
        if not new_value or not PARAM_EXPR_RE.fullmatch(new_value):
            raise BadRequest(f"invalid parameter value: {value!r}", "value must be an integer or a + - * / % expression using declared parameters")
        updated = [dict(p) for p in params]
        if action == "add":
            updated.append({"name": wanted, "default": new_value, "comment": ""})
            changed = wanted
        else:
            changed = match["name"]
            updated = [{**p, "default": new_value if p["name"] == changed else p["default"]} for p in updated]
        _parameter_values(updated)
        effective_name = changed
    elif action == "rename":
        if value is not None or not new_name:
            raise BadRequest("rename needs new_name and no value", 'use action="rename", name="LEN", new_name="WIDTH"')
        effective_name = _validate_parameter_name(new_name, params, ports)
        changed = match["name"]
        updated = [{**p, "name": effective_name if p["name"] == changed else p["name"],
                    "default": _replace_parameter_token(p["default"], changed, effective_name)} for p in params]
        _parameter_values(updated)
        new_value = match["default"]
    else:
        if value is not None or new_name is not None:
            raise BadRequest("delete does not accept value or new_name", 'use action="delete", name="LEN"')
        changed = match["name"]
        refs = [f"{model['name']}.{p['name']} Datasize" for p in ports if changed in PARAM_TOKEN_RE.findall(str(p.get("datasize") or ""))]
        refs += [f"{model['name']}.{p['name']} default" for p in params if p['name'] != changed and changed in PARAM_TOKEN_RE.findall(p.get('default', ''))]
        if refs:
            raise BadRequest(f"cannot delete {changed}: it is still referenced", "remove references first: " + ", ".join(refs))
        updated = [p for p in params if p["name"] != changed]
        _parameter_values(updated)
        effective_name = None
        new_value = None
    original_text = path.read_text(encoding="utf-8")
    new_text = _rewrite_root_attr(original_text, "Parameters", _parameter_text(updated))
    if action == "rename":
        new_text = re.sub(r'(\bDatasize=")([^"]*)(")', lambda m: m.group(1) + _replace_parameter_token(m.group(2), changed, effective_name) + m.group(3), new_text)
    updated_model = parse_robei_model_text(new_text, path)
    warnings, skipped, parent_scan, parents_scanned = _direct_parent_scan(path, model, params, updated_model["parameter_list"], changed)
    if action == "delete" and parent_scan:
        names = ", ".join(f"{p.stem}.{i['instance']}" for p, i, _ in parent_scan)
        raise BadRequest(f"cannot delete {changed}: parent overrides exist", "remove overrides first: " + names)
    parent_updates, parent_texts = [], []
    if action == "rename":
        # Build one replacement text per parent so multiple instances in the same
        # parent are applied cumulatively instead of overwriting one another.
        parent_text_by_path: dict[Path, str] = {}
        for parent_path, instance, _ in parent_scan:
            if not rename_in_parents:
                raise BadRequest(f"cannot rename {changed}: parent overrides exist", "pass rename_in_parents=true; affected: " + f"{parent_path.stem}.{instance['instance']}")
            replacement = _replace_parameter_token(instance.get("parameters") or "", changed, effective_name)
            parent_updates.append({"parent_model": parent_path.stem, "parent_path": str(parent_path), "instance": instance["instance"], "old_parameters": instance.get("parameters") or "", "new_parameters": replacement})
            base_text = parent_text_by_path.get(parent_path)
            if base_text is None:
                base_text = parent_path.read_text(encoding="utf-8")
            parent_text_by_path[parent_path] = _rewrite_instance_parameters(base_text, instance["instance"], replacement)
        parent_texts = list(parent_text_by_path.items())
    new_values = _parameter_values(updated_model["parameter_list"])
    width_rows = []
    affected_ports = []
    for port in updated_model.get("ports") or []:
        expr = str(port.get("datasize") or "")
        rendered = expr
        for key, val in sorted(new_values.items(), key=lambda item: len(item[0]), reverse=True):
            if val is not None:
                rendered = _replace_parameter_token(rendered, key, str(val))
        info = width_info(rendered)
        width_rows.append({"port": port["name"], "datasize": expr, "expression": rendered, "width": info.get("width"), "resolved": info.get("resolved"), "range": info.get("range")})
        affected_ports.append({"port": port["name"], "datasize": expr, "expression": rendered, "effective_width": info.get("width"), "resolved": info.get("resolved"), "range": info.get("range")})
    result = {"ok": True, "dry_run": bool(dry_run), "action": action, "model": updated_model["name"], "path": str(path), "relative_path": _rel(path),
              "parameter": effective_name if action != "rename" else changed, "old_value": match["default"] if match else None, "new_value": new_value,
              "parameters": updated_model["parameter_list"], "parameters_text": updated_model["parameters"], "width_info": width_rows, "affected_ports": affected_ports,
              "parents_scanned": parents_scanned, "parent_updates": parent_updates, "parents_skipped": skipped, "warnings": warnings, "sha256_before": _sha256(path)}
    if dry_run:
        result["sha256"] = None
        return result
    originals = {path: path.read_bytes(), **{p: p.read_bytes() for p, _ in parent_texts}}
    backups = {}
    try:
        backups[path] = _backup_file(path)
        for parent, _ in parent_texts:
            backups[parent] = _backup_file(parent)
        path.write_text(new_text, encoding="utf-8", newline="\n")
        for parent, text_value in parent_texts:
            parent.write_text(text_value, encoding="utf-8", newline="\n")
    except Exception:
        for target, data in originals.items():
            target.write_bytes(data)
        raise
    result.update({"backup": str(backups[path]) if backups[path] else None, "parent_backups": {str(p): str(b) for p, b in backups.items() if p != path and b}, "sha256": _sha256(path)})
    return result


def _set_robei_parameter_h2(
    model_path: str,
    name: str,
    value: str | int | None,
    dry_run: bool,
    action: str,
    new_name: str | None,
    rename_in_parents: bool,
) -> dict[str, Any]:
    return _h2_core(model_path, name, value, dry_run, action, new_name, rename_in_parents)
    path = resolve_model_path(model_path)
    if not dry_run and not is_within(path, SETTINGS.workspace):
        raise PermissionError(f"refusing to modify a model outside the workspace: {path}")
    model = parse_robei_model(path)
    if model["parameter_bad"]:
        raise ValueError(
            f"{model['name']}: root Parameters contain unparsable chunks {model['parameter_bad']}; "
            "fix them before editing a single parameter"
        )
    wanted = (name or "").strip()
    match = next(
        (param for param in model["parameter_list"] if param["name"].lower() == wanted.lower()),
        None,
    )
    if match is None:
        declared = [param["name"] for param in model["parameter_list"]]
        raise UnknownParameter(f"{model['name']}: unknown parameter {name!r}; declared: {declared}")
    new_value = str(value).strip()
    if not new_value or any(char in new_value for char in ';"<>'):
        raise ValueError(f"invalid parameter value: {value!r}")

    updated_list = [
        {**param, "default": new_value if param["name"] == match["name"] else param["default"]}
        for param in model["parameter_list"]
    ]
    new_text = _rewrite_root_attr(path.read_text(encoding="utf-8"), "Parameters", _parameter_text(updated_list))
    updated = parse_robei_model_text(new_text, path)
    landed = next(
        (param for param in updated["parameter_list"] if param["name"] == match["name"]), None
    )
    if landed is None or landed["default"] != new_value:
        raise ValueError(
            f"parameter {match['name']} did not update (got {landed['default'] if landed else None!r})"
        )
    result: dict[str, Any] = {
        "ok": True,
        "dry_run": bool(dry_run),
        "model": updated["name"],
        "path": str(path),
        "relative_path": _rel(path),
        "parameter": match["name"],
        "old_value": match["default"],
        "new_value": new_value,
        "parameters": updated["parameter_list"],
        "parameters_text": updated["parameters"],
        "width_info": _param_width_report(updated, match["name"], new_value),
        "sha256_before": _sha256(path),
    }
    if dry_run:
        result["sha256"] = None
        return result
    backup = _backup_file(path)
    path.write_text(new_text, encoding="utf-8", newline="\n")
    result.update({"backup": str(backup) if backup else None, "sha256": _sha256(path)})
    return result


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


def _split_project_name(name: str) -> tuple[str, Path]:
    """``lab2/exp1`` -> ``("exp1", Path("lab2/exp1"))``; every segment is a model name."""
    raw = (name or "").strip()
    if not raw:
        raise ValueError("project name must not be empty")
    if "\\" in raw:
        raise ValueError(
            f"use forward slashes in a nested project name: {name!r} (e.g. lab2/exp1)"
        )
    if raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise ValueError(f"project name must be relative to the workspace: {name!r}")
    parts = raw.split("/")
    for part in parts:
        if not part:
            raise ValueError(f"empty segment in project name: {name!r}")
        if part in (".", ".."):
            raise ValueError(f"project name must not contain {part!r}: {name!r}")
        if not MODEL_NAME_RE.match(part):
            raise ValueError(
                f"invalid project name segment {part!r} in {name!r}: each segment must "
                "match [A-Za-z_][A-Za-z0-9_]*"
            )
    return parts[-1], Path(*parts)


def create_robei_project(
    name: str,
    models: list[str] | None = None,
    skeleton: bool = True,
) -> dict[str, Any]:
    """Create a Robei project skeleton (folders + .proj + blank models) in the workspace.

    ``name`` may be nested (``lab2/exp1``): the directory becomes
    ``workspace/lab2/exp1`` while the ``.proj`` file, its XML root tag and its
    ``name`` attribute use the last segment only (Robei keys the tree off that).

    ``models=None`` keeps the old behaviour (one blank model named like the
    project) only while ``skeleton`` is true; ``models=[]`` or
    ``skeleton=False`` creates no blank model at all.
    """
    leaf, rel = _split_project_name(name)
    models = ([leaf] if skeleton else []) if models is None else list(models)
    for model in models:
        if not MODEL_NAME_RE.match(model):
            raise ValueError(f"invalid model name: {model!r}")
    root = SETTINGS.workspace / rel
    if not is_within(root, SETTINGS.workspace):
        raise ValueError(f"project path escapes the workspace: {root}")
    if root.exists():
        raise FileExistsError(f"project path already exists: {root}")
    for sub in ("model", "test", "const", "verilog"):
        (root / sub).mkdir(parents=True, exist_ok=True)

    if models:
        entries = "\n".join(
            f'  <{m}.model name="{m}.model" parent="model" type="model"/>' for m in models
        )
        model_folder = (
            f' <model name="model" parent="{leaf}" type="folder">\n{entries}\n </model>\n'
        )
    else:
        model_folder = f' <model name="model" parent="{leaf}" type="folder"/>\n'
    proj_text = (
        f'<{leaf} name="{leaf}" parent="0" type="proj">\n'
        f"{model_folder}"
        f' <test name="test" parent="{leaf}" type="folder"/>\n'
        f' <const name="const" parent="{leaf}" type="folder"/>\n'
        f' <verilog name="verilog" parent="{leaf}" type="folder"/>\n'
        f"</{leaf}>\n"
    )
    proj_path = root / f"{leaf}.proj"
    proj_path.write_text(proj_text, encoding="utf-8", newline="\n")
    ET.fromstring(proj_text)

    created = []
    for model in models:
        model_path = root / "model" / f"{model}.model"
        model_path.write_text(_model_xml(model, [], "", ""), encoding="utf-8", newline="\n")
        created.append(str(model_path))
    return {
        "ok": True,
        "project": leaf,
        "project_path": "/".join(rel.parts),
        "root": str(root),
        "proj": str(proj_path),
        "models": created,
        "relative_root": _rel(root),
        "skeleton": bool(models),
    }


def create_robei_model(
    proj_path: str,
    name: str,
    ports: list[dict[str, Any]] | None = None,
    code: str = "",
    comment: str = "",
    overwrite: bool = False,
    parameters: dict[str, Any] | str | None = None,
) -> dict[str, Any]:
    """Create a .model with root parameters, ports and Code, and register it.

    ``parameters`` uses the same object form as an instance override
    (``{"LEN": 4}``) and is written to the root ``<Module Parameters>``, so
    another model can override it and a port width may reference it
    (``width: "LEN"`` -> ``Datasize="LEN-1:0"``).
    """
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

    parameters_text, param_warnings = _normalise_instance_parameters(
        name, parameters, None, root=True
    )
    declared = {
        item["name"].upper() for item in parse_parameter_text_checked(parameters_text)[0]
    }

    cleaned: list[dict[str, Any]] = []
    for port in ports or []:
        entry = {
            "name": str(port["name"]),
            "direction": str(port.get("direction", "input")).lower(),
            "width": _width_text(port.get("width", 1), f"port {port.get('name')!r} width"),
            "datatype": str(port.get("datatype", "wire")).lower(),
            "side": (str(port["side"]).lower() if port.get("side") else None),
        }
        if not PORT_NAME_RE.match(entry["name"]):
            raise ValueError(f"invalid port name: {entry['name']!r}")
        if entry["direction"] not in PORT_DIRECTIONS:
            raise ValueError(f"invalid direction: {entry['direction']}")
        if entry["datatype"] not in PORT_DATATYPES:
            raise ValueError(f"invalid datatype: {entry['datatype']}")
        for token in re.findall(r"[A-Za-z_][A-Za-z0-9_$]*", entry["width"]):
            if token.upper() not in declared:
                raise BadRequest(
                    f"port {entry['name']!r} width {entry['width']!r} uses {token!r}, "
                    "which this model does not declare",
                    f"declare it first (parameters={{\"{token}\": 4}}) or use a literal "
                    f"width such as width=4 (declared: {_names(declared)})",
                )
        cleaned.append(entry)

    xml_text = _model_xml(name, cleaned, code, comment, parameters_text)
    ET.fromstring(xml_text)
    backup = None
    if target.exists():
        backup = _backup_file(target)
    target.write_text(xml_text, encoding="utf-8", newline="\n")
    registration = _register_model_in_project(proj, name)

    parsed = parse_robei_model(target)
    return _result_widths({
        "ok": True,
        "model": name,
        "path": str(target),
        "relative_path": _rel(target),
        "project": str(proj),
        "registered_in_project": registration in ("created", "exists"),
        "registration": registration,
        "backup": str(backup) if backup else None,
        "sha256": _sha256(target),
        "parameters": parsed.get("parameter_list"),
        "parameters_text": parsed.get("parameters"),
        "ports": parsed.get("ports"),
        "code": parsed.get("code"),
        "warnings": param_warnings,
    })


# ---------------------------------------------------------------------------
# 1.1.0 — component library (read-only scan of the installed Robei ToolBox)
# ---------------------------------------------------------------------------

LIBRARY_ENV = "ROBEI_MCP_LIBRARY"

# Skips ``<!DOCTYPE ...>`` / ``<?xml ...?>`` and stops at the root element's
# start tag. Attribute values may contain ``>``, so quoting is respected.
_ROOT_TAG_RE = re.compile(
    r"<\s*(?![!?])[A-Za-z_][\w.:-]*(?:[^>\"']|\"[^\"]*\"|'[^']*')*>",
    re.DOTALL,
)


def _norm_path(path: Path) -> str:
    try:
        return os.path.normcase(str(Path(path).resolve()))
    except OSError:  # pragma: no cover - only for unreadable roots
        return os.path.normcase(str(path))


def library_roots() -> tuple[Path, ...]:
    """Where the installed Robei model libraries live.

    Defaults to every configured model root except the workspace itself: on
    Windows that is ``C:\\ProgramData\\Robei`` (the 24 ToolBox models). Override
    with ``ROBEI_MCP_LIBRARY`` (``;``-separated on Windows, ``:`` elsewhere).
    """
    raw = os.environ.get(LIBRARY_ENV, "").strip()
    if raw:
        return tuple(Path(part).expanduser() for part in raw.split(os.pathsep) if part.strip())
    workspace = _norm_path(SETTINGS.workspace)
    return tuple(root for root in SETTINGS.model_roots if _norm_path(root) != workspace)


def _library_files() -> list[Path]:
    seen: set[str] = set()
    files: list[Path] = []
    for root in library_roots():
        if not root.is_dir():
            continue
        for path in sorted(root.glob("*.model")):
            key = _norm_path(path)
            if key in seen:
                continue
            seen.add(key)
            files.append(path)
    return files


def _find_library_file(name: str) -> Path | None:
    """Match by file stem first, then by the module name declared inside."""
    wanted = (name or "").strip()
    if not wanted:
        raise ValueError("library model name must not be empty")
    if wanted.lower().endswith(".model"):
        wanted = wanted[:-6]
    lowered = wanted.lower()
    by_module: Path | None = None
    for path in _library_files():
        if path.stem.lower() == lowered:
            return path
        if by_module is None:
            try:
                if parse_robei_model(path)["name"].lower() == lowered:
                    by_module = path
            except Exception:  # noqa: BLE001 - one broken file must not hide the rest
                continue
    return by_module


def library_lint(model: dict[str, Any], timeout_s: int = 20) -> dict[str, Any]:
    """Compile one library model with the bundled ``iverilog -g2012``.

    The library roots are passed as ``-y`` so a model that instantiates its
    neighbours still resolves; today's ToolBox has no such model, but the flag
    costs nothing and keeps the result honest if that changes.
    """
    text, warnings = model_to_verilog_with_warnings(model)
    run_dir = _new_run_dir("library")
    source = run_dir / f"{model['name'] or 'model'}.v"
    source.write_text(text, encoding="utf-8", newline="\n")
    cmd: list[str | Path] = [SETTINGS.iverilog, "-g2012", "-o", run_dir / "lint.out"]
    for root in library_roots():
        if root.is_dir():
            cmd += ["-y", root]
    cmd.append(source)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return {"ok": False, "returncode": None, "error": "timeout", "warnings": warnings}
    except OSError as exc:
        return {"ok": False, "returncode": None, "error": f"{type(exc).__name__}: {exc}", "warnings": warnings}
    stderr = (proc.stderr or "").strip()
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "tool": "iverilog -g2012",
        "first_error": next((line.strip() for line in stderr.splitlines() if "error" in line.lower()), ""),
        "stderr": stderr[:1000],
        "warnings": warnings,
    }


def list_library_models(
    filter: str | None = None,
    include_lint: bool = True,
    limit: int = 200,
) -> dict[str, Any]:
    """Read-only inventory of the installed ToolBox models.

    ``lint`` is a real ``iverilog -g2012`` compile of the exported module, not a
    guess. The library is never written to.
    """
    needle = (filter or "").strip().lower()
    limit = max(1, min(int(limit), 500))
    entries: list[dict[str, Any]] = []
    for path in _library_files():
        try:
            model = parse_robei_model(path)
        except Exception as exc:  # noqa: BLE001 - report, never abort the scan
            entry = {
                "name": path.stem,
                "module_name": "",
                "path": str(path),
                "error": f"{type(exc).__name__}: {exc}",
            }
            if needle and needle not in path.stem.lower():
                continue
            entries.append(entry)
            continue
        haystack = " ".join(
            [path.stem, model["name"], model["class"], model["category"], model["type"]]
        ).lower()
        if needle and needle not in haystack:
            continue
        entry = {
            "name": path.stem,
            "module_name": model["name"],
            "name_matches_module": path.stem.lower() == model["name"].lower(),
            "type": model["type"] or model["root_tag"],
            "class": model["class"],
            "category": model["category"],
            "path": str(path),
            "ports": [
                {
                    "name": port["name"],
                    "direction": port["inout"] or "input",
                    "width": robei_width(port["datasize"]),
                    "datasize": port["datasize"],
                }
                for port in model["ports"]
            ],
            "parameters": model["parameter_list"],
            "instances": len(model["instances"]),
            "size_bytes": model["size_bytes"],
        }
        if include_lint:
            entry["lint"] = library_lint(model)
        entries.append(entry)
        if len(entries) >= limit:
            break
    return {
        "ok": True,
        "roots": [str(root) for root in library_roots()],
        "model_count": len(entries),
        "filter": filter or "",
        "include_lint": bool(include_lint),
        "models": entries,
    }


def _rewrite_root_attr(text: str, attribute: str, value: str) -> str:
    """Replace (or add) one attribute on the root element, leaving the rest byte-identical."""
    match = _ROOT_TAG_RE.search(text)
    if match is None:
        raise ValueError("model has no root element")
    tag = match.group(0)
    escaped = _attr(value)
    pattern = re.compile(rf'(\s{re.escape(attribute)}\s*=\s*)"[^"]*"')
    if pattern.search(tag):
        new_tag = pattern.sub(lambda found: found.group(1) + f'"{escaped}"', tag, count=1)
    else:
        closing = "/>" if tag.endswith("/>") else ">"
        new_tag = tag[: -len(closing)] + f' {attribute}="{escaped}"' + closing
    return text[: match.start()] + new_tag + text[match.end():]


def import_library_model(
    name: str,
    project: str | None = None,
    as_name: str | None = None,
    overwrite: bool = False,
    dry_run: bool = False,
    proj_path: str | None = None,
) -> dict[str, Any]:
    """Copy a ToolBox model into ``<project>/model``, register it, then lint it.

    ``project`` is the project path (``work/a.proj``); ``proj_path`` is accepted as an
    alias so a caller that guesses the other name does not have to retry blind.

    ``constant.model`` declares ``module register`` — a copied file whose name
    disagrees with its module name breaks every canvas that instantiates it, so
    that case is refused unless the caller picks ``as_name`` (the copy is then
    renamed *and* its root ``Name`` attribute is rewritten to match).
    """
    if project and proj_path and str(project) != str(proj_path):
        raise ValueError(
            "project and proj_path disagree; pass only one of them, e.g. "
            '{"name": "and2", "project": "work/a.proj"}'
        )
    target_project = project or proj_path
    if not target_project:
        raise ValueError(
            "import_library_model needs the project path: pass "
            '{"name": "and2", "project": "work/a.proj"} (alias: proj_path)'
        )
    proj = resolve_project_path(target_project)
    if not is_within(proj, SETTINGS.workspace):
        raise ValueError("refusing to modify a project outside the workspace")
    source = _find_library_file(name)
    if source is None:
        raise FileNotFoundError(
            f"library model {name!r} not found under {[str(root) for root in library_roots()]}; "
            "call list_library_models to see the available names"
        )
    model = parse_robei_model(source)
    module_name = model["name"] or source.stem

    target_stem = (as_name or "").strip()
    if target_stem.lower().endswith(".model"):
        target_stem = target_stem[:-6]
    if target_stem and not MODEL_NAME_RE.match(target_stem):
        raise ValueError(f"invalid as_name: {as_name!r}")
    if not target_stem:
        if module_name.lower() != source.stem.lower():
            raise ValueError(
                f"library file {source.stem}.model declares module {module_name!r}; "
                f"pass as_name={module_name!r} so the copied file and its module name agree"
            )
        target_stem = source.stem

    model_dir = proj.parent / "model"
    if not model_dir.is_dir():
        raise FileNotFoundError(f"project has no model folder: {model_dir}")
    target = model_dir / f"{target_stem}.model"
    if target.exists() and not overwrite:
        raise FileExistsError(f"model already exists: {target} (pass overwrite=true to replace it)")

    text = source.read_text(encoding="utf-8")
    renamed_module = None
    if target_stem != module_name:
        text = _rewrite_root_attr(text, "Name", target_stem)
        renamed_module = {"from": module_name, "to": target_stem}

    result: dict[str, Any] = {
        "ok": True,
        "dry_run": bool(dry_run),
        "name": source.stem,
        "module_name": target_stem if renamed_module else module_name,
        "source": str(source),
        "target": str(target),
        "relative_path": _rel(target),
        "project": str(proj),
        "overwrite": bool(overwrite),
        "renamed_module": renamed_module,
    }
    if dry_run:
        result.update({"registration": "pending", "lint": None, "backup": None, "proj_backup": None})
        return result

    backup = _backup_file(target)
    target.write_text(text, encoding="utf-8", newline="\n")
    proj_backup = _backup_file(proj)
    registration = _register_model_in_project(proj, target_stem)
    imported = parse_robei_model(target)
    result.update(
        {
            "dry_run": False,
            "backup": str(backup) if backup else None,
            "proj_backup": str(proj_backup) if proj_backup else None,
            "registration": registration,
            "registered_in_project": registration in ("created", "exists"),
            "sha256": _sha256(target),
            "ports": imported["ports"],
            "parameters": imported["parameter_list"],
            "lint": library_lint(imported),
        }
    )
    return result
