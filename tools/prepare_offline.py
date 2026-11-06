"""Prepare an offline wheelhouse on a connected machine; standard library only.

Run with 64-bit CPython 3.12. Target Windows can be prepared from Linux and vice
versa. Only the separate compiler-layout BAT requires Windows.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.request


ROOT = Path(__file__).resolve().parents[1]


def run(*args):
    subprocess.run([str(a) for a in args], check=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", choices=["windows", "linux"], default="windows")
    parser.add_argument("--destination", type=Path, default=ROOT / "downloads")
    parser.add_argument("--python-installer", action="store_true")
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 12):
        parser.error("Run the preparation script with CPython 3.12")
    dest = args.destination.resolve()
    wheelhouse = dest / f"wheels_{args.platform}"
    wheelhouse.mkdir(parents=True, exist_ok=True)
    platforms = ["win_amd64"] if args.platform == "windows" else [
        "manylinux_2_28_x86_64", "manylinux_2_27_x86_64", "manylinux_2_17_x86_64", "manylinux2014_x86_64"]
    command = [sys.executable, "-m", "pip", "download", "--only-binary=:all:", "--implementation", "cp",
               "--python-version", "312", "--abi", "cp312", "--dest", str(wheelhouse),
               "-r", str(ROOT / "requirements-offline-py312.txt")]
    for platform in platforms:
        command += ["--platform", platform]
    run(*command)
    # A pure-Python project wheel can be built for both targets on the host.
    build_env = dest / "build_env"
    if not build_env.exists():
        run(sys.executable, "-m", "venv", str(build_env))
    build_python = build_env / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    run(build_python, "-m", "pip", "install", "--no-index", "--find-links", wheelhouse,
        "setuptools==84.0.0", "wheel==0.48.0")
    run(build_python, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation", "--wheel-dir", wheelhouse, ROOT)
    entries = []
    for path in sorted(wheelhouse.glob("*.whl")):
        entries.append({"file": path.name, "bytes": path.stat().st_size,
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    manifest = {"python": "3.12", "platform": args.platform, "files": entries}
    (wheelhouse / "SHA256.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    if args.python_installer:
        installer = dest / "python-3.12.10-amd64.exe"
        if not installer.exists():
            with urllib.request.urlopen("https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe", timeout=120) as response:
                installer.write_bytes(response.read())
    print(f"Prepared {len(entries)} wheels in {wheelhouse}. Copy the entire project and downloads directory.")


if __name__ == "__main__":
    main()
