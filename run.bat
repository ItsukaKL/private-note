@echo off
setlocal

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1"
if errorlevel 1 (
  echo.
  echo Startup failed. Review the messages above.
  pause
)
