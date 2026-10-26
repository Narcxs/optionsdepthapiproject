@echo off
REM Started by the "OD-API" Scheduled Task (see create-api-task.bat) —
REM not meant to be run by hand, though nothing stops you from testing
REM it that way.
REM
REM Restarts uvicorn if it ever exits, mirroring the systemd unit's
REM `Restart=on-failure` / `RestartSec=5` (deploy/od-api.service, the
REM Linux version of this same service). Task Scheduler has no
REM built-in "restart this process if it crashes" for a command-line
REM trigger, so the loop does it instead.
REM
REM The project's install path is never hardcoded: %~dp0 is the folder
REM this script lives in (deploy\), so its parent is the project root —
REM correct no matter where the repo was cloned to.

setlocal

for %%I in ("%~dp0..") do set PROJECT_DIR=%%~fI

cd /d "%PROJECT_DIR%"

:loop
"%PROJECT_DIR%\.venv\Scripts\python.exe" -m uvicorn server.main:app --host 127.0.0.1 --port 8000
echo [run-api] uvicorn exited (code %errorlevel%) — restarting in 5 seconds...
timeout /t 5 /nobreak >nul
goto loop
