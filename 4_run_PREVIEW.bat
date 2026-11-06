@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0pystencils.json" set "PYSTENCILS_CONFIG=%~dp0pystencils.json"
if exist "%~dp0pystencils.json" set "PYSTENCILS_CACHE_DIR=%~dp0.cache\pystencils_symbolic"
echo This is a coarse startup demonstration, not a validated polar or a resolved turbulence calculation.
set "AEROKINETIC_RUN=preview_%RANDOM%_%RANDOM%"
.venv\Scripts\python.exe -m aerokinetic.cli run examples\naca0012_preview.json --output "%AEROKINETIC_RUN%" --animate
if errorlevel 1 (
  pause
  exit /b 1
)
start "" "%AEROKINETIC_RUN%\omega_z.gif"
pause
