from __future__ import annotations

import os
import re
from pathlib import Path

from .config import SETTINGS


def is_within(path: Path, root: Path) -> bool:
    try:
        path = path.resolve()
        root = root.resolve()
    except OSError:
        return False
    p = os.path.normcase(str(path))
    r = os.path.normcase(str(root))
    return p == r or p.startswith(r + os.sep)


def resolve_workspace_path(value: str | Path, *, must_exist: bool = True) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = SETTINGS.workspace / candidate
    candidate = candidate.resolve()
    if not is_within(candidate, SETTINGS.workspace):
        raise ValueError(f"path escapes workspace: {candidate}")
    if must_exist and not candidate.is_file():
        raise FileNotFoundError(f"file not found in workspace: {candidate}")
    return candidate


def resolve_source_files(files: list[str] | tuple[str, ...]) -> list[Path]:
    if not files:
        raise ValueError("files must not be empty")
    if len(files) > 32:
        raise ValueError("at most 32 source files are allowed")
    allowed = {".v", ".sv", ".vh", ".svh"}
    result: list[Path] = []
    for item in files:
        path = resolve_workspace_path(item)
        if path.suffix.lower() not in allowed:
            raise ValueError(f"unsupported Verilog extension: {path.suffix}")
        if path.stat().st_size > SETTINGS.max_source_bytes:
            raise ValueError(f"source file too large: {path}")
        result.append(path)
    return result


def resolve_vcd_path(value: str | Path) -> Path:
    path = resolve_workspace_path(value)
    if path.suffix.lower() != ".vcd":
        raise ValueError("waveform file must have .vcd extension")
    if path.stat().st_size > SETTINGS.max_vcd_bytes:
        raise ValueError("VCD file exceeds size limit")
    return path


def resolve_model_path(value: str | Path) -> Path:
    candidate = Path(value)
    if candidate.is_absolute():
        candidates = [candidate]
    else:
        candidates = [SETTINGS.workspace / candidate]
        candidates.extend(root / candidate for root in SETTINGS.model_roots)

    roots = (SETTINGS.workspace, *SETTINGS.model_roots)
    for item in candidates:
        try:
            resolved = item.resolve()
        except OSError:
            continue
        if not any(is_within(resolved, root) for root in roots):
            if resolved.is_file():
                # The file exists but is outside every allowed root: that is a
                # policy rejection, not a missing file; clients must tell them apart.
                raise ValueError(
                    "model path escapes allowed roots: " + str(resolved) + "; allowed: "
                    + "; ".join(str(r) for r in roots)
                )
            continue
        if resolved.suffix.lower() != ".model":
            raise ValueError(f"model file must have a .model extension: {resolved}")
        if resolved.is_file():
            if resolved.stat().st_size > SETTINGS.max_model_bytes:
                raise ValueError("model file exceeds size limit")
            return resolved
    raise FileNotFoundError(
        "model not found under allowed roots: " + "; ".join(str(r) for r in roots)
    )


def resolve_project_path(value: str | Path) -> Path:
    """Resolve a Robei .proj path. Read-only: the project is never written back."""
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = SETTINGS.workspace / candidate
    resolved = candidate.resolve()
    roots = (SETTINGS.workspace, *SETTINGS.model_roots, SETTINGS.robei_home / "project")
    if not any(is_within(resolved, root) for root in roots):
        raise ValueError(f"project path escapes allowed roots: {resolved}")
    if resolved.suffix.lower() != ".proj":
        raise ValueError("project file must have .proj extension")
    if not resolved.is_file():
        raise FileNotFoundError(f"project not found: {resolved}")
    return resolved


# ---------------------------------------------------------------------------
# Static path scan used before simulate()
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?\*/', re.DOTALL)
_INCLUDE_RE = re.compile(r'`include\s+"([^"\n]+)"|`include\s+<([^>\n]+)>', re.IGNORECASE)
_PATH_FUNC_RE = re.compile(
    r"\$(fopen|readmemh|readmemb|writememh|writememb|dumpfile)\s*\(",
    re.IGNORECASE,
)


def strip_comments(text: str) -> str:
    """Blank out comments while keeping string literals and byte offsets intact."""

    def replace(match: re.Match[str]) -> str:
        chunk = match.group(0)
        if chunk.startswith('"'):
            return chunk
        return "".join("\n" if ch == "\n" else " " for ch in chunk)

    return _TOKEN_RE.sub(replace, text)


def _line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def _quoted_literal(text: str, start: int) -> str | None:
    index = start
    while index < len(text) and text[index] in " \t\r\n":
        index += 1
    if index >= len(text) or text[index] != '"':
        return None
    end = text.find('"', index + 1)
    if end == -1:
        return None
    return text[index + 1 : end]


def _resolve_checked(raw: str, base: Path) -> tuple[Path, bool]:
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = base / candidate
    try:
        resolved = candidate.resolve()
    except OSError:
        resolved = candidate
    return resolved, is_within(resolved, SETTINGS.workspace)


def scan_source_paths(sources: list[Path]) -> dict:
    """Reject simulation inputs that point at files outside the workspace.

    Scans `include and $fopen/$readmemh/$readmemb/$writememh/$writememb/$dumpfile.
    Literal paths that resolve outside the workspace are rejected. Non-literal
    expressions are reported as unresolved instead of being guessed.
    """
    findings: list[dict] = []
    rejected: list[dict] = []
    unresolved: list[dict] = []

    for source in sources:
        try:
            text = source.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            rejected.append({"file": str(source), "reason": f"unreadable: {exc}"})
            continue
        clean = strip_comments(text)

        entries: list[tuple[str, str, int]] = []
        for match in _INCLUDE_RE.finditer(clean):
            raw = match.group(1) or match.group(2) or ""
            entries.append(("include", raw, match.start()))
        for match in _PATH_FUNC_RE.finditer(clean):
            raw = _quoted_literal(clean, match.end())
            func = "$" + match.group(1).lower()
            if raw is None:
                unresolved.append(
                    {
                        "file": str(source),
                        "line": _line_of(clean, match.start()),
                        "kind": func,
                        "reason": "non-literal path expression; cannot verify statically",
                    }
                )
                continue
            entries.append((func, raw, match.start()))

        for kind, raw, index in entries:
            if not raw.strip():
                continue
            resolved, inside = _resolve_checked(raw, source.parent)
            item = {
                "file": str(source),
                "line": _line_of(clean, index),
                "kind": kind,
                "raw": raw,
                "resolved": str(resolved),
                "inside_workspace": inside,
            }
            findings.append(item)
            if not inside:
                rejected.append(item)

    return {
        "ok": not rejected,
        "workspace": str(SETTINGS.workspace),
        "scanned_files": [str(p) for p in sources],
        "findings": findings,
        "rejected": rejected,
        "unresolved": unresolved,
    }
