"""Create a TXT-delivered Windows launcher for standalone load diagnostics."""
import base64
import hashlib
import json
from pathlib import Path
import re


def main():
    root = Path(__file__).resolve().parents[1]
    source = (root / "src/aerokinetic/temporal.py").read_bytes()
    compile(source, "aerokinetic_temporal.py", "exec")
    digest = hashlib.sha256(source).hexdigest()
    ps = " ".join([
        "$ErrorActionPreference='Stop'; $script=$null; $code=1;",
        "try {",
        "$raw=[IO.File]::ReadAllText($env:AEROKINETIC_STATS_SELF);",
        "$m=[regex]::Match($raw,'(?ms)^AEROKINETIC_STATS_BEGIN\\r?\\n(?<data>.*?)^AEROKINETIC_STATS_END\\r?$');",
        "if (-not $m.Success) { throw 'Save the whole text file, including the payload.' };",
        "$bytes=[Convert]::FromBase64String($m.Groups['data'].Value);",
        f"if ($bytes.Length -ne {len(source)}) {{ throw 'Incomplete diagnostic script.' }};",
        "$sha=[Security.Cryptography.SHA256]::Create();",
        "try { $hash=([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-','').ToLowerInvariant() } finally { $sha.Dispose() };",
        f"if ($hash -ne '{digest}') {{ throw 'SHA-256 mismatch. The text is damaged or incomplete.' }};",
        "$script=Join-Path ([IO.Path]::GetTempPath()) ('AeroKineticDiagnostics_'+[Guid]::NewGuid().ToString('N')+'.py');",
        "[IO.File]::WriteAllBytes($script,$bytes);",
        "& $env:AEROKINETIC_STATS_PYTHON $script; $code=$LASTEXITCODE;",
        "} catch { Write-Host $_.Exception.Message -ForegroundColor Red; $code=1 }",
        "finally { if ($script -and (Test-Path -LiteralPath $script)) { Remove-Item -LiteralPath $script -ErrorAction SilentlyContinue } };",
        "exit $code;",
    ])
    assert '"' not in ps and len(ps) < 7000
    header = [
        "@echo off", "setlocal DisableDelayedExpansion",
        "title AeroKinetic - inspect load history",
        "rem Save this complete text as ANALYZE_RESULTS.bat in the program folder.",
        'cd /d "%~dp0"',
        'if not exist ".venv\\Scripts\\python.exe" (',
        "  echo Put this BAT in the AeroKinetic folder containing .venv.",
        "  pause", "  exit /b 1", ")",
        'set "AEROKINETIC_STATS_SELF=%~f0"',
        'set "AEROKINETIC_STATS_PYTHON=%~dp0.venv\\Scripts\\python.exe"',
        "echo Select a completed or saved result folder in the dialog.",
        "echo It must contain history.csv, metadata.json and summary.json.",
        "echo A diagnostic report and FOR_REVIEW.zip will be created in a new subfolder.",
        "echo The solver and existing results are preserved. Internet is not needed.",
        f'powershell.exe -NoLogo -NoProfile -NonInteractive -Command "{ps}"',
        "if errorlevel 1 (", "  echo Analysis failed. Copy the error text above.",
        "  pause", "  exit /b 1", ")", "pause", "exit /b 0", "",
        "AEROKINETIC_STATS_BEGIN",
    ]
    encoded = base64.b64encode(source).decode("ascii")
    content = ("\r\n".join(header) + "\r\n" +
               "\r\n".join(encoded[i:i+76] for i in range(0,len(encoded),76)) +
               "\r\nAEROKINETIC_STATS_END\r\n").encode("ascii")
    output = root.parent / "DOWNLOAD_AEROKINETIC" / "ANALYZE_RESULTS.txt"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(content)
    output.chmod(0o644)
    for saved in (content, content.replace(b"\r\n", b"\n")):
        match = re.search(rb"(?ms)^AEROKINETIC_STATS_BEGIN\r?\n(?P<data>.*?)^AEROKINETIC_STATS_END\r?$", saved)
        decoded = base64.b64decode(re.sub(rb"\s+", b"", match["data"]), validate=True)
        assert decoded == source
    report = {"artifact": output.name, "bytes": len(content),
              "sha256": hashlib.sha256(content).hexdigest(), "source_sha256": digest,
              "source_roundtrip_crlf_and_lf": "passed", "windows_launcher_execution": "not_run",
              "solver_or_dependency_changes": False}
    output.with_suffix(".check.json").write_text(json.dumps(report, indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
