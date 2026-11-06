@echo off
setlocal
cd /d "%~dp0"
py -3.12 -c "import struct; assert struct.calcsize('P')==8" >nul 2>&1
if not errorlevel 1 (
  echo CPython 3.12 x64 is already available.
  pause
  exit /b 0
)
if not exist downloads\python-3.12.10-amd64.exe (
  echo Run the online preparation first and copy downloads here.
  pause
  exit /b 1
)
start /wait "" downloads\python-3.12.10-amd64.exe /passive InstallAllUsers=0 InstallLauncherAllUsers=0 PrependPath=1 Include_test=0
set "AEROKINETIC_RESULT=%ERRORLEVEL%"
echo Reopen the terminal after the Python installation.
pause
exit /b %AEROKINETIC_RESULT%
