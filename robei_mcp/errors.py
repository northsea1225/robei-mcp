"""Stable error codes so MCP callers can branch without parsing prose."""
from __future__ import annotations

from typing import Any

from .xmlsafe import UnsafeXml

HINTS = {
    "unsafe_xml": "the XML declares a DTD or entities; Robei files never do",
    "not_found": "the path must exist and, for models/projects, sit under an allowed root",
    "already_exists": "pass overwrite=true (the previous file is kept as .bak-<timestamp>)",
    "permission_denied": "the server may only write inside the configured workspace",
    "timeout": "raise timeout_s (bounded by max_timeout_s in robei.toml) or shorten the testbench",
    "invalid_argument": "check the argument names and types in the tool schema",
    "bad_argument": "check the argument names and types in the tool schema",
    "cancelled": "the client cancelled the request; child processes were killed",
    "unsupported": "this build does not implement the requested feature",
    "internal": "see workspace/logs for the child-process trace",
}


def code_for(exc: BaseException) -> str:
    if isinstance(exc, UnsafeXml):
        return "unsafe_xml"
    if isinstance(exc, FileNotFoundError):
        return "not_found"
    if isinstance(exc, FileExistsError):
        return "already_exists"
    if isinstance(exc, PermissionError):
        return "permission_denied"
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, (KeyError, IndexError, TypeError)):
        return "bad_argument"
    if isinstance(exc, ValueError):
        return "invalid_argument"
    if isinstance(exc, NotImplementedError):
        return "unsupported"
    return "internal"


def payload(exc: BaseException, **extra: Any) -> dict[str, Any]:
    code = code_for(exc)
    data: dict[str, Any] = {
        "ok": False,
        "code": code,
        "error": f"{type(exc).__name__}: {exc}",
        "hint": HINTS.get(code),
    }
    stage = getattr(exc, "stage", None)
    if stage:
        data["stage"] = stage
    data.update(extra)
    return data
