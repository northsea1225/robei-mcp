"""Stable error codes so MCP callers can branch without parsing prose."""
from __future__ import annotations

from typing import Any

from .xmlsafe import UnsafeXml


class PathEscape(ValueError):
    """A supplied path resolved outside every root the tool is allowed to touch."""


class WidthMismatch(ValueError):
    """Two wire endpoints do not carry the same number of bits."""


class NotBehavioral(ValueError):
    """A Code-only edit was attempted on a structural (canvas) model."""


class UnknownParameter(ValueError):
    """set_robei_parameter named a parameter the model does not declare."""


class DeviceUnsupported(ValueError):
    """The requested FPGA part has no place-and-route model in the installed database."""


class FpgaStepFailed(RuntimeError):
    """One FPGA stage (synth/route/package) failed; ``stage`` names it."""

    def __init__(self, message: str, stage: str | None = None, record: dict | None = None) -> None:
        super().__init__(message)
        self.stage = stage
        self.record = record or {}


HINTS = {
    "unsafe_xml": "the XML declares a DTD or entities; Robei files never do",
    "path_escape": (
        "the path must stay inside the workspace: write it relative to workspace/ "
        "(e.g. proj/a.v), never with ../ or an absolute path outside"
    ),
    "width_mismatch": (
        "connect endpoints of equal width: widen the port/wire, or drive the narrow "
        "side through an explicit slice"
    ),
    "not_behavioral": (
        "set_robei_code only edits models without instances or wires; for a structural "
        "model use edit_robei_canvas or create_robei_model"
    ),
    "unknown_parameter": "read_robei_model lists the declared names in parameter_list[].name",
    "device_unsupported": (
        "this part has no chipdb (or no part.yaml); list_fpga_devices() shows which parts are buildable "
        "on the installed Robei database"
    ),
    "fpga_step_failed": (
        "the failing stage is named in `stage` and its log tail in `record.log_tail`; "
        "fix that stage before retrying (synth -> route -> package)"
    ),
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
    if isinstance(exc, FpgaStepFailed):
        return getattr(exc, "code_override", None) or "fpga_step_failed"
    if isinstance(exc, DeviceUnsupported):
        return "device_unsupported"
    if isinstance(exc, UnsafeXml):
        return "unsafe_xml"
    if isinstance(exc, PathEscape):
        return "path_escape"
    if isinstance(exc, WidthMismatch):
        return "width_mismatch"
    if isinstance(exc, NotBehavioral):
        return "not_behavioral"
    if isinstance(exc, UnknownParameter):
        return "unknown_parameter"
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
    missing = getattr(exc, "missing", None)
    if missing:
        data["missing"] = missing
    record = getattr(exc, "record", None)
    if record:
        data["record"] = record
    data.update(extra)
    return data
