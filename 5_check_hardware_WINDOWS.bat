@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\collect_hardware_windows.ps1" -ReportPath "%~dp0hardware_windows.json"
if errorlevel 1 (
  echo Hardware detection failed. The numerical solver was not started.
  pause
  exit /b 1
)
echo Diagnostic report saved to hardware_windows.json. No software was installed.
pause
