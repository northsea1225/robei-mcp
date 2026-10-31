from __future__ import annotations

from robei_mcp import p1, tools
from robei_mcp.config import SETTINGS


def test_behavioral_suggestions_include_create_ports_for_exp1_exp3_exp6() -> None:
    cases = [("exp1_and", "and2"), ("exp3_decoder", "decode3_8"), ("exp6_counter", "dec_counter")]
    for project, name in cases:
        proj = SETTINGS.workspace / project / f"{project}.proj"
        source = SETTINGS.workspace / project / "verilog" / f"{name}.v"
        result = p1.import_verilog_canvas(str(proj), str(source.relative_to(SETTINGS.workspace)), mode="behavioral")
        assert result["create_robei_model"]["ports"]
        assert all({"name", "direction", "width"} <= set(port) for port in result["create_robei_model"]["ports"])


def test_run_all_tests_elapsed_ms_is_present() -> None:
    result = tools.run_all_tests(str(SETTINGS.workspace / "exp1_and" / "exp1_and.proj"))
    assert isinstance(result["elapsed_ms"], int)
    assert result["elapsed_ms"] >= 0
