from __future__ import annotations

from robei_mcp.gui_agent import doctor


def _patch(monkeypatch, *, battery=(2,), explorer=1, desktop="Default", lockscreen=(), fullscreen=False, clip=None, exe="explorer.exe"):
    monkeypatch.setattr(
        doctor,
        "_cim_probe",
        lambda timeout_s=20: {
            "battery_status": list(battery),
            "explorer_session1": explorer,
            "lockscreen_processes": list(lockscreen),
        },
    )
    monkeypatch.setattr(
        doctor,
        "_desktop_probe",
        lambda: {
            "window_station": "WinSta0",
            "thread_desktop_name": "Default",
            "input_desktop": desktop,
            "process_session": 1,
        },
    )
    monkeypatch.setattr(
        doctor,
        "_foreground_probe",
        lambda: {
            "hwnd": 1,
            "pid": 2,
            "exe": exe,
            "title": "",
            "rect": [0, 0, 100, 100],
            "monitor_rect": [0, 0, 100, 100],
            "fullscreen": fullscreen,
        },
    )
    monkeypatch.setattr(
        doctor,
        "_clip_probe",
        lambda: {
            "ok": True,
            "clip_rect": [0, 0, 2560, 1440] if clip is None else clip,
            "screen_rect": [0, 0, 2560, 1440],
            "screen_resolution": [2560, 1440],
        },
    )


def test_gui_doctor_passes_and_keeps_builtin_blocklist(monkeypatch):
    _patch(monkeypatch)
    result = doctor.run(blocked_foreground=["custom.exe"])
    assert result["ok"] is True
    assert result["code"] is None
    assert result["failed_checks"] == []
    assert {"endfield.exe", "hyp.exe", "custom.exe"}.issubset(result["foreground"]["blocked_set"])


def test_battery_missing_fails(monkeypatch):
    _patch(monkeypatch, battery=())
    result = doctor.run()
    assert result["ok"] is False
    assert result["code"] == "gui_preflight_failed"
    assert "battery" in result["failed_checks"]


def test_battery_not_all_two_fails(monkeypatch):
    _patch(monkeypatch, battery=(2, 1))
    result = doctor.run()
    assert "battery" in result["failed_checks"]


def test_explorer_session_must_equal_one(monkeypatch):
    _patch(monkeypatch, explorer=0)
    result = doctor.run()
    assert "explorer_session1" in result["failed_checks"]


def test_input_desktop_must_be_default(monkeypatch):
    _patch(monkeypatch, desktop="Winlogon")
    result = doctor.run()
    assert "input_desktop" in result["failed_checks"]
    assert "lockscreen" in result["failed_checks"]


def test_lockapp_process_fails(monkeypatch):
    _patch(monkeypatch, lockscreen=("LockApp.exe",))
    result = doctor.run()
    assert "lockscreen" in result["failed_checks"]


def test_foreground_fullscreen_fails(monkeypatch):
    _patch(monkeypatch, fullscreen=True)
    result = doctor.run()
    assert "fullscreen" in result["failed_checks"]


def test_clip_must_be_screen(monkeypatch):
    _patch(monkeypatch, clip=[10, 10, 100, 100])
    result = doctor.run()
    assert "clip" in result["failed_checks"]


def test_builtin_hyp_cannot_be_removed_from_blocklist(monkeypatch):
    _patch(monkeypatch, exe="HYP.exe")
    result = doctor.run(blocked_foreground=[])
    assert result["foreground"]["blocked"] is True
    assert "foreground" in result["failed_checks"]


def test_custom_foreground_blocklist_is_additive(monkeypatch):
    _patch(monkeypatch, exe="CustomGame.exe")
    result = doctor.run(blocked_foreground=["customgame.exe"])
    assert result["foreground"]["blocked"] is True
    assert "foreground" in result["failed_checks"]


def test_screenshot_is_optional_and_failure_is_warning(monkeypatch):
    _patch(monkeypatch)
    monkeypatch.setattr(doctor, "_normalise_blocked", lambda extra: set(doctor.BUILTIN_BLOCKED_FOREGROUND))
    monkeypatch.setitem(__import__("sys").modules, "PIL", object())
    monkeypatch.setattr(doctor, "_clip_probe", lambda: {"ok": True, "clip_rect": [0, 0, 2560, 1440], "screen_rect": [0, 0, 2560, 1440], "screen_resolution": [2560, 1440]})
    result = doctor.run(screenshot=False)
    assert result["screenshot"] is None
    assert result["ok"] is True
