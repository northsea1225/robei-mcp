from __future__ import annotations

import asyncio
import functools
import inspect
import json
from pathlib import Path
from typing import Any, Literal

import anyio
from mcp.server.mcpserver import Context, MCPServer
from mcp.types import CallToolResult, ImageContent, TextContent, ToolAnnotations

from . import __version__
from . import constraints, doctor, errors, fpga, gui, lifecycle, p1, runstate, tools


class LifecycleMCPServer(MCPServer):
    async def run_stdio_async(self) -> None:
        await lifecycle.run_stdio(self)


mcp = LifecycleMCPServer(
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


def _stale_wrapped(fn):
    """Same callable as ``fn``, with the stale-source note applied to the result.

    An ``async def`` tool must stay a coroutine function: the MCP server awaits
    coroutine tools, and wrapping one in a plain function made every async tool
    fail with "Error executing tool" (stage2/11 caught it).
    """
    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def async_wrapper(*args: Any, **any_kwargs: Any):
            return tools._with_stale_note(await fn(*args, **any_kwargs))

        return async_wrapper

    @functools.wraps(fn)
    def wrapper(*args: Any, **any_kwargs: Any):
        return tools._with_stale_note(fn(*args, **any_kwargs))

    return wrapper


def tool(**kwargs: Any):
    """``@mcp.tool`` plus the stale-source note, applied to every result.

    ``functools.wraps`` keeps ``__wrapped__``/``__annotations__``, so the JSON
    schema the client sees is byte-identical to a plain ``@mcp.tool``.
    """

    def decorate(fn):
        return mcp.tool(**kwargs)(_stale_wrapped(fn))

    return decorate


# Tool annotations: MCP clients use these to decide what is safe to call without
# a human in the loop and what can be retried.
READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
READ_GUI = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)
ANALYZE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
WRITE_GUI = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False)
DESTRUCTIVE_GUI = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True)


def _image_or_payload(
    payload: dict[str, Any],
    inline: dict[str, Any] | None,
    holder: dict[str, Any] | None = None,
) -> Any:
    """``[text, image]`` when a PNG was inlined, otherwise the plain payload.

    ``inline=true`` is opt-in: the image rides along as an ``ImageContent`` while
    the text block keeps the same JSON (minus the base64) plus an ``inline``
    report.  Anything that cannot be inlined keeps the payload and says why.
    """
    if not inline:
        return payload
    target = payload if holder is None else holder
    data = inline.get("data_b64")
    target["inline"] = {k: v for k, v in inline.items() if k != "data_b64"}
    content: list[Any] = [
        TextContent(
            type="text",
            text=json.dumps(payload, ensure_ascii=False, default=str),
        )
    ]
    if inline.get("ok") and data:
        content.append(
            ImageContent(
                type="image",
                mimeType=inline.get("mime_type") or "image/png",
                data=data,
            )
        )
    return CallToolResult(content=content, structured_content=payload)


def _error(exc: Exception) -> dict[str, Any]:
    """Failures carry a stable ``code`` so clients can branch without parsing prose."""
    return errors.payload(exc)


async def _quiet_progress(ctx: Context | None, done: float, total: float, message: str) -> None:
    if ctx is None:
        return
    try:
        await ctx.report_progress(done, total, message)
    except Exception:
        pass  # clients without progress support must not break the tool


