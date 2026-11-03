"""Lab 2 copies -> real Route/Package -> Download PLAN, gated by both doctors.

Never invoke a programmer, even to probe it. A failed experiment is retained
as a failure, and the independent second experiment still gets its own record.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from robei_mcp import constraints, doctor, errors, fpga, gui, tools
from robei_mcp.config import SETTINGS

PART = 'xc7a50tfgg484-1'
# Opt-in: run the GUI gate on a charged battery (ROBEI_MCP_ALLOW_BATTERY=1).
# Under 20% the doctor still refuses, so this can never mask a flat battery.
ALLOW_BATTERY = os.environ.get('ROBEI_MCP_ALLOW_BATTERY', '').strip().lower() in {'1', 'true', 'yes', 'on'}


def save(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding='utf-8')


def main():
    phase = SETTINGS.workspace/'_qa_runs/m6_112'
    phase.mkdir(parents=True, exist_ok=True)
    preflight = gui.gui_doctor(label='m6_112_acceptance', allow_battery=ALLOW_BATTERY)
    save(phase/'doctor_gui.json', preflight)
    if not preflight.get('ok') or preflight.get('session', {}).get('process_session') != 1:
        print('GUI_PREFLIGHT_BLOCKED:', preflight.get('code'), 'Route/Package were not started')
        return 2
    environment = doctor.run(fpga=True, part=PART)
    save(phase/'doctor_fpga.json', environment)
    if not environment.get('ok'):
        print('FPGA_DOCTOR_FAILED: inspect doctor_fpga.json; no build started')
        return 2
    original_run = tools._run

    def guard(args, *positional, **kwargs):
        if 'openfpgaloader' in Path(args[0]).name.lower():
            raise RuntimeError('programmer execution is forbidden in acceptance')
        return original_run(args, *positional, **kwargs)
    tools._run = guard
    baseline = json.loads((SETTINGS.workspace/'_qa_runs/m5_111/baseline.json').read_text(encoding='utf-8'))
    original_hashes = baseline['lab2_sources']
    assert all(hashlib.sha256((SETTINGS.workspace/p).read_bytes()).hexdigest() == h for p,h in original_hashes.items())
    records = []
    for name in ('exp6_counter', 'exp1_and'):
        source = SETTINGS.workspace/name
        dest = phase/name
        assert not dest.exists(), f'acceptance needs a clean copy: {dest}'
        copied = []
        paths = [source/(name+'.proj')]
        for folder, suffix in [('model','.model'),('test','.test'),('const','.const'),('verilog','.v')]:
            paths.extend(sorted((source/folder).glob('*'+suffix)))
        for path in paths:
            target = dest/path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            assert hashlib.sha256(target.read_bytes()).hexdigest() == digest
            copied.append({'source':str(path),'copy':str(target),'sha256':digest})
        proj = str(dest/(name+'.proj'))
        const_name = 'dec_counter'
        if name == 'exp1_and':
            const_name = 'and2_pins'
            constraints.create_const(proj, const_name, 'and2', pins={'a':['A1'],'b':['A14'],'y':['A20']},
                                     device=PART, family='artix7')
        request = {'proj_path':proj, 'part':PART, 'const_name':const_name,
                   'steps':['synth','route','package','download'], 'dry_run':True, 'timeout_s':120}
        save(dest/'acceptance_request.json', request)
        try:
            result = fpga.build_bitstream(**request)
        except Exception as exc:
            result = errors.payload(exc)
        if result.get('ok'):
            assert result['download']['executed'] is False
            assert result['download']['returncode'] is None
            assert result['download']['bitstream']['validated'] is True
            for item in result['artifacts'].values():
                if item:
                    path = Path(item['path'])
                    assert path.stat().st_size == item['size_bytes'] > 0
                    assert hashlib.sha256(path.read_bytes()).hexdigest() == item['sha256']
        save(dest/'acceptance_result.json', result)
        save(dest/'source_copy_manifest.json', copied)
        records.append({'project':name, 'ok':result.get('ok'), 'result':str(dest/'acceptance_result.json'),
                        'code':result.get('code'), 'stage':result.get('stage'),
                        'artifacts':result.get('artifacts'),
                        'constraint_note':'Lab2 copied constraint unchanged' if name == 'exp6_counter' else
                            'QA-only A1/A14/A20 routable pins; not a physical board pin map'})
        print(json.dumps({'project':name, 'ok':result.get('ok'), 'code':result.get('code'), 'stage':result.get('stage')}), flush=True)
    assert all(hashlib.sha256((SETTINGS.workspace/p).read_bytes()).hexdigest() == h for p,h in original_hashes.items())
    save(phase/'fpga_acceptance.json', {'experiments':records, 'lab2_sha_unchanged':len(original_hashes),
                                     'programmer_executed':False, 'hardware_verified':False})
    return 0 if all(item['ok'] for item in records) else 1


if __name__ == '__main__':
    raise SystemExit(main())
