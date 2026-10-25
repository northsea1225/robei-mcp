#!/usr/bin/env bash
# Read-only. Never starts/restarts the MCP service or changes the remote machine.
set -euo pipefail
ssh my-win 'cd /c/dev/robei-mcp && ./.venv/Scripts/python.exe -m robei_mcp.lifecycle --health'
