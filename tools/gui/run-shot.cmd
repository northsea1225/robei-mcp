@echo off
setlocal
set "LABEL=%~1"
if "%LABEL%"=="" set "LABEL=probe"
cd /d C:\dev\robei-mcp
".venv\Scripts\python.exe" "tools\gui\shot.py" "C:\dev\robei-mcp\workspace\gui_shots" "%LABEL%" > "C:\dev\robei-mcp\workspace\gui_shots\shot.log" 2>&1
exit /b %ERRORLEVEL%
