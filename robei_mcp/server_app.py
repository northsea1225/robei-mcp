from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from . import __version__
from . import gui, tools

mcp = MCPServer(
    name="robei-mcp",
    version=__version__,
    instructions=(
        "Local Robei EDA helper. It wraps bundled Icarus Verilog, vvp, Yosys and "
        "(optionally) Verilator, parses VCD files, reads Robei .model/.proj XML "
        "without writing it back, and exports Verilog into the configured "
        "workspace. All source/output paths are restricted to the workspace. "
        "The robei_gui_* tools drive the Robei IDE itself through its built-in "
        "UIA accessibility bridge (open project, open model, compile, read log)."
    ),
)


def _error(exc: Exception) -> dict[str, Any]:
    return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


@mcp.tool()
def runtime_status() -> dict[str, Any]:
    """Check Robei, iverilog, vvp, yosys and verilator paths and versions."""
    try:
        return tools.runtime_status()
    except Exception as exc:
        return _error(exc)


@mcp.tool()
def lint_verilog(files: list[str], top: str | None = None, timeout_s: int = 30) -> dict[str, Any]:
    """Lint with iverilog -Wall, Verilator --lint-only -Wall and Yosys (synth + unused-wire pass)."""
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
def find_events(
    vcd_path: str,
    signal: str,
    condition: str,
    limit: int = 10,
) -> dict[str, Any]:
    """Find times where a signal enters a condition, e.g. signal='count' condition=\"== 4'b1111\"."""
    try:
        return tools.find_events(vcd_path=vcd_path, signal=signal, condition=condition, limit=limit)
    except Exception as exc:
        return _error(exc)


@mcp.tool()
def render_waveform(
    vcd_path: str,
    signals: list[str],
    out_name: str | None = None,
    title: str | None = None,
) -> dict[str, Any]:
    """Render selected VCD signals to a PNG under workspace/build/waveforms."""
    try:
        return tools.render_waveform(vcd_path=vcd_path, signals=signals, out_name=out_name, title=title)
    except Exception as exc:
        return _error(exc)


@mcp.tool()
def read_robei_model(path: str) -> dict[str, Any]:
    """Read a Robei .model XML file and return metadata, ports, parameters and Verilog code."""
    try:
        return tools.read_robei_model(path=path)
    except Exception as exc:
        return _error(exc)


@mcp.tool()
def read_robei_project(proj_path: str) -> dict[str, Any]:
    """Read a Robei .proj project (read-only): folders, files and every model."""
    try:
        return tools.read_robei_project(proj_path=proj_path)
    except Exception as exc:
        return _error(exc)


@mcp.tool()
def set_robei_port(
    model_path: str,
    name: str,
    direction: str = "input",
    width: int = 1,
    datatype: str = "wire",
    side: str | None = None,
) -> dict[str, Any]:
    """Add or update one port in a .model (workspace only, backup + XML validation)."""
    try:
        return tools.set_port(
            model_path=model_path,
            name=name,
            direction=direction,
            width=width,
            datatype=datatype,
            side=side,
        )
    except Exception as exc:
        return _error(exc)


@mcp.tool()
def export_robei_verilog(
    model_path: str,
    out_name: str | None = None,
    parameter_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a full module from a .model Code+Ports, write it to workspace/build/export, then lint it."""
    try:
        return tools.export_robei_verilog(
            model_path=model_path,
            out_name=out_name,
            parameter_overrides=parameter_overrides,
        )
    except Exception as exc:
        return _error(exc)


@mcp.tool()
def generate_testbench(
    verilog_path: str,
    top: str,
    out_path: str | None = None,
    clock_period_ns: int = 10,
) -> dict[str, Any]:
    """Generate a clock/reset/VCD testbench skeleton for a module without overwriting files."""
    try:
        return tools.generate_testbench(
            verilog_path=verilog_path,
            top=top,
            out_path=out_path,
            clock_period_ns=clock_period_ns,
        )
    except Exception as exc:
        return _error(exc)


@mcp.tool()
def robei_gui_status(output_lines: int = 20) -> dict[str, Any]:
    """Report whether Robei is running, the loaded project, open models and the Output tail."""
    try:
        return gui.status(output_lines=output_lines)
    except Exception as exc:
        return _error(exc)


@mcp.tool()
def robei_gui_launch(wait_s: int = 30) -> dict[str, Any]:
    """Start the Robei IDE on the interactive desktop and wait for its main window."""
    try:
        return gui.launch(wait_s=wait_s)
    except Exception as exc:
        return _error(exc)


@mcp.tool()
def robei_gui_open_project(proj_path: str, timeout_s: int = 90) -> dict[str, Any]:
    """Open a .proj through File > Open and verify the Files tree shows that project."""
    try:
        return gui.open_project(proj_path=proj_path, timeout_s=timeout_s)
    except Exception as exc:
        return _error(exc)


@mcp.tool()
def robei_gui_open_model(name: str, timeout_s: int = 90) -> dict[str, Any]:
    """Double-click a model in the Robei Files tree so its canvas becomes the current one."""
    try:
        return gui.open_model(name=name, timeout_s=timeout_s)
    except Exception as exc:
        return _error(exc)


@mcp.tool()
def robei_gui_compile(timeout_s: int = 90) -> dict[str, Any]:
    """Run Build > Compile on the open model and return the new Output lines."""
    try:
        return gui.compile_model(timeout_s=timeout_s)
    except Exception as exc:
        return _error(exc)


@mcp.tool()
def robei_gui_output(lines: int = 80) -> dict[str, Any]:
    """Read the last N lines of the Robei Output dock."""
    try:
        return gui.output(lines=lines)
    except Exception as exc:
        return _error(exc)


@mcp.tool()
def robei_gui_code_read() -> dict[str, Any]:
    """Read the Verilog snippet in the active model's Code tab (the model's Code field)."""
    try:
        return gui.code_read()
    except Exception as exc:
        return _error(exc)


@mcp.tool()
def robei_gui_code_write(code: str, save: bool = True) -> dict[str, Any]:
    """Replace the active model's Code tab text and save it with Ctrl+S."""
    try:
        return gui.code_write(code=code, save=save)
    except Exception as exc:
        return _error(exc)


@mcp.tool()
def robei_gui_close() -> dict[str, Any]:
    """Force-close the Robei IDE. Unsaved model edits are lost."""
    try:
        return gui.close()
    except Exception as exc:
        return _error(exc)
