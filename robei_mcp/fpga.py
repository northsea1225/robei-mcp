"""FPGA backend: yosys synthesis, then Roplace / fasm2frames / xc7frames2bit to a .bit.

The three command lines below were read off the Robei 5.0 IDE itself (Build menu)
and reproduced byte-for-byte, so a headless build can be compared with one the GUI
produced:

  Synthesis  yosys -p "read_verilog <build>/<top>.v; synth_xilinx -flatten -arch xc7
                     -abc9 -nodsp; write_json -noscopeinfo <build>/<const>.json"
  Route      Roplace --chipdb <db>/chipdb/<part>.bin --xdc <build>/<const>.xdc
                     --json <build>/<const>.json
                     --write <build>/<const>_routed.json
                     --fasm <build>/<const>.fasm
  Package    fasm2frames --db-root <db>/<family>/ --part <part>
                         <build>/<const>.fasm <build>/<const>.frame
             xc7frames2bit --part_file <db>/<family>/<part>/part.yaml
                            --part_name <part> --frm_file <frame>
                            --output_file <build>/<const>.bit

No timing sign-off: Roplace's own Fmax line is reported as a reference number only.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
from pathlib import Path
from typing import Any, Literal

from .config import SETTINGS
from .errors import DeviceUnsupported, FpgaStepFailed, UnconstrainedIO
from .model import model_to_verilog_with_warnings, parse_robei_model
from .security import is_within, resolve_project_path
from .verilog import parse_module_ports, robei_width

PART_RE = re.compile(r"^[a-z][a-z0-9]*-[0-9][a-z0-9]*$")
STAGES = ("synth", "route", "package")
# 7-series bitstreams carry an ASCII header ('a' = .frame path, 'b' = part,
# 'c'/'d' = date/time) before this sync word; only what follows it is configuration.
BIT_SYNC_WORD = b"\xaa\x99\x55\x66"
FMAX_RE = re.compile(r"Max frequency for clock\s+'([^']+)'\s*:\s*([0-9.]+)\s*MHz", re.IGNORECASE)
_CLOCK_HINT = "no clocks found in design"
_YOSYS_FPGA_ARCH = "xc7"


# ---------------------------------------------------------------------------
# Toolchain and device database discovery
# ---------------------------------------------------------------------------
def _db_candidates() -> list[Path]:
    out: list[Path] = []
    env = os.environ.get("ROBEI_FPGA_DB")
    if env:
        out.append(Path(env))
    if os.name == "nt":
        program_data = os.environ.get("ProgramData")
        if program_data:
            out.append(Path(program_data) / "Robei" / "xilinx")
        out.append(Path(r"C:\ProgramData\Robei\xilinx"))
    out.append(SETTINGS.robei_home / "xilinx")
    out.append(SETTINGS.workspace / "xilinx")
    return out


def db_root() -> Path:
    """Directory holding ``chipdb/`` and ``<family>/<part>/part.yaml``."""
    for candidate in _db_candidates():
        if (candidate / "chipdb").is_dir():
            return candidate
    return _db_candidates()[0]


def _bin_dir() -> Path:
    env = os.environ.get("ROBEI_FPGA_BIN")
    if env:
        return Path(env)
    return SETTINGS.robei_home / "bin"


def tool_paths() -> dict[str, Path]:
    """Roplace / fasm2frames / xc7frames2bit, next to Robei (PATH as fallback)."""
    bin_dir = _bin_dir()
    out: dict[str, Path] = {}
    for key, stem in (
        ("roplace", "Roplace"),
        ("fasm2frames", "fasm2frames"),
        ("xc7frames2bit", "xc7frames2bit"),
    ):
        exe = f"{stem}.exe" if os.name == "nt" else stem
        bundled = bin_dir / exe
        if bundled.is_file():
            out[key] = bundled
            continue
        found = shutil.which(exe) or shutil.which(stem)
        out[key] = Path(found) if found else bundled
    return out


def _part_yaml(root: Path, part: str) -> tuple[str | None, Path | None]:
    for family_dir in sorted(root.glob("*")):
        if not family_dir.is_dir():
            continue
        candidate = family_dir / part / "part.yaml"
        if candidate.is_file():
            return family_dir.name, candidate
    return None, None


def device_table(root: Path | None = None) -> list[dict[str, Any]]:
    """Every part the installed database knows, from chipdb/*.bin plus */*/part.yaml."""
    base = root or db_root()
    rows: dict[str, dict[str, Any]] = {}
    chip_dir = base / "chipdb"
    if chip_dir.is_dir():
        for chipdb in sorted(chip_dir.glob("*.bin")):
            rows[chipdb.stem] = {"part": chipdb.stem, "chipdb": chipdb, "part_yaml": None}
    for family_dir in sorted(base.glob("*")):
        if not family_dir.is_dir() or family_dir.name == "chipdb":
            continue
        for part_dir in sorted(family_dir.glob("*")):
            part_yaml = part_dir / "part.yaml"
            if not part_yaml.is_file():
                continue
            row = rows.setdefault(part_dir.name, {"part": part_dir.name, "chipdb": None, "part_yaml": None})
            row["part_yaml"] = part_yaml
    out: list[dict[str, Any]] = []
    for part in sorted(rows):
        row = rows[part]
        family = None
        if row["part_yaml"] is not None:
            family = row["part_yaml"].parent.parent.name
        chipdb: Path | None = row["chipdb"]
        reasons: list[str] = []
        if chipdb is None:
            reasons.append(f"chipdb/{part}.bin is missing (no place-and-route model for this device)")
        if row["part_yaml"] is None:
            reasons.append("no part.yaml for this part")
        out.append(
            {
                "part": part,
                "family": family,
                "chipdb": str(chipdb) if chipdb else None,
                "chipdb_bytes": chipdb.stat().st_size if chipdb else None,
                "part_yaml": str(row["part_yaml"]) if row["part_yaml"] else None,
                "buildable": not reasons,
                "reason": "; ".join(reasons) if reasons else None,
            }
        )
    return out


def list_fpga_devices(part: str | None = None) -> dict[str, Any]:
    """List FPGA parts with their chipdb/part.yaml pair and whether a .bit can be built."""
    root = db_root()
    devices = device_table(root)
    if part:
        wanted = part.strip()
        devices = [row for row in devices if row["part"] == wanted]
        if not devices:
            raise ValueError(f"unknown part {part!r}; call list_fpga_devices for the installed parts")
    tools = tool_paths()
    return {
        "ok": bool(devices),
        "db_root": str(root),
        "db_root_exists": root.is_dir(),
        "buildable_count": sum(1 for row in devices if row["buildable"]),
        "devices": devices,
        "tools": {key: {"path": str(path), "exists": path.is_file()} for key, path in tools.items()},
        "note": "synthesis needs yosys only; route/package need the three Robei binaries above",
    }


def _device(part: str) -> dict[str, Any]:
    clean = (part or "").strip()
    if not clean:
        raise ValueError("part is required, e.g. xc7a50tfgg484-1")
    for row in device_table():
        if row["part"] == clean:
            return row
    known = [row["part"] for row in device_table() if row["buildable"]]
    raise DeviceUnsupported(f"unknown part {clean!r}; buildable parts: {known}")


# ---------------------------------------------------------------------------
# Project -> Verilog / XDC
# ---------------------------------------------------------------------------
def _project_paths(proj: Path) -> dict[str, Path]:
    return {
        "model": proj.parent / "model",
        "const": proj.parent / "const",
        "build": proj.parent / "build",
    }


def _const_path(proj: Path, const_name: str | None) -> Path:
    """The .const to build from, or a ValueError that says what to do next."""
    const_dir = _project_paths(proj)["const"]
    if const_name:
        stem = const_name[:-6] if const_name.lower().endswith(".const") else const_name
        target = const_dir / f"{stem}.const"
        if not target.is_file():
            available = sorted(path.stem for path in const_dir.glob("*.const")) if const_dir.is_dir() else []
            raise ValueError(
                f"constraint {stem!r} not found under {const_dir} (available: {available}); "
                "create it with create_robei_const(proj_path, name, model, pins) or pass "
                "const_name=<existing .const stem>"
            )
        return target
    candidates = sorted(const_dir.glob("*.const"))
    if not candidates:
        raise ValueError(
            f"project has no const/*.const file in {const_dir}; build_bitstream needs pin "
            "constraints - create one with create_robei_const(proj_path, name, model, pins) first"
        )
    return candidates[0]


def _write_filelist(build: Path, sources: list[Path]) -> Path:
    """``build/filelist.vf``: the file list Robei's Compile leaves for Synthesis."""
    target = build / "filelist.vf"
    target.write_text(
        "\n".join(source.as_posix() for source in sources) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return target


def _const_model(const: Path) -> str | None:
    """Model instantiated by a .const (``None`` when the file declares none)."""
    from . import constraints

    try:
        return constraints.pin_map(const).get("model")
    except Exception:
        return None


def _export_build_verilog(proj: Path) -> dict[str, Any]:
    """Write ``build/<module>.v`` for every model, the way Build > Synthesis does."""
    paths = _project_paths(proj)
    model_dir = paths["model"]
    if not model_dir.is_dir():
        raise FileNotFoundError(f"project has no model folder: {model_dir}")
    build = paths["build"]
    build.mkdir(parents=True, exist_ok=True)
    written: list[dict[str, Any]] = []
    warnings: list[str] = []
    for model_file in sorted(model_dir.glob("*.model")):
        model = parse_robei_model(model_file)
        text, model_warnings = model_to_verilog_with_warnings(model)
        target = build / f"{model['name']}.v"
        target.write_text(text, encoding="utf-8", newline="\n")
        warnings.extend(f"{model['name']}: {item}" for item in model_warnings)
        written.append(
            {
                "path": str(target),
                "module": model["name"],
                "size_bytes": target.stat().st_size,
                "ports": [
                    {"name": port["name"], "width": int(port["datasize"] or 1)}
                    for port in model["ports"]
                ],
            }
        )
    return {"build_dir": str(build), "files": written, "warnings": warnings}


MODULE_RE = re.compile(r"\bmodule\s+([A-Za-z_][A-Za-z0-9_$]*)")


def _pick_sources(exported: dict[str, Any], top: str | None) -> tuple[list[Path], str | None]:
    """Top model plus every model it instantiates, the way Robei feeds yosys.

    A freshly created project also carries blank placeholder models; handing those
    to yosys would give it two top-level candidates, so only the reachable set is used.
    """
    by_name = {item["module"]: Path(item["path"]) for item in exported["files"]}
    if not by_name:
        return [], top
    resolved = top or next(iter(by_name))
    chosen: list[Path] = []
    seen: set[str] = set()
    stack = [resolved]
    while stack:
        name = stack.pop()
        if name in seen or name not in by_name:
            continue
        seen.add(name)
        path = by_name[name]
        chosen.append(path)
        text = path.read_text(encoding="utf-8", errors="replace")
        for other in by_name:
            if other != name and other not in seen and re.search(rf"\b{re.escape(other)}\b", text):
                stack.append(other)
    return chosen, resolved


def _io_bits(sources: list[Path], top: str | None) -> int:
    """Port bits of the top module only (children are not package pins)."""
    for source in sources:
        text = source.read_text(encoding="utf-8", errors="replace")
        if top:
            if not re.search(rf"\bmodule\s+{re.escape(top)}\b", text):
                continue
        else:
            match = MODULE_RE.search(text)
            top = match.group(1) if match else None
            if not top:
                continue
        try:
            ports = parse_module_ports(text, top)
        except ValueError:
            continue
        return sum(robei_width(port.get("width")) or 1 for port in ports)
    return 0


# ---------------------------------------------------------------------------
# yosys
# ---------------------------------------------------------------------------
def _yosys_script(sources: list[Path], json_out: Path) -> str:
    """Same -p script Robei's Build > Synthesis hands to yosys."""
    reads = "; ".join(f"read_verilog {source.as_posix()}" for source in sources)
    return (
        f"{reads}; synth_xilinx -flatten -arch {_YOSYS_FPGA_ARCH} -abc9 -nodsp; "
        f"write_json -noscopeinfo {json_out.as_posix()}"
    )


# synth_xilinx leaves generic Xilinx cells behind: LUT2..LUT6, FDRE/FDSE/SDFF*,
# CARRY4, RAMB18/36, DSP48*.  Roplace then maps them onto its own bels.
_UTIL_KEYS = {
    "LUT": ("LUT", "INV"),  # an INV also lands in a LUT site on 7-series
    "FF": ("FD", "SDFF"),
    "CARRY": ("CARRY",),
    "BRAM36": ("RAMB36",),
    "BRAM18": ("RAMB18",),
    "DSP": ("DSP48",),
}


def _parse_stat(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for line in text.splitlines():
        stripped = line.strip()
        match = re.match(r"^([A-Z][A-Za-z0-9_]+)\s+(\d+)$", stripped)
        if match:
            counts[match.group(1)] = counts.get(match.group(1), 0) + int(match.group(2))
    return counts


def _utilization(counts: dict[str, int], io_bits: int) -> dict[str, Any]:
    out: dict[str, int] = {}
    for key, prefixes in _UTIL_KEYS.items():
        out[key] = sum(value for name, value in counts.items() if name.startswith(prefixes))
    out["IO"] = io_bits
    return out


def _bit_payload(bit_path: Path) -> dict[str, Any]:
    """Hash of the bitstream from the 0xAA995566 sync word on (header excluded).

    The 'a' field holds the absolute .frame path and 'c'/'d' the date/time, so a
    byte-for-byte .bit comparison fails on identical builds: both size (path length)
    and those bytes (timestamp) move. The payload digest does not.
    """
    data = bit_path.read_bytes()
    offset = data.find(BIT_SYNC_WORD)
    if offset < 0:
        return {"offset": None, "sha256": None, "reason": "sync word 0xAA995566 not found"}
    return {"offset": offset, "sha256": hashlib.sha256(data[offset:]).hexdigest()}


def _timeout_budget(timeout_s: int) -> dict[str, Any]:
    """``_run`` clamps to max_timeout_s; report both numbers instead of silently cutting."""
    requested = max(1, int(timeout_s))
    effective = max(1, min(requested, SETTINGS.max_timeout_s))
    return {
        "requested_s": requested,
        "effective_s": effective,
        "max_timeout_s": SETTINGS.max_timeout_s,
        "clamped": effective != requested,
    }


def _tail(text: str, lines: int = 25) -> list[str]:
    kept = [line for line in (text or "").splitlines() if line.strip()]
    return kept[-lines:]


_SECRET_RE = re.compile(r"(gh[pousr]_[A-Za-z0-9]+|github_pat_[A-Za-z0-9_]+)")


def _redact_argv(args: list[Any]) -> list[str]:
    """Command line as text, with anything token-shaped masked."""
    out: list[str] = []
    for arg in args:
        text = str(arg)
        text = re.sub(r"https://[^@\s]*@", "https://***@", text)
        out.append(_SECRET_RE.sub("***", text))
    return out


def _run_stage(
    args: list[Any],
    cwd: Path,
    timeout_s: int,
    stage: str,
    outputs: dict[str, Path],
    run_dir: Path | None = None,
) -> dict[str, Any]:
    from . import tools

    result = tools._run(args, cwd=cwd, timeout_s=timeout_s)
    text = (result.get("stdout") or "") + (result.get("stderr") or "")
    log_path = None
    if run_dir is not None:
        run_dir.mkdir(parents=True, exist_ok=True)
        log_file = run_dir / f"{stage}.log"
        log_file.write_text(text, encoding="utf-8", errors="replace")
        log_path = str(log_file)
    record = {
        "stage": stage,
        "command": result.get("command"),
        "argv": _redact_argv(args),
        "returncode": result.get("returncode"),
        "duration_s": round((result.get("duration_ms") or 0) / 1000.0, 2),
        "timeout": bool(result.get("timeout")),
        "log_tail": _tail(text),
        "log_path": log_path,
        "run_dir": str(run_dir) if run_dir is not None else None,
        "outputs": {},
    }
    for name, path in outputs.items():
        record["outputs"][name] = {
            "path": str(path),
            "exists": path.is_file(),
            "size_bytes": path.stat().st_size if path.is_file() else None,
        }
    record["ok"] = bool(result.get("ok")) and all(
        item["exists"] for item in record["outputs"].values()
    )
    if not record["ok"]:
        code = "timeout" if record["timeout"] else "fpga_step_failed"
        raise FpgaStepFailed.from_record(record, code=code)
    return record


def _fmax(text: str) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    for clock, value in FMAX_RE.findall(text or ""):
        entry = {"clock": clock, "mhz": float(value)}
        if best is None or entry["mhz"] > best["mhz"]:
            best = entry
    return best


def _common(proj: Path, part: str | None) -> dict[str, Any]:
    paths = _project_paths(proj)
    tools = tool_paths()
    return {
        "project": str(proj),
        "project_relative": _rel(proj),
        "build_dir": str(paths["build"]),
        "part": part,
        "db_root": str(db_root()),
        "tool_paths": {key: str(path) for key, path in tools.items()},
    }


def _rel(path: Path) -> str:
    try:
        return path.relative_to(SETTINGS.workspace).as_posix()
    except ValueError:
        return path.as_posix()


def _new_run_dir() -> Path:
    """One directory per FPGA call: stage logs, summary.json, error pointers."""
    from . import tools

    return tools._new_run_dir("fpga")


def _write_summary(run_dir_file: Path, payload: dict[str, Any]) -> str:
    import json

    run_dir_file.parent.mkdir(parents=True, exist_ok=True)
    run_dir_file.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
    return str(run_dir_file)


# ---------------------------------------------------------------------------
# M1: synthesis
# ---------------------------------------------------------------------------
def synthesize_project(
    proj_path: str,
    top: str | None = None,
    part: str | None = None,
    timeout_s: int = 120,
) -> dict[str, Any]:
    """Run yosys ``synth_xilinx`` on a project and report LUT/FF/BRAM/DSP/IO counts.

    ``timeout_s`` defaults to 120 s, which is also ``ROBEI_MAX_TIMEOUT_S``; a larger
    request is clamped (and reported in ``timeout.clamped``) rather than silently cut.
    """
    proj = resolve_project_path(proj_path)
    if not is_within(proj, SETTINGS.workspace):
        raise ValueError("refusing to synthesise a project outside the workspace")
    if part:
        _device(part)
    const = _const_path(proj, None)
    preferred = top or _const_model(const)
    exported = _export_build_verilog(proj)
    chosen, resolved_top = _pick_sources(exported, preferred)
    if not chosen:
        raise FileNotFoundError("project has no models to synthesise")
    filelist = _write_filelist(_project_paths(proj)["build"], chosen)
    json_out = _project_paths(proj)["build"] / f"{const.stem}.json"
    run_dir = _new_run_dir()

    info = _common(proj, part)
    info.update(
        {
            "top": resolved_top,
            "sources": [item["path"] for item in exported["files"]],
            "const": str(const),
            "filelist": str(filelist),
            "json_path": str(json_out),
            "warnings": exported["warnings"],
        }
    )
    script = _yosys_script(chosen, json_out)
    budget = _timeout_budget(timeout_s)
    stage = _run_stage(
        [SETTINGS.yosys, "-p", script],
        cwd=_project_paths(proj)["build"],
        timeout_s=budget["effective_s"],
        stage="synth",
        outputs={"json": json_out},
        run_dir=run_dir,
    )
    stat = _stat_pass(json_out, budget["effective_s"])
    utilization = _utilization(stat["counts"], _io_bits(chosen, resolved_top))
    info.update(
        {
            "ok": True,
            "yosys_script": script,
            "seconds": stage["duration_s"],
            "timeout": budget,
            "log_tail": stage["log_tail"],
            "cells": stat["counts"],
            "utilization": utilization,
            "utilization_note": (
                "LUT=LUT*/INV cells (one LUT site each), FF=FD*/SDFF*, CARRY=CARRY* cells, "
                "BRAM36/BRAM18=RAM block counts, DSP=DSP48*, IO=top module port bits"
            ),
            "json_size_bytes": json_out.stat().st_size,
            "json_sha256": _sha(json_out),
        }
    )
    info["run_dir"] = str(run_dir)
    info["summary_path"] = _write_summary(run_dir / "summary.json", info)
    return info


def _stat_pass(json_path: Path, timeout_s: int) -> dict[str, Any]:
    """Cell counts from yosys ``stat``.

    ``tee -o`` puts the report in a file: the tool layer clips long stdout, which
    would cut the cell table in half.
    """
    from . import tools

    report = json_path.with_name(f"{json_path.stem}_stat.txt")
    script = (
        f"read_json {json_path.as_posix()}; "
        f"tee -o {report.as_posix()} stat"
    )
    result = tools._run([SETTINGS.yosys, "-p", script], cwd=json_path.parent, timeout_s=int(timeout_s))
    if report.is_file():
        text = report.read_text(encoding="utf-8", errors="replace")
    else:
        text = (result.get("stdout") or "") + (result.get("stderr") or "")
    return {
        "ok": bool(result.get("ok")),
        "counts": _parse_stat(text),
        "report": str(report) if report.is_file() else None,
        "log_tail": _tail(text, 30),
    }


def _sha(path: Path) -> str:
    from . import tools

    return tools._sha256(path)


# ---------------------------------------------------------------------------
# M2: route + package -> .bit
# ---------------------------------------------------------------------------
def _missing_io(proj: Path, const: Path, exported: dict[str, Any], top: str | None) -> list[dict[str, Any]]:
    """Port bits of the top model that the .const does not bind to a package pin."""
    from . import constraints

    info = constraints.pin_map(const)
    covered = {(port, bit) for port, bits in (info.get("pins") or {}).items() for bit in bits}
    model_name = info.get("model")
    model_file = _project_paths(proj)["model"] / f"{model_name}.model" if model_name else None
    ports: list[dict[str, Any]] = []
    if model_file and model_file.is_file():
        ports = parse_robei_model(model_file)["ports"]
    elif exported["files"]:
        chosen, _ = _pick_sources(exported, top)
        source = chosen[0] if chosen else None
        ports = []
        if source is not None:
            text = source.read_text(encoding="utf-8", errors="replace")
            for port in parse_module_ports(text, top or MODULE_RE.search(text).group(1)):
                ports.append(
                    {"name": port["name"], "datasize": str(robei_width(port.get("width")) or 1),
                     "inout": port.get("direction") or ""}
                )
    missing: list[dict[str, Any]] = []
    for port in ports:
        width = int(port.get("datasize") or 1)
        holes = [bit for bit in range(width) if (port["name"], bit) not in covered]
        if holes:
            missing.append(
                {
                    "port": port["name"],
                    "direction": port.get("inout") or "input",
                    "missing_bits": holes if width > 1 else [0],
                }
            )
    return missing


def _normalize_steps(steps: list[Literal["synth", "route", "package"]] | None) -> list[str]:
    chosen = [str(step).strip().lower() for step in (steps or STAGES)]
    unknown = [step for step in chosen if step not in STAGES]
    if unknown:
        raise ValueError(f"unknown step(s) {unknown}; expected a subset of {list(STAGES)}")
    ordered = [step for step in STAGES if step in chosen]
    if len(set(chosen)) != len(chosen):
        raise ValueError("steps contains duplicates")
    return ordered


def build_bitstream(
    proj_path: str,
    part: str,
    const_name: str | None = None,
    steps: list[Literal["synth", "route", "package"]] | None = None,
    timeout_s: int = 120,
) -> dict[str, Any]:
    """Run yosys -> Roplace -> fasm2frames -> xc7frames2bit and report every stage.

    ``timeout_s`` defaults to 120 s (== ``ROBEI_MAX_TIMEOUT_S``); asking for more is
    clamped and flagged in ``timeout.clamped`` instead of silently truncated.
    """
    import time

    from . import constraints, tools

    started = time.monotonic()
    proj = resolve_project_path(proj_path)
    if not is_within(proj, SETTINGS.workspace):
        raise ValueError("refusing to build a project outside the workspace")
    device = _device(part)
    if not device["buildable"]:
        raise DeviceUnsupported(
            f"{part} cannot be built: {device['reason']}; "
            "call list_fpga_devices() for the parts that have both chipdb and part.yaml"
        )
    wanted = _normalize_steps(steps)
    binaries = tool_paths()
    missing_tools = [key for key, path in binaries.items() if not path.is_file()]
    if missing_tools:
        raise FpgaStepFailed(
            f"missing FPGA binaries {missing_tools} next to Robei ({_bin_dir()}); "
            "set ROBEI_FPGA_BIN to the folder holding Roplace.exe",
            stage=wanted[0],
        )

    const = _const_path(proj, const_name)
    exported = _export_build_verilog(proj)
    sources, top = _pick_sources(exported, _const_model(const))
    build = _project_paths(proj)["build"]
    run_dir = _new_run_dir()
    _write_filelist(build, sources)
    xdc_path = build / f"{const.stem}.xdc"
    xdc_generated = False
    if not xdc_path.is_file():
        # Build > Route reads build/<const>.xdc; regenerate it from the .const the way
        # export_robei_xdc does instead of dying on a missing file.
        exported_xdc = constraints.export_xdc(proj_path=str(proj), const_name=const.stem, also_build=True)
        xdc_path = Path(exported_xdc["build_copy"] or exported_xdc["path"])
        xdc_generated = True
    missing_io = _missing_io(proj, const, exported, top)
    if missing_io:
        names = [f"{item['port']}[{bit}]" for item in missing_io for bit in item["missing_bits"]]
        raise UnconstrainedIO(
            f"{const.name} leaves {len(names)} port bit(s) without a package pin: {names}",
            missing=missing_io,
        )

    budget = _timeout_budget(timeout_s)
    json_out = build / f"{const.stem}.json"
    fasm_out = build / f"{const.stem}.fasm"
    frame_out = build / f"{const.stem}.frame"
    bit_out = build / f"{const.stem}.bit"
    routed_out = build / f"{const.stem}_routed.json"
    family = device["family"]
    chipdb = Path(device["chipdb"] or "")
    part_yaml = Path(device["part_yaml"] or "")

    records: list[dict[str, Any]] = []
    fmax: dict[str, Any] | None = None
    if "synth" in wanted:
        script = _yosys_script(sources, json_out)
        records.append(
            _run_stage(
                [SETTINGS.yosys, "-p", script],
                cwd=build,
                timeout_s=budget["effective_s"],
                stage="synth",
                outputs={"json": json_out},
                run_dir=run_dir,
            )
        )
    elif "route" in wanted and not json_out.is_file():
        raise FpgaStepFailed(
            f"route needs {json_out.name}: run the synth step first (steps=['synth','route',...])",
            stage="route",
        )

    if "route" in wanted:
        route = _run_stage(
            [
                binaries["roplace"],
                "--chipdb",
                chipdb,
                "--xdc",
                xdc_path,
                "--json",
                json_out,
                "--write",
                routed_out,
                "--fasm",
                fasm_out,
            ],
            cwd=build,
            timeout_s=budget["effective_s"],
            stage="route",
            outputs={"fasm": fasm_out, "routed_json": routed_out},
            run_dir=run_dir,
        )
        route["unconstrained_note"] = None
        fmax = _fmax("\n".join(route["log_tail"]))
        records.append(route)
    elif "package" in wanted and not fasm_out.is_file():
        raise FpgaStepFailed(
            f"package needs {fasm_out.name}: run the route step first",
            stage="package",
        )

    if "package" in wanted:
        package = _run_stage(
            [
                binaries["fasm2frames"],
                "--db-root",
                (db_root() / (family or "")).as_posix().rstrip("/") + "/",
                "--part",
                part,
                fasm_out,
                frame_out,
            ],
            cwd=build,
            timeout_s=budget["effective_s"],
            stage="package.frames",
            outputs={"frame": frame_out},
            run_dir=run_dir,
        )
        records.append(package)
        records.append(
            _run_stage(
                [
                    binaries["xc7frames2bit"],
                    "--part_file",
                    part_yaml,
                    "--part_name",
                    part,
                    "--frm_file",
                    frame_out,
                    "--output_file",
                    bit_out,
                ],
                cwd=build,
                timeout_s=budget["effective_s"],
                stage="package.bit",
                outputs={"bit": bit_out},
                run_dir=run_dir,
            )
        )

    stat = _stat_pass(json_out, budget["effective_s"]) if json_out.is_file() else {"counts": {}}
    bit_payload = _bit_payload(bit_out) if bit_out.is_file() else {"offset": None, "sha256": None}
    payload = _common(proj, part)
    payload.update(
        {
            "ok": bit_out.is_file(),
            "family": family,
            "top": top,
            "const": str(const),
            "xdc": str(xdc_path),
            "xdc_generated": xdc_generated,
            "run_dir": str(run_dir),
            "steps_run": [record["stage"] for record in records],
            "stages": records,
            "artifacts": {
                "json": {"path": str(json_out), "size_bytes": json_out.stat().st_size} if json_out.is_file() else None,
                "fasm": {"path": str(fasm_out), "size_bytes": fasm_out.stat().st_size} if fasm_out.is_file() else None,
                "frame": {"path": str(frame_out), "size_bytes": frame_out.stat().st_size} if frame_out.is_file() else None,
                "bit": {
                    "path": str(bit_out),
                    "size_bytes": bit_out.stat().st_size,
                    "sha256": _sha(bit_out),
                    "payload_sha256": bit_payload["sha256"],
                    "payload_offset": bit_payload["offset"],
                }
                if bit_out.is_file()
                else None,
            },
            "bit_payload_sha256": bit_payload["sha256"],
            "bit_payload_offset": bit_payload["offset"],
            "utilization": _utilization(stat["counts"], _io_bits(sources, top)),
            "fmax_reference": fmax,
            "timing_note": (
                "Roplace's Fmax is a reference number only: this build is not timing-signed off "
                "(no .xdc clock constraints are turned into a sign-off report)"
            ),
            "seconds": round(time.monotonic() - started, 2),
            "timeout": budget,
            "warnings": exported["warnings"]
            + (
                [f"timeout_s was clamped to max_timeout_s={budget['max_timeout_s']}"]
                if budget["clamped"]
                else []
            ),
        }
    )
    payload["summary_path"] = _write_summary(run_dir / "summary.json", payload)
    return payload
