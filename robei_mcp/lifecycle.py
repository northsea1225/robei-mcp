"""File-only service lifecycle journal. Importing this module never starts a server."""
from __future__ import annotations

import atexit
import base64
from contextlib import contextmanager
import csv
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import traceback
import uuid

MAX_BYTES = 1_000_000
_LOCK = threading.RLock()
_ACTIVE = None


def _redact_command(value: str) -> str:
    value = re.sub(r"(https?://)[^\s/]*@", r"\1***@", value)
    return re.sub(r"gh[pousr]_[A-Za-z0-9]+|github_pat_[A-Za-z0-9_]+", "***", value)


def log_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "robei-mcp" / "lifecycle.log"


@contextmanager
def _file_lock(path: Path):
    """One bounded interprocess lock shared by server and its waiting launcher."""
    with _LOCK, path.with_suffix(".log.lock").open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        acquired = False
        for _ in range(100):
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError:
                time.sleep(0.01)
        if not acquired:
            raise TimeoutError("lifecycle journal lock unavailable")
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def event(kind: str, *, pid: int | None = None, run_id: str | None = None,
          path: Path | None = None, **fields) -> None:
    """Best effort, no stdout/stderr, no exception messages or argument capture."""
    try:
        target = path or log_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        record = {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                  "event": kind, "pid": os.getpid() if pid is None else pid,
                  "run_id": run_id, **fields}
        line = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
        with _file_lock(target):
            if target.exists() and target.stat().st_size + len(line) > MAX_BYTES:
                target.replace(target.with_suffix(".log.1"))
            with target.open("ab") as handle:
                handle.write(line)
                handle.flush()
    except Exception:
        pass  # a full disk or locked journal must not corrupt or stop JSON-RPC


def read_events(limit: int | None = None, path: Path | None = None) -> list[dict]:
    """Read-only; tolerate a partially written line and the single rotation boundary."""
    target = path or log_path()
    sources = (target.with_suffix(".log.1"), target)

    def signature():
        out = []
        for source in sources:
            try:
                stat = source.stat()
                out.append((stat.st_ino, stat.st_size, stat.st_mtime_ns))
            except FileNotFoundError:
                out.append(None)
        return out

    # Do not create/touch the writer lock in this read-only health path. Retry if
    # append/rotation changed either file while the two-file snapshot was read.
    for _ in range(5):
        before = signature()
        texts = []
        for source in sources:
            try:
                texts.append(source.read_text(encoding="utf-8", errors="replace"))
            except FileNotFoundError:
                texts.append("")
        if before == signature():
            break
    else:
        raise OSError("lifecycle journal changed during every snapshot attempt")
    rows = []
    for text in texts:
        for line in text.splitlines():
            try:
                row = json.loads(line)
                if (isinstance(row, dict) and isinstance(row.get("pid"), int)
                        and row.get("event") in {"start", "ready", "exit", "clean_exit", "crash", "stdin_closed"}):
                    rows.append(row)
            except (ValueError, TypeError):
                continue
    return rows if limit is None else rows[-limit:]


def _reason(rows: list[dict]) -> dict:
    exit_row = next((r for r in reversed(rows) if r["event"] == "exit"), None)
    crash = next((r for r in reversed(rows) if r["event"] == "crash"), None)
    graceful = next((r for r in reversed(rows) if r["event"] == "clean_exit"), None)
    eof = next((r for r in reversed(rows) if r["event"] == "stdin_closed"), None)
    if crash:
        reason = "crash:" + crash["exception_type"]
    elif exit_row and exit_row["exit_code"] != 0:
        reason = "abrupt_exit:" + str(exit_row["exit_code"])
    elif eof:
        reason = "stdin_closed"
    elif graceful:
        reason = "clean_exit"
    elif exit_row:
        reason = "exit_code:" + str(exit_row["exit_code"])
    else:
        reason = None  # may still be alive; status must not invent a termination
    terminal = exit_row or crash or graceful or eof
    return {"pid": rows[-1]["pid"], "run_id": rows[-1].get("run_id"), "reason": reason,
            "exit_code": exit_row["exit_code"] if exit_row else None,
            "observed_at": terminal["ts"] if terminal else None,
            "last_event_at": rows[-1]["ts"]}


