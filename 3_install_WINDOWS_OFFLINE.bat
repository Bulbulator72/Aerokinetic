@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0pystencils.json" set "PYSTENCILS_CONFIG=%~dp0pystencils.json"
if exist "%~dp0pystencils.json" set "PYSTENCILS_CACHE_DIR=%~dp0.cache\pystencils_symbolic"
py -3.12 tools\verify_offline.py downloads\wheels_windows
if errorlevel 1 goto fail
if not exist .venv\Scripts\python.exe py -3.12 -m venv .venv
if errorlevel 1 goto fail
.venv\Scripts\python.exe -m pip install --no-index --find-links downloads\wheels_windows -r requirements-offline-py312.txt
if errorlevel 1 goto fail
.venv\Scripts\python.exe -m pip install --no-index --find-links downloads\wheels_windows aerokinetic==0.5.1
if errorlevel 1 goto fail
.venv\Scripts\python.exe -m pip check
if errorlevel 1 goto fail
.venv\Scripts\python.exe -m pytest -q -o pythonpath=
if errorlevel 1 goto fail
echo Offline installation and numerical checks passed.
pause
exit /b 0
:fail
echo Installation or checks failed. C++ kernel compilation requires the MSVC C++ tools.
pause
exit /b 1
