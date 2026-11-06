"""Wrap an existing release ZIP in a Windows batch file delivered as plain text.

The payload is base64, not executable code. PowerShell verifies the original ZIP
size and SHA-256 before writing it and extracting it into a new folder. This is
a transport fallback for clients that can open TXT but fail to download ZIP.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import tempfile
import zipfile


def make_transfer(archive: Path, destination: Path) -> dict:
    payload = archive.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    with zipfile.ZipFile(archive) as bundle:
        if bundle.testzip() is not None:
            raise ValueError("The release ZIP has a corrupt member")
        names = bundle.namelist()
        if "aerokinetic/1_download_ONLINE_WINDOWS.bat" not in names:
            raise ValueError("The ZIP does not contain the expected project")

    # One command stays well below cmd.exe's 8191-character limit. Paths are
    # passed through an environment variable, never interpolated into PS code.
    ps = " ".join(
        [
            "$ErrorActionPreference = 'Stop';",
            "try {",
            "$self = [IO.Path]::GetFullPath($env:AEROKINETIC_TRANSFER_FILE);",
            "$text = [IO.File]::ReadAllText($self);",
            "$m = [regex]::Match($text, '(?ms)^AEROKINETIC_ZIP_BEGIN\\r?\\n(?<data>.*?)^AEROKINETIC_ZIP_END\\r?$');",
            "if (-not $m.Success) { throw 'Missing payload. Save the whole text file.' };",
            "$bytes = [Convert]::FromBase64String($m.Groups['data'].Value);",
            f"if ($bytes.Length -ne {len(payload)}) {{ throw 'Archive length mismatch. Save the whole text file.' }};",
            "$sha = [Security.Cryptography.SHA256]::Create();",
            "try { $hash = ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-', '').ToLowerInvariant() } finally { $sha.Dispose() };",
            f"if ($hash -ne '{digest}') {{ throw 'SHA-256 mismatch. The text file is damaged or incomplete.' }};",
            "$root = Join-Path ([IO.Path]::GetDirectoryName($self)) ('AEROKINETIC_02_' + [Guid]::NewGuid().ToString('N'));",
            "New-Item -ItemType Directory -Path $root -ErrorAction Stop | Out-Null;",
            "$zip = Join-Path $root 'AEROKINETIC_START.zip';",
            "[IO.File]::WriteAllBytes($zip, $bytes);",
            "Expand-Archive -LiteralPath $zip -DestinationPath $root -ErrorAction Stop;",
            "$project = Join-Path $root 'aerokinetic';",
            "if (-not (Test-Path -LiteralPath (Join-Path $project '1_download_ONLINE_WINDOWS.bat') -PathType Leaf)) { throw 'The restored project is missing its installer.' };",
            "Write-Host 'SHA-256 verified. Program files restored.' -ForegroundColor Green;",
            "Write-Host ('Folder: ' + $project);",
            "Write-Host 'Next: read 0_READ_SMALL_PACKAGE_RU.txt in that folder.';",
            "} catch { Write-Host ('Restore failed: ' + $_.Exception.Message) -ForegroundColor Red; exit 1 };",
            "exit 0;",
        ]
    )
    command = f'powershell.exe -NoLogo -NoProfile -NonInteractive -Command "{ps}"'
    if len(command) >= 7000 or '"' in ps:
        raise ValueError("PowerShell command exceeds the conservative batch limit")
    batch = [
        "@echo off",
        "setlocal DisableDelayedExpansion",
        "title AEROKINETIC 0.2.0 - restore program files",
        "rem Save this ENTIRE text as AEROKINETIC_RESTORE.bat and double-click.",
        "rem This file restores the small kit. Dependency preparation is separate.",
        f"rem Embedded ZIP SHA-256: {digest}",
        'set "AEROKINETIC_TRANSFER_FILE=%~f0"',
        command,
        "if errorlevel 1 (",
        "  echo No installer was started. See the error above.",
        "  pause",
        "  exit /b 1",
        ")",
        "pause",
        "endlocal",
        "exit /b 0",
        "",
        "AEROKINETIC_ZIP_BEGIN",
    ]
    text = "\r\n".join(batch) + "\r\n"
    encoded = base64.b64encode(payload).decode("ascii")
    text += "\r\n".join(encoded[i : i + 76] for i in range(0, len(encoded), 76))
    text += "\r\nAEROKINETIC_ZIP_END\r\n"
    content = text.encode("ascii")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)
    destination.chmod(0o644)

    # Verify the actual saved file, with both Windows and Unix line endings.
    for raw in (content, content.replace(b"\r\n", b"\n")):
        match = re.search(
            rb"(?ms)^AEROKINETIC_ZIP_BEGIN\r?\n(?P<data>.*?)^AEROKINETIC_ZIP_END\r?$",
            raw,
        )
        if match is None:
            raise AssertionError("Saved text has no payload")
        decoded = base64.b64decode(re.sub(rb"\s+", b"", match["data"]), validate=True)
        if decoded != payload:
            raise AssertionError("Text round-trip changed the original ZIP")

    with tempfile.TemporaryDirectory(prefix="aerokinetic_text_roundtrip_") as tmp:
        root = Path(tmp)
        zip_path = root / "restored.zip"
        zip_path.write_bytes(decoded)
        with zipfile.ZipFile(zip_path) as bundle:
            bundle.extractall(root / "restored")
            for info in bundle.infolist():
                if not info.is_dir():
                    if (root / "restored" / info.filename).read_bytes() != bundle.read(info):
                        raise AssertionError(f"Extraction differs: {info.filename}")

    return {
        "text_file": destination.name,
        "text_bytes": len(content),
        "text_sha256": hashlib.sha256(content).hexdigest(),
        "embedded_zip_bytes": len(payload),
        "embedded_zip_sha256": digest,
        "batch_command_characters": len(command),
        "base64_round_trip_crlf_and_lf": "passed",
        "zip_crc_and_all_extracted_files": "passed",
        "windows_batch_execution": "not_run",
        "user_text_download": "not_tested",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    report = make_transfer(args.archive, args.destination)
    report_path = args.destination.with_suffix(".check.json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
