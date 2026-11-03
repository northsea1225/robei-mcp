"""Run unchanged stage assertions against copied fixtures in a selected QA tree.

Run the gui lane in Session 1. No task registration, installation, board access,
or repository fixture writes occur here. The only bindings changed in a loaded
stage are workspace paths and explicit inheritance of the server environment.
"""
import argparse
import asyncio
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


class ScopedRoot:
    def __init__(self, workspace):
        self.workspace = workspace

    def __truediv__(self, name):
        return self.workspace if name == 'workspace' else ROOT / name

    def __str__(self):
        return str(ROOT)


def run_stage(name, workspace):
    path = ROOT/'tests'/f'{name}.py'
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ROOT = ScopedRoot(workspace)
    module.WS = workspace
    for attr, rel in {'PROJ': 'counter4_scratch/counter4_scratch.proj',
                      'COUNTER': 'counter4_scratch/counter4_scratch.proj',
                      'TWO_LEVEL': 'robei_samples/two_level/two_level.proj'}.items():
        if hasattr(module, attr):
            setattr(module, attr, workspace/rel)
    if hasattr(module, 'StdioServerParameters'):
        params = module.StdioServerParameters

        def inherited(**kwargs):
            kwargs['env'] = {**os.environ, **(kwargs.get('env') or {})}
            return params(**kwargs)
        module.StdioServerParameters = inherited
    if name == 'portable_core':
        return module.main([])
    if asyncio.iscoroutinefunction(module.main):
        return asyncio.run(module.main())
    return module.main() or 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--phase', choices=['m5_111', 'm6_112'], required=True)
    parser.add_argument('--lane', choices=['core', 'gui'], required=True)
    parser.add_argument('--stage')
    parser.add_argument('--allow-battery', action='store_true',
                        help='let the GUI lane run on a charged battery (>=20%); passed to every stage')
    args = parser.parse_args()
    phase = ROOT/'workspace/_qa_runs'/args.phase
    workspace = phase/'regression/workspace'
    workspace.mkdir(parents=True, exist_ok=True)
    os.environ['ROBEI_MCP_WORKSPACE'] = str(workspace)
    # stage13 intentionally reads the original .const; allow only that read root.
    os.environ['ROBEI_MODEL_ROOTS'] = os.pathsep.join([str(ROOT/'workspace'), r'C:\ProgramData\Robei', str(workspace)])
    os.environ['PYTHONPATH'] = str(ROOT)
    os.environ['PYTHONUTF8'] = '1'
    os.environ['PYTHONIOENCODING'] = 'utf-8'
    # tmp_path must act as a non-repository even though QA lives under this repo.
    os.environ['GIT_CEILING_DIRECTORIES'] = str(workspace)
    # Stage 10/11/12/18 each gate on gui_doctor; the flag travels as an env var
    # so a nested stage process inherits the same decision.  Default stays off.
    if args.allow_battery:
        os.environ['ROBEI_MCP_ALLOW_BATTERY'] = '1'
    else:
        os.environ.pop('ROBEI_MCP_ALLOW_BATTERY', None)
    sys.path.insert(0, str(ROOT))
    if args.stage:
        raise SystemExit(run_stage(args.stage, workspace))
    fixture_hashes = {}
    tracked = subprocess.check_output(['git', 'ls-files', '-z', 'workspace'], cwd=ROOT).decode().split('\0')
    baseline = json.loads((ROOT/'workspace/_qa_runs/m5_111/baseline.json').read_text(encoding='utf-8'))
    lab2 = baseline['lab2_sources']
    for rel, digest in lab2.items():
        assert hashlib.sha256((ROOT/'workspace'/rel).read_bytes()).hexdigest() == digest, rel
    lab2_verilog = [str(p.relative_to(ROOT)) for name in sorted({p.replace('\\', '/').split('/')[0] for p in lab2})
                   for p in sorted((ROOT/'workspace'/name/'verilog').glob('*.v'))]
    for rel in list(filter(None, tracked)) + ['workspace/' + p for p in lab2] + lab2_verilog:
        source = ROOT/rel
        dest = workspace/Path(rel).relative_to('workspace')
        if not source.is_file():
            continue
        fixture_hashes[rel] = hashlib.sha256(source.read_bytes()).hexdigest()
        # Do not reset the mutable fixture between sequential GUI stages.
        if args.lane == 'core' or not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, dest)
    logs = phase/'regression_logs'/f'{args.lane}-{time.time_ns()}'
    logs.mkdir(parents=True, exist_ok=False)
    if args.lane == 'gui':
        from robei_mcp import gui
        preflight = gui.gui_doctor(allow_battery=args.allow_battery)
        (logs/'gui_doctor.json').write_text(json.dumps(preflight, indent=2), encoding='utf-8')
        if not preflight.get('ok') or preflight.get('session', {}).get('process_session') != 1:
            raise RuntimeError('Session 1 gui_doctor failed; GUI stages not started')
    stages = ['pytest', 'portable_core', 'stage13_doctor', 'stage21_packaging'] if args.lane == 'core' else [
        'stage10_counter_native', 'stage11_mcp_native', 'stage12_ops', 'stage18_p1']
    results = []
    for stage in stages:
        cmd = [sys.executable, '-u', str(Path(__file__).resolve()), '--phase', args.phase, '--lane', args.lane, '--stage', stage]
        if args.allow_battery:
            # The child re-parses its own argv, so pass the flag explicitly: the
            # env var alone would be dropped by the child's own default reset.
            cmd.append('--allow-battery')
        if stage == 'pytest':
            cmd = [sys.executable, '-m', 'pytest', '-o', 'addopts=', '-q',
                   '--junitxml=' + str(logs/'pytest.xml'), '--basetemp=' + str(workspace/'pytest_tmp')]
        log = logs/(stage+'.log'); started = time.monotonic()
        with log.open('w', encoding='utf-8') as output:
            proc = subprocess.run(cmd, cwd=ROOT, env=os.environ.copy(), stdout=output, stderr=subprocess.STDOUT, timeout=900)
        tail = log.read_text(encoding='utf-8', errors='replace').splitlines()[-6:]
        row = {'stage': stage, 'returncode': proc.returncode, 'seconds': round(time.monotonic()-started, 2),
               'log': str(log), 'tail': tail, 'sha256': hashlib.sha256(log.read_bytes()).hexdigest()}
        results.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
        (phase/f'regression_{args.lane}.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
        if proc.returncode:
            break
    changed = [p for p, digest in fixture_hashes.items() if hashlib.sha256((ROOT/p).read_bytes()).hexdigest() != digest]
    assert not changed, changed
    print('TRACKED_FIXTURES_UNCHANGED', len(fixture_hashes), flush=True)
    return 0 if len(results) == len(stages) and all(r['returncode'] == 0 for r in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
