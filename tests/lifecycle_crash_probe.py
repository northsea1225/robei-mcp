"""Test-only entry. The production entry never reads this opt-in switch."""
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from robei_mcp.lifecycle import run_server

if __name__ == "__main__":
    run_server(_test_crash=os.environ.get("ROBEI_MCP_TEST_CRASH") == "1")
