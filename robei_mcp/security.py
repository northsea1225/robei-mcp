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


def _strip_base_prefix(value: Path, base: Path) -> Path | None:
    """Drop a leading ``<base.name>/`` from a relative path.

    Matching is case-insensitive and accepts both ``/`` and ``\\``, so
    ``Workspace\\proj\\a.v`` and ``workspace/proj/a.v`` both resolve the same
    way.  The remainder is still resolved and escape-checked by the caller.
    """
    raw = str(value)
    match = re.match(rf"^(?i:{re.escape(base.name)})[\\/]+(.+)$", raw)
    if not match:
        return None
    rest = match.group(1).replace("\\", "/")
    if rest.strip() in {"", ".", ".."}:
        return None
    return Path(rest)


def _base_readings(value: str | Path, base: Path) -> list[tuple[Path, Path | None]]:
    """``(preferred, plain)`` readings for one base directory.

    ``preferred`` has the redundant ``workspace/`` prefix removed; ``plain`` is
    kept only to detect a genuine ``<workspace>/workspace/`` child.
    """
    candidate = Path(value)
    if candidate.is_absolute():
        return [(candidate, None)]
    stripped = _strip_base_prefix(candidate, base)
    if stripped is None:
        return [(base / candidate, None)]
    return [(base / stripped, base / candidate)]


def _ambiguous(value: str | Path, preferred: Path, plain: Path | None) -> None:
    if plain is None:
        return
    try:
        fallback = plain.resolve()
    except OSError:
        return
    if preferred == fallback:
        return
    if preferred.exists() and fallback.exists():
        raise ValueError(
            f"ambiguous path {str(value)!r}: both {preferred} and {fallback} exist; "
            "drop the leading workspace/ prefix or rename one of them"
        )


def resolve_workspace_path(value: str | Path, *, must_exist: bool = True) -> Path:
    for preferred, plain in _base_readings(value, SETTINGS.workspace):
        resolved = preferred.resolve()
        if not is_within(resolved, SETTINGS.workspace):
            raise ValueError(f"path escapes workspace: {resolved}")
        _ambiguous(value, resolved, plain)
        if not must_exist:
            return resolved
        if resolved.is_file():
            return resolved
    raise FileNotFoundError(f"file not found in workspace: {value}")


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
    roots = (SETTINGS.workspace, *SETTINGS.model_roots)
    if candidate.is_absolute():
        candidates: list[tuple[Path, Path | None]] = [(candidate, None)]
    else:
        candidates = []
        for base in roots:
            candidates.extend(_base_readings(candidate, base))

    for item, plain in candidates:
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
        _ambiguous(value, resolved, plain)
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
    roots = (SETTINGS.workspace, *SETTINGS.model_roots, SETTINGS.robei_home / "project")
    readings: list[tuple[Path, Path | None]] = (
        [(candidate, None)] if candidate.is_absolute()
        else [pair for base in roots for pair in _base_readings(candidate, base)]
    )
    resolved_paths: list[Path] = []
    for path, plain in readings:
        try:
            item = path.resolve()
        except OSError:
            continue
        if not any(is_within(item, root) for root in roots):
            raise ValueError(f"project path escapes allowed roots: {item}")
        _ambiguous(value, item, plain)
        if item.suffix.lower() != ".proj":
            raise ValueError("project file must have .proj extension")
        resolved_paths.append(item)
        if item.is_file():
            return item
    if not resolved_paths:
        raise FileNotFoundError(f"project not found: {candidate}")
    raise FileNotFoundError(f"project not found: {resolved_paths[0]}")


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


def _scan_text(
    source: Path,
    text: str,
    visited: set[Path],
    findings: list[dict],
    rejected: list[dict],
    unresolved: list[dict],
    strict: bool,
    depth: int,
) -> None:
    """Scan one source and every `include it pulls in from the workspace."""
    clean = strip_comments(text)
    entries: list[tuple[str, str, int]] = []
    for match in _INCLUDE_RE.finditer(clean):
        entries.append(("include", match.group(1) or match.group(2) or "", match.start()))
    for match in _PATH_FUNC_RE.finditer(clean):
        raw = _quoted_literal(clean, match.end())
        func = "$" + match.group(1).lower()
        if raw is None:
            item = {
                "file": str(source),
                "line": _line_of(clean, match.start()),
                "kind": func,
                "reason": "non-literal path expression; cannot verify statically",
            }
            unresolved.append(item)
            if strict:
                rejected.append({**item, "reason": item["reason"] + " (strict mode)"})
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
            continue
        if kind != "include":
            continue
        if depth >= 8:
            unresolved.append({**item, "reason": "include nesting deeper than 8 levels"})
            continue
        if not resolved.is_file():
            unresolved.append({**item, "reason": "included file not found next to the including source"})
            continue
        if resolved in visited:
            continue
        visited.add(resolved)
        try:
            if resolved.stat().st_size > SETTINGS.max_source_bytes:
                unresolved.append({**item, "reason": "included file exceeds max_source_bytes"})
                continue
            nested = resolved.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            unresolved.append({**item, "reason": f"unreadable: {exc}"})
            continue
        _scan_text(resolved, nested, visited, findings, rejected, unresolved, strict, depth + 1)


def scan_source_paths(sources: list[Path], strict: bool | None = None) -> dict:
    """Reject simulation inputs that point at files outside the workspace.

    Scans `include and $fopen/$readmemh/$readmemb/$writememh/$writememb/$dumpfile
    in the given files *and* in every workspace-local file they include.
    Non-literal expressions cannot be verified statically; strict mode (the
    default) rejects them instead of compiling code it could not inspect.
    """
    strict = SETTINGS.strict_path_scan if strict is None else strict
    findings: list[dict] = []
    rejected: list[dict] = []
    unresolved: list[dict] = []
    visited: set[Path] = {path.resolve() for path in sources}

    for source in sources:
        try:
            text = source.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            rejected.append({"file": str(source), "reason": f"unreadable: {exc}"})
            continue
        _scan_text(source, text, visited, findings, rejected, unresolved, strict, 0)

    return {
        "ok": not rejected,
        "strict": strict,
        "workspace": str(SETTINGS.workspace),
        "scanned_files": [str(path) for path in sorted(visited)],
        "findings": findings,
        "rejected": rejected,
        "unresolved": unresolved,
    }
