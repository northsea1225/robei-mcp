from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _env_path(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value).expanduser() if value else default


def _default_model_roots(workspace: Path) -> tuple[Path, ...]:
    raw = os.environ.get("ROBEI_MODEL_ROOTS")
    if raw:
        return tuple(Path(p).expanduser() for p in raw.split(os.pathsep) if p.strip())
    return (Path(r"C:\ProgramData\Robei"), workspace)


@dataclass(frozen=True)
class Settings:
    project_root: Path
    workspace: Path
    robei_home: Path
    robei_exe: Path
    iverilog: Path
    vvp: Path
    yosys: Path
    model_roots: tuple[Path, ...]
    default_timeout_s: int = 30
    max_timeout_s: int = 120
    max_source_bytes: int = 2_000_000
    max_model_bytes: int = 5_000_000
    max_vcd_bytes: int = 50_000_000
    max_output_chars: int = 20_000


def load_settings() -> Settings:
    workspace = _env_path("ROBEI_MCP_WORKSPACE", PROJECT_ROOT / "workspace").resolve()
    robei_home = _env_path("ROBEI_HOME", Path(r"E:\Robei")).resolve()
    bin_dir = robei_home / "bin"
    return Settings(
        project_root=PROJECT_ROOT,
        workspace=workspace,
        robei_home=robei_home,
        robei_exe=_env_path("ROBEI_EXE", bin_dir / "Robei.exe").resolve(),
        iverilog=_env_path("IVERILOG_EXE", bin_dir / "iverilog.exe").resolve(),
        vvp=_env_path("VVP_EXE", bin_dir / "vvp.exe").resolve(),
        yosys=_env_path("YOSYS_EXE", bin_dir / "yosys.exe").resolve(),
        model_roots=_default_model_roots(workspace),
    )


SETTINGS = load_settings()
SETTINGS.workspace.mkdir(parents=True, exist_ok=True)


def _query_fixed_file_version(path: Path) -> str | None:
    if os.name != "nt" or not path.is_file():
        return None
    try:
        version = ctypes.WinDLL("version")
        version.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
        version.GetFileVersionInfoSizeW.restype = wintypes.DWORD
        version.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID]
        version.GetFileVersionInfoW.restype = wintypes.BOOL
        version.VerQueryValueW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR, ctypes.POINTER(wintypes.LPVOID), ctypes.POINTER(wintypes.UINT)]
        version.VerQueryValueW.restype = wintypes.BOOL

        size = version.GetFileVersionInfoSizeW(str(path), None)
        if not size:
            return None
        buf = ctypes.create_string_buffer(size)
        if not version.GetFileVersionInfoW(str(path), 0, size, buf):
            return None
        ptr = wintypes.LPVOID()
        length = wintypes.UINT()
        if not version.VerQueryValueW(buf, "\\", ctypes.byref(ptr), ctypes.byref(length)):
            return None
        if not ptr.value:
            return None

        class VS_FIXEDFILEINFO(ctypes.Structure):
            _fields_ = [
                ("dwSignature", wintypes.DWORD),
                ("dwStrucVersion", wintypes.DWORD),
                ("dwFileVersionMS", wintypes.DWORD),
                ("dwFileVersionLS", wintypes.DWORD),
                ("dwProductVersionMS", wintypes.DWORD),
                ("dwProductVersionLS", wintypes.DWORD),
                ("dwFileFlagsMask", wintypes.DWORD),
                ("dwFileFlags", wintypes.DWORD),
                ("dwFileOS", wintypes.DWORD),
                ("dwFileType", wintypes.DWORD),
                ("dwFileSubtype", wintypes.DWORD),
                ("dwFileDateMS", wintypes.DWORD),
                ("dwFileDateLS", wintypes.DWORD),
            ]

        info = ctypes.cast(ptr, ctypes.POINTER(VS_FIXEDFILEINFO)).contents
        ms, ls = info.dwFileVersionMS, info.dwFileVersionLS
        return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
    except Exception:
        return None


def file_version(path: Path) -> str | None:
    return _query_fixed_file_version(path)
