@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Python environment not found. Complete the README setup first.
    pause
    exit /b 1
)
set "PYTHONIOENCODING=utf-8"
".venv\Scripts\python.exe" -m app.local_start %*
set "AGENT_EXIT_CODE=%ERRORLEVEL%"
if not "%AGENT_EXIT_CODE%"=="0" pause
exit /b %AGENT_EXIT_CODE%
