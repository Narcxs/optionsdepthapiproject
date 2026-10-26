@echo off
REM Run this once, as Administrator, on the VPS.
REM Right-click the terminal icon, "Run as administrator", then just run
REM this script directly (no need to type any path — see below):
REM   deploy\create-scheduled-task.bat
REM
REM Creates a Scheduled Task that starts supervisor.py at logon — not at
REM boot: "At system startup" runs in Session 0, which has no desktop,
REM and Chromium (headless=False — see src/live.py) cannot open a
REM window there. "At log on" only works because
REM deploy\windows-session-settings.bat already set up autologon, so a
REM real desktop session exists right after a reboot for this to fire
REM into.
REM
REM Runs under whichever account this script itself is run as (%USERNAME%),
REM not SYSTEM: SYSTEM has no desktop session either, same reason as above.
REM
REM The project's install path is never hardcoded here: %~dp0 is the
REM folder this very script lives in (deploy\), so its parent is the
REM project root — correct no matter where the repo was cloned to,
REM without the operator having to type or verify a path by hand.

setlocal

for %%I in ("%~dp0..") do set PROJECT_DIR=%%~fI

set PYTHON_EXE=%PROJECT_DIR%\.venv\Scripts\python.exe
set SUPERVISOR=%PROJECT_DIR%\supervisor.py

echo Project directory: %PROJECT_DIR%

if not exist "%PYTHON_EXE%" (
    echo ERROR: %PYTHON_EXE% not found.
    echo The virtual environment does not seem to exist there yet.
    exit /b 1
)

schtasks /create /tn "OD-Supervisor" ^
    /tr "\"%PYTHON_EXE%\" \"%SUPERVISOR%\"" ^
    /sc onlogon ^
    /ru "%USERNAME%" ^
    /rl HIGHEST ^
    /f

echo.
echo Done. The task fires at the next logon (including the one autologon
echo performs after a reboot). It will NOT run right now.
echo To start supervisor.py immediately instead (without rebooting),
echo just run it directly: .venv\Scripts\python.exe supervisor.py

endlocal
