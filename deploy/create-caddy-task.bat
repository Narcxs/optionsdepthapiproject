@echo off
REM Run this once, as Administrator, on the VPS, AFTER
REM deploy\install-caddy.bat has downloaded caddy.exe.
REM   deploy\create-caddy-task.bat
REM
REM Creates a Scheduled Task that starts Caddy (deploy\run-caddy.bat,
REM which reverse-proxies deploy\Caddyfile's domain to the API at
REM 127.0.0.1:8000 — see deploy\run-api.bat) at boot, under SYSTEM —
REM same reasoning as the API's own task: Caddy needs no desktop
REM session, only network access, so it comes up before any logon.
REM
REM Also opens inbound TCP 80 and 443 in the **local** Windows
REM Firewall — required both for normal HTTPS traffic (443) and for
REM Let's Encrypt's HTTP-01 challenge (80), which Caddy uses the first
REM time it issues a certificate. This does NOT touch any firewall at
REM the hosting provider / cloud level (a security group, a separate
REM network firewall panel, ...) — if the VPS sits behind one of
REM those, 80 and 443 must be opened there too, by hand, outside this
REM script's reach.

setlocal

for %%I in ("%~dp0..") do set PROJECT_DIR=%%~fI

set CADDY_EXE=%PROJECT_DIR%\deploy\caddy\caddy.exe
set RUN_CADDY=%PROJECT_DIR%\deploy\run-caddy.bat

echo Project directory: %PROJECT_DIR%

if not exist "%CADDY_EXE%" (
    echo ERROR: %CADDY_EXE% not found.
    echo Run deploy\install-caddy.bat first.
    exit /b 1
)

netsh advfirewall firewall add rule name="OD-Caddy-HTTP" dir=in action=allow protocol=TCP localport=80
netsh advfirewall firewall add rule name="OD-Caddy-HTTPS" dir=in action=allow protocol=TCP localport=443

schtasks /create /tn "OD-Caddy" ^
    /tr "\"%RUN_CADDY%\"" ^
    /sc onstart ^
    /ru "SYSTEM" ^
    /rl HIGHEST ^
    /f

echo.
echo Done. The task fires at boot, under SYSTEM.
echo It will NOT run right now. To start it immediately instead
echo (without rebooting): schtasks /run /tn "OD-Caddy"
echo.
echo Before starting it, make sure:
echo   - the domain in deploy\Caddyfile has a DNS A record pointing
echo     at this VPS's public IP, and it has propagated
echo   - if this VPS is behind a cloud/provider firewall or security
echo     group (separate from Windows Firewall), ports 80 and 443 are
echo     open there too — this script only opened the local one

endlocal
