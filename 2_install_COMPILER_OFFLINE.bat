@echo off
setlocal DisableDelayedExpansion
title AeroKinetic - install offline C++ Build Tools
cd /d "%~dp0"
if errorlevel 1 goto folder_fail
if not exist "downloads" mkdir "downloads"
if not exist "downloads" goto folder_fail
set "AEROKINETIC_INSTALL_LOG=%CD%\downloads\compiler_install.log"
set "AEROKINETIC_INSTALL_LAYOUT=%CD%\downloads\vs_buildtools"
set "AEROKINETIC_INSTALL_EXE=%CD%\downloads\vs_buildtools\vs_BuildTools.exe"
set "AEROKINETIC_INSTALL_CHANNEL=%CD%\downloads\vs_buildtools\ChannelManifest.json"
set "AEROKINETIC_INSTALL_DETAILS=%CD%\downloads\compiler_install_details"
> "%AEROKINETIC_INSTALL_LOG%" echo AeroKinetic offline compiler installation: %DATE% %TIME%
if errorlevel 1 goto folder_fail
>> "%AEROKINETIC_INSTALL_LOG%" echo Layout: "%AEROKINETIC_INSTALL_LAYOUT%"
>> "%AEROKINETIC_INSTALL_LOG%" echo Channel: "%AEROKINETIC_INSTALL_CHANNEL%"
>> "%AEROKINETIC_INSTALL_LOG%" echo Arguments: --noWeb --channelUri LOCAL_CHANNEL_MANIFEST --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended --addProductLang en-US --passive --wait --norestart

echo.
echo AeroKinetic: install the C++ compiler from the LOCAL offline layout.
echo Run this BAT as administrator. No package downloads are requested.
echo Layout: "%AEROKINETIC_INSTALL_LAYOUT%"
echo Report: "%AEROKINETIC_INSTALL_LOG%"
echo.
echo [1/3] Checking administrator rights and layout files...
powershell.exe -NoLogo -NoProfile -NonInteractive -Command "$ErrorActionPreference='Stop'; try { $identity=[Security.Principal.WindowsIdentity]::GetCurrent(); $principal=New-Object Security.Principal.WindowsPrincipal($identity); if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { Write-Host 'Administrator rights are required. Right-click this BAT and choose Run as administrator.'; exit 1 }; exit 0 } catch { Write-Host $_.Exception.Message; exit 1 }"
if errorlevel 1 goto admin_fail
if not exist "%AEROKINETIC_INSTALL_EXE%" goto layout_fail
if not exist "%AEROKINETIC_INSTALL_LAYOUT%\Catalog.json" goto layout_fail
if not exist "%AEROKINETIC_INSTALL_CHANNEL%" goto channel_fail

echo [2/3] Starting the Microsoft installer. Wait for its window to finish.
echo Requested workload: Visual Studio C++ Build Tools, English packages.
echo The --noWeb option prevents downloading missing product packages.
start "" /wait "%AEROKINETIC_INSTALL_EXE%" --noWeb --channelUri "%AEROKINETIC_INSTALL_CHANNEL%" --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended --addProductLang en-US --passive --wait --norestart
set "AEROKINETIC_INSTALL_RESULT=%ERRORLEVEL%"
goto report_result

:admin_fail
set "AEROKINETIC_INSTALL_RESULT=740"
echo ERROR: administrator check failed. Right-click this BAT and choose Run as administrator.
>> "%AEROKINETIC_INSTALL_LOG%" echo Administrator check failed or could not be completed.
goto report_result

:layout_fail
set "AEROKINETIC_INSTALL_RESULT=2"
echo ERROR: vs_BuildTools.exe or Catalog.json is missing from the offline layout.
echo Check that the ENTIRE downloads\vs_buildtools folder was copied, without an extra nested folder.
>> "%AEROKINETIC_INSTALL_LOG%" echo Missing vs_BuildTools.exe or Catalog.json in the expected layout folder.
goto report_result

