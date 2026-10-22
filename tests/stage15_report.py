"""Stage 15: lab report, VCD diff, operation log, GUI lock.

Covers the four things added after stage14:
  report_lab            headless run -> markdown + PNG
  compare_waveforms     golden vs dut, first divergence per signal
  oplog                 workspace/logs/mcp-YYYYMMDD.log
  gui lock              a stale lock makes GUI calls fail fast instead of colliding
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(r"C:\dev\robei-mcp")
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
PROJ = ROOT / "workspace" / "counter4_native" / "counter4_native.proj"
LOCK = ROOT / "tools" / "gui" / "agent.lock"

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
        print("      " + json.dumps(data, ensure_ascii=False)[:600])


async def main() -> int:
    params = StdioServerParameters(command=str(PYTHON), args=[str(ROOT / "server.py")], cwd=str(ROOT))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = [t.name for t in (await session.list_tools()).tools]
            print("TOOL_COUNT=", len(names))
            for want in ("report_lab", "compare_waveforms"):
                check(f"registered {want}", want in names)

            # --- 1. lab report ------------------------------------------------
            data = payload(await session.call_tool("report_lab", {
                "proj_path": str(PROJ), "test_name": "tb_counter4.test", "title": "4 位计数器",
            }))
            report = Path(data.get("report_path") or "C:/nope")
            check("report_lab ok", data.get("ok") is True, data)
            check("report file exists", report.is_file(), str(report))
            text = report.read_text(encoding="utf-8") if report.is_file() else ""
            check("report has signal table", "| 信号 | 位宽 | 类型 | 跳变次数 |" in text)
            check("report embeds PNG", "![" in text and ".png" in text)
            check("report lists display output", "t=165 count=1111" in text)
            check("report checks all pass", all(c["ok"] for c in data.get("checks", [])), data.get("checks"))

            # --- 2. VCD diff --------------------------------------------------
            vcd = data.get("vcd")
            same = payload(await session.call_tool("compare_waveforms", {
                "golden_vcd": str(vcd), "dut_vcd": str(vcd),
            }))
            check("compare identical ok", same.get("ok") is True and same.get("mismatch_total") == 0, same)

            mutated = ROOT / "workspace" / "build" / "mutated_stage15.vcd"
            original = Path(vcd).read_text(encoding="utf-8", errors="replace")
            mutated.write_text(original.replace("b1111", "b1110", 1), encoding="utf-8")
            diff = payload(await session.call_tool("compare_waveforms", {
                "golden_vcd": str(vcd), "dut_vcd": str(mutated),
            }))
            first = next((r["first_mismatch"] for r in diff.get("results", []) if r.get("first_mismatch")), None)
            check("compare detects mutation", diff.get("ok") is False and diff.get("mismatch_total") == 1, diff)
            check("compare pinpoints t=155", bool(first) and first.get("time") == 155, first)

            # --- 3. operation log --------------------------------------------
            log = ROOT / "workspace" / "logs" / f"mcp-{datetime.now():%Y%m%d}.log"
            check("oplog file written", log.is_file(), str(log))
            rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
            kinds = {row.get("kind") for row in rows}
            exec_rows = [row for row in rows if row.get("kind") == "exec"]
            check("oplog has exec rows", len(exec_rows) >= 1, list(kinds))
            check("oplog records vvp/iverilog", any(r.get("tool") in ("iverilog.exe", "vvp.exe") for r in exec_rows),
                  [r.get("tool") for r in exec_rows][-5:])

            # --- 4. GUI lock ---------------------------------------------------
            LOCK.write_text(f"0 {time.time():.0f}", encoding="utf-8")
            try:
                busy = payload(await session.call_tool("robei_gui_status", {}))
            finally:
                LOCK.unlink(missing_ok=True)
            check("lock makes GUI fail fast", busy.get("ok") is False and "another GUI job" in str(busy.get("error")), busy)

    passed = sum(1 for _, ok in results if ok)
    print(f"\n--- summary ---\n{passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
