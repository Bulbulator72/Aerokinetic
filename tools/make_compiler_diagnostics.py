"""Package the read-only collector as one self-extracting TXT/BAT."""
import base64
import hashlib
import json
from pathlib import Path
import re


def main():
    root = Path(__file__).resolve().parents[1]
    source = (root / "tools/collect_compiler_diagnostics.py").read_bytes()
    compile(source, "collect_compiler_diagnostics.py", "exec")
    digest = hashlib.sha256(source).hexdigest()
    ps = " ".join([
        "$ErrorActionPreference='Stop'; $script=$null; $code=1; try {",
        "$raw=[IO.File]::ReadAllText($env:AK_DIAG_SELF);",
        "$m=[regex]::Match($raw,'(?ms)^AK_DIAG_BEGIN\\r?\\n(?<data>.*?)^AK_DIAG_END\\r?$');",
        "if (-not $m.Success) { throw 'Save the entire TXT, including its payload.' };",
        "$bytes=[Convert]::FromBase64String($m.Groups['data'].Value);",
        f"if ($bytes.Length -ne {len(source)}) {{ throw 'Incomplete diagnostics file.' }};",
        "$sha=[Security.Cryptography.SHA256]::Create();",
        "try { $hash=([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-','').ToLowerInvariant() } finally { $sha.Dispose() };",
        f"if ($hash -ne '{digest}') {{ throw 'SHA-256 mismatch. Download the complete TXT again.' }};",
        "$script=Join-Path ([IO.Path]::GetTempPath()) ('AeroKineticDiag_'+[Guid]::NewGuid().ToString('N')+'.py');",
        "[IO.File]::WriteAllBytes($script,$bytes);",
        "& $env:AK_DIAG_PYTHON -I $script --root $env:AK_DIAG_ROOT; $code=$LASTEXITCODE;",
        "} catch { Write-Host $_.Exception.Message -ForegroundColor Red; $code=1 }",
        "finally { if ($script -and (Test-Path -LiteralPath $script)) { Remove-Item -LiteralPath $script -ErrorAction SilentlyContinue } }; exit $code;",
    ])
    assert '"' not in ps and len(ps) < 6000
    header = ["@echo off", "setlocal DisableDelayedExpansion",
              "title AeroKinetic - collect offline compiler diagnostics",
              "rem Save as COLLECT_COMPILER_DIAGNOSTICS.bat in your AeroKinetic folder.",
              'cd /d "%~dp0"', 'if errorlevel 1 exit /b 1',
              'if not exist ".venv\\Scripts\\python.exe" (',
              "  echo Put this BAT next to 6_run_AEROKINETIC.bat in the EXISTING installed program folder.",
              "  echo The collector uses its existing Python. No packages or C++ compiler are needed.",
              "  pause", "  exit /b 1", ")",
              'set "AK_DIAG_SELF=%~f0"', 'set "AK_DIAG_ROOT=%~dp0."',
              'set "AK_DIAG_PYTHON=%~dp0.venv\\Scripts\\python.exe"',
              "echo Collecting local setup logs and a read-only Microsoft certificate inventory...",
              "echo No downloads, setup, certificate imports or policy changes will be performed.",
              "echo A new COMPILER_DIAGNOSTICS_*.txt report will appear in this folder.",
              f'powershell.exe -NoLogo -NoProfile -NonInteractive -Command "{ps}"',
              'set "AK_DIAG_RESULT=%ERRORLEVEL%"',
              'if "%AK_DIAG_RESULT%"=="0" echo DONE. Send the new COMPILER_DIAGNOSTICS_*.txt report.',
              'if not "%AK_DIAG_RESULT%"=="0" echo FAILED. Send the error text displayed above.',
              "pause", "exit /b %AK_DIAG_RESULT%", "", "AK_DIAG_BEGIN"]
    encoded = base64.b64encode(source).decode("ascii")
    raw = ("\r\n".join(header) + "\r\n" + "\r\n".join(encoded[i:i+76] for i in range(0, len(encoded), 76)) +
           "\r\nAK_DIAG_END\r\n").encode("ascii")
    output = root.parent / "DOWNLOAD_AEROKINETIC/COLLECT_COMPILER_DIAGNOSTICS.txt"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(raw)
    output.chmod(0o644)
    for data in (raw, raw.replace(b"\r\n", b"\n")):
        match = re.search(rb"(?ms)^AK_DIAG_BEGIN\r?\n(?P<data>.*?)^AK_DIAG_END\r?$", data)
        assert base64.b64decode(re.sub(rb"\s+", b"", match["data"]), validate=True) == source
    report = {"file": output.name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
              "source_sha256": digest, "roundtrip_crlf_and_lf": "passed",
              "windows_bat_powershell_execution": "not_run", "dependencies_or_solver_changed": False}
    output.with_suffix(".check.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