def status() -> dict:
    base = {"path": str(log_path()), "pid": os.getpid(), "recent_events": [], "previous_exit": None}
    try:
        rows = read_events()
    except (OSError, ValueError) as exc:
        return {**base, "available": False, "error_type": type(exc).__name__}
    previous = next((r for r in reversed(rows) if r["pid"] != os.getpid()
                     and r["event"] in {"start", "ready"}), None)
    group = [r for r in rows if previous and r.get("run_id") == previous.get("run_id")]
    previous_exit = _reason(group) if group else None
    if previous_exit is not None:
        previous_exit["state"] = "exit_observed" if previous_exit["reason"] else "exit_not_observed"
    return {**base, "available": True, "recent_events": rows[-5:], "previous_exit": previous_exit}


def _commit(root: Path) -> str | None:
    try:
        proc = subprocess.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, timeout=5)
        value = proc.stdout.strip()
        return value if proc.returncode == 0 and re.fullmatch(r"[a-f0-9]{7,40}", value) else None
    except (OSError, subprocess.TimeoutExpired):
        return None


class Session:
    def __init__(self, run_id: str, path: Path | None = None):
        self.run_id, self.path = run_id, path
        self.crashed = self.stdin_closed = self.finished = False

    def write(self, kind, **fields):
        event(kind, run_id=self.run_id, path=self.path, **fields)

    def eof(self):
        if not self.stdin_closed:
            self.stdin_closed = True
            self.write("stdin_closed")

    def crash(self, exc_type, exc, tb):
        self.crashed = True
        frames = traceback.extract_tb(tb) if tb else []
        last = frames[-1] if frames else None
        self.write("crash", exception_type=exc_type.__name__,
                   last_frame={"file": last.filename, "line": last.lineno, "function": last.name} if last else None)

    def clean_exit(self):
        if not self.crashed and not self.finished:
            self.finished = True
            self.write("clean_exit", reason="stdin_closed" if self.stdin_closed else "normal_return")


def install() -> Session:
    global _ACTIVE
    if _ACTIVE is not None:
        return _ACTIVE
    run_id = os.environ.get("ROBEI_MCP_LIFECYCLE_RUN_ID") or uuid.uuid4().hex
    _ACTIVE = Session(run_id)
    # A Windows venv executable may be a redirector: Popen.pid is not always the
    # interpreter actually serving JSON-RPC. The child is the authority for PID.
    _ACTIVE.write("start", git_commit=_commit(Path(__file__).resolve().parents[1]), cwd=os.getcwd())
    old_hook = sys.excepthook

    def hook(exc_type, exc, tb):
        _ACTIVE.crash(exc_type, exc, tb)
        old_hook(exc_type, exc, tb)  # retain the ordinary stderr traceback

    sys.excepthook = hook
    atexit.register(_ACTIVE.clean_exit)
    return _ACTIVE


def ready() -> None:
    # Library imports and registry-only tests are not service launches.
    if _ACTIVE is not None:
        _ACTIVE.write("ready")


class StdinObserver:
    """Preserve SDK sender contexts and descriptor claiming; observe only true EOF."""
    def __init__(self, stream):
        self.stream = stream

    def __getattr__(self, name):
        return getattr(self.stream, name)

    def __aiter__(self):
        return self

    async def __anext__(self):
        import anyio
        try:
            return await self.receive()
        except anyio.EndOfStream:
            raise StopAsyncIteration

    async def receive(self):
        import anyio
        try:
            return await self.stream.receive()
        except anyio.EndOfStream:
            if _ACTIVE is not None:
                _ACTIVE.eof()
            raise


async def run_stdio(server) -> None:
    from mcp.server.stdio import stdio_server
    async with stdio_server() as (reader, writer):
        await server._lowlevel_server.run(StdinObserver(reader), writer,
                                         server._lowlevel_server.create_initialization_options())


def run_server(*, _test_crash: bool = False) -> None:
    install()
    from .server_app import mcp
    if _test_crash:
        # Only tests/lifecycle_crash_probe.py opts in. No env/CLI/MCP production switch.
        raise RuntimeError("test-only lifecycle crash")
    mcp.run()


