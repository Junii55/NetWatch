@echo off
REM Double-click this to install NetWatch for the current user.
REM No administrator rights required.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
if errorlevel 1 (
  echo.
  echo Install failed. See the messages above.
  pause
)
