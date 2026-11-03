"""Exp7 + MIT seqdet acceptance; only write beneath m5_111 or m6_112.

Source projects remain read-only. Request JSON is retained for a separate
resident MCP replay; this script's direct calls are not resident-channel proof.
"""
import argparse
import hashlib
import json
from pathlib import Path
import random
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from robei_mcp import tools
from robei_mcp.config import SETTINGS


def oracle(a, b, n):
    mask = (1 << (n-1)) - 1
    ma, mb, sa, sb = a & mask, b & mask, a >> (n-1), b >> (n-1)
    if sa == sb:
        return (sa << (n-1)) | ((ma+mb) & mask)
    if ma == mb:
        return 0
    return ((sa if ma > mb else sb) << (n-1)) | abs(ma-mb)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--phase', choices=['m5_111', 'm6_112'], required=True)
    args = parser.parse_args()
    phase = SETTINGS.workspace/'_qa_runs'/args.phase
    phase.mkdir(parents=True, exist_ok=True)
    src = SETTINGS.workspace/'_qa_runs/m4_110/exp7_qadd'
    dst = phase/'exp7_qadd'
    assert not dst.exists(), 'use a clean phase/project for acceptance'
    hashes = {}
    for source in [src/'exp7_qadd.proj', *sorted((src/'model').glob('*.model')), *sorted((src/'test').glob('*.test'))]:
        target = dst/source.relative_to(src); target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        hashes[str(source)] = hashlib.sha256(source.read_bytes()).hexdigest()
    for folder in ('test', 'verilog', 'build'):
        (dst/folder).mkdir(exist_ok=True)
    proj = str(dst/'exp7_qadd.proj')
    duts = [{'instance': inst, 'model_path': str(dst/'model'/f'{model}.model'),
             'connections': {'a': 'a', 'b': 'b', 'c': 'c' if inst == 'u' else 'c_ref'}}
            for inst, model in [('u', 'qadd'), ('u_ref', 'qadd_ref')]]
    samples = [(0, 0), (0, 128), (128, 128), (127, 1), (255, 255), (127, 255), (128, 1), (255, 127)]
    rng = random.Random(111)
    while len(samples) < 32:
        pair = (rng.randrange(256), rng.randrange(256))
        if pair not in samples:
            samples.append(pair)
    cases = []
    for n, pairs in [(4, [(a,b) for a in range(16) for b in range(16)]), (8, samples)]:
        cases.append({'parameters': {'N': n}, 'allow_wide_vectors': n == 8,
                      'vectors': [{'drive': {'a': a, 'b': b},
                                   'expect': {'u.c': oracle(a,b,n), 'u_ref.c': oracle(a,b,n)}} for a,b in pairs]})
    sweep_request = {'proj_path': proj, 'name': 'tb_exp7_sweep', 'duts': duts, 'sweep': cases,
                     'comparisons': [{'left': 'u.c', 'right': 'u_ref.c'}], 'run': True}
    result = tools.create_testbench(**sweep_request)
    (phase/'exp7_sweep.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    assert result['ok'], result
    mapping = {item['test']: {'instances': {
        inst: {'top': model, 'parameters': case['parameters'],
               **({'vectors': [v['drive'] for v in case['vectors']]} if case['parameters']['N'] == 8 else {})}
        for inst, model in [('u', 'qadd'), ('u_ref', 'qadd_ref')]}}
        for item, case in zip(result['sweep'], cases)}
    report = tools.build_report(proj, tests=[item['test'] for item in result['sweep']],
        out='exp7_sweep.md', include=['simulation', 'truth_table', 'display', 'assertions', 'waveform'],
        truth_map=mapping, testbench_results=[result])
    assert report['ok'] and not report['missing'], report
    (phase/'exp7_report_result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    for item in result['sweep']:
        print('EXP7', item['parameters'], item['semantic']['vector_count'], item['run']['tests'][0]['selftest'], item['run']['tests'][0]['returncode'])

    seq = tools.create_robei_project(f'_qa_runs/{args.phase}/seqdet', models=[])['proj']
    seq_root = Path(seq).parent
    fixture = ROOT/'examples/seqdet'
    shutil.copytree(fixture, seq_root/'source')
    rtl = (fixture/'seq100111_detector.v').read_text(encoding='utf-8')
    model = tools.create_robei_model(seq, 'seq100111_detector',
        code=rtl.split(';',1)[1].rsplit('endmodule',1)[0],
        ports=[{'name': p, 'direction': 'input'} for p in ('clk','reset','din')]
        + [{'name':'detected','direction':'output','datatype':'reg'}])['path']
    stream = '1001110011100010011x10011100010011z100111'
    history = ''; stimuli = []; assertions = []
    for index, bit in enumerate(stream):
        history = '' if bit in 'xz' else history + bit
        stimuli.append({'at':12+10*index,'drive':{'din':bit}})
        assertions.append({'at':15+10*index,'expect':{'detected':int(history.endswith('100111'))}})
    seq_request = {'proj_path':seq, 'name':'tb_seqdet', 'model_path':model, 'mode':'timed',
        'clock':{'signal':'clk','period':10}, 'reset':{'signal':'reset','active':1,'duration':12},
        'stimuli':stimuli,'assertions':assertions,'run':True}
    seq_result = tools.create_testbench(**seq_request)
    (phase/'seqdet.json').write_text(json.dumps(seq_result, indent=2), encoding='utf-8')
    assert seq_result['ok'], seq_result
    seq_report = tools.build_report(seq, tests=['tb_seqdet'], out='seqdet.md',
        include=['simulation','display','assertions','waveform'], testbench_results=[seq_result])
    assert seq_report['ok'] and not seq_report['missing'], seq_report
    print('SEQDET', len(assertions), seq_result['run']['tests'][0]['selftest'], seq_result['run']['tests'][0]['returncode'])
    for request, name in [(sweep_request, 'request_exp7_sweep.json'), (seq_request, 'request_seqdet.json')]:
        request['name'] += '_resident'; request['overwrite'] = True
        (phase/name).write_text(json.dumps(request, indent=2), encoding='utf-8')
    assert all(hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest for path,digest in hashes.items())
    (phase/'acceptance_sources.json').write_text(json.dumps(hashes, indent=2), encoding='utf-8')
    print('READ_ONLY_SOURCE_HASHES', len(hashes), 'unchanged')


if __name__ == '__main__':
    main()
