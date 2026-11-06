"""Deliver the isolated portable Windows builder as one TXT/BAT, not an EXE."""
import argparse
import base64
import hashlib
import io
import json
from pathlib import Path
import re
import zipfile


def make_transfer(wheel, output):
    root = Path(__file__).resolve().parents[1]
    wheel, output = Path(wheel), Path(output)
    batch_name = output.stem + '.bat'
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', batch_name):
        raise ValueError('Use an ASCII artifact filename containing letters, digits, dot, dash or underscore')
    paths = [root / name for name in ('AGENTS.md', 'LICENSE', 'README_RU.md', 'VALIDATION_RU.md',
             'requirements-offline-py312.txt', 'pyproject.toml', 'START_DESKTOP_RU.md',
             'tools/configure_windows_compiler.py', 'tools/make_portable_builder_transfer.py',
             'tools/smoke_ui_050.py')]
    for directory in ('tools/portable', 'src/aerokinetic', 'examples', '.agents/skills', 'tests'):
        paths += [p for p in (root / directory).rglob('*') if p.is_file() and '__pycache__' not in p.parts]
    paths += [p for p in (root / 'docs').rglob('*') if p.suffix in ('.md', '.json', '.csv', '.xml', '.py')]
    entries = {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(set(paths))}
    entries['wheelhouse/' + wheel.name] = wheel.read_bytes()
    manifest = {name: {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                for name, data in entries.items()}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(entries.items()):
            archive.writestr(name, data)
        archive.writestr('KIT_SHA256.json', json.dumps(manifest, indent=2) + '\n')
    payload = buffer.getvalue()
    digest = hashlib.sha256(payload).hexdigest()
    ps = ' '.join([
        "$ErrorActionPreference='Stop'; $code=1; try {",
        "$raw=[IO.File]::ReadAllText($env:AK_BUILD_SELF);",
        "$m=[regex]::Match($raw,'(?ms)^AK_BUILDER_BEGIN\\r?\\n(?<data>.*?)^AK_BUILDER_END\\r?$');",
        "if (-not $m.Success) { throw 'Save the complete TXT, including the payload.' };",
        "$bytes=[Convert]::FromBase64String($m.Groups['data'].Value);",
        f"if ($bytes.Length -ne {len(payload)}) {{ throw 'Incomplete build kit.' }};",
        "$sha=[Security.Cryptography.SHA256]::Create();",
        "try { $hash=([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-','').ToLowerInvariant() } finally { $sha.Dispose() };",
        f"if ($hash -ne '{digest}') {{ throw 'Build kit SHA-256 mismatch. Save the complete TXT again.' }};",
        "$folder=Join-Path (Join-Path $env:AK_BUILD_ROOT 'portable_builds') ('kit_'+[Guid]::NewGuid().ToString('N'));",
        "New-Item -ItemType Directory -Path $folder -ErrorAction Stop | Out-Null;",
        "$zip=Join-Path $folder 'builder.zip'; [IO.File]::WriteAllBytes($zip,$bytes);",
        "Expand-Archive -LiteralPath $zip -DestinationPath $folder -ErrorAction Stop;",
        "$manifest=Get-Content -LiteralPath (Join-Path $folder 'KIT_SHA256.json') -Raw | ConvertFrom-Json;",
        "foreach ($entry in $manifest.PSObject.Properties) {",
        "$file=Join-Path $folder $entry.Name;",
        "if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw ('Missing kit file: '+$entry.Name) };",
        "if ((Get-Item -LiteralPath $file).Length -ne $entry.Value.bytes -or (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant() -ne $entry.Value.sha256) { throw ('Kit file differs: '+$entry.Name) };",
        "};",
        "& $env:AK_BUILD_PYTHON (Join-Path $folder 'tools/portable/build.py') --app-root $env:AK_BUILD_ROOT --kit-root $folder;",
        "$code=$LASTEXITCODE;",
        "if ($code -ne 0) { Write-Host 'Build failed. Send PORTABLE_BUILD_LAST.log from the original app folder.' -ForegroundColor Red };",
        "} catch { Write-Host $_.Exception.Message -ForegroundColor Red; $code=1 }; exit $code;",
    ])
    assert '"' not in ps and len(ps) < 6500
    header = ['@echo off', 'setlocal DisableDelayedExpansion',
              'title AeroKinetic - build PRIVATE portable Windows application',
              f'rem Save as {batch_name} next to 6_run_AEROKINETIC.bat.',
              'cd /d "%~dp0"', 'if errorlevel 1 exit /b 1',
              'if not exist ".venv\\Scripts\\python.exe" (',
              '  echo Put this BAT in your EXISTING AeroKinetic folder on the HOME Windows PC.',
              '  echo This folder must contain .venv and 6_run_AEROKINETIC.bat.',
              '  pause', '  exit /b 1', ')',
              'set "AK_BUILD_SELF=%~f0"', 'set "AK_BUILD_ROOT=%~dp0."',
              'set "AK_BUILD_PYTHON=%~dp0.venv\\Scripts\\python.exe"',
              'echo Run on the HOME Windows PC with internet and working C++ calculation tools.',
              'echo Close AeroKinetic. Administrator rights are not needed for this build.',
              'echo The existing app and its .venv will not be changed.',
              'echo A private compiler/SDK copy and isolated environment will need several GB.',
              'echo Preparation and cold-cache tests can take several minutes. Keep this window open.',
              f'powershell.exe -NoLogo -NoProfile -NonInteractive -Command "{ps}"',
              'set "AK_BUILD_RESULT=%ERRORLEVEL%"',
              'echo BUILD_EXIT_CODE=%AK_BUILD_RESULT%',
              'if "%AK_BUILD_RESULT%"=="0" echo Open PORTABLE_READY_TO_COPY.txt for the exact folder to transfer.',
              'pause', 'exit /b %AK_BUILD_RESULT%', '', 'AK_BUILDER_BEGIN']
    encoded = base64.b64encode(payload).decode('ascii')
    raw = ('\r\n'.join(header) + '\r\n' + '\r\n'.join(encoded[n:n+76] for n in range(0, len(encoded), 76)) + '\r\nAK_BUILDER_END\r\n').encode('ascii')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(raw)
    output.chmod(0o644)
    for variant in (raw, raw.replace(b'\r\n', b'\n')):
        match = re.search(rb'(?ms)^AK_BUILDER_BEGIN\r?\n(?P<data>.*?)^AK_BUILDER_END\r?$', variant)
        decoded = base64.b64decode(re.sub(rb'\s+', b'', match['data']), validate=True)
        assert decoded == payload
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        assert archive.testzip() is None
        for name, item in manifest.items():
            assert archive.read(name) == entries[name]
    result = {'artifact': output.name, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
              'embedded_zip_sha256': digest, 'manifest_files': len(manifest),
              'source_wheel_sha256': hashlib.sha256(wheel.read_bytes()).hexdigest(),
              'transport_crc_sha_crlf_lf': 'passed', 'native_windows_build': 'not_run',
              'artifact_kind': 'Windows build script/kit; NOT a finished Windows executable'}
    output.with_suffix('.check.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('wheel', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    make_transfer(args.wheel, args.output)
