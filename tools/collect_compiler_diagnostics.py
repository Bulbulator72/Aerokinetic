"""Read local VS bootstrapper evidence; write one new report, without downloads.

Uses the standard library only. The optional Windows PowerShell subprocess
reads certificate stores and registry values; it does not validate chains,
import certificates, run setup, change policy, or invoke network APIs.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import locale
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid


LOG_LIMIT = 512 * 1024
PCA2024_THUMBPRINT = "D30F05F637E605239C0070D1EA9860D434AC2A94"
WINDOWS_PROBE = r"""
$ErrorActionPreference='Stop';
$result=@{};
try {
  $os=Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion';
  $result.Windows=@{ProductName=$os.ProductName;DisplayVersion=$os.DisplayVersion;CurrentBuild=$os.CurrentBuild;UBR=$os.UBR};
} catch { $result.WindowsError=$_.Exception.Message };
$result.PowerShell=$PSVersionTable.PSVersion.ToString();
try {
  $id=[Security.Principal.WindowsIdentity]::GetCurrent();
  $p=New-Object Security.Principal.WindowsPrincipal($id);
  $result.Elevated=$p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator);
} catch { $result.ElevationError=$_.Exception.Message };
$stores=@();
foreach ($name in @('Cert:\LocalMachine\Root','Cert:\LocalMachine\CA','Cert:\CurrentUser\Root','Cert:\CurrentUser\CA','Cert:\LocalMachine\Disallowed','Cert:\CurrentUser\Disallowed')) {
  try {
    $certs=@(Get-ChildItem -LiteralPath $name -ErrorAction Stop | Where-Object { $_.Subject -match 'Microsoft.*(Root|Code Signing|PCA)' } | ForEach-Object {
      @{Subject=$_.Subject;Issuer=$_.Issuer;Thumbprint=$_.Thumbprint;NotBeforeUtc=$_.NotBefore.ToUniversalTime().ToString('o');NotAfterUtc=$_.NotAfter.ToUniversalTime().ToString('o')}
    });
    $stores+=@{Store=$name;Certificates=$certs};
  } catch { $stores+=@{Store=$name;Error=$_.Exception.Message} };
};
$result.Stores=$stores;
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding;
$result | ConvertTo-Json -Depth 7 -Compress;
"""


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def decode_log(data):
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    if data[:128].count(b"\0") > 10:
        return data.decode("utf-16-le", errors="replace")
    for encoding in ("utf-8-sig", locale.getpreferredencoding(False), "cp1251"):
        try:
            return data.decode(encoding)
        except (UnicodeError, LookupError):
            pass
    return data.decode("utf-8", errors="replace")


def bounded_log(path):
    size = path.stat().st_size
    with path.open("rb") as stream:
        if size <= LOG_LIMIT:
            return decode_log(stream.read(LOG_LIMIT))
        # Bootstrapper header and final exception are both useful. Never load
        # an unbounded installation log into memory or into the report.
        first = stream.read(LOG_LIMIT // 4)
        stream.seek(-3 * LOG_LIMIT // 4, os.SEEK_END)
        tail = stream.read(3 * LOG_LIMIT // 4)
    if first.startswith((b"\xff\xfe", b"\xfe\xff")):
        tail = first[:2] + tail
    return (decode_log(first) + "\n[TRUNCATED: middle of file omitted]\n" + decode_log(tail))


def recent_logs(root, temp):
    paths = []
    notes = []
    if temp:
        try:
            paths.extend(p for p in temp.glob("dd_*.log") if p.is_file() and
                         p.name.lower().startswith(("dd_bootstrapper", "dd_setup", "dd_installer", "dd_vs_")))
        except OSError as error:
            notes.append("TEMP log scan failed: " + str(error))
    details = root / "downloads/compiler_install_details"
    if details.is_dir():
        try:
            directories = sorted((p for p in details.iterdir() if p.is_dir()),
                                 key=lambda p: p.stat().st_mtime, reverse=True)[:3]
            for directory in directories:
                paths.extend(p for p in directory.glob("*.log") if p.is_file())
        except OSError as error:
            notes.append("Copied-log scan failed: " + str(error))
    ordered = []
    for path in set(paths):
        try:
            ordered.append((path.stat().st_mtime, str(path), path))
        except OSError as error:
            notes.append("Log stat failed: " + str(error))
    ordered.sort(reverse=True)
    # Reserve space for bootstrapper evidence even if many setup logs are newer.
    bootstrap = [p for _, _, p in ordered if p.name.lower().startswith("dd_bootstrapper")][:4]
    other = [p for _, _, p in ordered if p not in bootstrap][:8]
    return bootstrap + other, notes


def probe_windows():
    if sys.platform != "win32":
        return {"status": "not_run", "reason": "Requires native Windows; no certificate/registry probe run"}
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", WINDOWS_PROBE],
            capture_output=True, timeout=45, encoding="utf-8", errors="replace", check=False)
        if result.returncode:
            return {"status": "failed", "exit_code": result.returncode, "stderr": result.stderr[-8000:]}
        return {"status": "passed", "inventory_only_not_signature_validation": True,
                "data": json.loads(result.stdout.lstrip("\ufeff"))}
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        return {"status": "failed", "error": str(error)}


def collect(root, temp=None):
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError("Program folder does not exist")
    temp = Path(temp) if temp else None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    output = root / ("COMPILER_DIAGNOSTICS_" + stamp + ".txt")
    notes = []
    lines = ["AeroKinetic - OFFLINE compiler diagnostics (read-only collection)",
             "UTC: " + datetime.now(timezone.utc).isoformat(),
             "Local time: " + datetime.now().astimezone().isoformat(),
             "Python: " + sys.version.replace("\n", " "),
             "Platform: " + sys.platform, "Program folder: " + str(root),
             "No installer executed. No certificate imported. No downloads requested.",
             "This report does NOT establish the cause of exit code 5003 or verify the full layout.",
             "", "=== WINDOWS / MICROSOFT CERTIFICATE INVENTORY ===",
             "Inventory only: file signatures, chains and revocation are NOT checked.",
             "Expected Microsoft Windows Code Signing PCA 2024 thumbprint: " + PCA2024_THUMBPRINT,
             json.dumps(probe_windows(), ensure_ascii=False, indent=2),
             "", "=== LOCAL LAYOUT FILES ==="]
    layout = root / "downloads/vs_buildtools"
    expected = ["vs_BuildTools.exe", "Catalog.json", "ChannelManifest.json", "Response.json",
                "Layout.json", "vs_installer.opc", "vs_installer.version.json",
                "Certificates/manifestRootCertificate.cer", "Certificates/manifestCounterSignRootCertificate.cer",
                "Certificates/vs_installer_opc.RootCertificate.cer",
                "ExtraCertificates/Intermediate/MicrosoftWindowsCodeSigningPCA2024.cer"]
    for name in expected:
        path = layout / name
        try:
            if not path.is_file():
                lines.append("MISSING: " + name)
                continue
            stat = path.stat()
            lines.append(f"{name}: bytes={stat.st_size}; UTC={datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()}; SHA256={sha256(path)}")
        except OSError as error:
            lines.append(name + ": READ ERROR: " + str(error))
            notes.append("Some layout metadata could not be read")
    lines.append("File presence/hash is not Microsoft layout verification; some optional files may be absent.")
    logs, scan_notes = recent_logs(root, temp)
    notes.extend(scan_notes)
    headings = [root / "downloads/compiler_install.log", root / "downloads/compiler_download.log"]
    selected = [p for p in headings if p.is_file()] + logs
    if not any(p.name.lower().startswith("dd_bootstrapper") for p in selected):
        notes.append("No dd_bootstrapper log found. Run immediately after a failed BAT 2 attempt, using the same Windows account.")
    seen = set()
    lines.extend(["", "=== RECENT LOCAL INSTALLER LOGS ===", "Paths and timestamps distinguish old attempts from the current attempt."])
    for path in selected:
        try:
            digest = sha256(path)
            stat = path.stat()
            lines.extend(["", "--- " + str(path) + " ---",
                          f"bytes={stat.st_size}; UTC={datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()}; SHA256={digest}"])
            if digest in seen:
                lines.append("Identical log already included above.")
                continue
            seen.add(digest)
            body = bounded_log(path)
            important = [line for line in body.splitlines() if re.search(
                r"InvalidCertificate|certificate could not|Unable to verify|5003|0x[0-9a-f]{8}|error|failed|vs_installer\.opc", line, re.I)]
            if important:
                lines.extend(["[Keyword excerpts; full bounded log follows]", *important[-40:]])
            lines.extend(["[Log contents]", body, "[End log]"])
        except OSError as error:
            lines.append("READ ERROR: " + str(error))
            notes.append("Some logs could not be read")
    lines.extend(["", "=== COLLECTION NOTES ===", *(notes or ["No file collection errors recorded."]),
                  "Latest installation root cause: inconclusive pending review of this report.",
                  "Native Windows installer execution: not performed by this collector."])
    # Exclusive creation: preserve previous reports, projects, installers and logs.
    with output.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write("\n".join(lines) + "\n")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")
    try:
        report = collect(args.root, os.environ.get("TEMP"))
        print("Report saved:", report, flush=True)
        print("Send this COMPILER_DIAGNOSTICS_*.txt file. Nothing was installed or downloaded.", flush=True)
        return 0
    except Exception as error:
        print("Could not create diagnostics report:", error, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
