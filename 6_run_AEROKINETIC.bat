@echo off
setlocal
if not defined OPENBLAS_NUM_THREADS set "OPENBLAS_NUM_THREADS=1"
if not defined OMP_WAIT_POLICY set "OMP_WAIT_POLICY=PASSIVE"
cd /d "%~dp0"
if exist "%~dp0pystencils.json" set "PYSTENCILS_CONFIG=%~dp0pystencils.json"
if exist "%~dp0pystencils.json" set "PYSTENCILS_CACHE_DIR=%~dp0.cache\pystencils_symbolic"
if not exist .venv\Scripts\python.exe (
  echo First run 3_install_WINDOWS_OFFLINE.bat.
  pause
  exit /b 1
)
.venv\Scripts\python.exe -m aerokinetic.gui %*
if errorlevel 1 (
  echo AeroKinetic could not start. Keep the error text above.
  pause
  exit /b 1
)