def supervise(root: Path, *, entry: Path | None = None) -> int:
    """run-mcp.cmd's waiting side: record the real server PID and its exit code."""
    run_id = uuid.uuid4().hex
    env = {**os.environ, "ROBEI_MCP_LIFECYCLE_RUN_ID": run_id,
           "ROBEI_MCP_LIFECYCLE_SUPERVISED": run_id}
    commit = _commit(root)
    started = time.monotonic()
    proc = subprocess.Popen([sys.executable, "-u", str(entry or root / "robei_mcp" / "server.py")], cwd=root, env=env)
    code = proc.wait()
    try:
        starts = [r for r in read_events() if r.get("run_id") == run_id and r["event"] == "start"]
    except OSError:
        starts = []
    server_pid = starts[-1]["pid"] if starts else proc.pid
    if not starts:
        event("start", pid=server_pid, run_id=run_id, git_commit=commit, cwd=str(root),
              pid_source="launcher_fallback")
    event("exit", pid=server_pid, run_id=run_id, exit_code=code,
          duration_s=round(time.monotonic() - started, 3), launcher_pid=os.getpid(), spawn_pid=proc.pid)
    return code


def health() -> int:
    """Read-only probe: journal, CIM process identity, then one conclusion line."""
    rows = read_events()
    for row in rows[-20:]:
        print(json.dumps(row, ensure_ascii=False))
    script = (
        "$ProgressPreference='SilentlyContinue'; "
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.Name -eq 'python.exe' } | "
        "Select-Object ProcessId,ExecutablePath,CommandLine | ConvertTo-Json -Compress"
    )
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    powershell = os.environ.get("SystemRoot", r"C:\Windows") + r"\System32\WindowsPowerShell\v1.0\powershell.exe"
    proc = subprocess.run([powershell, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                          capture_output=True, text=True, errors="replace", timeout=15)
    if proc.returncode:
        raise RuntimeError("CIM process query failed")
    raw = proc.stdout
    start = min((i for i in (raw.find("["), raw.find("{")) if i >= 0), default=-1)
    if start < 0:
        processes = []
    else:
        try:
            processes = json.JSONDecoder().raw_decode(raw[start:])[0]
        except json.JSONDecodeError as exc:
            raise RuntimeError("CIM process query returned invalid JSON") from exc
    if isinstance(processes, dict):
        processes = [processes]
    by_pid = {int(row["ProcessId"]): row for row in processes
              if isinstance(row, dict) and str(row.get("ProcessId", "")).isdigit()}
    services = [r for r in rows if r["event"] in {"start", "ready"}]
    exited_runs = {r.get("run_id") for r in rows if r["event"] == "exit"}
    known = {r["pid"] for r in services if r.get("run_id") not in exited_runs}
    venv_prefix = "c:\\dev\\robei-mcp\\.venv\\"
    alive, stale = [], []
    for pid in sorted(known):
        row = by_pid.get(pid) or {}
        executable = str(row.get("ExecutablePath") or "")
        command = str(row.get("CommandLine") or "")
        normalized = command.replace("\\", "/").lower()
        valid = executable.replace("/", "\\").lower().startswith(venv_prefix) and \
            executable.replace("/", "\\").lower().endswith("\\python.exe") and \
            "robei_mcp/server" in normalized
        if valid:
            alive.append(pid)
        else:
            stale.append({"pid": pid, "executable": executable, "command": _redact_command(command)})
    for row in stale:
        print("stale_pid " + json.dumps(row, ensure_ascii=False))
    print("CIM robei-mcp python pids: " + (",".join(map(str, alive)) or "none"))
    if alive:
        print("alive")
    elif stale:
        print("stale_pid")
    elif not services:
        print("never_started")
    else:
        latest = services[-1]
        group = [r for r in rows if r.get("run_id") == latest.get("run_id")]
        reason = _reason(group)["reason"]
        print("exited:" + ("客户端断开" if reason == "stdin_closed" else reason or "未观察到终止"))
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--supervise"]:
        raise SystemExit(supervise(Path(__file__).resolve().parents[1]))
    if sys.argv[1:] == ["--health"]:
        raise SystemExit(health())
    raise SystemExit("usage: python -m robei_mcp.lifecycle --supervise|--health")
