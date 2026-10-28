from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_probe_push():
    path = Path(__file__).parents[1] / "tools" / "gui" / "probe_push.py"
    spec = importlib.util.spec_from_file_location("probe_push_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_tag_push_command_and_ref_validation(monkeypatch):
    probe_push = _load_probe_push()
    calls = []

    def fake_run(args, **kwargs):
        calls.append(list(args))
        return probe_push.subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(probe_push.subprocess, "run", fake_run)
    result = probe_push.run("refs/tags/v1.6.1")

    assert result["ok"] is True
    assert ["git", "push", "origin", "refs/tags/v1.6.1"] in calls
    assert ["git", "rev-parse", "--short", "refs/tags/v1.6.1^{commit}"] in calls

    invalid = probe_push.run("refs/tags/v1.6.1;bad")
    assert invalid["ok"] is False
    assert "refs/tags/v*" in invalid["error"]
