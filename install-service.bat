@echo off
REM One-click: register + start the OmSreeSyncAgent auto-start service.
REM Double-click this file. If it fails on permissions, right-click -> Run as administrator.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-service.ps1"
echo.
pause
