@echo off
REM Run this once, as Administrator, on the VPS.
REM Right-click the terminal icon, "Run as administrator", then just
REM run this script directly (no need to type any path):
REM   deploy\create-api-task.bat
REM
REM Creates a Scheduled Task that starts the data API (deploy\run-api.bat,
REM which runs `uvicorn server.main:app`) at boot, under SYSTEM — unlike
REM supervisor.py's task (see create-scheduled-task.bat), this one does
REM NOT need a desktop session: the API only reads data/od.db and
REM serves HTTP, no browser involved. Running it under SYSTEM at
REM startup means it comes up on its own even before anyone logs in,
REM and does not depend on autologon at all.
REM
REM Binds to 127.0.0.1:8000 only (see run-api.bat) — reachable from
REM this machine, not yet from the internet. Putting a reverse proxy
REM (for TLS and a real domain) in front of it is a separate step.

setlocal

for %%I in ("%~dp0..") do set PROJECT_DIR=%%~fI

set PYTHON_EXE=%PROJECT_DIR%\.venv\Scripts\python.exe
set RUN_API=%PROJECT_DIR%\deploy\run-api.bat

echo Project directory: %PROJECT_DIR%

if not exist "%PYTHON_EXE%" (
    echo ERROR: %PYTHON_EXE% not found.
    echo The virtual environment does not seem to exist there yet.
    exit /b 1
)

schtasks /create /tn "OD-API" ^
    /tr "\"%RUN_API%\"" ^
    /sc onstart ^
    /ru "SYSTEM" ^
    /rl HIGHEST ^
    /f

echo.
echo Done. The task fires at boot, under SYSTEM — no logon needed.
echo It will NOT run right now. To start it immediately instead
echo (without rebooting): schtasks /run /tn "OD-API"

endlocal
