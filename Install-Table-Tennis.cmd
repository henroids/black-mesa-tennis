@echo off
rem Double-click to build the Table Tennis APK from your own ISO (see README.md).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\installer\install.ps1"
echo.
pause
