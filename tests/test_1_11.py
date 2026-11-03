"""Bounded sweeps, parameterized truth exports and process/semantic separation."""
import hashlib
import asyncio
import itertools
import json
import os
from pathlib import Path
import shutil
import sys
import uuid

import pytest

from robei_mcp import p1, server_app, tools
from robei_mcp.config import SETTINGS


@pytest.fixture
def project():
    result = tools.create_robei_project('_qa_runs/m5_111/pytest_' + uuid.uuid4().hex[:8], models=[])
    root = Path(result['proj']).parent
    try:
        yield result['proj'], root
    finally:
        shutil.rmtree(root)


def adder(proj, name='sum_n', parameters=None):
    return tools.create_robei_model(proj, name, parameters=parameters or {'N': 4},
        ports=[{'name': p, 'direction': 'input', 'width': 'N'} for p in ('a', 'b')]
        + [{'name': 'c', 'direction': 'output', 'width': 'N'}], code='assign c = a + b;')['path']


def pairs(n):
    return list(itertools.product(range(16), repeat=2)) if n == 4 else [(i * 8, 255 - i * 7) for i in range(32)]


def vectors(n, multi=False):
    return [{'drive': {'a': a, 'b': b},
             'expect': {'u.c': 'a+b', 'v.c': (a + b) % (1 << n)} if multi else {'c': (a + b) % (1 << n)}}
            for a, b in pairs(n)]


def test_sweep_resolves_widths_without_mutating_models(project):
    proj, root = project
    path = Path(adder(proj))
    before = path.read_bytes()
    result = tools.create_testbench(proj, 'tb_sweep', model_path=str(path), run=True,
        sweep=[{'parameters': {'N': n}, 'vectors': vectors(n), 'allow_wide_vectors': n == 8} for n in (4, 8)])
    assert result['ok'], result
    assert result['case_count'] == 2
    assert path.read_bytes() == before
    assert [r['semantic']['input_bits'] for r in result['sweep']] == [8, 16]
    assert [r['semantic']['vector_count'] for r in result['sweep']] == [256, 32]
    for n, case in zip((4, 8), result['sweep']):
        assert f'#(.N({n})) dut(' in case['code']
        row = case['run']['tests'][0]
        assert row['returncode'] == 0 and row['selftest'] == 'PASS'
        assert case['semantic']['sweep']['parameters'] == {'N': n}


def test_flat_sweep_and_dependent_parameter(project):
    proj, _ = project
    path = adder(proj, parameters={'BASE': 2, 'N': 'BASE*2'})
    result = tools.create_testbench(proj, 'tb_flat', model_path=path,
        vectors=[{'drive': {'a': 15, 'b': 1}, 'expect': {'c': 'a+b'}}],
        sweep=[{'BASE': 2}, {'BASE': 4}], allow_wide_vectors=True, run=True)
    assert result['ok'], result
    assert [item['semantic']['input_bits'] for item in result['sweep']] == [8, 16]


@pytest.mark.parametrize('sweep', [[], [{'N': 4}] * 17, [{'N': [4, 8]}],
    [{'N': 4}, {'NO_SUCH_PARAMETER': 8}], [{'N': True}], [{'N': 2**31}],
    [{'parameters': {'N': 4}, 'surprise': 1}], [{'N': 4097}]])
def test_invalid_sweep_has_no_partial_writes(project, sweep):
    proj, root = project
    path = adder(proj)
    before = Path(proj).read_bytes()
    result = server_app.create_testbench(proj, 'tb_reject', model_path=path,
        vectors=[{'drive': {'a': 0, 'b': 0}, 'expect': {'c': 0}}], sweep=sweep)
    assert result['ok'] is False
    assert not list((root / 'test').glob('tb_reject*'))
    assert Path(proj).read_bytes() == before


