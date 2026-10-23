from __future__ import annotations

import ctypes
import os
import shutil

try:  # ctypes.wintypes only exists on Windows
    import ctypes.wintypes as wintypes
except Exception:  # pragma: no cover - non-Windows hosts
    wintypes = None
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


IS_WINDOWS = os.name == "nt"
POSIX_BIN_DIRS = (Path("/usr/bin"), Path("/usr/local/bin"), Path("/opt/homebrew/bin"), Path("/opt/local/bin"))

# Fallback directories for a standalone (non-Robei) toolchain install.
WINDOWS_TOOL_DIRS = (
    Path(r"C:\msys64\ucrt64\bin"),
    Path(r"C:\msys64\mingw64\bin"),
    Path(r"C:\ProgramData\chocolatey\bin"),
)

# Robei may be installed on any drive; these are only guesses, never requirements.
ROBEI_HOME_DRIVES = "CDEFGHIJ"


def _which(name: str) -> Path | None:
    """PATH lookup so a standalone iverilog/yosys install works without Robei."""
    names = (f"{name}.exe", name) if IS_WINDOWS else (name,)
    for candidate in names:
        hit = shutil.which(candidate)
        if hit:
            return Path(hit)
    for directory in WINDOWS_TOOL_DIRS:
        suffix = ".exe" if IS_WINDOWS else ""
        candidate = directory / f"{name}{suffix}"
        if candidate.is_file():
            return candidate
    return None


def _tool_default(name: str, bin_dir: Path) -> Path:
    """Prefer the copy Robei ships, then PATH, then a known install dir."""
    exe = f"{name}.exe" if IS_WINDOWS else name
    bundled = bin_dir / exe
    if bundled.is_file():
        return bundled
    found = _which(name)
    if found is not None:
        return found
    if not IS_WINDOWS:
        for directory in POSIX_BIN_DIRS:
            candidate = directory / name
            if candidate.is_file():
                return candidate
        return POSIX_BIN_DIRS[0] / name
    return bundled  # stays missing; runtime_status reports it as absent


def _robei_home_candidates() -> tuple[Path, ...]:
    out: list[Path] = []
    for var in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        base = os.environ.get(var)
        if base:
            out.append(Path(base) / "Robei")
    if IS_WINDOWS:
        for letter in ROBEI_HOME_DRIVES:
            out.append(Path(f"{letter}:\\Robei"))
    return tuple(dict.fromkeys(out))


def _find_robei_home() -> Path | None:
    """Locate the IDE install root without hardcoding one machine's layout."""
    candidates = _robei_home_candidates()
    for candidate in candidates:
        if (candidate / "bin" / "Robei.exe").is_file():
            return candidate
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    exe = _which("Robei")
    if exe is not None and exe.parent.name.lower() == "bin":
        return exe.parent.parent
    return None


def _default_model_roots(workspace: Path) -> tuple[Path, ...]:
    raw = os.environ.get("ROBEI_MODEL_ROOTS")
    if raw:
        return tuple(Path(p).expanduser() for p in raw.split(os.pathsep) if p.strip())
    if IS_WINDOWS:
        return (Path(r"C:\ProgramData\Robei"), workspace)
    return (workspace,)


def _first_existing(candidates: tuple[Path, ...]) -> Path | None:
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


VERILATOR_CANDIDATES = (
    Path(r"C:\msys64\ucrt64\bin\verilator_bin.exe"),
    Path(r"C:\msys64\ucrt64\bin\verilator.exe"),
    Path(r"C:\msys64\mingw64\bin\verilator_bin.exe"),
    Path("/usr/bin/verilator_bin"),
    Path("/usr/bin/verilator"),
    Path("/opt/homebrew/bin/verilator"),
    Path("/usr/local/bin/verilator"),
)

VERILATOR_ROOT_CANDIDATES = (
    Path(r"C:\msys64\ucrt64\share\verilator"),
    Path(r"C:\msys64\mingw64\share\verilator"),
)


def _default_gui_exe(robei_home: Path) -> Path:
    """Prefer a sandbox copy of the IDE so the install tree stays untouched."""
    for root in (Path(r"D:\robei-lab\robei"), Path(r"C:\robei-lab\robei")):
        candidate = root / "bin" / "Robei.exe"
        if candidate.is_file():
            return candidate
    return robei_home / "bin" / "Robei.exe"


@dataclass(frozen=True)
class Settings:
    project_root: Path
    workspace: Path
    robei_home: Path
    robei_exe: Path
    robei_gui_exe: Path
    iverilog: Path
    vvp: Path
    yosys: Path
    verilator: Path
    verilator_root: Path
    model_roots: tuple[Path, ...]
    default_timeout_s: int = 30
    max_timeout_s: int = 120
    max_source_bytes: int = 2_000_000
    max_model_bytes: int = 5_000_000
    max_vcd_bytes: int = 50_000_000
    max_output_chars: int = 20_000
    config_file: Path | None = None
    config_error: str | None = None
    sources: dict[str, str] = field(default_factory=dict)


CONFIG_ENV = "ROBEI_MCP_CONFIG"
CONFIG_NAME = "robei.toml"


def config_path() -> Path:
    raw = os.environ.get(CONFIG_ENV)
    if raw:
        return Path(raw).expanduser().resolve()
    return (PROJECT_ROOT / CONFIG_NAME).resolve()


def _read_config(path: Path) -> tuple[dict, str | None]:
    """Return (data, error). Needs tomllib (3.11+) or tomli; env vars always work."""
    if not path.is_file():
        return {}, None
    try:
        import tomllib  # type: ignore[import-not-found]
    except ModuleNotFoundError:  # pragma: no cover - python 3.10 only
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ModuleNotFoundError:
            return {}, "python < 3.11 has no tomllib and tomli is not installed; using env vars and defaults"
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except Exception as exc:  # malformed toml must not take the server down
        return {}, f"{type(exc).__name__}: {exc}"
    return data if isinstance(data, dict) else {}, None


