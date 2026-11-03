"""Read-only Session-1 desktop preflight for GUI operations."""
from __future__ import annotations

import ctypes
import json
import subprocess
from ctypes import wintypes
from typing import Any

BUILTIN_BLOCKED_FOREGROUND = frozenset({"endfield.exe", "hyp.exe"})
POWERSHELL = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
MIN_BATTERY_PERCENT = 20


def _object_name(handle: int | None) -> str | None:
    if not handle:
        return None
    user32 = ctypes.windll.user32
    buf = ctypes.create_unicode_buffer(256)
    needed = wintypes.DWORD()
    if user32.GetUserObjectInformationW(handle, 2, buf, ctypes.sizeof(buf), ctypes.byref(needed)):
        return buf.value
    return None


def _desktop_probe() -> dict[str, Any]:
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    input_desktop = user32.OpenInputDesktop(0, False, 0x0100)
    return {
        "window_station": _object_name(user32.GetProcessWindowStation()),
        "thread_desktop_name": _object_name(user32.GetThreadDesktop(kernel32.GetCurrentThreadId())),
        "input_desktop": _object_name(input_desktop),
        "process_session": int(kernel32.WTSGetActiveConsoleSessionId()),
    }


def _cim_probe(timeout_s: int = 20) -> dict[str, Any]:
    command = (
        "@{"
        "battery=@(Get-CimInstance Win32_Battery | ForEach-Object {[int]$_.BatteryStatus});"
        "charge=@(Get-CimInstance Win32_Battery | ForEach-Object {[int]$_.EstimatedChargeRemaining});"
        "explorer_session1=@((Get-Process -Name explorer -ErrorAction SilentlyContinue | Where-Object {$_.SessionId -eq 1}).Count);"
        "lockscreen_processes=@(Get-CimInstance Win32_Process | Where-Object {$_.Name -in @('LockApp.exe','LogonUI.exe') -and $_.SessionId -eq 1} | ForEach-Object {$_.Name})"
        "} | ConvertTo-Json -Compress"
    )
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        timeout=max(1, int(timeout_s)),
        creationflags=flags,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"CIM query failed rc={proc.returncode}: {(proc.stderr or proc.stdout).strip()[:300]}")
    raw = (proc.stdout or "").lstrip("\ufeff").strip()
    data = json.loads(raw) if raw else {}
    battery = data.get("battery") or []
    if isinstance(battery, int):
        battery = [battery]
    charge = data.get("charge") or []
    if isinstance(charge, int):
        charge = [charge]
    charge_values = [int(value) for value in charge if value is not None]
    lockscreen = data.get("lockscreen_processes") or []
    if isinstance(lockscreen, str):
        lockscreen = [lockscreen]
    explorer = data.get("explorer_session1")
    if isinstance(explorer, list):
        explorer = explorer[0] if explorer else 0
    return {
        "battery_status": [int(value) for value in battery],
        "charge_percent": min(charge_values) if charge_values else None,
        "explorer_session1": int(explorer or 0),
        "lockscreen_processes": list(lockscreen),
    }


def _foreground_probe() -> dict[str, Any]:
    import win32api
    import win32gui
    import win32process

    hwnd = int(win32gui.GetForegroundWindow() or 0)
    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    rect = list(win32gui.GetWindowRect(hwnd)) if hwnd else []
    monitor = list(win32api.GetMonitorInfo(win32api.MonitorFromWindow(hwnd, 2))["Monitor"]) if hwnd else []
    style = win32gui.GetWindowLong(hwnd, -16) if hwnd else 0
    class_name = win32gui.GetClassName(hwnd) if hwnd else ""
    covers = bool(rect and monitor and rect[0] <= monitor[0] and rect[1] <= monitor[1] and rect[2] >= monitor[2] and rect[3] >= monitor[3])
    shell = class_name in {"Progman", "WorkerW", "Shell_TrayWnd"}
    from . import ui

    return {
        "hwnd": hwnd,
        "pid": int(pid),
        "exe": ui._exe_name(int(pid)) or "unknown",
        "title": win32gui.GetWindowText(hwnd) if hwnd else "",
        "rect": rect,
        "monitor_rect": monitor,
        "fullscreen": bool(covers and not style & 0x00C00000 and not shell),
    }


def _clip_probe() -> dict[str, Any]:
    user32 = ctypes.windll.user32
    clip = wintypes.RECT()
    ok = bool(user32.GetClipCursor(ctypes.byref(clip)))
    left, top = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)
    screen = [left, top, left + user32.GetSystemMetrics(78), top + user32.GetSystemMetrics(79)]
    return {
        "ok": ok,
        "clip_rect": [clip.left, clip.top, clip.right, clip.bottom] if ok else None,
        "screen_rect": screen,
        "screen_resolution": [screen[2] - screen[0], screen[3] - screen[1]],
    }


def _normalise_blocked(extra: list[str] | None) -> set[str]:
    blocked = set(BUILTIN_BLOCKED_FOREGROUND)
    for name in extra or []:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("blocked_foreground entries must be non-empty strings")
        blocked.add(name.strip().lower())
    return blocked


