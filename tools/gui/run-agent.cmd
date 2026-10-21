@echo off
setlocal
cd /d C:\dev\robei-mcp
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set /p ARGS=<"C:\dev\robei-mcp\tools\gui\agent-args.txt"
".venv\Scripts\python.exe" "tools\gui\agent.py" %ARGS% >> "C:\dev\robei-mcp\workspace\gui_shots\agent.log" 2>&1
exit /b %ERRORLEVEL%