:channel_fail
set "AEROKINETIC_INSTALL_RESULT=3"
echo ERROR: ChannelManifest.json is missing from the local layout.
echo Restore the original manifest from the SAME offline layout. Do not substitute a manifest from another version.
>> "%AEROKINETIC_INSTALL_LOG%" echo Local ChannelManifest.json is missing; bootstrapper was not started.
goto report_result

:report_result
echo.
echo INSTALL_EXIT_CODE=%AEROKINETIC_INSTALL_RESULT%
>> "%AEROKINETIC_INSTALL_LOG%" echo INSTALL_EXIT_CODE=%AEROKINETIC_INSTALL_RESULT%
if "%AEROKINETIC_INSTALL_RESULT%"=="0" echo Installer reports SUCCESS. Next run 3_install_WINDOWS_OFFLINE.bat.
if "%AEROKINETIC_INSTALL_RESULT%"=="3010" echo Installer reports SUCCESS. REBOOT Windows before running 3_install_WINDOWS_OFFLINE.bat.
if "%AEROKINETIC_INSTALL_RESULT%"=="740" echo Administrator rights are required or the administrator check failed.
if "%AEROKINETIC_INSTALL_RESULT%"=="1001" echo Another Visual Studio Installer process is already running. Finish or close that installer and retry.
if "%AEROKINETIC_INSTALL_RESULT%"=="1618" echo Another installation is running. Let it finish, then retry.
if "%AEROKINETIC_INSTALL_RESULT%"=="5003" echo Bootstrapper could not obtain the installer. Inspect the copied Microsoft logs; do not remove --noWeb.
if "%AEROKINETIC_INSTALL_RESULT%"=="5005" echo Microsoft bootstrapper rejected its command-line arguments. Send the report below.
if not "%AEROKINETIC_INSTALL_RESULT%"=="0" if not "%AEROKINETIC_INSTALL_RESULT%"=="3010" echo Installation did not report success. The exact code is shown above and saved in the report.

echo.
echo [3/3] Copying recent Microsoft setup logs for diagnosis...
powershell.exe -NoLogo -NoProfile -NonInteractive -Command "$ErrorActionPreference='Stop'; try { $folder=Join-Path $env:AEROKINETIC_INSTALL_DETAILS ([DateTime]::Now.ToString('yyyyMMdd_HHmmss')+'_'+[Guid]::NewGuid().ToString('N').Substring(0,6)); New-Item -ItemType Directory -Path $folder -Force | Out-Null; $files=@(Get-ChildItem -LiteralPath $env:TEMP -Filter 'dd_*.log' -File -ErrorAction SilentlyContinue | Where-Object { $_.Name -match '^dd_(bootstrapper|setup|installer|vs_)' } | Sort-Object LastWriteTime -Descending | Select-Object -First 8); foreach ($file in $files) { Copy-Item -LiteralPath $file.FullName -Destination (Join-Path $folder $file.Name) -ErrorAction Stop }; $message='Recent Microsoft logs copied: '+$files.Count+'; folder: '+$folder; Write-Host $message; Add-Content -LiteralPath $env:AEROKINETIC_INSTALL_LOG -Value $message -Encoding UTF8; if ($files.Count -eq 0) { Add-Content -LiteralPath $env:AEROKINETIC_INSTALL_LOG -Value 'No matching Microsoft logs found in TEMP for this account.' -Encoding UTF8 }; exit 0 } catch { $message='Could not collect Microsoft logs: '+$_.Exception.Message; Write-Host $message; Add-Content -LiteralPath $env:AEROKINETIC_INSTALL_LOG -Value $message -Encoding UTF8 -ErrorAction SilentlyContinue; exit 1 }"
echo.
echo Report: "%AEROKINETIC_INSTALL_LOG%"
echo If installation failed, send this report or the INSTALL_EXIT_CODE shown above.
echo The final Press any key message is only a pause, not the installer result.
pause
exit /b %AEROKINETIC_INSTALL_RESULT%

:folder_fail
echo ERROR: cannot open or write to the program folder.
echo Extract or copy the program into a writable local folder and retry.
pause
exit /b 1
