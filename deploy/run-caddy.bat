@echo off
REM Started by the "OD-Caddy" Scheduled Task (see create-caddy-task.bat)
REM — not meant to be run by hand, though nothing stops you from
REM testing it that way.
REM
REM Restarts caddy if it ever exits, same reasoning as run-api.bat:
REM Task Scheduler has no built-in retry for a plain command trigger.
REM
REM Run from deploy\ (not the project root) so Caddy's own storage
REM (certificates, OCSP staples — managed automatically, nothing to
REM configure) lands under deploy\ rather than scattered wherever the
REM task happened to start from.

setlocal

for %%I in ("%~dp0..") do set PROJECT_DIR=%%~fI
set CADDY_EXE=%PROJECT_DIR%\deploy\caddy\caddy.exe
set CADDYFILE=%PROJECT_DIR%\deploy\Caddyfile

cd /d "%PROJECT_DIR%\deploy"

:loop
"%CADDY_EXE%" run --config "%CADDYFILE%" --adapter caddyfile
echo [run-caddy] caddy exited (code %errorlevel%) — restarting in 5 seconds...
timeout /t 5 /nobreak >nul
goto loop