def test_sweep_total_limit_and_late_invalid_vector(project):
    proj, root = project
    path = adder(proj)
    vector = {'drive': {'a': 0, 'b': 0}, 'expect': {'c': 0}}
    with pytest.raises(ValueError):
        tools.create_testbench(proj, 'tb_limit', model_path=path, vectors=[vector] * 4096, sweep=[{'N': 4}] * 3)
    assert not list((root / 'test').glob('tb_limit*'))
    result = server_app.create_testbench(proj, 'tb_late', model_path=path,
        sweep=[{'parameters': {'N': 4}, 'vectors': [vector]},
               {'parameters': {'N': 4}, 'vectors': [{'drive': {'a': 256, 'b': 0}, 'expect': {'c': 0}}]}])
    assert not result['ok'] and not list((root / 'test').glob('tb_late*'))


def test_sweep_existing_second_target_is_preflighted(project):
    proj, root = project
    path = adder(proj)
    tools.create_robei_test(proj, 'tb_collision_s2', 'initial #1 $finish;')
    before = Path(proj).read_bytes()
    with pytest.raises(FileExistsError):
        tools.create_testbench(proj, 'tb_collision', model_path=path, vectors=vectors(4), sweep=[{'N': 4}, {'N': 4}])
    assert Path(proj).read_bytes() == before
    assert not (root/'test/tb_collision_s1.test').exists()


def test_parameterized_sweep_report_has_four_truth_tables(project):
    proj, root = project
    paths = [adder(proj, name) for name in ('sum_n', 'sum_ref')]
    duts = [{'instance': inst, 'model_path': path, 'connections': {'a': 'a', 'b': 'b', 'c': 'c_' + inst}}
            for inst, path in zip(('u', 'v'), paths)]
    result = tools.create_testbench(proj, 'tb_report', duts=duts,
        comparisons=[{'left': 'u.c', 'right': 'v.c'}], run=True,
        sweep=[{'parameters': {'N': n}, 'vectors': vectors(n, multi=True), 'allow_wide_vectors': n == 8} for n in (4, 8)])
    assert result['ok'], result
    truth_map = {}
    for n, case in zip((4, 8), result['sweep']):
        truth_map[case['test']] = {'instances': {
            inst: {'top': top, 'parameters': {'N': n},
                   **({'vectors': [v['drive'] for v in vectors(n)]} if n == 8 else {})}
            for inst, top in zip(('u', 'v'), ('sum_n', 'sum_ref'))}}
    report = tools.build_report(proj, tests=[c['test'] for c in result['sweep']],
        include=['simulation', 'truth_table', 'display', 'assertions'], truth_map=truth_map, testbench_results=[result])
    assert report['ok'] and not report['missing'], report
    text = Path(report['report_path']).read_text(encoding='utf-8')
    assert text.count('mode=`exhaustive`') == text.count('mode=`sampled`') == 2
    assert 'Parameter sweep facts' in text and 'process rc' in text
    assert 'None' not in text and 'null' not in text
    evidence = [json.loads(line) for line in Path(report['evidence_path']).read_text(encoding='utf-8').splitlines()]
    tools._validate_report_evidence(text, evidence)
    counts = [item['value'] for item in evidence if item['field'].endswith('.row_count')]
    assert counts == [256, 256, 32, 32]


def test_truth_table_non_ansi_defaults_and_samples(project):
    _, root = project
    src = root/'numeric.v'
    src.write_text('module numeric(a,y); parameter BASE=2, N=BASE*2; input [N-1:0] a; output [N-1:0] y; assign y=a; endmodule')
    full = p1.truth_table(str(src), 'numeric')
    assert full['ok'] and full['row_count'] == 16 and full['exhaustive']
    sample = p1.truth_table(str(src), 'numeric', parameters={'BASE': 4}, vectors=[{'a': 0}, {'a': 255}])
    assert sample['ok'] and sample['row_count'] == 2 and not sample['exhaustive']
    assert sample['total_possible_rows'] == 256 and sample['rows'][-1]['y'] == '11111111'


@pytest.mark.parametrize('options', [{'max_inputs': 13}, {'max_inputs': 0},
    {'parameters': {'M': 4}}, {'parameters': {'N': [4, 8]}}, {'vectors': []},
    {'vectors': [{'a': 256}]}, {'vectors': [{'wrong': 0}]}])