async def _invoke(fn, ctx: Context | None, label: str, **kwargs):
    """Run a blocking tool body on a worker thread that the client can cancel.

    On cancellation the run token is aborted, which kills every child process the
    body registered (iverilog, vvp, yosys, the GUI bridge), so a 30 s simulation
    stops in ~100 ms instead of pinning the toolchain.
    """
    token = runstate.RunToken()
    loop = asyncio.get_running_loop()
    state = {"done": 0.0, "total": 1.0}

    def report(done: float, total: float, message: str) -> None:
        if ctx is None:
            return
        try:
            # Tools use different totals (simulate: 2, lint: 4, ...).  Keep one
            # non-decreasing total and never move the counter backwards.
            total = float(total or 1.0)
            if total <= 0:
                total = 1.0
            state["total"] = max(state["total"], total)
            done = min(max(float(done), state["done"]), state["total"])
            state["done"] = done
            asyncio.run_coroutine_threadsafe(
                ctx.report_progress(done, state["total"], f"{label}: {message}"), loop
            )
        except Exception:
            pass

    if "progress" in inspect.signature(fn).parameters:
        kwargs["progress"] = report
    await _quiet_progress(ctx, 0, 1, f"{label}: started")
    try:
        result = await anyio.to_thread.run_sync(
            functools.partial(runstate.call, fn, token, **kwargs), abandon_on_cancel=True
        )
    except anyio.get_cancelled_exc_class():
        token.abort()
        raise
    await _quiet_progress(ctx, state["total"], state["total"], f"{label}: done")
    return result


@tool(annotations=READ)
def runtime_status() -> dict[str, Any]:
    """Check Robei, iverilog, vvp, yosys and verilator paths and versions."""
    try:
        return tools.runtime_status()
    except Exception as exc:
        return _error(exc)


@tool(annotations=WRITE)
async def lint_verilog(files: list[str], top: str | None = None, timeout_s: int = 30, ctx: Context | None = None) -> dict[str, Any]:
    """Lint with iverilog -Wall, Verilator --lint-only -Wall and Yosys (synth + unused-wire pass)."""
    try:
        return await _invoke(tools.lint_verilog, ctx, "lint_verilog", files=files, top=top, timeout_s=timeout_s)
    except Exception as exc:
        return _error(exc)


@tool(annotations=WRITE)
async def simulate(files: list[str], top: str, timeout_s: int = 30, ctx: Context | None = None) -> dict[str, Any]:
    """Compile and run a Verilog testbench with iverilog/vvp; return output and VCD paths."""
    try:
        return await _invoke(tools.simulate, ctx, "simulate", files=files, top=top, timeout_s=timeout_s)
    except Exception as exc:
        return _error(exc)


