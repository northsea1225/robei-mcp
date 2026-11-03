"""FPGA artifact integrity and a downloader that can only produce dry-run plans."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import shutil
import uuid

import pytest

from robei_mcp import constraints, doctor, errors, fpga, security, tools
from robei_mcp.config import SETTINGS

PART = 'xc7a50tfgg484-1'


def synthetic_bit(part=PART):
    """A framing fixture only, NOT a programmable bitstream."""
    data = b'\0\x09' + b'\x0f\xf0'*4 + b'\0' + b'\0\x01'
    for key, value in [('a', 'synthetic'), ('b', part), ('c', '2026/11/03'), ('d', '00:00:00')]:
        encoded = value.encode() + b'\0'
        data += key.encode() + len(encoded).to_bytes(2, 'big') + encoded
    payload = b'\xff'*8 + fpga.BIT_SYNC_WORD + b'\x20\0\0\0'
    return data + b'e' + len(payload).to_bytes(4, 'big') + payload


@pytest.fixture
def scoped(tmp_path, monkeypatch):
    settings = replace(SETTINGS, workspace=tmp_path)
    for module in (fpga, security, tools, doctor):
        monkeypatch.setattr(module, 'SETTINGS', settings)
    return tmp_path


def test_bitstream_validation_is_structural_and_bounded(scoped):
    path = scoped/'sample.bit'; data = synthetic_bit(); path.write_bytes(data)
    result = fpga._validate_bitstream(path, PART)
    assert result['validated'] and result['part'] == PART
    assert result['sha256'] == hashlib.sha256(data).hexdigest()
    assert result['payload_bytes'] == 16
    assert 'not configuration CRC' in result['validation_scope']
    assert path.read_bytes() == data


@pytest.mark.parametrize('mutate', [
    lambda data: data[:-1],
    lambda data: data + b'\0',
    lambda data: b'\0'*len(data),
    lambda data: data.replace(fpga.BIT_SYNC_WORD, b'\x01\x02\x03\x04'),
    lambda data: data.replace(b'xc7a50', b'xc7a35'),
    lambda data: data.replace(b'\0\0\0\x10', b'\0\0\0\x0f'),
    lambda data: data[:20],
])
def test_bad_bitstream_never_produces_a_valid_plan(scoped, mutate):
    path = scoped/'bad.bit'; path.write_bytes(mutate(synthetic_bit()))
    with pytest.raises(errors.BadRequest) as caught:
        fpga._download_plan(path, PART)
    assert errors.code_for(caught.value) == 'bitstream_invalid'


def test_bitstream_path_rejection(scoped):
    with pytest.raises(errors.PathEscape):
        fpga._validate_bitstream(scoped.parent/'outside.bit', PART)
    with pytest.raises(errors.BadRequest):
        fpga._validate_bitstream(scoped/'missing.bit', PART)


def test_plan_never_invokes_programmer(scoped, monkeypatch):
    path = scoped/'ok.bit'; path.write_bytes(synthetic_bit())
    present = scoped/'present-tool'; present.write_bytes(b'not executable')
    monkeypatch.setattr(fpga, '_programmer_path', lambda: present)
    monkeypatch.setattr(tools, '_run', lambda *a, **k: pytest.fail('dry-run must not invoke a process'))
    monkeypatch.setattr(tools.subprocess, 'Popen', lambda *a, **k: pytest.fail('dry-run must not start a subprocess'))
    result = fpga._download_plan(path, PART)
    assert result['argv'] == [str(present), '--write-sram', '--file-type', 'bit', str(path)]
    assert result['ok'] and result['dry_run'] and result['command_validated']
    assert not result['executed'] and not result['hardware_verified']
    assert result['returncode'] is None and result['hardware_target'] is None


@pytest.mark.parametrize('dry_run', [False, None, 0, 'false'])
def test_hardware_guard_precedes_all_io(scoped, monkeypatch, dry_run):
    monkeypatch.setattr(fpga, 'resolve_project_path', lambda *a: pytest.fail('must reject before project access'))
    monkeypatch.setattr(tools, '_run', lambda *a, **k: pytest.fail('must never run a programmer'))
    with pytest.raises(errors.BadRequest) as caught:
        fpga.build_bitstream('not_even_a_project.proj', PART, steps=['download'], dry_run=dry_run)
    assert errors.code_for(caught.value) == 'hardware_execution_disabled'


def test_fpga_doctor_reports_missing_paths_without_subprocess(scoped, monkeypatch):
    missing = {name: scoped/(name+'.missing') for name in ('roplace','fasm2frames','xc7frames2bit')}
    monkeypatch.setattr(fpga, 'tool_paths', lambda: missing)
    monkeypatch.setattr(fpga, 'device_table', lambda: [])
    monkeypatch.setattr(fpga, '_programmer_path', lambda: scoped/'absent-programmer')
    monkeypatch.setattr(tools, '_run', lambda *a, **k: pytest.fail('doctor must remain read-only'))
    result = fpga.doctor(PART)
    assert not result['ok']
    assert set(missing) <= set(result['failures']) and 'device_database' in result['failures']
    assert not result['download']['exists'] and not result['download']['execution_supported']
    assert all(item['hint'] for item in result['checks'] if not item['ok'])


def test_missing_programmer_has_doctor_diagnostic(scoped, monkeypatch):
    path = scoped/'sample.bit'; path.write_bytes(synthetic_bit())
    monkeypatch.setattr(fpga, '_programmer_path', lambda: scoped/'absent')
    with pytest.raises(errors.FpgaStepFailed) as caught:
        fpga._download_plan(path, PART)
    payload = errors.payload(caught.value)
    assert payload['stage'] == 'download' and 'doctor' in payload
    assert not payload['doctor']['download']['exists']


def test_stale_artifact_cannot_mask_successful_no_output_process(scoped, monkeypatch):
    artifact = scoped/'old.fasm'; artifact.write_bytes(b'old artifact')
    monkeypatch.setattr(tools, '_run', lambda *a, **k: {'ok': True, 'returncode': 0, 'stdout': '', 'stderr': ''})
    with pytest.raises(errors.FpgaStepFailed) as caught:
        fpga._run_stage(['mock-route'], scoped, 1, 'route', {'fasm': artifact}, scoped/'run')
    record = caught.value.record
    assert record['returncode'] == 0 and not record['ok']
    assert not artifact.exists() and not record['outputs']['fasm']['exists']
    assert Path(record['backups']['fasm']).read_bytes() == b'old artifact'


@pytest.mark.parametrize('data', [b'', b'new artifact'])
def test_stage_requires_nonempty_output_and_records_sha(scoped, monkeypatch, data):
    path = scoped/'result.frame'

    def fake_run(*a, **k):
        path.write_bytes(data)
        return {'ok': True, 'returncode': 0, 'duration_ms': 1, 'stdout': 'mock', 'stderr': ''}
    monkeypatch.setattr(tools, '_run', fake_run)
    if not data:
        with pytest.raises(errors.FpgaStepFailed):
            fpga._run_stage(['mock-package'], scoped, 1, 'package.frames', {'frame': path}, scoped/'run')
    else:
        result = fpga._run_stage(['mock-package'], scoped, 1, 'package.frames', {'frame': path}, scoped/'run')
        assert result['outputs']['frame']['sha256'] == hashlib.sha256(data).hexdigest()
        assert result['outputs']['frame']['size_bytes'] == len(data)


def test_default_steps_do_not_add_an_implicit_download():
    assert fpga._normalize_steps(None) == ['synth', 'route', 'package']
    assert fpga._normalize_steps(['download','package','route','synth']) == list(fpga.STAGES)
    with pytest.raises(ValueError):
        fpga._normalize_steps(['download','download'])


def test_fpga_doctor_requires_explicit_opt_in():
    with pytest.raises(ValueError, match='fpga=true'):
        doctor.run(part=PART)


@pytest.mark.skipif(not all(p.is_file() for p in fpga.tool_paths().values()) or not (fpga.db_root()/'chipdb').is_dir(), reason='FPGA toolchain/database unavailable')
def test_real_full_chain_records_artifacts_but_never_runs_download(monkeypatch):
    proj = tools.create_robei_project('_qa_runs/m6_112/pytest_download_' + uuid.uuid4().hex[:8], models=[])['proj']
    root = Path(proj).parent
    original_run = tools._run

    def guarded(args, *a, **kwargs):
        assert 'openfpgaloader' not in Path(args[0]).name.lower(), 'programmer execution is forbidden'
        return original_run(args, *a, **kwargs)
    monkeypatch.setattr(tools, '_run', guarded)
    try:
        tools.create_robei_model(proj, 'and2', code='assign y=a&b;',
            ports=[{'name': p, 'direction': 'output' if p == 'y' else 'input'} for p in ('a','b','y')])
        constraints.create_const(proj, 'and2_pins', 'and2', pins={'a':['A1'],'b':['A14'],'y':['A20']}, device=PART, family='artix7')
        result = fpga.build_bitstream(proj, PART, steps=list(fpga.STAGES))
        assert result['ok'] and result['download']['dry_run'] and not result['download']['executed']
        assert result['download']['returncode'] is None and result['bitstream_built']
        for name in ('json','routed_json','fasm','frame','bit','xdc'):
            item = result['artifacts'][name]; path = Path(item['path'])
            assert item['size_bytes'] == path.stat().st_size > 0
            assert item['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
        bit_hash = result['artifacts']['bit']['sha256']
        reuse = fpga.build_bitstream(proj, PART, steps=['download'])
        assert reuse['ok'] and not reuse['bitstream_built']
        assert reuse['artifacts']['bit']['sha256'] == bit_hash
        partial = fpga.build_bitstream(proj, PART, steps=['synth'])
        assert not partial['ok'] and partial['requested_steps_ok']
        assert partial['artifacts']['bit'] is None and partial['bit_payload_sha256'] is None
    finally:
        shutil.rmtree(root)


# --- gui_doctor power policy: allow_battery is opt-in, never a low-charge bypass

def _patch_gui_probes(monkeypatch, battery=(1,), charge=91, desktop='Default', fullscreen=False):
    from robei_mcp.gui_agent import doctor as gui_doctor

    monkeypatch.setattr(gui_doctor, '_cim_probe', lambda timeout_s=20: {
        'battery_status': list(battery), 'charge_percent': charge,
        'explorer_session1': 1, 'lockscreen_processes': []})
    monkeypatch.setattr(gui_doctor, '_desktop_probe', lambda: {
        'window_station': 'WinSta0', 'thread_desktop_name': 'Default',
        'input_desktop': desktop, 'process_session': 1})
    monkeypatch.setattr(gui_doctor, '_foreground_probe', lambda: {
        'hwnd': 1, 'pid': 2, 'exe': 'explorer.exe', 'title': '',
        'rect': [0, 0, 100, 100], 'monitor_rect': [0, 0, 100, 100], 'fullscreen': fullscreen})
    monkeypatch.setattr(gui_doctor, '_clip_probe', lambda: {
        'ok': True, 'clip_rect': [0, 0, 2560, 1440],
        'screen_rect': [0, 0, 2560, 1440], 'screen_resolution': [2560, 1440]})
    return gui_doctor


def test_gui_doctor_still_refuses_battery_by_default(monkeypatch):
    gui_doctor = _patch_gui_probes(monkeypatch)
    result = gui_doctor.run()
    assert result['ok'] is False
    assert result['code'] == 'gui_preflight_failed'
    assert 'battery' in result['failed_checks']
    assert result['power'] == {'on_battery': True, 'allowed_by': None,
                               'charge_percent': 91, 'min_charge_percent': 20}


def test_gui_doctor_allow_battery_downgrades_to_warning(monkeypatch):
    gui_doctor = _patch_gui_probes(monkeypatch)
    result = gui_doctor.run(allow_battery=True)
    assert result['ok'] is True
    assert result['code'] is None
    assert result['failed_checks'] == []
    assert result['power'] == {'on_battery': True, 'allowed_by': 'allow_battery',
                               'charge_percent': 91, 'min_charge_percent': 20}
    assert any('allow_battery=True' in line for line in result['warnings'])
    assert result['battery']['charge_percent'] == 91


def test_gui_doctor_allow_battery_still_refuses_low_charge(monkeypatch):
    gui_doctor = _patch_gui_probes(monkeypatch, charge=12)
    result = gui_doctor.run(allow_battery=True)
    assert result['ok'] is False
    assert result['code'] == 'battery_too_low'
    assert 'battery' in result['failed_checks']
    assert result['power']['allowed_by'] is None
    assert 'below the 20% floor' in result['error']


def test_gui_doctor_allow_battery_refuses_unknown_charge(monkeypatch):
    gui_doctor = _patch_gui_probes(monkeypatch, charge=None)
    result = gui_doctor.run(allow_battery=True)
    assert result['ok'] is False
    assert result['code'] == 'battery_charge_unknown'
    assert result['power']['charge_percent'] is None


def test_gui_doctor_allow_battery_is_inert_on_ac(monkeypatch):
    gui_doctor = _patch_gui_probes(monkeypatch, battery=(2,), charge=100)
    result = gui_doctor.run(allow_battery=True)
    assert result['ok'] is True
    assert result['power']['on_battery'] is False
    assert result['power']['allowed_by'] is None


def test_gui_doctor_wrapper_forwards_allow_battery(monkeypatch):
    from robei_mcp import gui

    seen = {}

    def fake_call(op, args=None, timeout_s=None):
        seen.update(op=op, args=args or {})
        return {'ok': True}

    monkeypatch.setattr(gui, 'call', fake_call)
    gui.gui_doctor(allow_battery=True)
    assert seen['op'] == 'doctor' and seen['args']['allow_battery'] is True
    gui.gui_doctor()
    assert seen['args']['allow_battery'] is False
