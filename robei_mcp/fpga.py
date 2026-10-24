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

import os
import re
import shutil
from pathlib import Path
from typing import Any

from .config import SETTINGS
from .errors import DeviceUnsupported, FpgaStepFailed
from .model import model_to_verilog_with_warnings, parse_robei_model
from .security import is_within, resolve_project_path
from .verilog import parse_module_ports, robei_width

PART_RE = re.compile(r"^[a-z][a-z0-9]*-[0-9][a-z0-9]*$")
STAGES = ("synth", "route", "package")
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
        ("ropllace", "Roplace"),
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
    const_dir = _project_paths(proj)["const"]
    if const_name:
        stem = const_name[:-6] if const_name.lower().endswith(".const") else const_name
        target = const_dir / f"{stem}.const"
        if not target.is_file():
            raise FileNotFoundError(f"constraint not found: {target}")
        return target
    candidates = sorted(const_dir.glob("*.const"))
    if not candidates:
        raise FileNotFoundError(f"project has no const/*.const file: {const_dir}")
    return candidates[0]


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


def _tail(text: str, lines: int = 25) -> list[str]:
    kept = [line for line in (text or "").splitlines() if line.strip()]
    return kept[-lines:]


def _run_stage(
    args: list[Any],
    cwd: Path,
    timeout_s: int,
    stage: str,
    outputs: dict[str, Path],
) -> dict[str, Any]:
    from . import tools

    result = tools._run(args, cwd=cwd, timeout_s=timeout_s)
    text = (result.get("stdout") or "") + (result.get("stderr") or "")
    record = {
        "stage": stage,
        "command": result.get("command"),
        "returncode": result.get("returncode"),
        "duration_s": round((result.get("duration_ms") or 0) / 1000.0, 2),
        "timeout": bool(result.get("timeout")),
        "log_tail": _tail(text),
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


def _summary_path(stamp_kind: str) -> Path:
    from . import tools

    return tools._new_run_dir(stamp_kind) / "summary.json"


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
    timeout_s: int = 300,
) -> dict[str, Any]:
    """Run yosys ``synth_xilinx`` on a project and report LUT/FF/BRAM/DSP/IO counts."""
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
    json_out = _project_paths(proj)["build"] / f"{const.stem}.json"
    summary_file = _summary_path("fpga")

    info = _common(proj, part)
    info.update(
        {
            "top": resolved_top,
            "sources": [item["path"] for item in exported["files"]],
            "const": str(const),
            "json_path": str(json_out),
            "warnings": exported["warnings"],
        }
    )
    script = _yosys_script(chosen, json_out)
    stage = _run_stage(
        [SETTINGS.yosys, "-p", script],
        cwd=_project_paths(proj)["build"],
        timeout_s=int(timeout_s),
        stage="synth",
        outputs={"json": json_out},
    )
    stat = _stat_pass(json_out, timeout_s)
    utilization = _utilization(stat["counts"], _io_bits(chosen, resolved_top))
    info.update(
        {
            "ok": True,
            "yosys_script": script,
            "seconds": stage["duration_s"],
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
    info["summary_path"] = _write_summary(summary_file, info)
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
