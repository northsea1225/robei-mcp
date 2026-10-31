from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from robei_mcp.errors import GuiNeedsActiveDesktop
from robei_mcp.gui_agent import ui


def test_foreground_set_false_uses_attach_retry(monkeypatch):
    calls = []
    switched = iter([False, True])
    fake_gui = SimpleNamespace(
        SetForegroundWindow=lambda hwnd: calls.append(hwnd) or False,
    )
    monkeypatch.setitem(sys.modules, "win32gui", fake_gui)
    monkeypatch.setattr(ui, "main_window", lambda: (object(), {"hwnd": 42}))
    monkeypatch.setattr(
        ui,
        "_foreground_info",
        lambda: {"foreground_hwnd": 42, "foreground_pid": 7, "foreground_exe": "Robei.exe"},
    )
    monkeypatch.setattr(ui, "_wait_foreground", lambda hwnd, timeout_s=3.0: next(switched))
    attached = []

    def attach(hwnd):
        attached.append(hwnd)
        calls.append(hwnd)
        return True

    monkeypatch.setattr(ui, "_attach_thread_input", attach)
    result = ui._foreground()
    assert result["ok"] is True
    assert result["foreground_pid"] == 7
    assert result["foreground_exe"] == "Robei.exe"
    assert attached == [42]
    assert calls == [42, 42]


def test_foreground_call_succeeds_but_window_never_switches(monkeypatch):
    fake_gui = SimpleNamespace(SetForegroundWindow=lambda hwnd: True)
    monkeypatch.setitem(sys.modules, "win32gui", fake_gui)
    monkeypatch.setattr(ui, "main_window", lambda: (object(), {"hwnd": 42}))
    monkeypatch.setattr(
        ui,
        "_foreground_info",
        lambda: {"foreground_hwnd": 9, "foreground_pid": 99, "foreground_exe": "other.exe"},
    )
    monkeypatch.setattr(ui, "_wait_foreground", lambda hwnd, timeout_s=3.0: False)
    monkeypatch.setattr(ui, "_attach_thread_input", lambda hwnd: False)
    monkeypatch.setattr(ui, "_simulate_alt", lambda: False)
    result = ui._foreground()
    assert result["ok"] is False
    assert result["foreground_pid"] == 99
    assert result["foreground_exe"] == "other.exe"
    assert "foreground window did not switch" in result["error"]


def test_foreground_switch_success_skips_attach(monkeypatch):
    fake_gui = SimpleNamespace(SetForegroundWindow=lambda hwnd: True)
    monkeypatch.setitem(sys.modules, "win32gui", fake_gui)
    monkeypatch.setattr(ui, "main_window", lambda: (object(), {"hwnd": 42}))
    monkeypatch.setattr(
        ui,
        "_foreground_info",
        lambda: {"foreground_hwnd": 42, "foreground_pid": 7, "foreground_exe": "Robei.exe"},
    )
    monkeypatch.setattr(ui, "_wait_foreground", lambda hwnd, timeout_s=3.0: True)
    attach = SimpleNamespace(called=False)

    def unexpected_attach(hwnd):
        attach.called = True
        return True

    monkeypatch.setattr(ui, "_attach_thread_input", unexpected_attach)
    result = ui._foreground()
    assert result["ok"] is True
    assert result["attached"] is False
    assert attach.called is False


def test_mouse_retries_only_after_confirmed_focus(monkeypatch):
    class FakeMouse:
        def __init__(self):
            self.calls = 0

        def click(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("active desktop refused input")

    mouse = FakeMouse()
    monkeypatch.setitem(sys.modules, "pywinauto", SimpleNamespace(mouse=mouse))
    monkeypatch.setattr(
        ui,
        "_foreground",
        lambda: {"ok": True, "foreground_pid": 7, "foreground_exe": "Robei.exe"},
    )
    monkeypatch.setattr(ui.time, "sleep", lambda delay: None)
    ui._mouse("click", (10, 10))
    assert mouse.calls == 2


def test_mouse_focus_failure_reports_identity_without_retry(monkeypatch):
    class FakeMouse:
        def __init__(self):
            self.calls = 0

        def click(self, **kwargs):
            self.calls += 1
            raise RuntimeError("active desktop refused input")

    mouse = FakeMouse()
    monkeypatch.setitem(sys.modules, "pywinauto", SimpleNamespace(mouse=mouse))
    monkeypatch.setattr(
        ui,
        "_foreground",
        lambda: {
            "ok": False,
            "foreground_pid": 99,
            "foreground_exe": "BatteryWidgetHost.exe",
        },
    )
    with pytest.raises(GuiNeedsActiveDesktop, match=r"foreground_pid=99.*BatteryWidgetHost\.exe"):
        ui._mouse("click", (10, 10))
    assert mouse.calls == 1
