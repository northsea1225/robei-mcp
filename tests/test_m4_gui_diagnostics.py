"""Desktop failures must remain diagnostic, never look like missing models."""
from __future__ import annotations

import runpy
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from robei_mcp.errors import GuiNeedsActiveDesktop
from robei_mcp.gui_agent import ui


def test_stage12_missing_fixture_points_to_stage11():
    ns = runpy.run_path(str(Path(__file__).with_name('stage12_ops.py')))
    entry = {'tests': ['tb_counter4']}
    assert '先跑 stage11' in ns['_fixture_hint'](entry)
    assert entry == {'tests': ['tb_counter4']}
    assert ns['_fixture_hint']({'tests': ['tb_counter4', 'tb_counter4_mcp']}) is None


def test_compile_all_propagates_desktop_failure(monkeypatch):
    failure = GuiNeedsActiveDesktop('desktop refused Files tree input')
    monkeypatch.setattr(ui, 'main_window', lambda: (object(), {'hwnd': 7}))
    monkeypatch.setattr(ui, '_require_tree_input', lambda win: None, raising=False)
    monkeypatch.setattr(ui, '_file_tree', lambda dlg: object())
    monkeypatch.setattr(ui, '_tree_entries', lambda tree: [{'text': 'model', 'state': 0, 'item': object()}])
    monkeypatch.setattr(ui.time, 'sleep', lambda delay: None)

    def fail(item):
        raise failure

    monkeypatch.setattr(ui, '_expand_tree_item', fail)
    with pytest.raises(GuiNeedsActiveDesktop) as caught:
        ui.compile_all()
    assert caught.value is failure


def good_state():
    return {'foreground_hwnd': 7, 'foreground_process': 'Robei.exe',
            'fullscreen_app': False, 'clip_rect': [0, 0, 800, 600],
            'screen_rect': [0, 0, 800, 600]}


@pytest.mark.parametrize('updates,match', [
    ({'foreground_hwnd': 8, 'foreground_process': 'other.exe', 'fullscreen_app': True}, 'fullscreen'),
    ({'clip_rect': [400, 300, 400, 300]}, 'clip_rect'),
    ({'clip_rect': [0, 0, 400, 300]}, 'clip_rect'),
])
def test_tree_input_rejects_unusable_desktop(monkeypatch, updates, match):
    state = {**good_state(), **updates}
    monkeypatch.setattr(ui, '_tree_input_state', lambda: state, raising=False)
    with pytest.raises(GuiNeedsActiveDesktop, match=match):
        ui._require_tree_input({'hwnd': 7})


@pytest.mark.parametrize('updates', [
    {},
    {'fullscreen_app': True},  # Robei itself may occupy the whole screen.
    {'foreground_hwnd': 8, 'foreground_process': 'BatteryWidgetHost.exe'},
])
def test_tree_input_accepts_nonblocking_desktop(monkeypatch, updates):
    monkeypatch.setattr(ui, '_tree_input_state', lambda: {**good_state(), **updates}, raising=False)
    ui._require_tree_input({'hwnd': 7})


@pytest.mark.parametrize('operation', ['open_model', 'compile_all'])
def test_tree_gate_runs_before_navigation(monkeypatch, operation):
    monkeypatch.setitem(sys.modules, 'pywinauto', SimpleNamespace(mouse=object()))
    monkeypatch.setattr(ui, 'main_window', lambda: (object(), {'hwnd': 7}))
    monkeypatch.setattr(ui, '_tree_input_state', lambda: {**good_state(), 'clip_rect': [400, 300, 400, 300]}, raising=False)
    calls = []
    monkeypatch.setattr(ui, '_file_tree', lambda dlg: calls.append('tree'))
    with pytest.raises(GuiNeedsActiveDesktop, match='clip_rect'):
        if operation == 'open_model':
            ui.open_model('and2.model')
        else:
            ui.compile_all()
    assert calls == []
