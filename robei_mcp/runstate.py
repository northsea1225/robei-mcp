"""Client-cancellable runs.

MCP handlers are async; the actual work is blocking (iverilog, vvp, the GUI
bridge). Each heavy call gets a :class:`RunToken`, runs on a worker thread with
``abandon_on_cancel=True``, and registers the token in a thread-local so
``tools._run_impl`` can see it. When the client cancels, the wrapper calls
``token.abort()``: the flag is set and every registered child process is killed,
so the worker unwinds within ~100 ms instead of holding the IDE/toolchain.
"""
from __future__ import annotations

import contextlib
import itertools
import subprocess
import threading

_ids = itertools.count(1)


class RunToken:
    def __init__(self) -> None:
        self.id = f"run-{next(_ids)}"
        self._event = threading.Event()
        self._procs: set[subprocess.Popen] = set()
        self._lock = threading.Lock()

    def aborted(self) -> bool:
        return self._event.is_set()

    def register(self, proc: subprocess.Popen) -> None:
        with self._lock:
            self._procs.add(proc)
        if self._event.is_set():  # abort raced with spawn
            self._kill(proc)

    def unregister(self, proc: subprocess.Popen) -> None:
        with self._lock:
            self._procs.discard(proc)

    def abort(self) -> None:
        self._event.set()
        with self._lock:
            procs = list(self._procs)
        for proc in procs:
            self._kill(proc)

    @staticmethod
    def _kill(proc: subprocess.Popen) -> None:
        try:
            if proc.poll() is None:
                proc.kill()
        except Exception:
            pass


_local = threading.local()


def current() -> RunToken | None:
    return getattr(_local, "token", None)


@contextlib.contextmanager
def use(token: RunToken | None):
    previous = current()
    _local.token = token
    try:
        yield token
    finally:
        _local.token = previous


def call(fn, token: RunToken, **kwargs):
    """Run ``fn`` on the worker thread with ``token`` visible to _run_impl."""
    with use(token):
        return fn(**kwargs)
