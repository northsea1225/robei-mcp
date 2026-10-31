"""Stage 18: P1 features — Verilog->canvas, ERC, truth table, waveform assertions/stats, combo TB.

Everything goes through a real stdio MCP client and real iverilog/vvp runs. The
canvas import is additionally proved electrically: Robei-style model -> headless
simulation of the imported netlist (y = a & b & c).
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import shutil
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = pathlib.Path(r"C:\dev\robei-mcp")
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
WS = ROOT / "workspace"

results: list[tuple[str, bool]] = []


def payload(result) -> dict:
    if result.content and hasattr(result.content[0], "text"):
        try:
            return json.loads(result.content[0].text)
        except Exception:
            return {"raw": result.content[0].text[:400]}
    return {}


def check(name: str, ok: bool, data=None) -> None:
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}")
    if not ok and data is not None:
        print("      " + json.dumps(data, ensure_ascii=False)[:700])


TB = """reg a; reg b; reg c; wire y;
two_and dut (.a(a), .b(b), .c(c), .y(y));
initial begin
  a = 0; b = 0; c = 0; #10;
  a = 0; b = 1; c = 1; #10;
  a = 1; b = 1; c = 1; #10;
  $finish;
end
always @(a or b or c) $strobe("t=%0t y=%b", $time, y);"""


async def main() -> int:
    params = StdioServerParameters(command=str(PYTHON), args=[str(ROOT / "server.py")], cwd=str(ROOT))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            doctor = payload(await session.call_tool("gui_doctor", {}))
            print("GUI_DOCTOR", json.dumps(doctor, ensure_ascii=False))
            if not doctor.get("ok"):
                print("GUI_DOCTOR_FAILED", json.dumps(doctor.get("failed_checks") or [], ensure_ascii=False))
                return 2
            tools = {t.name: t for t in (await session.list_tools()).tools}
            print("TOOL_COUNT=", len(tools))
            new_tools = ["import_verilog_canvas", "check_connectivity", "truth_table", "expect_waveform", "waveform_stats"]
            check("five P1 tools registered", all(name in tools for name in new_tools), sorted(set(new_tools) - set(tools)))
            check("check_connectivity is read-only", tools["check_connectivity"].annotations.read_only_hint is True)

            (WS / "testbench" / "p1_and2_combo.v").unlink(missing_ok=True)

            # --- truth table -------------------------------------------------
            table = payload(await session.call_tool("truth_table", {"verilog_path": "cases/and2.v", "top": "and2"}))
            rows = table.get("rows") or []
            check("truth_table has 4 rows", table.get("row_count") == 4, table)
            check("truth_table matches AND", all(r["y"] == ("1" if r["a"] == "1" and r["b"] == "1" else "0") for r in rows), rows)
            big = payload(await session.call_tool("truth_table", {"verilog_path": "cases/and2.v", "top": "and2", "max_inputs": 1}))
            check("truth_table rejects too many inputs", big.get("code") == "invalid_argument", big)

            # --- connectivity -------------------------------------------------
            erc = payload(await session.call_tool("check_connectivity", {"files": ["cases/and2.v"], "top": "and2"}))
            check("clean design has no ERC issues", erc.get("ok") is True, erc)
            bad = payload(await session.call_tool("check_connectivity",
                                                 {"files": ["cases/and2.v", "cases/erc_bad.v"], "top": "erc_bad"}))
            counts = bad.get("counts") or {}
            check("ERC finds multi-driver", counts.get("multi_driver") == 1, bad)
            check("ERC finds undriven nets", counts.get("undriven_net", 0) >= 2, bad)

            # --- Verilog -> canvas --------------------------------------------
            shutil.rmtree(WS / "p1_demo", ignore_errors=True)
            proj = payload(await session.call_tool("create_robei_project", {"name": "p1_demo", "models": []}))["proj"]
            leaf = payload(await session.call_tool("create_robei_model", {
                "proj_path": proj, "name": "and2",
                "ports": [{"name": "a", "direction": "input"}, {"name": "b", "direction": "input"},
                          {"name": "y", "direction": "output"}],
                "code": "  assign y = a & b;"}))
            check("leaf model for import", leaf.get("ok") is True, leaf)
            imported = payload(await session.call_tool("import_verilog_canvas", {
                "proj_path": proj, "verilog_path": "cases/two_and.v"}))
            check("import_verilog_canvas ok", imported.get("ok") is True, imported)
            check("import produced two instances", len(imported.get("instances") or []) == 2, imported.get("instances"))
            webs = {w["net"]: w for w in imported.get("wires") or []}
            check("t1 mirrors u1.y -> u2.a",
                  webs.get("t1", {}).get("from", {}).get("instance") == "u1"
                  and webs.get("t1", {}).get("to", {}).get("instance") == "u2", webs.get("t1"))
            check("no dangling instance ports",
                  not (imported.get("netlist") or {}).get("dangling_instance_ports"), imported.get("netlist"))

            positional = payload(await session.call_tool("import_verilog_canvas", {
                "proj_path": proj, "verilog_path": "cases/two_and_positional.v"}))
            pos_wires = {w["net"]: w for w in positional.get("wires") or []}
            check("positional connections are resolved", positional.get("ok") is True
                  and pos_wires.get("t1", {}).get("from", {}).get("instance") == "u1", positional)
            erc_pos = payload(await session.call_tool("check_connectivity",
                                                    {"files": ["cases/and2.v", "cases/two_and_positional.v"]}))
            check("positional design passes ERC", erc_pos.get("ok") is True, erc_pos)

            test = payload(await session.call_tool("create_robei_test", {
                "proj_path": proj, "name": "tb_two_and", "code": TB}))
            check("test model for imported canvas", test.get("ok") is True, test)
            sim = payload(await session.call_tool("simulate_project", {"proj_path": proj, "test_name": "tb_two_and.test"}))
            lines = [ln for ln in (sim.get("display_output") or "").splitlines() if ln.startswith("t=")]
            check("imported canvas simulates", sim.get("ok") is True, sim)
            check("imported wiring is y = a&b&c",
                  lines[:3] == ["t=0 y=0", "t=10 y=0", "t=20 y=1"], lines[:4])

            # --- the imported canvas must be acceptable to Robei itself --------
            await session.call_tool("robei_gui_close", {})  # start from a clean IDE state
            # 1.3 keeps our sim runs inside <proj>/build, so only drop the files
            # Robei itself regenerates - not the whole directory.
            for stale in (WS / "p1_demo" / "build").glob("*"):
                if stale.is_file():
                    stale.unlink(missing_ok=True)
            await session.call_tool("robei_gui_launch", {"wait_s": 45})
            opened = payload(await session.call_tool("robei_gui_open_project", {"proj_path": proj}))
            compiled = payload(await session.call_tool("robei_gui_compile_all", {}))
            built = [p for p in (WS / "p1_demo" / "build").glob("*.v")
                     if p.stem in {"two_and", "two_and_pos"}]
            newest = max(built, key=lambda p: p.stat().st_mtime) if built else None
            text = newest.read_text(encoding="utf-8", errors="replace") if newest else ""
            check("Robei opens the imported project", opened.get("ok") is True, opened)
            check("Robei compiles the imported canvas", compiled.get("ok") is True, compiled)
            check(f"Robei emitted the imported instantiations ({newest.name if newest else 'none'})",
                  "and2 u1(" in text and ".y(t1)" in text, text[:300])
            await session.call_tool("robei_gui_close", {})

            # --- waveform assertions ------------------------------------------
            vcd = (sim.get("vcd_paths") or [None])[0]
            ok_case = payload(await session.call_tool("expect_waveform", {"vcd_path": vcd, "checks": [
                {"signal": "tb_two_and.y", "value": "0", "at": 5},
                {"signal": "tb_two_and.y", "value": "1", "at": 25}]}))
            check("expect_waveform passes true assertions", ok_case.get("ok") is True, ok_case)
            bad_case = payload(await session.call_tool("expect_waveform", {"vcd_path": vcd, "checks": [
                {"signal": "tb_two_and.y", "value": "1", "at": 5}]}))
            check("expect_waveform fails false assertions",
                  bad_case.get("ok") is False and bad_case.get("failed") == 1, bad_case)
            window = payload(await session.call_tool("expect_waveform", {"vcd_path": vcd, "checks": [
                {"signal": "tb_two_and.y", "value": "0", "from": 0, "to": 10}]}))
            check("expect_waveform supports held windows", window.get("ok") is True, window)

            # --- waveform stats ------------------------------------------------
            counter = payload(await session.call_tool("simulate", {
                "files": ["counter4/counter4.v", "counter4/counter4_tb.v"], "top": "counter4_tb", "timeout_s": 20}))
            stats = payload(await session.call_tool("waveform_stats", {"vcd_path": counter["vcd_paths"][0]}))
            clock = stats.get("clock") or {}
            check("waveform_stats finds the 10 ns clock", clock.get("period") == 10, clock)
            check("waveform_stats reports 100 MHz", clock.get("frequency_mhz") == 100.0, clock)
            check("waveform_stats coverage is complete",
                  (stats.get("coverage") or {}).get("ratio") == 1.0, stats.get("coverage"))

            # --- combo testbench ----------------------------------------------
            tb = payload(await session.call_tool("generate_testbench", {
                "verilog_path": "cases/and2.v", "top": "and2",
                "out_path": "testbench/p1_and2_combo.v", "full_combo": True}))
            check("generate_testbench full_combo", tb.get("ok") is True and tb.get("full_combo") is True, tb)
            combo = payload(await session.call_tool("simulate", {
                "files": ["cases/and2.v", tb["relative_path"]], "top": "and2_tb", "timeout_s": 20}))
            combos = [ln for ln in (combo.get("display_output") or "").splitlines() if ln.startswith("combo ")]
            check("combo testbench prints 4 rows", len(combos) == 4, combos)

    passed = sum(1 for _, ok in results if ok)
    print(f"\n--- summary ---\n{passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
