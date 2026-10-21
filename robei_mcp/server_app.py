from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from . import __version__
from . import tools

mcp = MCPServer(
    name="robei-mcp",
    version=__version__,
    instructions=(
        "Local Robei EDA helper. It wraps bundled Icarus Verilog, vvp and Yosys, "
        "parses VCD files, and reads Robei .model XML without writing it back. "
        "All source/output paths are restricted to the configured workspace."
    ),
)


def _error(exc: Exception) -> dict[str, Any]:
    return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


@mcp.tool()
def runtime_status() -> dict[str, Any]:
    """Check Robei, iverilog, vvp and yosys paths and versions."""
    try:
        return tools.runtime_status()
    except Exception as exc:
        return _error(exc)


@mcp.tool()
def lint_verilog(files: list[str], top: str | None = None, timeout_s: int = 30) -> dict[str, Any]:
    """Lint Verilog with iverilog -Wall and run a separate Yosys synthesis check."""
    try:
        return tools.lint_verilog(files=files, top=top, timeout_s=timeout_s)
    except Exception as exc:
        return _error(exc)


@mcp.tool()
def simulate(files: list[str], top: str, timeout_s: int = 30) -> dict[str, Any]:
    """Compile and run a Verilog testbench with iverilog/vvp; return output and VCD paths."""
    try:
        return tools.simulate(files=files, top=top, timeout_s=timeout_s)
    except Exception as exc:
        return _error(exc)


@mcp.tool()
def read_waveform(
    vcd_path: str,
    signals: list[str] | None = None,
    time: int | None = None,
) -> dict[str, Any]:
    """List VCD signals or read selected signal values at a raw VCD time tick."""
    try:
        return tools.read_waveform(vcd_path=vcd_path, signals=signals, time=time)
    except Exception as exc:
        return _error(exc)


@mcp.tool()
def read_robei_model(path: str) -> dict[str, Any]:
    """Read a Robei .model XML file and return metadata, ports, parameters and Verilog code."""
    try:
        return tools.read_robei_model(path=path)
    except Exception as exc:
        return _error(exc)
