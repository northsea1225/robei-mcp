@echo off
setlocal
chcp 65001 >nul
set "ROOT=%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
"%ROOT%.venv\Scripts\python.exe" -u "%ROOT%server.py"