def run(
    blocked_foreground: list[str] | None = None,
    screenshot: bool = False,
    label: str = "gui-doctor",
    timeout_s: int = 20,
    allow_battery: bool = False,
) -> dict[str, Any]:
    """Collect desktop facts without focusing, injecting input, or killing processes.

    ``allow_battery=True`` downgrades the AC-power check to a warning so GUI
    stages can run on a charged battery.  It never overrides a low charge: under
    ``MIN_BATTERY_PERCENT`` the preflight still refuses, with ``code`` naming the
    power reason instead of the generic ``gui_preflight_failed``.
    """
    warnings: list[str] = []
    try:
        desktop = _desktop_probe()
    except Exception as exc:
        desktop = {"window_station": None, "thread_desktop_name": None, "input_desktop": None, "process_session": None}
        warnings.append(f"desktop_probe: {type(exc).__name__}: {exc}")
    try:
        cim = _cim_probe(timeout_s)
    except Exception as exc:
        cim = {"battery_status": [], "charge_percent": None, "explorer_session1": 0, "lockscreen_processes": []}
        warnings.append(f"cim_probe: {type(exc).__name__}: {exc}")
    try:
        foreground = _foreground_probe()
    except Exception as exc:
        foreground = {"hwnd": None, "pid": None, "exe": "unknown", "title": "", "rect": [], "monitor_rect": [], "fullscreen": False}
        warnings.append(f"foreground_probe: {type(exc).__name__}: {exc}")
    try:
        input_state = _clip_probe()
    except Exception as exc:
        input_state = {"ok": False, "clip_rect": None, "screen_rect": None, "screen_resolution": None}
        warnings.append(f"clip_probe: {type(exc).__name__}: {exc}")

    blocked = _normalise_blocked(blocked_foreground)
    foreground_exe = str(foreground.get("exe") or "").lower()
    input_desktop = desktop.get("input_desktop")
    lockscreen = bool(cim.get("lockscreen_processes")) or input_desktop != "Default"
    clip_rect = input_state.get("clip_rect")
    screen_rect = input_state.get("screen_rect")
    charge = cim.get("charge_percent")
    on_battery = bool(cim.get("battery_status")) and not all(
        value == 2 for value in cim["battery_status"]
    )
    allowed_by: str | None = None
    power_block: str | None = None
    if allow_battery and on_battery:
        if charge is None:
            power_block = "battery_charge_unknown"
            warnings.append(
                "allow_battery was requested but the charge level could not be read; "
                "GUI stages are not started on an unknown battery level"
            )
        elif charge < MIN_BATTERY_PERCENT:
            power_block = "battery_too_low"
            warnings.append(
                f"allow_battery was requested but the battery is at {charge}% "
                f"(< {MIN_BATTERY_PERCENT}%); GUI stages are not started"
            )
        else:
            allowed_by = "allow_battery"
            warnings.append(
                f"running on battery at {charge}% because allow_battery=True; "
                "keep the charger handy"
            )
    checks = {
        "battery": bool(cim.get("battery_status")) and all(value == 2 for value in cim["battery_status"]),
        "explorer_session1": cim.get("explorer_session1") == 1,
        "input_desktop": input_desktop == "Default",
        "lockscreen": not lockscreen,
        "fullscreen": foreground.get("fullscreen") is False,
        "clip": bool(input_state.get("ok")) and clip_rect in ([], screen_rect),
        "foreground": bool(foreground.get("pid")) and foreground_exe not in blocked,
        "session": desktop.get("process_session") == 1,
    }
    failed_checks = [name for name, passed in checks.items() if not passed]
    if allowed_by and "battery" in failed_checks:
        failed_checks = [name for name in failed_checks if name != "battery"]
    if failed_checks:
        code = power_block or "gui_preflight_failed"
    else:
        code = None
    error = None
    if power_block == "battery_too_low":
        error = f"battery at {charge}% is below the {MIN_BATTERY_PERCENT}% floor; connect AC power"
    elif power_block == "battery_charge_unknown":
        error = "battery level could not be read; connect AC power or retry once CIM reports a charge"
    payload: dict[str, Any] = {
        "ok": not failed_checks,
        "code": code,
        "error": error,
        "session": {
            "process_session": desktop.get("process_session"),
            "explorer_session1": cim.get("explorer_session1"),
        },
        "battery": {
            "status": cim.get("battery_status", []),
            "ac": checks["battery"],
            "charge_percent": charge,
        },
        "power": {
            "on_battery": on_battery,
            "allowed_by": allowed_by,
            "charge_percent": charge,
            "min_charge_percent": MIN_BATTERY_PERCENT,
        },
        "desktop": {
            "window_station": desktop.get("window_station"),
            "thread_desktop_name": desktop.get("thread_desktop_name"),
            "input_desktop": input_desktop,
            "lockscreen": int(lockscreen),
            "lockscreen_processes": cim.get("lockscreen_processes", []),
        },
        "foreground": {**foreground, "blocked": foreground_exe in blocked, "blocked_set": sorted(blocked)},
        "input": input_state,
        "checks": checks,
        "failed_checks": failed_checks,
        "warnings": warnings,
        "screenshot": None,
    }
    if screenshot:
        try:
            from . import driver
            payload["screenshot"] = driver.shot(label)
        except Exception as exc:
            payload["warnings"].append(f"screenshot: {type(exc).__name__}: {exc}")
    return payload
