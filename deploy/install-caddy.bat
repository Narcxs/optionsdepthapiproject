@echo off
REM Run this once on the VPS, with internet access, before
REM create-caddy-task.bat. Downloads the official Windows build of
REM caddy.exe straight from Caddy's own build server — no installer,
REM no extra package manager needed.
REM   deploy\install-caddy.bat

setlocal

for %%I in ("%~dp0..") do set PROJECT_DIR=%%~fI
set CADDY_DIR=%PROJECT_DIR%\deploy\caddy
set CADDY_EXE=%CADDY_DIR%\caddy.exe

if not exist "%CADDY_DIR%" mkdir "%CADDY_DIR%"

echo Downloading caddy.exe (Windows amd64) from caddyserver.com...
REM The whole -Command argument is inside one pair of double quotes,
REM so the & in the URL is already protected from cmd.exe's own
REM command-separator parsing — no caret-escaping needed here.
powershell -NoProfile -Command "Invoke-WebRequest -Uri 'https://caddyserver.com/api/download?os=windows&arch=amd64' -OutFile '%CADDY_EXE%'"

if not exist "%CADDY_EXE%" (
    echo ERROR: download failed — check this VPS has internet access.
    exit /b 1
)

echo.
echo Done: %CADDY_EXE%
echo Next: check deploy\Caddyfile has the right domain, then run
echo deploy\create-caddy-task.bat

endlocal
