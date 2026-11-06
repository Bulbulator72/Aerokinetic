"""Deliver the standalone compiler repair as a self-contained TXT/BAT."""
import base64
import hashlib
import json
from pathlib import Path
import re


def main():
    root = Path(__file__).resolve().parents[1]
    source = (root / "tools/configure_windows_compiler.py").read_bytes()
    compile(source, "configure_windows_compiler.py", "exec")
    digest = hashlib.sha256(source).hexdigest()
    ps = " ".join([
        "$ErrorActionPreference='Stop'; $script=$null; $code=1;",
        "try {",
        "$raw=[IO.File]::ReadAllText($env:AEROKINETIC_FIX_SELF);",
        "$m=[regex]::Match($raw,'(?ms)^AEROKINETIC_FIX_BEGIN\\r?\\n(?<data>.*?)^AEROKINETIC_FIX_END\\r?$');",
        "if (-not $m.Success) { throw 'Save the entire TXT file, including the payload.' };",
        "$bytes=[Convert]::FromBase64String($m.Groups['data'].Value);",
        f"if ($bytes.Length -ne {len(source)}) {{ throw 'Incomplete repair script.' }};",
        "$sha=[Security.Cryptography.SHA256]::Create();",
        "try { $hash=([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-','').ToLowerInvariant() } finally { $sha.Dispose() };",
        f"if ($hash -ne '{digest}') {{ throw 'SHA-256 mismatch. Save the entire text again.' }};",
        "$script=Join-Path ([IO.Path]::GetTempPath()) ('AeroKineticCompiler_'+[Guid]::NewGuid().ToString('N')+'.py');",
        "[IO.File]::WriteAllBytes($script,$bytes);",
        "& $env:AEROKINETIC_FIX_PYTHON $script --root $env:AEROKINETIC_FIX_ROOT --no-dialog; $code=$LASTEXITCODE;",
        "} catch { Write-Host $_.Exception.Message -ForegroundColor Red; $code=1 }",
        "finally { if ($script -and (Test-Path -LiteralPath $script)) { Remove-Item -LiteralPath $script -ErrorAction SilentlyContinue } };",
        "exit $code;",
    ])
    assert '"' not in ps and len(ps) < 7000
    header = ["@echo off", "setlocal DisableDelayedExpansion", "title AeroKinetic - repair compiler configuration",
              "rem Save this entire text as FIX_COMPILER_WINDOWS.bat in the program folder.",
              'cd /d "%~dp0"', 'if not exist ".venv\\Scripts\\python.exe" (',
              "  echo Put this BAT in the folder containing .venv and 6_run_AEROKINETIC.bat.",
              "  pause", "  exit /b 1", ")",
              'set "AEROKINETIC_FIX_SELF=%~f0"', 'set "AEROKINETIC_FIX_ROOT=%~dp0."',
              'set "AEROKINETIC_FIX_PYTHON=%~dp0.venv\\Scripts\\python.exe"',
              "echo Close AeroKinetic before running this repair.",
              "echo Looking for an installed C++ compiler, then compiling a small test.",
              "echo No internet, package installation or administrator rights are needed for this check.",
              "echo The report is saved as COMPILER_CHECK.log in this folder.",
              f'powershell.exe -NoLogo -NoProfile -NonInteractive -Command "{ps}"',
              "set AEROKINETIC_FIX_RESULT=%ERRORLEVEL%",
              'if "%AEROKINETIC_FIX_RESULT%"=="2" (',
              "  echo C++ Tools were not found. Run 2_install_COMPILER_OFFLINE.bat AS ADMINISTRATOR.",
              "  echo After setup, reboot if requested and run this repair again.", ")",
              'if "%AEROKINETIC_FIX_RESULT%"=="1" echo Check COMPILER_CHECK.log for the first error.',
              'if "%AEROKINETIC_FIX_RESULT%"=="0" echo SUCCESS. Rerun 3_install_WINDOWS_OFFLINE.bat, then start the program.',
              "pause", "exit /b %AEROKINETIC_FIX_RESULT%", "", "AEROKINETIC_FIX_BEGIN"]
    encoded = base64.b64encode(source).decode("ascii")
    raw = ("\r\n".join(header) + "\r\n" + "\r\n".join(encoded[i:i+76] for i in range(0,len(encoded),76)) +
           "\r\nAEROKINETIC_FIX_END\r\n").encode("ascii")
    output = root.parent / "DOWNLOAD_AEROKINETIC/FIX_COMPILER_WINDOWS.txt"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(raw)
    output.chmod(0o644)
    for data in (raw, raw.replace(b"\r\n", b"\n")):
        match = re.search(rb"(?ms)^AEROKINETIC_FIX_BEGIN\r?\n(?P<data>.*?)^AEROKINETIC_FIX_END\r?$", data)
        assert base64.b64decode(re.sub(rb"\s+", b"", match["data"]), validate=True) == source
    report = {"file": output.name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
              "source_sha256": digest, "roundtrip_crlf_and_lf": "passed",
              "windows_execution": "not_run", "dependencies_or_solver_changed": False}
    output.with_suffix(".check.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
