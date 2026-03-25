@echo off
setlocal

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop.ps1"
if errorlevel 1 (
  echo.
  echo Stop failed. Review the messages above.
  pause
)
