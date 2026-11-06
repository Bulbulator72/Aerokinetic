@echo off
setlocal
cd /d "%~dp0"
py -3.12 tools\prepare_offline.py --platform linux
if errorlevel 1 (
  pause
  exit /b 1
)
echo Linux wheels prepared. Target Linux needs CPython 3.12 and g++ installed separately.
pause
