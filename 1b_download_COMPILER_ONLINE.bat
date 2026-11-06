@echo off
setlocal DisableDelayedExpansion
title AeroKinetic - download offline C++ Build Tools
cd /d "%~dp0"
if errorlevel 1 goto folder_fail
if not exist "downloads" mkdir "downloads"
if not exist "downloads" goto folder_fail
set "AEROKINETIC_COMPILER_LOG=%CD%\downloads\compiler_download.log"
set "AEROKINETIC_COMPILER_LAYOUT=%CD%\downloads\vs_buildtools"
set "AEROKINETIC_COMPILER_PART=%CD%\downloads\vs_BuildTools.part.exe"
set "AEROKINETIC_COMPILER_EXE=%CD%\downloads\vs_BuildTools.exe"
set "AEROKINETIC_COMPILER_URL=https://aka.ms/vs/17/release/vs_BuildTools.exe"
> "%AEROKINETIC_COMPILER_LOG%" echo AeroKinetic compiler preparation: %DATE% %TIME%
if errorlevel 1 goto folder_fail
echo.
echo AeroKinetic: prepare Visual Studio 2022 C++ Build Tools for OFFLINE use.
echo Internet is required. Run this BAT as administrator.
echo Several GB will be downloaded; this can take a long time.
echo Log: "%AEROKINETIC_COMPILER_LOG%"
echo Layout: "%AEROKINETIC_COMPILER_LAYOUT%"
echo.
rem Microsoft documents a layout path shorter than 80 characters.
powershell.exe -NoLogo -NoProfile -NonInteractive -Command "if ($env:AEROKINETIC_COMPILER_LAYOUT.Length -ge 80) { $message='Layout path is too long. Move the program folder to C:\AK and run this BAT again.'; Write-Host $message -ForegroundColor Red; Add-Content -LiteralPath $env:AEROKINETIC_COMPILER_LOG -Value $message -Encoding UTF8; exit 1 }; exit 0"
if errorlevel 1 goto fail
echo [1/4] Downloading the Microsoft bootstrapper, approximately 5 MB...
>> "%AEROKINETIC_COMPILER_LOG%" echo [1/4] Download bootstrapper
where.exe curl.exe >nul 2>&1
if errorlevel 1 goto powershell_download
curl.exe --fail --location --show-error --progress-bar --connect-timeout 20 --max-time 180 --output "%AEROKINETIC_COMPILER_PART%" "%AEROKINETIC_COMPILER_URL%"
set "AEROKINETIC_RESULT=%ERRORLEVEL%"
>> "%AEROKINETIC_COMPILER_LOG%" echo curl exit code: %AEROKINETIC_RESULT%
if "%AEROKINETIC_RESULT%"=="0" goto check_bootstrapper
echo curl failed with code %AEROKINETIC_RESULT%. Trying Windows PowerShell...
:powershell_download
echo PowerShell download: progress is shown; request timeout is 120 seconds.
powershell.exe -NoLogo -NoProfile -NonInteractive -Command "$ErrorActionPreference='Stop'; $ProgressPreference='Continue'; try { [Net.ServicePointManager]::SecurityProtocol=[Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -UseBasicParsing -Uri $env:AEROKINETIC_COMPILER_URL -OutFile $env:AEROKINETIC_COMPILER_PART -TimeoutSec 120; exit 0 } catch { $message=($_ | Out-String); Write-Host $message -ForegroundColor Red; Add-Content -LiteralPath $env:AEROKINETIC_COMPILER_LOG -Value $message -Encoding UTF8; exit 1 }"
if errorlevel 1 goto fail
:check_bootstrapper
echo Checking the downloaded executable and its Microsoft signature...
powershell.exe -NoLogo -NoProfile -NonInteractive -Command "$ErrorActionPreference='Stop'; try { $p=$env:AEROKINETIC_COMPILER_PART; $f=Get-Item -LiteralPath $p; if ($f.Length -lt 100000) { throw 'Bootstrapper is too small; the download may contain an HTML error page.' }; $signature=Get-AuthenticodeSignature -LiteralPath $p; if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation(?:,|$)') { throw ('Microsoft signature check failed: '+$signature.Status+'; '+$signature.StatusMessage) }; $line='Bootstrapper SHA-256: '+(Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash; Write-Host $line; Add-Content -LiteralPath $env:AEROKINETIC_COMPILER_LOG -Value $line -Encoding UTF8; Move-Item -LiteralPath $p -Destination $env:AEROKINETIC_COMPILER_EXE -Force; exit 0 } catch { $message=($_ | Out-String); Write-Host $message -ForegroundColor Red; Add-Content -LiteralPath $env:AEROKINETIC_COMPILER_LOG -Value $message -Encoding UTF8; exit 1 }"
if errorlevel 1 goto fail
echo.
echo [2/4] Downloading the full C++ workload. Watch the Microsoft progress window.
echo The bootstrapper alone is NOT the offline compiler package.
>> "%AEROKINETIC_COMPILER_LOG%" echo [2/4] Create or resume the C++ layout
start "" /wait "%AEROKINETIC_COMPILER_EXE%" --layout "%AEROKINETIC_COMPILER_LAYOUT%" --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended --lang en-US --wait
set "AEROKINETIC_RESULT=%ERRORLEVEL%"
>> "%AEROKINETIC_COMPILER_LOG%" echo Layout exit code: %AEROKINETIC_RESULT%
if "%AEROKINETIC_RESULT%"=="3010" echo Microsoft reports that a reboot is required.
if not "%AEROKINETIC_RESULT%"=="0" if not "%AEROKINETIC_RESULT%"=="3010" goto layout_fail
if not exist "%AEROKINETIC_COMPILER_LAYOUT%\vs_BuildTools.exe" goto layout_missing
if not exist "%AEROKINETIC_COMPILER_LAYOUT%\Catalog.json" goto layout_missing
echo.
echo [3/4] Verifying all downloaded compiler packages...
>> "%AEROKINETIC_COMPILER_LOG%" echo [3/4] Verify layout
start "" /wait "%AEROKINETIC_COMPILER_EXE%" --layout "%AEROKINETIC_COMPILER_LAYOUT%" --verify --wait
set "AEROKINETIC_RESULT=%ERRORLEVEL%"
>> "%AEROKINETIC_COMPILER_LOG%" echo Verification exit code: %AEROKINETIC_RESULT%
if not "%AEROKINETIC_RESULT%"=="0" goto verify_fail
>> "%AEROKINETIC_COMPILER_LOG%" echo SUCCESS: layout created and verified by Microsoft bootstrapper.
echo.
echo [4/4] Preparing the current Microsoft code-signing intermediate certificate...
echo This step downloads a certificate only. It does not import certificates or change Windows trust stores.
>> "%AEROKINETIC_COMPILER_LOG%" echo [4/4] Download Microsoft Windows Code Signing PCA 2024
powershell.exe -NoLogo -NoProfile -NonInteractive -Command "$ErrorActionPreference='Stop'; try { [Net.ServicePointManager]::SecurityProtocol=[Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12; $folder=Join-Path $env:AEROKINETIC_COMPILER_LAYOUT 'ExtraCertificates\Intermediate'; New-Item -ItemType Directory -Path $folder -Force | Out-Null; $target=Join-Path $folder 'MicrosoftWindowsCodeSigningPCA2024.cer'; $part=$target+'.part'; $url='https://www.microsoft.com/pkiops/certs/'+[Uri]::EscapeDataString('Microsoft Windows Code Signing PCA 2024.crt'); Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $part -TimeoutSec 120; $hash=(Get-FileHash -LiteralPath $part -Algorithm SHA256).Hash.ToLowerInvariant(); if ($hash -ne 'fe229efc927f6d77b738896752a21803a59736baa17bf5be9a50c72e219cbcd2') { throw 'The published intermediate certificate differs from the checked copy. Keep the old layout and review the Microsoft certificate before using this new file.' }; $cert=[Security.Cryptography.X509Certificates.X509Certificate2]::new($part); try { $subject=$cert.Subject; if ($subject -notmatch 'CN=Microsoft Windows Code Signing PCA 2024(?:,|$)') { throw 'Unexpected certificate subject' } } finally { $cert.Dispose() }; Move-Item -LiteralPath $part -Destination $target -Force; $readme=@('Microsoft Windows Code Signing PCA 2024 - INTERMEDIATE certificate.','Source: '+$url,'SHA-256: '+$hash,'On the offline PC: Install Certificate -> Local Machine -> Intermediate Certification Authorities.','The layout Certificates folder contains the separate Microsoft root certificates.','No certificate was imported by this download script.','This prepares a Microsoft-recommended offline prerequisite; it does not prove the cause of a particular installation failure.'); Set-Content -LiteralPath (Join-Path $folder 'READ_ME.txt') -Value $readme -Encoding UTF8; $message='Intermediate certificate saved: '+$target+'; subject: '+$subject+'; SHA-256: '+$hash; Write-Host $message; Add-Content -LiteralPath $env:AEROKINETIC_COMPILER_LOG -Value $message -Encoding UTF8; exit 0 } catch { $message=($_ | Out-String); Write-Host $message -ForegroundColor Red; Add-Content -LiteralPath $env:AEROKINETIC_COMPILER_LOG -Value $message -Encoding UTF8; exit 1 }"
if errorlevel 1 goto certificate_fail
echo.
echo SUCCESS: the offline C++ compiler layout is ready and verified.
echo Copy the ENTIRE program folder including downloads to the offline PC.
echo Additional certificate: downloads\vs_buildtools\ExtraCertificates\Intermediate.
echo If required, import that certificate into Local Machine - Intermediate Certification Authorities.
echo On that PC, run 2_install_COMPILER_OFFLINE.bat as administrator.
pause
exit /b 0
:layout_missing
>> "%AEROKINETIC_COMPILER_LOG%" echo ERROR: bootstrapper or Catalog.json is missing from the layout.
echo ERROR: the layout is incomplete. Keep the downloads folder and rerun this BAT.
goto fail
:layout_fail
echo ERROR: Microsoft layout creation failed with code %AEROKINETIC_RESULT%.
goto collect_logs
:verify_fail
echo ERROR: Microsoft layout verification failed with code %AEROKINETIC_RESULT%.
echo Keep the downloads folder and rerun this BAT online to complete it.
goto collect_logs
:certificate_fail
echo ERROR: compiler packages were verified, but the additional Microsoft certificate could not be prepared.
echo Send downloads\compiler_download.log. The certificate has not been imported.
:collect_logs
echo Collecting the most recent Microsoft installer logs...
powershell.exe -NoLogo -NoProfile -NonInteractive -Command "$ErrorActionPreference='Continue'; $files=Get-ChildItem -LiteralPath $env:TEMP -Filter 'dd_*.log' -File -ErrorAction SilentlyContinue | Where-Object { $_.Name -match '^dd_(bootstrapper|setup|vs_)' } | Sort-Object LastWriteTime -Descending | Select-Object -First 6; foreach ($f in $files) { $dest=Join-Path ([IO.Path]::GetDirectoryName($env:AEROKINETIC_COMPILER_LOG)) $f.Name; Copy-Item -LiteralPath $f.FullName -Destination $dest -Force -ErrorAction Continue; Add-Content -LiteralPath $env:AEROKINETIC_COMPILER_LOG -Value ('Microsoft log: '+$f.Name) -Encoding UTF8 }"
:fail
echo.
echo FAILED. The offline compiler is not ready.
echo Copy the error text above or send downloads\compiler_download.log.
echo The last Microsoft exit code, if any, is recorded in that log.
echo If the download was interrupted, rerun this BAT; keep existing downloads.
pause
exit /b 1
:folder_fail
echo ERROR: cannot write to the program folder. Extract it to C:\AK and retry.
pause
exit /b 1
