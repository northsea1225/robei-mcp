"""Environment self-check behind the ``robei_doctor`` MCP tool.

The server can fail for reasons that have nothing to do with Verilog: a missing
venv, a moved Robei install, a stale scheduled task, a read-only workspace. Each
check therefore reports its own verdict plus a concrete remediation hint.
"""
from __future__ import annotations

import importlib.metadata as metadata
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import tools
from .config import PROJECT_ROOT, SETTINGS

ENV_KEYS = ("ROBEI_HOME", "ROBEI_EXE", "ROBEI_MCP_WORKSPACE", "ROBEI_MODEL_ROOTS")


def _pkg_version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except Exception:
        return None


def _check(name: str, ok: bool, detail: Any, hint: str | None = None, required: bool = True) -> dict[str, Any]:
    return {
        "name": name,
        "ok": bool(ok),
        "required": required,
        "detail": detail,
        "hint": None if ok else hint,
    }


def _writable(path: Path) -> dict[str, Any]:
    if not path.is_dir():
        return {"exists": False, "writable": False}
    probe = path / ".doctor-write-test"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return {"exists": True, "writable": True}
    except Exception as exc:
        return {"exists": True, "writable": False, "error": f"{type(exc).__name__}: {exc}"}


def _deep_sim() -> dict[str, Any]:
    """Compile and run a two-line testbench to prove iverilog+vvp really work."""
    scratch = SETTINGS.workspace / "build" / "doctor"
    scratch.mkdir(parents=True, exist_ok=True)
    src = scratch / "doctor_tb.v"
    exe = scratch / "doctor_tb.out"
    src.write_text(
        "module doctor_tb;\n"
        "  initial begin\n"
        "    $display(\"DOCTOR_OK\");\n"
        "    $finish;\n"
        "  end\n"
        "endmodule\n",
        encoding="utf-8",
        newline="\n",
    )
    build = subprocess.run(
        [str(SETTINGS.iverilog), "-o", str(exe), str(src)], capture_output=True, text=True, timeout=30
    )
    if build.returncode != 0:
        return {"ok": False, "stage": "iverilog", "output": (build.stdout + build.stderr).strip()[-400:]}
    run = subprocess.run([str(SETTINGS.vvp), str(exe)], capture_output=True, text=True, timeout=30)
    text = (run.stdout + run.stderr).strip()
    return {"ok": "DOCTOR_OK" in text and run.returncode == 0, "stage": "vvp", "output": text[-400:]}


def run(deep: bool = False) -> dict[str, Any]:
    from . import gui  # imported lazily: doctor must work even if the bridge cannot start

    checks: list[dict[str, Any]] = []

    version = sys.version_info
    checks.append(
        _check(
            "python",
            version >= (3, 10),
            f"{version.major}.{version.minor}.{version.micro}",
            "install Python 3.10+ and recreate .venv",
        )
    )

    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    checks.append(
        _check(
            "venv",
            in_venv,
            sys.prefix,
            "create it with: python -m venv C:\\dev\\robei-mcp\\.venv",
            required=False,
        )
    )

    mcp_version = _pkg_version("mcp")
    checks.append(
        _check(
            "mcp_sdk",
            bool(mcp_version),
            mcp_version or "not installed",
            "install the pinned SDK: .venv\\Scripts\\python.exe -m pip install -r requirements.txt",
        )
    )
    matplotlib_version = _pkg_version("matplotlib")
    checks.append(
        _check(
            "matplotlib",
            bool(matplotlib_version),
            matplotlib_version or "not installed",
            "render_waveform needs matplotlib: .venv\\Scripts\\python.exe -m pip install matplotlib",
            required=False,
        )
    )

    workspace = _writable(SETTINGS.workspace)
    checks.append(
        _check(
            "workspace",
            workspace["exists"] and workspace["writable"],
            {"path": str(SETTINGS.workspace), **workspace},
            "point ROBEI_MCP_WORKSPACE at a writable directory",
        )
    )

    for label, path in (("robei_exe", SETTINGS.robei_exe), ("robei_gui_exe", SETTINGS.robei_gui_exe)):
        checks.append(
            _check(
                label,
                path.is_file(),
                str(path),
                "set ROBEI_HOME (e.g. E:\\Robei) or ROBEI_EXE to the real install",
                required=label == "robei_exe",
            )
        )

    status = tools.runtime_status()
    for name in ("iverilog", "vvp", "yosys"):
        entry = status["tools"].get(name) or {}
        checks.append(
            _check(
                name,
                bool(entry.get("exists")),
                {"path": entry.get("path"), "version": entry.get("version")},
                "Robei ships these under <ROBEI_HOME>\\bin; set ROBEI_HOME, or point IVERILOG_EXE/VVP_EXE/YOSYS_EXE",
            )
        )
    verilator = status["tools"].get("verilator") or {}
    checks.append(
        _check(
            "verilator",
            bool(status.get("verilator_available")),
            {"path": verilator.get("path"), "version": verilator.get("version")},
            "optional; lint falls back to iverilog -Wall + yosys",
            required=False,
        )
    )

    try:
        task = gui.task_status()
    except Exception as exc:
        task = {"exists": False, "error": f"{type(exc).__name__}: {exc}"}
    if os.name == "nt":
        try:
            import importlib.util

            missing_gui = [m for m in ("pywinauto", "PIL", "win32gui") if importlib.util.find_spec(m) is None]
        except Exception as exc:
            missing_gui = [f"import check failed: {type(exc).__name__}: {exc}"]
    else:
        missing_gui = []
    checks.append(
        _check(
            "gui_deps",
            not missing_gui,
            missing_gui or ("present" if os.name == "nt" else "not Windows; GUI is optional"),
            "install the GUI extra: .venv\\Scripts\\python.exe -m pip install -e .[gui]",
            required=False,
        )
    )
    checks.append(
        _check(
            "gui_task",
            bool(task.get("exists")),
            task,
            "register the Session-1 helper: python tools\\gui\\register_task.py",
            required=False,
        )
    )

    overrides = {key: os.environ[key] for key in ENV_KEYS if os.environ.get(key)}
    checks.append(_check("config_overrides", True, overrides or "defaults", None, required=False))

    if deep:
        try:
            deep_result = _deep_sim()
        except Exception as exc:
            deep_result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        checks.append(
            _check(
                "deep_simulate",
                bool(deep_result.get("ok")),
                deep_result,
                "iverilog/vvp compile or run failed; check the paths above",
            )
        )

    required_failures = [c["name"] for c in checks if c["required"] and not c["ok"]]
    warnings = [c["name"] for c in checks if not c["required"] and not c["ok"]]
    return {
        "ok": not required_failures,
        "failures": required_failures,
        "warnings": warnings,
        "checks": checks,
        "paths": {
            "project_root": str(PROJECT_ROOT),
            "workspace": str(SETTINGS.workspace),
            "robei_home": str(SETTINGS.robei_home),
        },
    }