def _pick(
    env_name: str,
    data: dict,
    section: str,
    key: str,
    default: Path,
    base: Path,
) -> tuple[Path, str]:
    raw = os.environ.get(env_name)
    if raw:
        return Path(raw).expanduser(), "env"
    value = (data.get(section) or {}).get(key) if isinstance(data.get(section), dict) else None
    if isinstance(value, str) and value.strip():
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = base / candidate
        return candidate, "toml"
    return default, "default"


def _pick_int(env_name: str, data: dict, section: str, key: str, default: int) -> tuple[int, str]:
    raw = os.environ.get(env_name)
    if raw:
        try:
            return int(raw), "env"
        except ValueError:
            pass
    value = (data.get(section) or {}).get(key) if isinstance(data.get(section), dict) else None
    if isinstance(value, int) and not isinstance(value, bool):
        return value, "toml"
    return default, "default"


def _pick_discovered(
    env_name: str,
    data: dict,
    section: str,
    key: str,
    base: Path,
    discover,
    fallback: Path,
) -> tuple[Path, str]:
    """env > toml > auto-discovery > built-in fallback."""
    raw = os.environ.get(env_name)
    if raw:
        return Path(raw).expanduser(), "env"
    value = (data.get(section) or {}).get(key) if isinstance(data.get(section), dict) else None
    if isinstance(value, str) and value.strip():
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = base / candidate
        return candidate, "toml"
    found = discover()
    if found is not None:
        return found, "discovered"
    return fallback, "default"


def _pick_roots(env_name: str, data: dict, section: str, key: str, default: tuple[Path, ...], base: Path) -> tuple[tuple[Path, ...], str]:
    raw = os.environ.get(env_name)
    if raw:
        return tuple(Path(p).expanduser() for p in raw.split(os.pathsep) if p.strip()), "env"
    value = (data.get(section) or {}).get(key) if isinstance(data.get(section), dict) else None
    if isinstance(value, list) and value:
        roots = []
        for item in value:
            if isinstance(item, str) and item.strip():
                candidate = Path(item).expanduser()
                if not candidate.is_absolute():
                    candidate = base / candidate
                roots.append(candidate)
        if roots:
            return tuple(roots), "toml"
    return default, "default"


def load_settings() -> Settings:
    path = config_path()
    data, error = _read_config(path)
    base = path.parent
    sources: dict[str, str] = {}

    workspace, sources["workspace"] = _pick(
        "ROBEI_MCP_WORKSPACE", data, "paths", "workspace", PROJECT_ROOT / "workspace", base
    )
    workspace = workspace.resolve()
    robei_home, sources["robei_home"] = _pick_discovered(
        "ROBEI_HOME",
        data,
        "paths",
        "robei_home",
        base,
        _find_robei_home,
        Path(r"E:\Robei") if IS_WINDOWS else Path("/opt/robei"),
    )
    robei_home = robei_home.resolve()
    bin_dir = robei_home / "bin"
    verilator_default = _first_existing(VERILATOR_CANDIDATES) or VERILATOR_CANDIDATES[0]
    verilator_root_default = _first_existing(VERILATOR_ROOT_CANDIDATES) or VERILATOR_ROOT_CANDIDATES[0]

    robei_exe, sources["robei_exe"] = _pick("ROBEI_EXE", data, "paths", "robei_exe", bin_dir / "Robei.exe", base)
    gui_exe, sources["robei_gui_exe"] = _pick(
        "ROBEI_GUI_EXE", data, "paths", "gui_exe", _default_gui_exe(robei_home), base
    )
    iverilog, sources["iverilog"] = _pick(
        "IVERILOG_EXE", data, "paths", "iverilog", _tool_default("iverilog", bin_dir), base
    )
    vvp, sources["vvp"] = _pick("VVP_EXE", data, "paths", "vvp", _tool_default("vvp", bin_dir), base)
    yosys, sources["yosys"] = _pick("YOSYS_EXE", data, "paths", "yosys", _tool_default("yosys", bin_dir), base)
    verilator, sources["verilator"] = _pick("VERILATOR_EXE", data, "paths", "verilator", verilator_default, base)
    verilator_root, sources["verilator_root"] = _pick(
        "VERILATOR_ROOT_DIR", data, "paths", "verilator_root", verilator_root_default, base
    )
    model_roots, sources["model_roots"] = _pick_roots(
        "ROBEI_MODEL_ROOTS", data, "paths", "model_roots", _default_model_roots(workspace), base
    )
    default_timeout, sources["default_timeout_s"] = _pick_int(
        "ROBEI_DEFAULT_TIMEOUT_S", data, "limits", "default_timeout_s", 30
    )
    max_timeout, sources["max_timeout_s"] = _pick_int(
        "ROBEI_MAX_TIMEOUT_S", data, "limits", "max_timeout_s", 120
    )
    default_timeout = max(1, min(default_timeout, 3600))
    max_timeout = max(default_timeout, min(max_timeout, 3600))
    return Settings(
        project_root=PROJECT_ROOT,
        workspace=workspace,
        robei_home=robei_home,
        robei_exe=robei_exe.resolve(),
        robei_gui_exe=gui_exe.resolve(),
        iverilog=iverilog.resolve(),
        vvp=vvp.resolve(),
        yosys=yosys.resolve(),
        verilator=verilator,
        verilator_root=verilator_root,
        model_roots=model_roots,
        default_timeout_s=default_timeout,
        max_timeout_s=max_timeout,
        config_file=path,
        config_error=error,
        sources=sources,
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
