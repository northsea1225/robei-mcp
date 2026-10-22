"""pytest entry point for the portable core checks.

Each group runs as its own test case, so CI reports which subsystem broke
(simulate / testbench / robei-xml / headless / lint) instead of one big failure.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("portable_core", ROOT / "tests" / "portable_core.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("group", ["simulate", "testbench", "robei-xml", "headless", "lint"])
def test_group(group: str) -> None:
    module = _load()
    module.results.clear()
    module.GROUPS[group]()
    failed = [name for name, state in module.results if state == "FAIL"]
    assert not failed, f"{group}: {failed}"
