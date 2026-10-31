"""Stable error codes so MCP callers can branch without parsing prose."""
from __future__ import annotations

from typing import Any

from .xmlsafe import UnsafeXml


class PathEscape(ValueError):
    """A supplied path resolved outside every root the tool is allowed to touch."""


class LogPathEscape(PathEscape):
    """read_run_log was pointed at a file outside workspace/logs and workspace/build."""


class BadRequest(ValueError):
    """A semantically invalid argument; carries a concrete ``hint``.

    Still a ``ValueError``, so the code stays ``invalid_argument`` for clients
    that branch on it - only the hint becomes specific ("port a is 4 bit(s)
    wide, use a[0]..a[3]") instead of the generic "check the argument names and
    types", which belongs to genuine type/missing-argument failures.
    """

    def __init__(self, message: str, hint: str, code: str | None = None) -> None:
        super().__init__(message)
        self.hint = hint
        self.code_override = code


class WidthMismatch(ValueError):
    """Two wire endpoints do not carry the same number of bits."""

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.hint = hint


class NotBehavioral(ValueError):
    """A Code-only edit was attempted on a structural (canvas) model."""


class UnknownParameter(ValueError):
    """set_robei_parameter named a parameter the model does not declare."""


class DeviceUnsupported(ValueError):
    """The requested FPGA part has no place-and-route model in the installed database."""

    def __init__(self, message: str, available: list[str] | None = None) -> None:
        super().__init__(message)
        self.available = available or []


class UnconstrainedIO(ValueError):
    """A port bit has no package pin, so place-and-route cannot build a bitstream."""

    def __init__(self, message: str, missing: list[dict] | None = None) -> None:
        super().__init__(message)
        self.missing = missing or []


class GuiForeignInstance(RuntimeError):
    """A Robei window this bridge did not start is running."""


class GuiNeedsActiveDesktop(RuntimeError):
    """The interactive desktop cannot accept mouse input (locked or disconnected)."""


class GuiUnsavedChanges(RuntimeError):
    """The open canvas has unsaved edits and the caller did not pass discard_unsaved."""


class FpgaStepFailed(RuntimeError):
    """One FPGA stage (synth/route/package) failed; ``stage`` names it.

    ``record`` is the stage report the FPGA backend already built (rc, argv,
    log tail, outputs). ``from_record`` turns that dict into the exception, so a
    failing stage never has to be re-raised as a bare RuntimeError.
    """

    def __init__(self, message: str, stage: str | None = None, record: dict | None = None) -> None:
        super().__init__(message)
        self.stage = stage
        self.record = record or {}
        self.code_override: str | None = None

    @classmethod
    def from_record(cls, record: dict, code: str = "fpga_step_failed") -> "FpgaStepFailed":
        """Build the exception from a stage report (``stage``/``returncode``/...)."""
        stage = str(record.get("stage") or "unknown")
        tail = list(record.get("log_tail") or [])
        last = tail[-1] if tail else "no output captured"
        rc = record.get("returncode")
        error = cls(f"{stage} failed (rc={rc}): {last}", stage=stage, record=dict(record))
        error.code_override = code
        return error


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
    "unconstrained_io": (
        "every port bit needs a package pin: create_robei_const / set_port the missing pins, "
        "then export_xdc again"
    ),
    "gui_foreign_instance": (
        "a Robei window started outside this bridge is in the way; close it yourself "
        "(the bridge only stops instances it launched), then retry"
    ),
    "gui_needs_active_desktop": (
        "the Windows session is locked or its desktop is disconnected: unlock it and "
        "retry. open_project/compile work without the desktop, the Files tree needs the mouse"
    ),
    "gui_unsaved_changes": (
        "Robei reports unsaved canvas edits; pass discard_unsaved=true to reopen and lose "
        "them, or save the project first"
    ),
    "fpga_step_failed": (
        "the failing stage is named in `stage`; `log_path` (or record.log_path) is a file "
        "under workspace/build that read_run_log can open, and log_tail already holds the last "
        "lines - fix that stage before retrying (synth -> route -> package)"
    ),
    "not_found": "the path must exist and, for models/projects, sit under an allowed root",
    "already_exists": "pass overwrite=true (the previous file is kept as .bak-<timestamp>)",
    "permission_denied": "the server may only write inside the configured workspace",
    "timeout": "raise timeout_s (bounded by max_timeout_s in robei.toml) or shorten the testbench",
    "invalid_argument": "check the argument names and types in the tool schema",
    "bad_argument": "check the argument names and types in the tool schema",
    "cancelled": "the client cancelled the request; child processes were killed",
    "unsupported": "this build does not implement the requested feature",
    "internal": (
        "unexpected failure; retry with the same arguments and, if it repeats, open the "
        "`log_path` file (or any file under `run_dir`) with read_run_log"
    ),
    "path_escape_log": (
        "read_run_log opens files inside a logs/ or build/ directory under the workspace; "
        "every fpga_step_failed payload carries the exact `log_path` to pass back"
    ),
}


def code_for(exc: BaseException) -> str:
    if isinstance(exc, BadRequest) and exc.code_override:
        return exc.code_override
    if isinstance(exc, GuiForeignInstance):
        return "gui_foreign_instance"
    if isinstance(exc, GuiNeedsActiveDesktop):
        return "gui_needs_active_desktop"
    if isinstance(exc, GuiUnsavedChanges):
        return "gui_unsaved_changes"
    if isinstance(exc, FpgaStepFailed):
        return getattr(exc, "code_override", None) or "fpga_step_failed"
    if isinstance(exc, DeviceUnsupported):
        return "device_unsupported"
    if isinstance(exc, UnconstrainedIO):
        return "unconstrained_io"
    if isinstance(exc, UnsafeXml):
        return "unsafe_xml"
    if isinstance(exc, LogPathEscape):
        return "path_escape_log"
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
        # A semantic error names the offending field itself; the table is the
        # fallback for failures we cannot classify any further.
        "hint": getattr(exc, "hint", None) or HINTS.get(code),
    }
    stage = getattr(exc, "stage", None)
    if stage:
        data["stage"] = stage
    missing = getattr(exc, "missing", None)
    if missing:
        data["missing"] = missing
    available = getattr(exc, "available", None)
    if available:
        data["available_parts"] = available
    references = getattr(exc, "references", None)
    if references is not None:
        data["references"] = references
    record = getattr(exc, "record", None)
    if record:
        data["record"] = record
        # A failed FPGA stage is only actionable if the caller can see what ran
        # and where the full log sits; keep both at the top level.
        for key in ("argv", "returncode", "log_tail", "log_path", "run_dir"):
            if record.get(key) not in (None, "", []):
                data[key] = record[key]
    data.update(extra)
    return data
