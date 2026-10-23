@echo off
setlocal
cd /d "%~dp0..\.."
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set /p ARGS=<"%~dp0..\..\workspace\gui_agent\agent-args.txt"
".venv\Scripts\python.exe" -m robei_mcp.gui_agent.driver %ARGS% >> "%~dp0..\..\workspace\gui_shots\agent.log" 2>&1
exit /b %ERRORLEVEL%
