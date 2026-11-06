@echo off
setlocal
cd /d "%~dp0"
py -3.12 -c "import struct,sys; assert struct.calcsize('P')==8; assert sys.version_info[:2]==(3,12)" >nul 2>&1
if errorlevel 1 (
  if not exist downloads mkdir downloads
  powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe' -OutFile 'downloads\python-3.12.10-amd64.exe'"
  if errorlevel 1 goto fail
  echo Install downloads\python-3.12.10-amd64.exe, then run this BAT again.
  pause
  exit /b 1
)
py -3.12 tools\prepare_offline.py --platform windows --python-installer
if errorlevel 1 goto fail
echo Wheels and Python installer are ready. If MSVC C++ is absent, run 1b_download_COMPILER_ONLINE.bat too.
pause
exit /b 0
:fail
echo Preparation failed. Keep the error output and retry on a connected computer.
pause
exit /b 1
