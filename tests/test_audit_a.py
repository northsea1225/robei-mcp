"""Audit batch A: security regressions (cwd-aware path scan, XML entity guard, run limits)."""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest

from robei_mcp import tools
from robei_mcp.config import SETTINGS
from robei_mcp.security import scan_source_paths


@pytest.fixture()
def work() -> Path:
    root = SETTINGS.workspace / ".tmp" / f"pytest-{uuid.uuid4().hex[:8]}"
    root.mkdir(parents=True, exist_ok=False)
    yield root
    shutil.rmtree(root, ignore_errors=True)


def _escape_name() -> str:
    return f"audit_escape_{uuid.uuid4().hex[:8]}.txt"


def test_fopen_relative_path_is_checked_against_the_real_run_cwd(work: Path) -> None:
    """``../../../x`` is inside the workspace relative to the source, but vvp runs in
    <workspace>/build/sim-*: three levels up leaves the workspace."""
    name = _escape_name()
    victim = SETTINGS.workspace.parent / name
    deep = work / "a" / "b"
    deep.mkdir(parents=True)
    src = deep / "t.v"
    src.write_text(
        'module t; integer f; initial begin f = $fopen("../../../%s", "w");'
        ' $fwrite(f, "x"); $fclose(f); $finish; end endmodule\n' % name,
        encoding="utf-8",
    )
    try:
        result = tools.simulate([str(src)], "t", timeout_s=20)
        assert result["ok"] is False
        assert result["code"] == "path_rejected"
        assert result["rejected"], "decisive detail must be mirrored at top level"
        assert not victim.exists(), "simulation wrote outside the workspace"
    finally:
        victim.unlink(missing_ok=True)


def test_scan_allows_paths_that_stay_inside_for_both_bases(work: Path) -> None:
    src = work / "t.v"
    src.write_text(
        'module t; reg [7:0] m[0:0]; initial begin $dumpfile("w.vcd"); $readmemh("../data.hex", m); end endmodule\n',
        encoding="utf-8",
    )
    run_cwd = SETTINGS.workspace / "build" / "sim-scan"
    assert scan_source_paths([src], run_cwd=run_cwd)["ok"] is True


def test_scan_rejects_when_only_the_run_cwd_reading_escapes(work: Path) -> None:
    deep = work / "a" / "b" / "c"
    deep.mkdir(parents=True)
    src = deep / "t.v"
    src.write_text('module t; initial $readmemh("../../../../zz.hex", m); endmodule\n', encoding="utf-8")
    run_cwd = SETTINGS.workspace / "build" / "sim-scan"
    verdict = scan_source_paths([src], run_cwd=run_cwd)
    assert verdict["ok"] is False
    assert verdict["rejected"][0]["base"] == "run_cwd"


def test_relative_include_that_escapes_the_run_cwd_is_rejected(work: Path) -> None:
    deep = work / "a" / "b" / "c"
    deep.mkdir(parents=True)
    src = deep / "t.v"
    src.write_text('`include "../../../secret.vh"\nmodule t; endmodule\n', encoding="utf-8")
    run_cwd = SETTINGS.workspace / "build" / "sim-scan"
    verdict = scan_source_paths([src], run_cwd=run_cwd)
    assert verdict["ok"] is False


def test_scan_without_run_cwd_keeps_the_legacy_behaviour(work: Path) -> None:
    deep = work / "a" / "b" / "c"
    deep.mkdir(parents=True)
    src = deep / "t.v"
    src.write_text('module t; initial $readmemh("../../../../zz.hex", m); endmodule\n', encoding="utf-8")
    assert scan_source_paths([src])["ok"] is True  # source-relative reading only


def test_simulate_project_base_depth_is_honoured(work: Path) -> None:
    """A project keeps its build at <proj>/build; the placeholder cwd must use that depth."""
    base = work / "proj" / "build"
    base.mkdir(parents=True)
    name = _escape_name()
    src = work / "t.v"
    # base/sim-x/../../ = work/proj (inside); base/sim-x/../../../../../ climbs far out
    src.write_text(
        'module t; integer f; initial begin f = $fopen("../../../../../../../%s", "w"); $fclose(f); $finish; end endmodule\n' % name,
        encoding="utf-8",
    )
    result = tools.simulate([str(src)], "t", timeout_s=20, base=base)
    assert result["code"] == "path_rejected"


def test_simulate_rejects_include_read_from_outside_via_run_cwd(work: Path) -> None:
    name = f"audit_secret_{uuid.uuid4().hex[:8]}.vh"
    victim = SETTINGS.workspace.parent / name
    victim.write_text("wire leaked = 1;\n", encoding="utf-8")
    deep = work / "a" / "b"
    deep.mkdir(parents=True)
    src = deep / "t.v"
    src.write_text('`include "../../../%s"\nmodule t; endmodule\n' % name, encoding="utf-8")
    try:
        result = tools.simulate([str(src)], "t", timeout_s=20)
        assert result["code"] == "path_rejected"
        assert result["compile"] is None
    finally:
        victim.unlink(missing_ok=True)


