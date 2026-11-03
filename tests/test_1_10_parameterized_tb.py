"""Semantic TB widths must match the DUT's root parameter defaults."""
import hashlib
import itertools
from pathlib import Path
import shutil
import uuid
import xml.etree.ElementTree as ET

import pytest

from robei_mcp import server_app, tools
from robei_mcp.config import SETTINGS


@pytest.fixture
def project():
    name = '_qa_runs/pytest_m4_param_' + uuid.uuid4().hex[:8]
    proj = tools.create_robei_project(name, models=[], skeleton=False)['proj']
    root = Path(proj).parent
    try:
        yield proj, root
    finally:
        shutil.rmtree(root)


def model(proj, name, parameters):
    return tools.create_robei_model(proj, name, parameters=parameters,
        ports=[{'name': p, 'direction': 'input', 'width': 'N'} for p in ('a', 'b')]
            + [{'name': 'c', 'direction': 'output', 'width': 'N'}],
        code='assign c = a + b;')['path']


@pytest.mark.parametrize('n', [4, 8])
def test_multidut_root_parameters_resolve_and_simulate(project, n):
    proj, _ = project
    paths = [model(proj, name, {'N': n}) for name in ('sum_a', 'sum_b')]
    pairs = list(itertools.product(range(16), repeat=2)) if n == 4 else [
        (0, 0), (1, 255), (127, 1), (255, 255), (128, 127), (255, 0)]
    duts = [{'instance': inst, 'model_path': path,
             'connections': {'a': 'a', 'b': 'b', 'c': 'c_' + inst}}
            for inst, path in zip(('u', 'u_ref'), paths)]
    result = tools.create_testbench(proj, 'tb_params', duts=duts,
        vectors=[{'drive': {'a': a, 'b': b},
                  'expect': {'u.c': 'a+b', 'u_ref.c': (a+b) % (1 << n)}} for a, b in pairs],
        comparisons=[{'left': 'u.c', 'right': 'u_ref.c'}],
        allow_wide_vectors=n == 8, run=True)
    assert result['ok'], result
    assert result['semantic']['input_bits'] == 2*n
    assert result['semantic']['expression_count'] == len(pairs)
    row = result['run']['tests'][0]
    assert row['returncode'] == 0
    assert 'SELFTEST PASS' in row['display_output']


def test_single_dut_dependent_parameter_width_and_literal_compatibility(project):
    proj, _ = project
    path = model(proj, 'dependent', {'BASE': 2, 'N': 'BASE*2'})
    result = tools.create_testbench(proj, 'tb_dependent', model_path=path,
        vectors=[{'drive': {'a': 15, 'b': 1}, 'expect': {'c': 0}}], run=True)
    assert result['ok'], result
    assert result['semantic']['input_bits'] == 8
    assert 'reg [3:0] a;' in result['code']


@pytest.mark.parametrize('parameters', ['N = M; M = N;', 'N = MISSING;'])
def test_invalid_parameter_dependencies_reject_before_writes(project, parameters):
    proj, root = project
    path = Path(model(proj, 'invalid', {'N': 4}))
    tree = ET.fromstring(path.read_bytes())
    tree.set('Parameters', parameters)
    path.write_bytes(ET.tostring(tree, encoding='utf-8'))
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (Path(proj), path)}
    result = server_app.create_testbench(proj, 'tb_invalid', model_path=str(path),
        vectors=[{'drive': {'a': 0, 'b': 0}, 'expect': {'c': 0}}])
    assert result['ok'] is False
    assert result['code'] == 'invalid_argument'
    assert 'hint' in result
    assert not (root/'test/tb_invalid.test').exists()
    assert before == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in before}


def test_resolved_parameter_width_still_enforces_limit(project):
    proj, root = project
    path = model(proj, 'too_wide', {'N': 4097})
    result = server_app.create_testbench(proj, 'tb_limit', model_path=path,
        vectors=[{'drive': {'a': 0, 'b': 0}, 'expect': {'c': 0}}])
    assert result['code'] == 'limit_exceeded'
    assert not (root/'test/tb_limit.test').exists()
