@echo off
setlocal
chcp 65001 >nul
set "ROOT=%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
cd /d "%ROOT%"
"%ROOT%.venv\Scripts\python.exe" -u -m robei_mcp.lifecycle --supervise
exit /b %errorlevel%