def test_truth_limits_and_bad_parameters(project, options):
    _, root = project
    src = root/'limited.v'
    src.write_text('module limited #(parameter N=4)(input [N-1:0] a, output [N-1:0] y); assign y=a; endmodule')
    before = list((root/'build').glob('*'))
    result = server_app.truth_table(str(src), 'limited', **options)
    assert not result['ok']
    assert list((root/'build').glob('*')) == before


def test_semantic_failure_is_consistent_and_rc_stays_zero(project):
    proj, root = project
    path = adder(proj)
    create = tools.create_testbench(proj, 'tb_wrong', model_path=path,
        vectors=[{'drive': {'a': 1, 'b': 1}, 'expect': {'c': 3}}], run=True)
    single = tools.simulate_project(proj, 'tb_wrong')
    batch = tools.run_all_tests(proj, tests=['tb_wrong'])
    report = tools.build_report(proj, tests=['tb_wrong'], include=['simulation', 'display', 'assertions'], testbench_results=[create])
    for result in (create, single, batch, report):
        assert result['ok'] is False and result['run']['code'] == 'selftest_failed', result
    assert single['simulation']['returncode'] == batch['tests'][0]['returncode'] == 0
    assert batch['passed'] == 0 and batch['failed'] == 1 and report['report_written']


def test_selftest_failure_survives_output_clipping(tmp_path):
    result = tools._run_impl([sys.executable, '-c',
        "print('x'*21000); print('SELFTEST PASS: old'); print('SELFTEST FAIL: 1 mismatch')"], tmp_path, 10)
    assert result['returncode'] == 0 and 'SELFTEST FAIL' not in result['stdout']
    assert result['selftest'] == 'FAIL'
    verdict = tools._semantic_verdict({'ok': True, 'simulation': result, 'display_output': result['stdout']})
    assert not verdict['ok'] and verdict['run']['code'] == 'selftest_failed'


def test_rejected_mirror_does_not_launch_compiler(project, monkeypatch):
    _, root = project
    source = root/'unsafe.v'
    source.write_text('module unsafe; reg [7:0] mem[0:1]; initial $readmemh(PATH,mem); endmodule')
    monkeypatch.setattr(tools, '_run', lambda *a, **k: pytest.fail('compiler must not start'))
    result = tools.simulate([str(source)], 'unsafe')
    assert result['stage'] == 'security_scan' and result['rejected']
    assert result['rejected'] == result['security']['rejected']
    assert result['compile'] is result['simulation'] is None


def test_stale_guidance_includes_nested_python_sources(tmp_path, monkeypatch):
    nested = tmp_path/'gui_agent'; nested.mkdir()
    (nested/'changed.py').write_text('# changed')
    monkeypatch.setattr(tools, '_source_dir', lambda: tmp_path)
    monkeypatch.setattr(tools, 'PROCESS_STARTED', 0)
    monkeypatch.setattr(tools, '_tool_version', lambda *a: {'exists': True})
    monkeypatch.setattr(tools, 'file_version', lambda *a: None)
    result = tools.runtime_status()
    assert result['stale'] and result['restart_required']
    assert any('stale=false' in line for line in result['restart_guidance'])
    assert any('does not stop' in line for line in result['restart_guidance'])


SEQ_ROOT = Path(__file__).resolve().parents[1]/'examples/seqdet'


def seq_model(proj, original=False):
    path = SEQ_ROOT/('upstream/seq100111_detector.v' if original else 'seq100111_detector.v')
    body = path.read_text(encoding='utf-8').split(';', 1)[1].rsplit('endmodule', 1)[0]
    return tools.create_robei_model(proj, 'seq_original' if original else 'seq_fixed', code=body,
        ports=[{'name': p, 'direction': 'input'} for p in ('clk', 'reset', 'din')]
        + [{'name': 'detected', 'direction': 'output', 'datatype': 'reg'}])['path']


def seq_events():
    stream = '1001110011100010011x10011100010011z100111'
    history = ''
    stimuli, assertions = [], []
    for i, bit in enumerate(stream):
        history = '' if bit in 'xz' else history + bit
        stimuli.append({'at': 12 + 10*i, 'drive': {'din': bit}})
        assertions.append({'at': 15 + 10*i, 'expect': {'detected': int(history.endswith('100111'))}})
    return stimuli, assertions


