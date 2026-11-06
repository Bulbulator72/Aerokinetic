"""Create a compact, hash-checked offline 0.4.0 updater delivered as TXT/BAT."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import zipfile


def make_update(wheel, output):
    root = Path(__file__).resolve().parents[1]
    entries = {wheel.name: wheel.read_bytes(), "install_update.py": (root / "tools/install_update.py").read_bytes()}
    files = [root / name for name in (
        "pyproject.toml", "LICENSE", "AGENTS.md", "README_RU.md", "requirements-offline-py312.txt", "3_install_WINDOWS_OFFLINE.bat",
        "2_install_COMPILER_OFFLINE.bat", "docs/COMPILER_INSTALL_FIX_RU.md",
        "docs/COMPILER_CERTIFICATES_RU.md",
        "1b_download_COMPILER_ONLINE.bat", "docs/HOME_COMPILER_PREP_RU.md",
        "START_DESKTOP_RU.md", "VALIDATION_RU.md",
        "4_run_PREVIEW.bat", "6_run_AEROKINETIC.bat",
        "tools/configure_windows_compiler.py", "tools/make_compiler_fix.py",
        "docs/COMPILER_DISCOVERY_FIX_RU.md",
        "docs/RELEASE_021_RU.md", "docs/RELEASE_030_RU.md", "docs/RELEASE_040_RU.md", "docs/MESH_RESIDUALS_CPU_RU.md", "docs/REFINED_AIRFOIL_RU.md",
        "docs/ROADMAP_RU.md", "docs/UI_WORKFLOW_RU.md", "docs/INTERFACE_RU.md", "docs/REFINEMENT_RU.md",
        "docs/PROGRAM_REQUIREMENTS_RU.md", "docs/BENCHMARK_PLAN_RU.md",
        "docs/TEMPORAL_DIAGNOSTICS_RU.md", "docs/verification_021/PLAN.json",
        "tools/install_update.py", "tools/make_update_transfer.py", "tools/smoke_statistics.py",
        "tools/install_linux_offline.sh", "tools/check_offline_runtime.py",
        "tools/check_refined_offline.py", "tools/check_040_offline.py", "tools/smoke_refined.py", "tools/smoke_040.py", "tools/benchmark_cpu.py",
        "examples/naca0012_refined_re30000_preview.json", "examples/naca0012_refined_re30000_cpu.json")]
    for directory in (root / "src/aerokinetic", root / "tests", root / ".agents/skills",
                      root / "docs/verification_021", root / "docs/verification_compiler_fix",
                      root / "docs/verification_compiler_install", root / "docs/verification_ui_skill"):
        files.extend(p for p in directory.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    # Preserve compact numerical evidence. Large fields, JIT caches and test
    # checkpoints are development artifacts and do not belong in the updater.
    for directory in ("verification_refinement", "verification_interface", "verification_refined_airfoil", "verification_040"):
        files.extend(p for p in (root / "docs" / directory).glob("*") if p.suffix in (".md", ".json", ".csv", ".py", ".xml", ".png"))
    for path in sorted(set(files)):
        entries["source/" + path.relative_to(root).as_posix()] = path.read_bytes()
    entries["README_RU.txt"] = (root / "docs/RELEASE_040_RU.md").read_bytes()
    manifest = {name: {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                for name, data in sorted(entries.items())}
    output.parent.mkdir(parents=True, exist_ok=True)
    archive = output.with_suffix(".zip")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name, data in sorted(entries.items()):
            bundle.writestr(name, data)
        bundle.writestr("SHA256.json", json.dumps(manifest, indent=2) + "\n")
    payload = archive.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    ps = " ".join([
        "$ErrorActionPreference='Stop';",
        "try {",
        "$raw=[IO.File]::ReadAllText($env:AEROKINETIC_UPDATE_SELF);",
        "$m=[regex]::Match($raw,'(?ms)^AEROKINETIC_UPDATE_BEGIN\\r?\\n(?<data>.*?)^AEROKINETIC_UPDATE_END\\r?$');",
        "if (-not $m.Success) { throw 'Save the whole TXT file, including the payload.' };",
        "$bytes=[Convert]::FromBase64String($m.Groups['data'].Value);",
        f"if ($bytes.Length -ne {len(payload)}) {{ throw 'Incomplete update payload.' }};",
        "$sha=[Security.Cryptography.SHA256]::Create();",
        "try { $hash=([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-','').ToLowerInvariant() } finally { $sha.Dispose() };",
        f"if ($hash -ne '{digest}') {{ throw 'SHA-256 mismatch. Save the whole file again.' }};",
        "$folder=Join-Path (Join-Path $env:AEROKINETIC_UPDATE_ROOT 'updates') ('update_0.4.0_'+[Guid]::NewGuid().ToString('N'));",
        "New-Item -ItemType Directory -Path $folder -ErrorAction Stop | Out-Null;",
        "$zip=Join-Path $folder 'update.zip'; [IO.File]::WriteAllBytes($zip,$bytes);",
        "Expand-Archive -LiteralPath $zip -DestinationPath $folder -ErrorAction Stop;",
        "& $env:AEROKINETIC_UPDATE_PYTHON (Join-Path $folder 'install_update.py') --root $env:AEROKINETIC_UPDATE_ROOT;",
        "if ($LASTEXITCODE -ne 0) { throw ('Update failed. Log: '+(Join-Path $folder 'install.log')) };",
        "Write-Host 'AeroKinetic 0.4.0 installed. Start 6_run_AEROKINETIC.bat.' -ForegroundColor Green;",
        "Write-Host ('Backup and update log: '+$folder);",
        "} catch { Write-Host $_.Exception.Message -ForegroundColor Red; exit 1 }; exit 0;",
    ])
    assert '"' not in ps and len(ps) < 7000
    header = ["@echo off", "setlocal DisableDelayedExpansion", "title AeroKinetic 0.4.0 - offline update",
              "rem Save this entire text as UPDATE_AEROKINETIC_040.bat in the existing program folder.",
              'cd /d "%~dp0"', 'if not exist ".venv\\Scripts\\python.exe" (',
              "  echo Put this BAT next to 6_run_AEROKINETIC.bat and the .venv folder.", "  pause", "  exit /b 1", ")",
              'set "AEROKINETIC_UPDATE_SELF=%~f0"', 'set "AEROKINETIC_UPDATE_ROOT=%~dp0."',
              'set "AEROKINETIC_UPDATE_PYTHON=%~dp0.venv\\Scripts\\python.exe"',
              "echo Close AeroKinetic before running this update.",
              "echo Installing mesh builder, residuals and threaded CPU calculation. Internet is not needed.",
              "echo A backup will be created; projects and calculation results are preserved.",
              f'powershell.exe -NoLogo -NoProfile -NonInteractive -Command "{ps}"',
              "if errorlevel 1 (", "  echo Update failed. Keep the error text above.", "  pause", "  exit /b 1", ")",
              "pause", "exit /b 0", "", "AEROKINETIC_UPDATE_BEGIN"]
    encoded = base64.b64encode(payload).decode("ascii")
    data = ("\r\n".join(header) + "\r\n" + "\r\n".join(encoded[i:i+76] for i in range(0,len(encoded),76)) +
            "\r\nAEROKINETIC_UPDATE_END\r\n").encode("ascii")
    output.write_bytes(data)
    output.chmod(0o644)
    for saved in (data, data.replace(b"\r\n", b"\n")):
        match = re.search(rb"(?ms)^AEROKINETIC_UPDATE_BEGIN\r?\n(?P<data>.*?)^AEROKINETIC_UPDATE_END\r?$", saved)
        assert base64.b64decode(re.sub(rb"\s+", b"", match["data"]), validate=True) == payload
    with zipfile.ZipFile(archive) as bundle:
        assert bundle.testzip() is None
        for name, value in manifest.items():
            assert hashlib.sha256(bundle.read(name)).hexdigest() == value["sha256"]
    result = {"artifact": output.name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
              "embedded_zip_bytes": len(payload), "embedded_zip_sha256": digest,
              "roundtrip_crlf_and_lf": "passed", "zip_crc_and_manifest": "passed",
              "windows_bat_execution": "not_run", "application_wheel": wheel.name,
              "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(), "dependencies_added": False}
    output.with_suffix(".check.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    make_update(args.wheel.resolve(), args.output.resolve())
