@echo off
REM Run this once, as Administrator, on the VPS — not on your own machine.
REM Right-click the terminal icon, "Run as administrator", then:
REM   cd C:\apps\od-scraper
REM   deploy\windows-session-settings.bat
REM
REM What it does, and why:
REM
REM   1. Enables autologon for the account this is run under. Without
REM      it, a VPS reboot leaves the machine sitting at the login
REM      screen forever — no desktop session exists for Chromium to
REM      open a window into (the scraper needs headless=False), so
REM      supervisor.py would never actually start capturing again.
REM
REM   2. Disables the RDP session's disconnect/idle timeout. Without
REM      it, closing your RDP client (not logging off — just closing
REM      the window) can let Windows tear down the session after a
REM      default timeout, which kills Chromium and the scraper with
REM      it. This does not affect a real "Sign out" — only the
REM      disconnect-without-signing-out case this deployment relies on.
REM
REM Assumes the account has **no password** (confirmed for this VPS).
REM If it ever gets one, DefaultPassword below must be set too, or
REM autologon will stop working silently.

setlocal

REM %USERNAME% is already just the account name (e.g. "Administrator"),
REM set directly by Windows — no need to parse it out of anything.
echo Configuring autologon and RDP session timeouts for account: %USERNAME%
echo.

reg add "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon" /v AutoAdminLogon /t REG_SZ /d 1 /f
reg add "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon" /v DefaultUserName /t REG_SZ /d "%USERNAME%" /f
reg add "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon" /v DefaultPassword /t REG_SZ /d "" /f

reg add "HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp" /v MaxDisconnectionTime /t REG_DWORD /d 0 /f
reg add "HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp" /v MaxIdleTime /t REG_DWORD /d 0 /f

echo.
echo Done. These settings take effect on the next reboot / RDP reconnect.

endlocal