# --- XML guard: non-UTF-8 documents -----------------------------------------------------

from robei_mcp import xmlsafe  # noqa: E402

_BOMB = (
    '<?xml version="1.0" encoding="{enc}"?>\n<!DOCTYPE r [<!ENTITY a "aaaaaaaaaa">'
    '<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]>\n<r>&b;&b;&b;</r>\n'
)


@pytest.mark.parametrize(
    "encoding,bom",
    [("UTF-16", True), ("UTF-16LE", False), ("UTF-16BE", False), ("UTF-32", True)],
)
def test_entities_hidden_in_other_encodings_are_rejected(encoding: str, bom: bool) -> None:
    text = _BOMB.format(enc=encoding)
    codec = {"UTF-16": "utf-16", "UTF-16LE": "utf-16-le", "UTF-16BE": "utf-16-be", "UTF-32": "utf-32"}[encoding]
    data = text.encode(codec)
    with pytest.raises(xmlsafe.UnsafeXml):
        xmlsafe.parse_bytes(data, "bomb")


def test_utf16_document_without_entities_still_parses() -> None:
    data = '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE Robei><r a="1"/>'.encode("utf-16")
    assert xmlsafe.parse_bytes(data).attrib == {"a": "1"}


def test_utf8_robei_style_documents_are_unaffected() -> None:
    data = '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE Robei>\n<Module Name="中文"/>\n'.encode("utf-8")
    assert xmlsafe.parse_bytes(data).get("Name") == "中文"


def test_model_loader_rejects_utf16_entity_file(work: Path) -> None:
    path = work / "evil.model"
    path.write_bytes(_BOMB.format(enc="UTF-16").encode("utf-16"))
    with pytest.raises(xmlsafe.UnsafeXml):
        xmlsafe.load(path)


def test_edited_text_goes_through_the_guard() -> None:
    with pytest.raises(xmlsafe.UnsafeXml):
        xmlsafe.fromstring('<!DOCTYPE r [<!ENTITY a "x">]><r>&a;</r>')
    with pytest.raises(xmlsafe.UnsafeXml):
        xmlsafe.fromstring(_BOMB.format(enc="UTF-16").encode("utf-16"))


# --- subprocess limits -------------------------------------------------------------------

import os  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    # a zombie still answers kill(0); read the state on Linux
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as handle:
            return handle.read().split(")")[-1].split()[0] != "Z"
    except OSError:
        return True


def test_output_is_capped_but_head_and_tail_survive(work: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import dataclasses

    monkeypatch.setattr(tools, "SETTINGS", dataclasses.replace(SETTINGS, max_capture_bytes=100_000))
    script = (
        "import sys\n"
        "sys.stdout.write('HEAD-MARK\\n')\n"
        "for _ in range(3000): sys.stdout.write('x' * 1000 + '\\n')\n"
        "sys.stdout.write('SELFTEST FAIL: tail\\n')\n"
    )
    result = tools._run_impl([sys.executable, "-c", script], work, 30)
    assert result["output_truncated"] is True
    assert result["truncated_bytes"]["stdout"] > 2_000_000
    assert result["stdout"].startswith("HEAD-MARK")
    assert result["selftest"] == "FAIL"  # verdict in the tail still counts
    assert result["returncode"] == 0


def test_normal_output_has_no_truncation_flag(work: Path) -> None:
    result = tools._run_impl([sys.executable, "-c", "print('hi')"], work, 30)
    assert "output_truncated" not in result and result["stdout"].strip() == "hi"


@pytest.mark.skipif(os.name == "nt", reason="uses /proc and sh")
def test_timeout_kills_the_whole_process_tree(work: Path) -> None:
    pidfile = work / "child.pid"
    cmd = f"sleep 60 & echo $! > '{pidfile}'; wait"
    result = tools._run_impl(["sh", "-c", cmd], work, 1)
    assert result.get("timeout") is True
    child = int(pidfile.read_text().strip())
    deadline = time.time() + 5
    while time.time() < deadline and _alive(child):
        time.sleep(0.1)
    assert not _alive(child), "grandchild survived the timeout"


@pytest.mark.skipif(os.name != "nt", reason="Windows process-tree check")
def test_timeout_kills_the_whole_process_tree_windows(work: Path) -> None:
    pidfile = work / "child.pid"
    inner = (
        "import subprocess,sys,time;"
        "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
        f"open(r'{pidfile}','w').write(str(p.pid));time.sleep(60)"
    )
    result = tools._run_impl([sys.executable, "-c", inner], work, 2)
    assert result.get("timeout") is True
    child = int(pidfile.read_text().strip())
    import subprocess

    time.sleep(1.0)
    out = subprocess.run(["tasklist", "/FI", f"PID eq {child}"], capture_output=True, text=True).stdout
    assert str(child) not in out
