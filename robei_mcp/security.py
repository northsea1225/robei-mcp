from __future__ import annotations

import os
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
        if resolved.suffix.lower() != ".model":
            continue
        if not any(is_within(resolved, root) for root in roots):
            continue
        if resolved.is_file():
            if resolved.stat().st_size > SETTINGS.max_model_bytes:
                raise ValueError("model file exceeds size limit")
            return resolved
    raise FileNotFoundError(
        "model not found under allowed roots: " + "; ".join(str(r) for r in roots)
    )