@tool(annotations=WRITE)
async def simulate_project(
    proj_path: str,
    test_name: str,
    timeout_s: int = 30,
    render: bool = False,
    inline: bool = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Run a Robei test/*.test model headlessly with the bundled iverilog (no IDE, no desktop).

    Rebuilds build/headless/<project>-<test>/<test>.v from the .test Code plus every
    model exported to Verilog, then compiles and runs it. Set render=True to also
    get a PNG of the produced VCD.
    """
    try:
        payload = await _invoke(
            tools.simulate_project,
            ctx,
            "simulate_project",
            proj_path=proj_path,
            test_name=test_name,
            timeout_s=timeout_s,
            render=render,
            inline=inline,
        )
    except Exception as exc:
        return _error(exc)
    plot = payload.get("plot") or {}
    return _image_or_payload(payload, plot.get("inline"), holder=plot)


@tool(annotations=READ)
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


@tool(annotations=READ)
def compare_waveforms(
    golden_vcd: str,
    dut_vcd: str,
    signals: list[str] | None = None,
    allow_extra_signals: bool = False,
) -> dict[str, Any]:
    """Diff two VCDs signal by signal; returns the first divergence per signal.

    Timescales and run lengths are compared after conversion to femtoseconds.
    ``allow_extra_signals=True`` compares only the shared signals (golden vs an
    alternative hierarchy) instead of failing on one-sided names.
    """
    try:
        return tools.compare_waveforms(
            golden_vcd=golden_vcd,
            dut_vcd=dut_vcd,
            signals=signals,
            allow_extra_signals=allow_extra_signals,
        )
    except Exception as exc:
        return _error(exc)


@tool(annotations=READ)
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


@tool(annotations=WRITE)
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
    """Export a bounded VCD window as CSV or Markdown using raw VCD ticks."""
    try:
        return tools.export_vcd_window(
            vcd=vcd, signals=signals, t_start=t_start, t_end=t_end,
            format=format, step=step, out=out, overwrite=overwrite,
            include_initial=include_initial,
        )
    except Exception as exc:
        return _error(exc)


@tool(annotations=WRITE)
def render_waveform(
    vcd_path: str,
    signals: list[str],
    out_name: str | None = None,
    title: str | None = None,
    inline: bool = False,
) -> dict[str, Any]:
    """Render selected VCD signals to a PNG (project build/ or workspace/build/waveforms).

    ``inline=true`` also returns the PNG as an ``ImageContent`` block (capped at
    5 MiB; over the cap the payload carries ``inline.code=image_too_large`` and the
    path is still returned).
    """
    try:
        payload = tools.render_waveform(
            vcd_path=vcd_path,
            signals=signals,
            out_name=out_name,
            title=title,
            inline=inline,
        )
    except Exception as exc:
        return _error(exc)
    return _image_or_payload(payload, payload.get("inline"))


@tool(annotations=READ)
def read_run_log(path: str, tail: int = 200, max_bytes: int = 500_000) -> dict[str, Any]:
    """Read a run log under workspace/logs or workspace/build (size-capped, tail lines).

    Every fpga_step_failed / internal payload names a file this tool can open.
    """
    try:
        return tools.read_run_log(path=path, tail=tail, max_bytes=max_bytes)
    except Exception as exc:
        return _error(exc)


@tool(annotations=READ)
def read_robei_model(path: str) -> dict[str, Any]:
    """Read a Robei .model XML file and return metadata, ports, parameters and Verilog code."""
    try:
        return tools.read_robei_model(path=path)
    except Exception as exc:
        return _error(exc)


@tool(annotations=ANALYZE)
def robei_doctor(deep: bool = False) -> dict[str, Any]:
    """Self-check the whole stack: python/venv, mcp+matplotlib, workspace, Robei, iverilog/vvp/yosys, GUI task.

    Set deep=True to also compile and run a two-line testbench with the configured iverilog/vvp.
    """
    try:
        return doctor.run(deep=deep)
    except Exception as exc:
        return _error(exc)


@tool(annotations=READ)
def list_projects(limit: int = 200, include_qa: bool = False) -> dict[str, Any]:
    """List every Robei project in the workspace with its models, tests, verilog and VCDs.

    QA scratch trees (workspace/_qa_runs, workspace/qa_*) are skipped unless
    include_qa=true.
    """
    try:
        return tools.list_projects(limit=limit, include_qa=include_qa)
    except Exception as exc:
        return _error(exc)


@tool(annotations=READ)
def read_robei_project(proj_path: str) -> dict[str, Any]:
    """Read a Robei .proj project (read-only): folders, files and every model."""
    try:
        return tools.read_robei_project(proj_path=proj_path)
    except Exception as exc:
        return _error(exc)


@tool(annotations=READ)
def list_library_models(
    filter: str | None = None,
    include_lint: bool = True,
    limit: int = 200,
) -> dict[str, Any]:
    """Read-only inventory of the installed Robei ToolBox models (name, module name, ports,
    parameters, and a real iverilog -g2012 lint per model)."""
    try:
        return tools.list_library_models(filter=filter, include_lint=include_lint, limit=limit)
    except Exception as exc:
        return _error(exc)


@tool(annotations=WRITE)
def import_library_model(
    name: str,
    project: str | None = None,
    as_name: str | None = None,
    overwrite: bool = False,
    dry_run: bool = False,
    proj_path: str | None = None,
) -> dict[str, Any]:
    """Copy a ToolBox model into <project>/model, register it in the .proj (backed up first),
    then lint it with iverilog. Pass as_name when the file name and module name differ.

    ``project`` is the project path (``work/a.proj``); ``proj_path`` is accepted as an
    alias for callers that guess the other name.
    """
    try:
        return tools.import_library_model(
            name=name,
            project=project,
            as_name=as_name,
            overwrite=overwrite,
            dry_run=dry_run,
            proj_path=proj_path,
        )
    except Exception as exc:
        return _error(exc)


@tool(annotations=WRITE)
def create_robei_project(
    name: str,
    models: list[str] | None = None,
    skeleton: bool = True,
) -> dict[str, Any]:
    """Create a Robei project skeleton (folders, .proj, blank models) under the workspace."""
    try:
        return tools.create_robei_project(name=name, models=models, skeleton=skeleton)
    except Exception as exc:
        return _error(exc)


@tool(annotations=WRITE)
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

    ``parameters`` uses the instance-override object form (``{"LEN": 4}``) and is
    written to the root ``<Module Parameters>``; a port width may then reference it
    (``width: "LEN"`` becomes ``Datasize="LEN-1:0"``).
    """
    try:
        return tools.create_robei_model(
            proj_path=proj_path,
            name=name,
            ports=ports,
            code=code,
            comment=comment,
            overwrite=overwrite,
            parameters=parameters,
        )
    except Exception as exc:
        return _error(exc)


@tool(annotations=DESTRUCTIVE)
def set_robei_port(
    model_path: str,
    name: str,
    direction: str = "input",
    width: int | str = 1,
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


@tool(annotations=WRITE)
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


@tool(annotations=DESTRUCTIVE)
def sync_robei_verilog(proj_path: str, overwrite: bool = True) -> dict[str, Any]:
    """Export every model of a project to <proj>/verilog/<model>.v (Robei's iverilog -y library dir)."""
    try:
        return tools.sync_robei_verilog(proj_path=proj_path, overwrite=overwrite)
    except Exception as exc:
        return _error(exc)


@tool(annotations=WRITE)
def create_robei_test(
    proj_path: str,
    name: str,
    code: str,
    timescale: str = "1ns/1ns",
    overwrite: bool = False,
    sync_deps: bool = True,
) -> dict[str, Any]:
    """Create a Robei test model (test/<name>.test, Type="test") and register it in the .proj.

    ``code`` is the testbench body only; Robei wraps it into ``module <name>();`` and adds
    the $dumpfile/$dumpvars block when it generates build/<name>.v.
    """
    try:
        return tools.create_robei_test(
            proj_path=proj_path,
            name=name,
            code=code,
            timescale=timescale,
            overwrite=overwrite,
            sync_deps=sync_deps,
        )
    except Exception as exc:
        return _error(exc)


@tool(annotations=WRITE)
def create_robei_canvas_model(
    proj_path: str,
    name: str,
    ports: list[dict[str, Any]],
    instances: list[dict[str, Any]],
    wires: list[dict[str, Any]],
    comment: str = "",
    overwrite: bool = False,
) -> dict[str, Any]:
    """Write a canvas-level model: placed instances (nested <Module>) + <Wire> nets.

    ``instances`` = [{"name": "u1", "model": "and2", "x": 200, "y": 120}]
    ``wires``     = [{"from": "u1.y", "to": "u2.a"}, {"from": "a", "to": "u1.a"}]
    Robei compiles this into Verilog instantiation, so a hierarchical design can be
    generated without drawing it.
    """
    try:
        return tools.create_robei_canvas_model(
            proj_path=proj_path,
            name=name,
            ports=ports,
            instances=instances,
            wires=wires,
            comment=comment,
            overwrite=overwrite,
        )
    except Exception as exc:
        return _error(exc)


@tool(annotations=WRITE)
def edit_robei_canvas(
    model_path: str,
    ops: list[dict[str, Any]],
    dry_run: bool = False,
) -> dict[str, Any]:
    """Edit a canvas model atomically (including move_instance and rename_port)."""
    try:
        return tools.edit_robei_canvas(model_path=model_path, ops=ops, dry_run=dry_run)
    except Exception as exc:
        return _error(exc)


@tool(annotations=WRITE)
def manage_robei_model(
    proj_path: str,
    model_path: str,
    op: Literal["rename", "delete", "duplicate"],
    new_name: str | None = None,
    overwrite: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Rename, delete or duplicate a project model with reference checks and rollback."""
    try:
        return tools.manage_robei_model(
            proj_path=proj_path,
            model_path=model_path,
            op=op,
            new_name=new_name,
            overwrite=overwrite,
            dry_run=dry_run,
        )
    except Exception as exc:
        return _error(exc)


@tool(annotations=WRITE)
def set_robei_code(model_path: str, code: str, dry_run: bool = False) -> dict[str, Any]:
    """Replace the Code body of a behavioural model (models with instances/wires are refused)."""
    try:
        return tools.set_robei_code(model_path=model_path, code=code, dry_run=dry_run)
    except Exception as exc:
        return _error(exc)


@tool(annotations=WRITE)
def set_robei_parameter(
    model_path: str,
    name: str,
    value: str | int | None = None,
    dry_run: bool = False,
    action: Literal["set", "add", "rename", "delete"] = "set",
    new_name: str | None = None,
    rename_in_parents: bool = False,
) -> dict[str, Any]:
    """Change one root parameter default and re-resolve the port widths it feeds."""
    try:
        return tools.set_robei_parameter(
            model_path=model_path, name=name, value=value, dry_run=dry_run, action=action,
            new_name=new_name, rename_in_parents=rename_in_parents
        )
    except Exception as exc:
        return _error(exc)


@tool(annotations=WRITE)
def generate_testbench(
    verilog_path: str,
    top: str,
    out_path: str | None = None,
    clock_period_ns: int = 10,
    full_combo: bool = False,
    max_combo_bits: int = 8,
) -> dict[str, Any]:
    """Generate a clock/reset/VCD testbench skeleton; full_combo enumerates every input combination."""
    try:
        return tools.generate_testbench(
            verilog_path=verilog_path,
            top=top,
            out_path=out_path,
            clock_period_ns=clock_period_ns,
            full_combo=full_combo,
            max_combo_bits=max_combo_bits,
        )
    except Exception as exc:
        return _error(exc)


@tool(annotations=WRITE)
def import_verilog_canvas(
    proj_path: str,
    verilog_path: str,
    name: str | None = None,
    overwrite: bool = False,
    mode: Literal["auto", "structural", "behavioral"] = "auto",
) -> dict[str, Any]:
    """Classify Verilog; import structural sources or return a behavioral suggestion."""
    try:
        return p1.import_verilog_canvas(
            proj_path=proj_path,
            verilog_path=verilog_path,
            name=name,
            overwrite=overwrite,
            mode=mode,
        )
    except Exception as exc:
        return _error(exc)


@tool(annotations=READ)
def check_connectivity(
    files: list[str] | None = None,
    top: str | None = None,
    proj_path: str | None = None,
) -> dict[str, Any]:
    """Static ERC: multi-driver, undriven net, width mismatch, unknown module, unconnected port."""
    try:
        return p1.check_connectivity(files=files, top=top, proj_path=proj_path)
    except Exception as exc:
        return _error(exc)


@tool(annotations=WRITE)
def truth_table(
    verilog_path: str,
    top: str,
    max_inputs: int = 8,
    timeout_s: int = 30,
    deps: list[str] | None = None,
) -> dict[str, Any]:
    """Exhaustively simulate a combinational module and return its truth table rows."""
    try:
        return p1.truth_table(
            verilog_path=verilog_path, top=top, max_inputs=max_inputs, timeout_s=timeout_s, deps=deps
        )
    except Exception as exc:
        return _error(exc)


@tool(annotations=WRITE)
def create_robei_const(
    proj_path: str,
    name: str,
    model: str,
    pins: dict[str, list[str]],
    overwrite: bool = False,
    kind: str = "const",
    vendor: str = "AMD",
    device: str = "",
    family: str = "",
    iostandard: str = "LVCMOS33",
) -> dict[str, Any]:
    """Write a pin-constraint canvas: pins={"swt":["G15","P15","W13","T16"], "led":["M14",...]}.

    vendor/device/family are only written for kind="const"; Robei reports
    "has no Device configured" until they match the board you synthesise for.
    """
    try:
        return constraints.create_const(
            proj_path=proj_path, name=name, model=model, pins=pins, overwrite=overwrite,
            kind=kind, vendor=vendor, device=device, family=family, iostandard=iostandard,
        )
    except Exception as exc:
        return _error(exc)


@tool(annotations=READ)
def read_robei_const(path: str) -> dict[str, Any]:
    """Read a .const: kind, instance model, and the port -> bit -> FPGA pin map."""
    try:
        return constraints.read_const(path=path)
    except Exception as exc:
        return _error(exc)


@tool(annotations=WRITE)
def export_robei_xdc(
    proj_path: str,
    const_name: str | None = None,
    out_name: str | None = None,
    iostandard: str = "LVCMOS33",
    also_build: bool = False,
    quote_bus: bool = True,
) -> dict[str, Any]:
    """Project a .const into <proj>/constrain/<name>.xdc (PACKAGE_PIN + IOSTANDARD lines).

    also_build=True also writes <proj>/build/<name>.xdc, the copy Build > Route reads.
    quote_bus=True emits standard Tcl ([get_ports {swt[0]}]); set it False only
    for a Robei build that requires its legacy unquoted spelling.
    """
    try:
        return constraints.export_xdc(
            proj_path=proj_path, const_name=const_name, out_name=out_name,
            iostandard=iostandard, also_build=also_build, quote_bus=quote_bus,
        )
    except Exception as exc:
        return _error(exc)


@tool(annotations=READ)
def expect_waveform(vcd_path: str, checks: list[dict[str, Any]]) -> dict[str, Any]:
    """Assert VCD values: [{"signal":"y","value":"1","at":45}] or {"from":30,"to":100} (held)."""
    try:
        return p1.expect_waveform(vcd_path=vcd_path, checks=checks)
    except Exception as exc:
        return _error(exc)


@tool(annotations=READ)
def waveform_stats(vcd_path: str, max_signals: int = 64) -> dict[str, Any]:
    """Toggle counts, level coverage, X/Z flags and a clock period/frequency estimate."""
    try:
        return p1.waveform_stats(vcd_path=vcd_path, max_signals=max_signals)
    except Exception as exc:
        return _error(exc)


@tool(annotations=READ)
def list_fpga_devices(part: str | None = None) -> dict[str, Any]:
    """List FPGA parts with their chipdb/part.yaml pair and whether a .bit can be built."""
    try:
        return fpga.list_fpga_devices(part=part)
    except Exception as exc:
        return _error(exc)


@tool(annotations=ANALYZE)
async def synthesize_project(
    proj_path: str,
    top: str | None = None,
    part: str | None = None,
    timeout_s: int = 120,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Run yosys synth_xilinx on a Robei project (same -p script as Build > Synthesis).

    Writes build/<const>.json next to the project and returns LUT/FF/BRAM/DSP/IO counts
    from a follow-up ``stat`` pass. ``part`` is optional and only recorded and validated.
    ``timeout_s`` defaults to 120 s, which is also ROBEI_MAX_TIMEOUT_S (no clamping).
    """
    try:
        return await _invoke(
            fpga.synthesize_project, ctx, "synthesize_project",
            proj_path=proj_path, top=top, part=part, timeout_s=timeout_s,
        )
    except Exception as exc:
        return _error(exc)


@tool(annotations=WRITE)
async def build_bitstream(
    proj_path: str,
    part: str,
    const_name: str | None = None,
    steps: list[Literal["synth", "route", "package"]] | None = None,
    timeout_s: int = 120,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Roplace -> fasm2frames -> xc7frames2bit, the three steps behind Build > Route/Package.

    Each stage reports rc, seconds, log tail and its artifact; the first failure stops the
    run with code=fpga_step_failed and that stage name. Parts without a chipdb return
    device_unsupported; port bits without a package pin return unconstrained_io with the
    missing list. Roplace's Fmax is a reference only - this is not a timing sign-off.
    ``timeout_s`` defaults to 120 s (== ROBEI_MAX_TIMEOUT_S); the payload also carries
    bit_payload_sha256, the bitstream hash from the 0xAA995566 sync word on.
    """
    try:
        return await _invoke(
            fpga.build_bitstream, ctx, "build_bitstream",
            proj_path=proj_path, part=part, const_name=const_name,
            steps=steps, timeout_s=timeout_s,
        )
    except Exception as exc:
        return _error(exc)


@tool(annotations=WRITE)
async def build_report(
    proj_path: str,
    tests: list[str] | None = None,
    out: str | None = None,
    include: list[str] | None = None,
    truth_map: dict[str, Any] | None = None,
    overwrite: bool = False,
    timeout_s: int = 60,
    reuse_existing_runs: bool = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Build a fact-only Markdown report plus a line-addressable evidence ledger."""
    try:
        return await _invoke(
            tools.build_report,
            ctx,
            "build_report",
            proj_path=proj_path,
            tests=tests,
            out=out,
            include=include,
            truth_map=truth_map,
            overwrite=overwrite,
            timeout_s=timeout_s,
            reuse_existing_runs=reuse_existing_runs,
        )
    except Exception as exc:
        return _error(exc)


@tool(annotations=ANALYZE)
async def run_all_tests(
    proj_path: str,
    tests: list[str] | None = None,
    timeout_s: int = 60,
    render: bool = False,
    max_parallel: int = 1,
    fail_fast: bool = False,
    total_timeout_s: int | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Run selected test/*.test models and aggregate isolated results."""
    try:
        return await _invoke(
            tools.run_all_tests,
            ctx,
            "run_all_tests",
            proj_path=proj_path,
            tests=tests,
            timeout_s=timeout_s,
            render=render,
            max_parallel=max_parallel,
            fail_fast=fail_fast,
            total_timeout_s=total_timeout_s,
        )
    except Exception as exc:
        return _error(exc)



def robei_gui_status(output_lines: int = 20, probe: bool = False) -> dict[str, Any]:
    """Report whether Robei is running, the loaded project, open models and the Output tail.

    ``probe=True`` also walks the UIA tree to prove the automation bridge still
    answers; it costs a second or two and touches the IDE, so it is off by default.
    """
    try:
        return gui.status(output_lines=output_lines, probe=probe)
    except Exception as exc:
        return _error(exc)


async def robei_gui_launch(wait_s: int = 30, ctx: Context | None = None) -> dict[str, Any]:
    """Start the Robei IDE on the interactive desktop and wait for its main window."""
    try:
        return await _invoke(gui.launch, ctx, "robei_gui_launch", wait_s=wait_s)
    except Exception as exc:
        return _error(exc)


async def robei_gui_open_project(
    proj_path: str,
    discard_unsaved: bool = False,
    timeout_s: int = 90,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Open a .proj by relaunching Robei with it on the command line (no dialog, no mouse).

    Any canvas edits are lost when the IDE restarts; pass discard_unsaved=true if
    Robei reports unsaved changes and you accept that.
    """
    try:
        return await _invoke(
            gui.open_project,
            ctx,
            "robei_gui_open_project",
            proj_path=proj_path,
            timeout_s=timeout_s,
            discard_unsaved=discard_unsaved,
        )
    except Exception as exc:
        return _error(exc)


def robei_gui_open_model(name: str, timeout_s: int = 90) -> dict[str, Any]:
    """Double-click a model in the Robei Files tree so its canvas becomes the current one."""
    try:
        return gui.open_model(name=name, timeout_s=timeout_s)
    except Exception as exc:
        return _error(exc)


async def robei_gui_compile(timeout_s: int = 90, ctx: Context | None = None) -> dict[str, Any]:
    """Run Build > Compile on the open model and return the new Output lines."""
    try:
        return await _invoke(gui.compile_model, ctx, "robei_gui_compile", timeout_s=timeout_s)
    except Exception as exc:
        return _error(exc)


def robei_gui_output(lines: int = 80) -> dict[str, Any]:
    """Read the last N lines of the Robei Output dock."""
    try:
        return gui.output(lines=lines)
    except Exception as exc:
        return _error(exc)


def robei_gui_save_project() -> dict[str, Any]:
    """File > Save in Robei (Build > Synthesis refuses to run on an unsaved project)."""
    try:
        return gui.save_project()
    except Exception as exc:
        return _error(exc)


def robei_gui_synthesis(timeout_s: int = 300) -> dict[str, Any]:
    """Build > Synthesis on the open constrain model; waits for 'Synthesis complete'."""
    try:
        return gui.synthesis(timeout_s=timeout_s)
    except Exception as exc:
        return _error(exc)


def robei_gui_close() -> dict[str, Any]:
    """Force-close the Robei IDE. Unsaved model edits are lost."""
    try:
        return gui.close()
    except Exception as exc:
        return _error(exc)


async def robei_gui_compile_all(models: list[str] | None = None, ctx: Context | None = None) -> dict[str, Any]:
    """Compile every .model of the open Robei project and report per-model warnings/errors."""
    try:
        return await _invoke(gui.compile_all, ctx, "robei_gui_compile_all", models=models)
    except Exception as exc:
        return _error(exc)


def robei_gui_screenshot(label: str = "shot", inline: bool = False) -> dict[str, Any]:
    """Grab the interactive desktop and save a PNG under workspace/gui_shots.

    ``inline=true`` also returns the PNG as an ``ImageContent`` block (5 MiB cap,
    same ``image_too_large`` report as ``render_waveform``).
    """
    try:
        payload = gui.screenshot(label=label)
    except Exception as exc:
        return _error(exc)
    if not inline:
        return payload
    path = payload.get("path")
    if not path:
        payload["inline"] = {"ok": False, "code": "not_found", "error": "no screenshot path"}
        return payload
    return _image_or_payload(payload, tools.inline_image_payload(Path(path)))


def gui_doctor(
    blocked_foreground: list[str] | None = None,
    screenshot: bool = False,
    label: str = "gui-doctor",
    timeout_s: int = 20,
) -> dict[str, Any]:
    """Read-only Session-1 desktop/power/input preflight for GUI stages."""
    try:
        return gui.gui_doctor(
            blocked_foreground=blocked_foreground,
            screenshot=screenshot,
            label=label,
            timeout_s=timeout_s,
        )
    except Exception as exc:
        return _error(exc)


# ---------------------------------------------------------------------------
# The GUI bridge is optional: Windows plus the driver package must be present.
# When it cannot run the tools are not advertised at all, instead of failing
# only when a client calls them.
# ---------------------------------------------------------------------------
GUI_TOOLS: tuple[tuple[Any, Any], ...] = (
    (gui_doctor, READ_GUI),
    (robei_gui_status, READ_GUI),
    (robei_gui_launch, WRITE_GUI),
    (robei_gui_open_project, WRITE_GUI),
    (robei_gui_open_model, WRITE_GUI),
    (robei_gui_compile, WRITE_GUI),
    (robei_gui_output, READ_GUI),
    (robei_gui_save_project, WRITE_GUI),
    (robei_gui_synthesis, WRITE_GUI),
    (robei_gui_close, DESTRUCTIVE_GUI),
    (robei_gui_compile_all, WRITE_GUI),
    (robei_gui_screenshot, WRITE_GUI),
)

GUI_ENABLED = gui.available()
if GUI_ENABLED:
    for _fn, _ann in GUI_TOOLS:
        mcp.add_tool(_stale_wrapped(_fn), annotations=_ann)

lifecycle.ready()