@pytest.mark.parametrize('original', [False, True])
def test_sequence_detector_timed_xz_behavior(project, original):
    proj, _ = project
    path = seq_model(proj, original)
    stimuli, assertions = seq_events()
    result = tools.create_testbench(proj, 'tb_seq', model_path=path, mode='timed',
        clock={'signal': 'clk', 'period': 10}, reset={'signal': 'reset', 'active': 1, 'duration': 12},
        stimuli=stimuli, assertions=assertions, run=True)
    assert result['ok'] is (not original), result
    assert result['run']['tests'][0]['returncode'] == 0
    if original:
        assert result['run']['code'] == 'selftest_failed'


@pytest.mark.needs_robei
@pytest.mark.needs_robei_yosys
def test_sequence_default_removes_yosys_latch(project):
    _, root = project
    counts = []
    for index, source in enumerate((SEQ_ROOT/'upstream/seq100111_detector.v', SEQ_ROOT/'seq100111_detector.v')):
        copied = root/f'seq_{index}.v'; shutil.copyfile(source, copied)
        output = root/f'seq_{index}.json'
        command = f'read_verilog {tools._quote_yosys(copied)}; hierarchy -top seq100111_detector; proc; opt; write_json {tools._quote_yosys(output)}'
        run = tools._run([SETTINGS.yosys, '-p', command], cwd=root, timeout_s=30)
        assert run['ok'], run
        cells = json.loads(output.read_text())['modules']['seq100111_detector']['cells']
        counts.append(sum('latch' in cell['type'].lower() for cell in cells.values()))
    assert counts[0] > 0 and counts[1] == 0


def test_upstream_preserved_and_only_default_added():
    provenance = json.loads((SEQ_ROOT/'PROVENANCE.json').read_text())
    for name, digest in provenance['upstream_sha256'].items():
        assert hashlib.sha256((SEQ_ROOT/'upstream'/name).read_bytes()).hexdigest() == digest
    original = (SEQ_ROOT/'upstream/seq100111_detector.v').read_text()
    fixed = (SEQ_ROOT/'seq100111_detector.v').read_text()
    assert fixed.replace('    next_state = S0; // Default also covers X/Z din in a legal state.\n', '', 1) == original


def test_mcp_parameters_do_not_coerce_bool_string_or_float(project):
    """Use the real protocol validator, not just a direct Python wrapper call."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    proj, root = project
    model = adder(proj)
    source_root = Path(__file__).resolve().parents[1]

    async def check():
        params = StdioServerParameters(command=sys.executable, args=[str(source_root/'server.py')],
            cwd=str(source_root), env={**os.environ, 'ROBEI_MCP_GUI': '0',
                                      'ROBEI_MCP_WORKSPACE': str(SETTINGS.workspace)})
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                for index, value in enumerate((True, '8', 8.5)):
                    name = 'tb_rpc_bad' + str(index)
                    result = await session.call_tool('create_testbench', {
                        'proj_path': proj, 'name': name, 'model_path': model,
                        'parameters': {'N': value}, 'allow_wide_vectors': True,
                        'vectors': [{'drive': {'a': 0, 'b': 0}, 'expect': {'c': 0}}]})
                    payload = json.loads(result.content[0].text)
                    assert payload['ok'] is False and payload['code'] == 'bad_argument', payload
                    assert not (root/'test'/f'{name}.test').exists()
                result = await session.call_tool('create_testbench', {
                    'proj_path': proj, 'name': 'tb_rpc_valid', 'model_path': model,
                    'parameters': {'N': 8}, 'allow_wide_vectors': True,
                    'vectors': [{'drive': {'a': 0, 'b': 0}, 'expect': {'c': 0}}]})
                assert json.loads(result.content[0].text)['semantic']['input_bits'] == 16
                for options in ({'parameters': {'N': True}},
                                {'parameters': {'N': 8}, 'vectors': [{'a': True, 'b': 0}]}):
                    result = await session.call_tool('truth_table', {
                        'verilog_path': str(root/'verilog/sum_n.v'), 'top': 'sum_n', **options})
                    assert json.loads(result.content[0].text)['ok'] is False
    asyncio.run(check())
