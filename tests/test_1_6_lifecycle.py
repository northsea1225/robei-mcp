"""H1: isolated journals and mocked termination paths; no real service is killed."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys

import anyio
import pytest

from robei_mcp import lifecycle as lc


@pytest.fixture
def journal(tmp_path, monkeypatch):
    path = tmp_path / "robei-mcp" / "lifecycle.log"
    monkeypatch.setattr(lc, "log_path", lambda: path)
    monkeypatch.setattr(lc, "_ACTIVE", None)
    return path


def test_rotation_keeps_only_one_backup_and_json_lines(journal, monkeypatch):
    monkeypatch.setattr(lc, "MAX_BYTES", 350)
    for n in range(12):
        lc.event("ready", run_id=str(n), data="x" * 80)
    assert journal.stat().st_size <= 350
    assert journal.with_suffix(".log.1").stat().st_size <= 350
    assert not journal.with_suffix(".log.2").exists()
    assert lc.read_events()[-1]["run_id"] == "11"
    assert len(lc.read_events(1)) == 1
    for file in (journal, journal.with_suffix(".log.1")):
        assert all(isinstance(json.loads(line), dict) for line in file.read_text().splitlines())


def test_io_failure_never_writes_protocol_output(journal, monkeypatch, capsys):
    monkeypatch.setattr(Path, "mkdir", lambda *a, **k: (_ for _ in ()).throw(PermissionError()))
    lc.event("start")
    assert capsys.readouterr() == ("", "")


def test_install_ready_and_atexit_once(journal, monkeypatch, capsys):
    registered = []
    monkeypatch.setattr(lc.atexit, "register", registered.append)
    monkeypatch.setattr(lc, "_commit", lambda root: "f9f50a9")
    monkeypatch.setattr(sys, "excepthook", lambda *args: None)
    monkeypatch.delenv("ROBEI_MCP_LIFECYCLE_RUN_ID", raising=False)
    monkeypatch.delenv("ROBEI_MCP_LIFECYCLE_SUPERVISED", raising=False)
    session = lc.install()
    assert lc.install() is session
    lc.ready()
    registered[0]()
    registered[0]()
    rows = lc.read_events()
    assert [r["event"] for r in rows] == ["start", "ready", "clean_exit"]
    assert rows[0]["git_commit"] == "f9f50a9" and rows[0]["cwd"]
    assert rows[-1]["reason"] == "normal_return"
    assert capsys.readouterr() == ("", "")


def test_crash_records_type_and_frame_without_values(journal):
    session = lc.Session("crash-case")
    try:
        raise RuntimeError("SECRET_ARGUMENT_VALUE")
    except RuntimeError as exc:
        session.crash(type(exc), exc, exc.__traceback__)
    session.clean_exit()
    row, = lc.read_events()
    assert row["event"] == "crash" and row["exception_type"] == "RuntimeError"
    assert row["last_frame"]["function"] == "test_crash_records_type_and_frame_without_values"
    assert set(row["last_frame"]) == {"file", "line", "function"}
    assert "SECRET_ARGUMENT_VALUE" not in journal.read_text()


def test_uncaught_hook_preserves_stderr_hook(journal, monkeypatch):
    calls = []
    monkeypatch.setattr(sys, "excepthook", lambda *args: calls.append(args))
    monkeypatch.setattr(lc.atexit, "register", lambda fn: None)
    monkeypatch.setattr(lc, "_commit", lambda root: None)
    lc.install()
    exc = ValueError("private")
    sys.excepthook(type(exc), exc, None)
    assert calls == [(ValueError, exc, None)]
    assert lc.read_events()[-1]["exception_type"] == "ValueError"


def test_stdin_eof_is_distinct_from_crash(journal, monkeypatch):
    session = lc.Session("eof-case")
    monkeypatch.setattr(lc, "_ACTIVE", session)

    class Source:
        context = "sender-context"

        async def receive(self):
            raise anyio.EndOfStream

    source = lc.StdinObserver(Source())
    assert source.context == "sender-context"
    async def read():
        assert [item async for item in source] == []
        with pytest.raises(anyio.EndOfStream):
            await source.receive()
    asyncio.run(read())
    session.clean_exit()
    assert [r["event"] for r in lc.read_events()] == ["stdin_closed", "clean_exit"]


def test_cancellation_is_not_stdin_eof(journal, monkeypatch):
    monkeypatch.setattr(lc, "_ACTIVE", lc.Session("cancel-case"))
    class Source:
        async def receive(self):
            raise anyio.ClosedResourceError
    with pytest.raises(anyio.ClosedResourceError):
        asyncio.run(lc.StdinObserver(Source()).receive())
    assert lc.read_events() == []


@pytest.mark.parametrize("code", [0, 1, -9, 255])
def test_launcher_records_real_child_exit_code(journal, monkeypatch, tmp_path, code):
    monkeypatch.setattr(lc, "_commit", lambda root: "f9f50a9")
    commands = []
    class Child:
        pid = 4567
        def __init__(self, command, **kwargs):
            commands.append((command, kwargs))
            lc.event("start", pid=self.pid,
                     run_id=kwargs["env"]["ROBEI_MCP_LIFECYCLE_RUN_ID"],
                     git_commit="f9f50a9", cwd=str(tmp_path), pid_source="server")
        def wait(self):
            return code
    monkeypatch.setattr(lc.subprocess, "Popen", Child)
    assert lc.supervise(tmp_path) == code
    start, exit_row = lc.read_events()
    assert start["event"] == "start" and start["pid"] == 4567
    assert exit_row["event"] == "exit" and exit_row["exit_code"] == code
    assert exit_row["duration_s"] >= 0
    assert start["run_id"] == exit_row["run_id"]
    assert commands[0][1]["env"]["ROBEI_MCP_LIFECYCLE_SUPERVISED"] == start["run_id"]
    assert commands[0][0] == [sys.executable, "-u", "-m", "robei_mcp.server"]
    assert len(commands) == 1  # no automatic restart


def test_real_supervised_package_entry_writes_ready_and_exits_cleanly(tmp_path):
    root = Path(__file__).resolve().parents[1]
    localappdata = tmp_path / "localappdata"
    env = os.environ.copy()
    env["LOCALAPPDATA"] = str(localappdata)
    env.pop("ROBEI_MCP_LIFECYCLE_RUN_ID", None)
    env.pop("ROBEI_MCP_LIFECYCLE_SUPERVISED", None)
    proc = subprocess.Popen(
        [sys.executable, "-u", "-m", "robei_mcp.lifecycle", "--supervise"],
        cwd=root,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    stdout, stderr = proc.communicate(timeout=30)
    assert proc.returncode == 0, f"stdout={stdout!r} stderr={stderr!r}"
    rows = lc.read_events(path=localappdata / "robei-mcp" / "lifecycle.log")
    run_id = next(row["run_id"] for row in rows if row["event"] == "start")
    group = [row for row in rows if row.get("run_id") == run_id]
    assert [row["event"] for row in group] == ["start", "ready", "stdin_closed", "clean_exit", "exit"]
    assert group[0]["pid_source"] == "server"
    assert group[0]["pid"] == group[-1]["pid"] and group[-1]["exit_code"] == 0


@pytest.mark.parametrize(
    "command_line",
    [
        "python -u C:/dev/robei-mcp/robei_mcp/server.py",
        r"python -u C:\dev\robei-mcp\robei_mcp\server.py",
        "python -u -m robei_mcp.server",
    ],
)
def test_health_accepts_all_server_command_spellings(journal, monkeypatch, capsys, command_line):
    lc.event("start", pid=4567, run_id="probe")
    payload = json.dumps([{
        "ProcessId": 4567,
        "ExecutablePath": r"C:\dev\robei-mcp\.venv\Scripts\python.exe",
        "CommandLine": command_line,
    }])
    monkeypatch.setattr(lc.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, payload, ""))
    assert lc.health() == 0
    assert capsys.readouterr().out.splitlines()[-1] == "alive"


def test_status_reports_last_five_and_previous_exit(journal):
    for kind in ("start", "ready", "stdin_closed", "clean_exit"):
        lc.event(kind, pid=1234, run_id="previous")
    lc.event("exit", pid=1234, run_id="previous", exit_code=0)
    lc.event("ready", run_id="current")
    result = lc.status()
    assert len(result["recent_events"]) == 5
    assert result["path"] == str(journal)
    assert result["pid"] == __import__("os").getpid()
    assert result["previous_exit"]["reason"] == "stdin_closed"
    assert result["previous_exit"]["exit_code"] == 0


def test_status_does_not_claim_unfinished_previous_process_exited(journal):
    lc.event("ready", pid=1234, run_id="other-live-process")
    result = lc.status()["previous_exit"]
    assert result["reason"] is None and result["state"] == "exit_not_observed"
    assert result["observed_at"] is None


def test_status_read_failure_is_local_to_lifecycle(journal, monkeypatch):
    monkeypatch.setattr(lc, "read_events", lambda *a, **k: (_ for _ in ()).throw(PermissionError()))
    from robei_mcp import tools
    result = tools.runtime_status()
    assert result["git_commit"] and result["tools"]
    assert result["lifecycle"]["available"] is False
    assert result["lifecycle"]["error_type"] == "PermissionError"


@pytest.mark.parametrize("alive,reason", [(True, "alive"), (False, "exited:abrupt_exit:1")])
def test_health_uses_tasklist_without_starting_service(journal, monkeypatch, capsys, alive, reason):
    lc.event("start", pid=4567, run_id="probe")
    if not alive:
        lc.event("exit", pid=4567, run_id="probe", exit_code=1)
    seen = []
    def run(args, **kwargs):
        seen.append(args)
        stdout = ('[{"ProcessId":4567,"ExecutablePath":"C:\\\\dev\\\\robei-mcp\\\\.venv\\\\Scripts\\\\python.exe",'
                  '"CommandLine":"python -u C:/dev/robei-mcp/robei_mcp/server.py"}]') if alive else "[]"
        return subprocess.CompletedProcess(args, 0, stdout, "")
    monkeypatch.setattr(lc.subprocess, "run", run)
    assert lc.health() == 0
    assert capsys.readouterr().out.splitlines()[-1] == reason
    assert len(seen) == 1 and "-EncodedCommand" in seen[0]


def test_health_never_started_is_read_only(journal, monkeypatch, capsys):
    monkeypatch.setattr(lc.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, "", ""))
    lc.health()
    assert capsys.readouterr().out.splitlines()[-1] == "never_started"
    assert not journal.parent.exists()


def test_health_marks_pid_reuse_or_wrong_command_stale(journal, monkeypatch, capsys):
    lc.event("start", pid=4567, run_id="probe")
    monkeypatch.setattr(lc.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a, 0, '[{"ProcessId":4567,"ExecutablePath":"C:\\\\dev\\\\robei-mcp\\\\.venv\\\\Scripts\\\\python.exe",'
        '"CommandLine":"python -u C:/dev/robei-mcp/server.py"}]', ""))
    assert lc.health() == 0
    output = capsys.readouterr().out
    assert '"pid": 4567' in output and output.splitlines()[-1] == "stale_pid"


def test_production_never_reads_test_crash_env(monkeypatch):
    from robei_mcp import server_app
    seen = []
    monkeypatch.setenv("ROBEI_MCP_TEST_CRASH", "1")
    monkeypatch.setattr(lc, "install", lambda: None)
    monkeypatch.setattr(server_app.mcp, "run", lambda: seen.append("run"))
    lc.run_server()
    assert seen == ["run"]
    with pytest.raises(RuntimeError, match="test-only"):
        lc.run_server(_test_crash=True)


def test_runtime_status_includes_lifecycle(journal):
    from robei_mcp import tools
    result = tools.runtime_status()["lifecycle"]
    assert result["path"] == str(journal)
    assert result["recent_events"] == [] and result["previous_exit"] is None
